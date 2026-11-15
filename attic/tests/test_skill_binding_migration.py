"""Legacy skill-binding migration tests (not executed here).

The online shape is a revision-2 binding with a legacy asset id and an MCP
section, but no ``skill-registry`` / ``packages`` / ``commits``. The migration
script must preview without touching state, apply once, preserve the MCP
section, and make a real ``TurnRunner`` load the baseline package.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from molt.agent.chat import ChatReply, MockAdapter
from molt.agent.probe import build_index
from molt.agent.runtime import AgentRuntime
from molt.agent.skill_admin import SkillAdmin, SkillAdminError
from molt.agent.store import SessionStore, new_id
from molt.agent.turn import TurnRunner
from molt.skills import BindingStore, CaptureAdapter, load_package
from molt.skills.eval_tasks import load_tasks
from molt.skills.lifecycle import migrate_existing_binding
from molt.upper.registry import UpperRegistry

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrate_skill_binding.py"
SKILL_ID = "repo-evidence"
OLD_ASSET_ID = "skill-repo-evidence-1.0.0"
TARGET_ASSET_ID = "skill-repo-evidence-1-0-0"


def _legacy_state(tmp_path: Path) -> tuple[BindingStore, Path, dict[str, Any]]:
    package = load_package(ROOT / "skills", SKILL_ID)
    state = tmp_path / "skill-state"
    store = BindingStore(state, repo_root=ROOT)
    old_entry = {
        "skill_id": SKILL_ID,
        "version": "1.0.0",
        "asset_id": OLD_ASSET_ID,
        "package_hash": package.content_hash,
    }
    store.commit("skills", SKILL_ID, old_entry, expected_revision=0)
    mcp_entry = {
        "server_id": "project_kb",
        "version": "1.0.0",
        "asset_id": "mcp-profile-project_kb",
        "package_hash": "sha256:" + "a" * 64,
    }
    store.commit("mcp", "project_kb", mcp_entry, expected_revision=1)
    snapshot = store.load()
    assert snapshot["revision"] == 2
    return store, state, old_entry


def _run_migration(*args: str) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout) if completed.stdout.strip() else {}
    return completed.returncode, payload


def _args(
    state: Path,
    *,
    apply: bool = False,
    skills_root: Path | None = None,
    extra: list[str] | None = None,
) -> list[str]:
    args = [
        "--skill-state",
        str(state),
        "--skills-root",
        str(skills_root or (ROOT / "skills")),
        "--repo-root",
        str(ROOT),
        "--skill-id",
        SKILL_ID,
        "--target-asset-id",
        TARGET_ASSET_ID,
        "--actor",
        "operator",
        "--reason",
        "migrate existing operator binding",
        "--json",
    ]
    if apply:
        args.append("--apply")
    if extra:
        args.extend(extra)
    return args


class _Adapter:
    name = "recording"
    model = "none"
    real_model = False
    adapter_ready = True
    provider_error = None

    def complete(self, messages, tools, tool_choice):  # noqa: ANN001
        return ChatReply(content="ok")


def _good(task: Any) -> str:
    tokens = "；".join(task.expect["must_include"])
    return f"结论：{tokens}。零写入 树快照 replay_of_run_id calls_model。"


def _manual_outputs() -> dict[str, str]:
    tasks = load_tasks(ROOT / "skills_eval")
    transfer = [task for task in tasks if task.phase == "transfer"]
    regression = [task for task in tasks if task.phase == "regression"]
    responses: dict[str, str] = {}
    for index, task in enumerate(transfer):
        responses[f"baseline:{task.task_id}"] = _good(task) if index != 0 else "未验证。"
        responses[f"candidate:{task.task_id}"] = _good(task)
    for task in regression:
        responses[f"baseline:{task.task_id}"] = _good(task)
        responses[f"candidate:{task.task_id}"] = _good(task)
    return responses


def _run_turn(runtime: AgentRuntime, task: str) -> tuple[CaptureAdapter, Any]:
    adapter = CaptureAdapter(_Adapter())
    store = SessionStore(provider="test")
    session = store.create()
    runtime.set_mode(session.session_id, "active")
    TurnRunner(store, build_index(ROOT), adapter, runtime=runtime).run(
        session, new_id("turn"), task
    )
    return adapter, session


def test_legacy_binding_dry_run_does_not_write_state(tmp_path: Path) -> None:
    store, state, _ = _legacy_state(tmp_path)
    before = store.path.read_bytes()

    code, payload = _run_migration(*_args(state))
    assert code == 0
    assert payload["status"] == "planned"
    assert payload["dry_run"] is True
    assert store.path.read_bytes() == before
    assert not (state / "skill-registry").exists()
    assert not (state / "skills" / "packages").exists()
    assert not (state / "skills" / "commits").exists()


def test_apply_migrates_binding_and_preserves_mcp(tmp_path: Path) -> None:
    store, state, old_entry = _legacy_state(tmp_path)
    before_mcp = store.load()["mcp"]

    code, payload = _run_migration(*_args(state, apply=True))
    assert code == 0
    assert payload["status"] == "applied"
    assert payload["dry_run"] is False
    assert payload["revision"] == 3

    snapshot = store.load()
    assert snapshot["revision"] == 3
    assert snapshot["skills"][SKILL_ID]["asset_id"] == TARGET_ASSET_ID
    assert snapshot["mcp"] == before_mcp
    backups = sorted((state / "skills").glob("current.json.bak-*"))
    assert backups
    assert stat.S_IMODE(backups[-1].stat().st_mode) == 0o600

    registry = UpperRegistry(state / "skill-registry", repo_root=ROOT)
    target = registry.get(TARGET_ASSET_ID)
    assert target.status == "active"
    sidecar = state / "skills" / "sidecars" / f"{TARGET_ASSET_ID}.json"
    assert sidecar.is_file()
    assert json.loads(sidecar.read_text(encoding="utf-8"))["review"]["status"] == "not_reviewed"

    package_dir = state / "skills" / "packages" / TARGET_ASSET_ID / SKILL_ID
    assert (package_dir / "manifest.json").is_file()
    assert (package_dir / "SKILL.md").is_file()

    commit = state / "skills" / "commits" / "3.json"
    assert commit.is_file()
    record = json.loads(commit.read_text(encoding="utf-8"))
    assert record["from"]["asset_id"] == old_entry["asset_id"]
    assert record["to"]["asset_id"] == TARGET_ASSET_ID


def test_runtime_loads_migrated_baseline_via_turn_runner(tmp_path: Path) -> None:
    store, state, _ = _legacy_state(tmp_path)
    code, _ = _run_migration(*_args(state, apply=True))
    assert code == 0

    package = load_package(ROOT / "skills", SKILL_ID)
    runtime = AgentRuntime(ROOT, ROOT / "skills", state, None, None, enabled=True)
    try:
        adapter, session = _run_turn(runtime, "如何验证这次改动零写入？")
        loaded = next(
            event.data for event in session.events if event.event == "skill.loaded"
        )
        assert loaded["version"] == "1.0.0"
        assert package.body.strip() in adapter.calls[0]["messages"][0]["content"]

        admin = SkillAdmin(
            ROOT,
            ROOT / "skills",
            state,
            state / "skill-registry",
            MockAdapter(),
            store,
            operator_identity="ops-reviewer",
        )
        candidate = admin.propose(
            SKILL_ID,
            "candidate",
            manual_body="# 修订\n\n零写入 树快照 证据 replay 口径\n",
            version="1.0.1",
        )
        evidence = admin.evaluate(
            candidate["asset_id"], manual_outputs=_manual_outputs()
        )
        admin.approve(
            candidate["asset_id"], "ops-reviewer", evidence_id=evidence["evidence_id"]
        )
        activation = admin.activate(
            candidate["asset_id"],
            store.load()["revision"],
            actor="ops-reviewer",
            evidence_id=evidence["evidence_id"],
        )
        adapter2, session2 = _run_turn(runtime, "如何验证这次改动零写入？")
        loaded2 = next(
            event.data for event in session2.events if event.event == "skill.loaded"
        )
        assert loaded2["version"] == "1.0.1"
        assert "修订" in adapter2.calls[0]["messages"][0]["content"]

        admin.rollback(
            reason="restore baseline",
            rolled_back_by="ops-reviewer",
            expected_revision=activation["revision"],
            evidence_id=evidence["evidence_id"],
        )
        adapter3, session3 = _run_turn(runtime, "如何验证这次改动零写入？")
        loaded3 = next(
            event.data for event in session3.events if event.event == "skill.loaded"
        )
        assert loaded3["version"] == "1.0.0"
        assert package.body.strip() in adapter3.calls[0]["messages"][0]["content"]
    finally:
        runtime.close()


def test_second_apply_is_noop(tmp_path: Path) -> None:
    store, state, _ = _legacy_state(tmp_path)
    assert _run_migration(*_args(state, apply=True))[0] == 0
    code, payload = _run_migration(*_args(state, apply=True))
    assert code == 0
    assert payload["status"] == "noop"
    assert store.load()["revision"] == 3


def test_package_hash_mismatch_leaves_binding_unchanged(tmp_path: Path) -> None:
    store, state, _ = _legacy_state(tmp_path)
    before = store.path.read_bytes()

    tampered = tmp_path / "tampered-skills"
    shutil.copytree(ROOT / "skills", tampered)
    with (tampered / SKILL_ID / "SKILL.md").open("a", encoding="utf-8") as handle:
        handle.write("\n未审查追加行\n")

    code, payload = _run_migration(*_args(state, skills_root=tampered))
    assert code == 2
    assert payload["code"] == "migration_package_mismatch"
    assert store.path.read_bytes() == before
    assert not (state / "skill-registry").exists()


def _crash_migration(
    store: BindingStore, state: Path, old_entry: dict[str, Any], point: str
) -> None:
    package = load_package(ROOT / "skills", SKILL_ID)
    registry = UpperRegistry(state / "skill-registry", repo_root=ROOT)

    def hook(current: str) -> None:
        if current == point:
            raise RuntimeError(f"crash:{point}")

    with pytest.raises(RuntimeError):
        migrate_existing_binding(
            registry,
            store,
            package,
            existing_entry=old_entry,
            target_asset_id=TARGET_ASSET_ID,
            actor="operator",
            reason="migrate existing operator binding",
            dry_run=False,
            fault_hook=hook,
        )


def test_reconcile_only_restores_missing_commit_record(tmp_path: Path) -> None:
    store, state, old_entry = _legacy_state(tmp_path)
    _crash_migration(store, state, old_entry, "after_commit")

    assert store.load()["revision"] == 3
    assert store.load()["skills"][SKILL_ID]["asset_id"] == TARGET_ASSET_ID
    assert not (state / "skills" / "commits" / "3.json").exists()

    code, payload = _run_migration(*_args(state, extra=["--reconcile-only"]))
    assert code == 0
    assert payload["status"] == "reconciled"
    assert payload["adopted"]
    assert (state / "skills" / "commits" / "3.json").is_file()

    admin = SkillAdmin(
        ROOT,
        ROOT / "skills",
        state,
        state / "skill-registry",
        MockAdapter(),
        store,
        operator_identity="ops-reviewer",
    )
    candidate = admin.propose(
        SKILL_ID,
        "candidate",
        manual_body="# 修订\n\n零写入 树快照 证据 replay 口径\n",
        version="1.0.1",
    )
    evidence = admin.evaluate(candidate["asset_id"], manual_outputs=_manual_outputs())
    admin.approve(
        candidate["asset_id"], "ops-reviewer", evidence_id=evidence["evidence_id"]
    )
    activation = admin.activate(
        candidate["asset_id"],
        3,
        actor="ops-reviewer",
        evidence_id=evidence["evidence_id"],
    )
    rolled = admin.rollback(
        reason="restore baseline",
        rolled_back_by="ops-reviewer",
        expected_revision=activation["revision"],
        evidence_id=evidence["evidence_id"],
    )
    assert rolled["target_asset_id"] == TARGET_ASSET_ID
    assert store.load()["skills"][SKILL_ID]["asset_id"] == TARGET_ASSET_ID


def test_after_intent_crash_is_stale_and_cleanup_preserves_current(
    tmp_path: Path,
) -> None:
    store, state, old_entry = _legacy_state(tmp_path)
    before = store.path.read_bytes()
    _crash_migration(store, state, old_entry, "after_intent")

    assert store.load()["revision"] == 2
    assert store.path.read_bytes() == before
    intents = list((state / "skills" / "intents").glob("*.json"))
    assert len(intents) == 1
    assert json.loads(intents[0].read_text(encoding="utf-8"))["status"] == "prepared"
    assert not (state / "skills" / "commits").exists()

    code, payload = _run_migration(
        *(_args(state, extra=["--cleanup-stale-intents"]))
    )
    assert code == 0
    assert payload["status"] == "cleaned"
    assert payload["stale"]
    assert store.path.read_bytes() == before
    assert json.loads(intents[0].read_text(encoding="utf-8"))["status"] == "stale"
    assert intents[0].is_file()
    assert not (state / "skills" / "commits").exists()

    admin = SkillAdmin(
        ROOT,
        ROOT / "skills",
        state,
        state / "skill-registry",
        MockAdapter(),
        store,
        operator_identity="ops-reviewer",
    )
    with pytest.raises(SkillAdminError) as excinfo:
        admin.rollback(
            reason="no target",
            rolled_back_by="ops-reviewer",
            expected_revision=2,
        )
    assert excinfo.value.code == "rollback_target_missing"
    assert store.path.read_bytes() == before
