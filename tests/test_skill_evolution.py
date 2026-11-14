"""Skill evolution tests: candidates, gate reuse, two-phase eval, lifecycle.

Written for pytest but intentionally not run by the implementing session (see the
repository DoD: the integration owner schedules pytest).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from molt.skills.binding import BindingConflict, BindingStore
from molt.skills.eval_runner import ScriptedAdapter, two_phase_evaluate
from molt.skills.eval_tasks import grade, load_tasks
from molt.skills.evolution import (
    ProviderUnavailable,
    SkillEvolutionError,
    candidate_asset_id,
    mirror_gate,
    propose_candidate,
)
from molt.skills.lifecycle import (
    _binding_matches_to,
    _lifecycle_lock,
    activate_asset,
    approve_asset,
    bootstrap_baseline,
    mark_evaluated_from_evidence,
    migrate_existing_binding,
    publish_package,
    reconcile_commits,
    register_asset,
    rollback_asset,
)
from molt.skills.loader import load_package, validate_package
from molt.skills.manifest import package_hash, parse_manifest
from molt.upper.evaluator import UpperEvaluator
from molt.upper.registry import UpperRegistry, UpperRegistryError
from molt.upper.types import utc_now

ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / "skills"
EVAL_ROOT = ROOT / "skills_eval"
SKILL_ID = "repo-evidence"

CANDIDATE_BODY = """\
# 仓库证据与零写入验收（修订版）

## 零写入

- 执行前后各取一次**树快照**并逐项比对；`git status --porcelain` 为空只是必要条件。

## 证据

- run / ledger 必须能重算，逐题配对表随聚合数字一起给出。

## 比较域

- `scenario_version` 不同即 `incomparable`。

## 标注

- replay / fixture / live 如实标注；回放带 `replay_of_run_id`，并记录 `calls_model`。
"""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def base_pkg():
    return load_package(SKILLS_ROOT, SKILL_ID)


def make_candidate(tmp_path: Path, *, version: str = "1.0.1", name: str = "candidates"):
    return propose_candidate(
        base_pkg(),
        "反馈：补充零写入步骤与比较域说明。",
        manual_body=CANDIDATE_BODY,
        version=version,
        candidate_root=tmp_path / name,
    )


def bound_evidence(
    pkg,
    *,
    final: str = "keep",
    asset_id: str | None = None,
    package_hash: str | None = None,
) -> dict[str, Any]:
    """Minimal evidence bound to ``pkg`` (D5.9 fields included)."""
    return {
        "evidence_kind": "evaluation",
        "eval_id": "eval-test",
        "asset_id": asset_id or candidate_asset_id(pkg.skill_id, pkg.version),
        "skill_id": pkg.skill_id,
        "candidate_version": pkg.version,
        "candidate_package_hash": package_hash or pkg.content_hash,
        "baseline_package_hash": pkg.content_hash,
        "final": final,
        "transfer": {"metrics": {"task_count": 3}},
        "regression": {"metrics": {"task_count": 2}},
        "tool_trace": [],
        "tools": [],
        "provenance": {"kind": "manual", "calls_model": False, "driver": "test"},
    }


def ready_asset(registry: UpperRegistry, sidecar_dir: Path, *, version: str, root: Path):
    candidate = make_candidate(root, version=version, name=f"cand-{version}")
    pkg = load_package(candidate.package_dir.parent, SKILL_ID, version)
    asset = register_asset(
        registry,
        pkg,
        created_by="operator",
        origin="synthetic",
        sidecar_dir=sidecar_dir,
        declared_origin="manual",
    )
    mark_evaluated_from_evidence(registry, asset.asset_id, bound_evidence(pkg))
    approve_asset(
        registry,
        asset.asset_id,
        approved_by="operator",
        note="two-phase keep",
        evidence=bound_evidence(pkg),
    )
    return candidate, registry.get(asset.asset_id)


def transfer_tasks():
    return [task for task in load_tasks(EVAL_ROOT) if task.phase == "transfer"]


def regression_tasks():
    return [task for task in load_tasks(EVAL_ROOT) if task.phase == "regression"]


def good_output(task) -> str:
    tokens = "；".join(task.expect["must_include"])
    return f"结论：{tokens}。步骤需在仓库外执行，并保留逐题记录以便重算。"


def scripted_for(baseline_status: list[bool], candidate_status: list[bool]) -> ScriptedAdapter:
    responses: dict[str, str] = {}
    for task, ok in zip(transfer_tasks(), baseline_status, strict=True):
        responses[f"baseline:{task.task_id}"] = good_output(task) if ok else "未验证。"
    for task, ok in zip(transfer_tasks(), candidate_status, strict=True):
        responses[f"candidate:{task.task_id}"] = good_output(task) if ok else "未验证。"
    for task in regression_tasks():
        responses[f"baseline:{task.task_id}"] = good_output(task)
    for task, _ok in zip(regression_tasks(), [True, True], strict=True):
        responses.setdefault(f"candidate:{task.task_id}", good_output(task))
    return ScriptedAdapter(responses=responses)


@dataclass
class FakeProvider:
    content: str
    name: str = "fake-provider"
    model: str = "fake-model"
    real_model: bool = True
    adapter_ready: bool = True
    provider_error: str | None = None
    calls: list[Any] = field(default_factory=list)

    def complete(self, messages, tools=None, tool_choice="none"):
        self.calls.append({"messages": messages, "tool_choice": tool_choice})
        return {"content": self.content, "tool_calls": []}


@dataclass
class PromptKeyedProvider:
    """Real-model-shaped adapter keyed by the last user prompt (for live labels)."""

    responses: dict[str, str]
    name: str = "prompt-keyed"
    model: str = "fake-model"
    real_model: bool = True
    adapter_ready: bool = True
    provider_error: str | None = None
    calls: list[Any] = field(default_factory=list)

    def complete(self, messages, tools=None, tool_choice="none"):
        prompt = messages[-1]["content"]
        self.calls.append(prompt)
        return {"content": self.responses[prompt], "tool_calls": []}


# --------------------------------------------------------------------------- #
# candidate proposal
# --------------------------------------------------------------------------- #
def test_manual_candidate_without_adapter(tmp_path):
    candidate = make_candidate(tmp_path)

    assert candidate.origin == "manual"
    assert candidate.generation_meta == {"authorship": "operator", "calls_model": False}
    assert candidate.version == "1.0.1"
    assert candidate.package_dir == tmp_path / "candidates" / SKILL_ID
    manifest = parse_manifest(candidate.package_dir / "manifest.json")
    body_bytes = (candidate.package_dir / "SKILL.md").read_bytes()
    assert manifest.version == candidate.version
    assert package_hash(manifest, body_bytes) == candidate.content_hash


def test_provider_unavailable_never_fabricates(tmp_path):
    with pytest.raises(ProviderUnavailable) as excinfo:
        propose_candidate(base_pkg(), "反馈", candidate_root=tmp_path / "cands")
    assert "provider_not_configured" in str(excinfo.value)

    # A scripted adapter is not a provider either.
    with pytest.raises(ProviderUnavailable):
        propose_candidate(
            base_pkg(),
            "反馈",
            adapter=ScriptedAdapter(responses={}),
            candidate_root=tmp_path / "cands",
        )
    assert not (tmp_path / "cands").exists()


def test_provider_candidate_records_model_not_credentials(tmp_path):
    adapter = FakeProvider(content=CANDIDATE_BODY)
    candidate = propose_candidate(
        base_pkg(), "反馈：补零写入", adapter=adapter, candidate_root=tmp_path / "cands"
    )

    assert candidate.origin == "provider"
    assert candidate.generation_meta["calls_model"] is True
    assert candidate.generation_meta["model"] == "fake-model"
    assert candidate.generation_meta["driver"] == "fake-provider"
    assert set(candidate.generation_meta) <= {"authorship", "calls_model", "driver", "model"}
    assert adapter.calls  # the adapter really was called


def test_provider_parse_failure_raises(tmp_path):
    adapter = FakeProvider(content="```markdown\n\n```")
    with pytest.raises(SkillEvolutionError):
        propose_candidate(
            base_pkg(), "反馈", adapter=adapter, candidate_root=tmp_path / "cands"
        )


def test_candidate_root_version_conflict(tmp_path):
    make_candidate(tmp_path)
    with pytest.raises(SkillEvolutionError):
        make_candidate(tmp_path, version="1.0.2")


# --------------------------------------------------------------------------- #
# gate reuse
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "metrics",
    [
        {
            "phase": "transfer",
            "candidate": {"pass_rate": 1.0},
            "baseline": {"pass_rate": 0.5},
            "tasks_regressed": [],
        },
        {
            "phase": "transfer",
            "candidate": {"pass_rate": 0.5},
            "baseline": {"pass_rate": 0.5},
            "tasks_regressed": ["t1"],
        },
        {
            "phase": "regression",
            "candidate": {"pass_rate": 0.5},
            "baseline": {"pass_rate": 1.0},
            "tasks_regressed": ["t2"],
        },
        {
            "phase": "regression",
            "candidate": {"pass_rate": 1.0},
            "baseline": {"pass_rate": 1.0},
            "tasks_regressed": [],
        },
    ],
)
def test_mirror_gate_is_the_upper_gate(metrics):
    phase = metrics["phase"]
    assert mirror_gate(phase, metrics) == UpperEvaluator._decide(None, phase, metrics)


def test_mirror_gate_rejects_unknown_phase():
    with pytest.raises(SkillEvolutionError):
        mirror_gate("holdout", {})


# --------------------------------------------------------------------------- #
# two-phase evaluation
# --------------------------------------------------------------------------- #
def _run_two_phase(tmp_path, responses, final_expected):
    baseline = base_pkg()
    candidate = make_candidate(tmp_path)
    adapter = ScriptedAdapter(responses=responses)
    state_dir = tmp_path / "state"
    result = two_phase_evaluate(
        baseline,
        candidate,
        transfer_tasks(),
        regression_tasks(),
        state_dir,
        adapter=adapter,
    )
    assert result["final"] == final_expected
    evidence_path = Path(result["evidence_path"])
    assert evidence_path.is_file()
    assert str(evidence_path).startswith(str(state_dir.resolve()))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    # D5.9: the record is bound to the asset it can promote.
    assert evidence["asset_id"] == candidate_asset_id(candidate.skill_id, candidate.version)
    assert evidence["candidate_version"] == candidate.version
    assert evidence["candidate_package_hash"] == candidate.content_hash
    assert evidence["baseline_package_hash"] == baseline.content_hash
    assert evidence["skill_id"] == candidate.skill_id
    # D45: no tool calls are recorded, and the empty trace is explicit.
    assert evidence["tool_trace"] == []
    assert evidence["tools"] == []
    return result, evidence, adapter


def test_two_phase_keep(tmp_path):
    responses = scripted_for([False, True, True], [True, True, True])
    result, evidence, adapter = _run_two_phase(tmp_path, responses, "keep")

    assert result["transfer"]["verdict"] == "keep"
    assert result["regression"]["verdict"] == "keep"
    assert result["transfer"]["metrics"]["tasks_improved"] == [
        transfer_tasks()[0].task_id
    ]
    assert len(evidence["transfer"]["rows"]) == 3
    assert len(evidence["regression"]["rows"]) == 2
    assert evidence["evidence_kind"] == "evaluation"
    assert evidence["mode"] == "manual"
    assert evidence["calls_model"] is False
    assert evidence["provenance"]["kind"] == "manual"
    assert evidence["provenance"]["calls_model"] is False
    assert evidence["provenance"]["driver"]
    assert evidence["provenance"]["created_at"]
    assert evidence["final"] == "keep"
    assert adapter.real_model is False
    # every row carries the provenance labels and an explicit empty tool trace
    for row in evidence["transfer"]["rows"]:
        assert row["mode"] == "manual"
        assert row["calls_model"] is False
        assert row["tool_trace"] == []
        assert row["tools"] == []


def test_two_phase_reject_without_transfer_gain(tmp_path):
    responses = scripted_for([True, True, True], [True, True, True])
    result, evidence, _ = _run_two_phase(tmp_path, responses, "reject")

    assert result["transfer"]["verdict"] == "reject"
    assert evidence["final"] == "reject"


def test_two_phase_rollback_on_regression(tmp_path):
    responses = scripted_for([False, True, True], [True, True, True])
    # candidate regresses the last regression task
    last = regression_tasks()[-1]
    responses.responses[f"candidate:{last.task_id}"] = "未验证。"
    result, evidence, _ = _run_two_phase(tmp_path, responses, "rollback")

    assert result["transfer"]["verdict"] == "keep"
    assert result["regression"]["verdict"] == "rollback"
    assert result["regression"]["metrics"]["regression_count"] == 1
    assert evidence["final"] == "rollback"


def test_live_label_requires_real_model_adapter(tmp_path):
    baseline = base_pkg()
    candidate = make_candidate(tmp_path)
    mapping: dict[str, str] = {}
    for task in transfer_tasks() + regression_tasks():
        mapping[task.prompt] = good_output(task)
    adapter = PromptKeyedProvider(responses=mapping)
    result = two_phase_evaluate(
        baseline,
        candidate,
        transfer_tasks(),
        regression_tasks(),
        tmp_path / "state",
        adapter=adapter,
    )
    evidence = json.loads(Path(result["evidence_path"]).read_text(encoding="utf-8"))

    assert evidence["mode"] == "live"
    assert evidence["calls_model"] is True
    assert evidence["provenance"] == {
        "kind": "live",
        "calls_model": True,
        "driver": "molt.skills.eval_runner",
        "created_at": evidence["created_at"],
    }
    assert adapter.calls  # actually invoked


def test_eval_tasks_and_grade_are_deterministic():
    tasks = load_tasks(EVAL_ROOT)
    assert [task.phase for task in tasks].count("transfer") == 3
    assert [task.phase for task in tasks].count("regression") == 2

    task = next(task for task in tasks if task.task_id == "zero-write-acceptance")
    ok = grade(task, good_output(task))
    assert ok == {"passed": True, "reasons": []}
    missing = grade(task, "git status --porcelain 就够了")
    assert missing["passed"] is False
    assert any(reason.startswith("missing:") for reason in missing["reasons"])
    long_output = good_output(task) + "补" * task.expect["max_chars"]
    assert grade(task, long_output)["passed"] is False


# --------------------------------------------------------------------------- #
# lifecycle
# --------------------------------------------------------------------------- #
def _activate(registry, store, asset_id, *, revision, sidecars=None, evidence_id="eval-test"):
    return activate_asset(
        registry,
        store,
        asset_id,
        expected_revision=revision,
        actor="operator",
        evidence_id=evidence_id,
        sidecar_dir=sidecars,
    )


def _pkg_for(candidate):
    return load_package(candidate.package_dir.parent, SKILL_ID, candidate.version)


def _packages_dir(state: Path) -> Path:
    return state / "skills" / "packages"


def _commits(state: Path) -> list[dict[str, Any]]:
    directory = state / "skills" / "commits"
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"), key=lambda item: int(item.stem))
    ]


def test_draft_asset_cannot_activate(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"
    candidate = make_candidate(tmp_path)
    pkg = _pkg_for(candidate)
    asset = register_asset(
        registry, pkg, sidecar_dir=sidecars, declared_origin="manual"
    )

    with pytest.raises(SkillEvolutionError):
        _activate(registry, store, asset.asset_id, revision=0, sidecars=sidecars)
    assert asset.status == "draft"
    assert store.get_section("skills", SKILL_ID) is None
    assert store.load()["skills"] == {}
    assert not (state / "skills" / "packages").exists()


def test_evaluated_then_approved_then_active(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"

    candidate = make_candidate(tmp_path)
    pkg = _pkg_for(candidate)
    evidence = bound_evidence(pkg)
    asset = register_asset(registry, pkg, sidecar_dir=sidecars, declared_origin="manual")

    with pytest.raises(UpperRegistryError):
        approve_asset(
            registry,
            asset.asset_id,
            approved_by="operator",
            note="early",
            evidence=evidence,
        )
    with pytest.raises(SkillEvolutionError):
        approve_asset(
            registry,
            asset.asset_id,
            approved_by="",
            note="no actor",
            evidence=evidence,
        )

    mark_evaluated_from_evidence(registry, asset.asset_id, evidence)
    assert registry.get(asset.asset_id).status == "evaluated"
    approve_asset(
        registry,
        asset.asset_id,
        approved_by="operator",
        note="ok",
        evidence=evidence,
    )
    assert registry.get(asset.asset_id).status == "approved"

    result = _activate(registry, store, asset.asset_id, revision=0, sidecars=sidecars)
    entry = result["entry"]
    binding = store.get_section("skills", SKILL_ID)
    assert binding == entry
    assert binding["asset_id"] == asset.asset_id
    assert binding["package_hash"] == candidate.content_hash
    assert result["revision"] == 1

    # D6.6: the adopted package is published and loadable with the same hash.
    published = _packages_dir(state) / asset.asset_id / SKILL_ID
    assert (published / "manifest.json").is_file()
    assert (published / "SKILL.md").is_file()
    published_pkg = load_package(published.parent, SKILL_ID, candidate.version)
    validate_package(published_pkg)
    assert published_pkg.content_hash == candidate.content_hash

    records = _commits(state)
    assert [record["revision"] for record in records] == [1]
    assert records[0]["action"] == "activate"
    assert records[0]["actor"] == "operator"
    assert records[0]["to"]["asset_id"] == asset.asset_id


def test_approve_requires_keep_evidence(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    sidecars = tmp_path / "sidecars"

    def evaluated_asset(version: str, final: str):
        candidate = make_candidate(tmp_path, version=version, name=f"cand-{version}")
        pkg = _pkg_for(candidate)
        asset = register_asset(
            registry, pkg, sidecar_dir=sidecars, declared_origin="manual"
        )
        evidence = bound_evidence(pkg, final=final)
        mark_evaluated_from_evidence(registry, asset.asset_id, evidence)
        return asset, evidence

    reject_asset, reject_evidence = evaluated_asset("1.0.1", "reject")
    with pytest.raises(SkillEvolutionError) as excinfo:
        approve_asset(
            registry,
            reject_asset.asset_id,
            approved_by="operator",
            note="reject must not approve",
            evidence=reject_evidence,
        )
    assert "evidence_not_keep" in str(excinfo.value)
    assert registry.get(reject_asset.asset_id).status == "evaluated"

    rollback_asset_obj, rollback_evidence = evaluated_asset("1.0.2", "rollback")
    with pytest.raises(SkillEvolutionError) as excinfo:
        approve_asset(
            registry,
            rollback_asset_obj.asset_id,
            approved_by="operator",
            note="rollback must not approve",
            evidence=rollback_evidence,
        )
    assert "evidence_not_keep" in str(excinfo.value)
    assert registry.get(rollback_asset_obj.asset_id).status == "evaluated"

    keep_asset, keep_evidence = evaluated_asset("1.0.3", "keep")
    with pytest.raises(SkillEvolutionError):
        approve_asset(
            registry,
            keep_asset.asset_id,
            approved_by="operator",
            note="no evidence",
            evidence=None,
        )
    approve_asset(
        registry,
        keep_asset.asset_id,
        approved_by="operator",
        note="keep",
        evidence=keep_evidence,
    )
    assert registry.get(keep_asset.asset_id).status == "approved"


def test_evidence_bound_to_another_asset_is_rejected(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    sidecars = tmp_path / "sidecars"

    cand_a = make_candidate(tmp_path, version="1.0.1", name="cand-a")
    cand_b = make_candidate(tmp_path, version="1.0.2", name="cand-b")
    pkg_a = _pkg_for(cand_a)
    pkg_b = _pkg_for(cand_b)
    asset_a = register_asset(registry, pkg_a, sidecar_dir=sidecars, declared_origin="manual")
    asset_b = register_asset(registry, pkg_b, sidecar_dir=sidecars, declared_origin="manual")
    evidence_a = bound_evidence(pkg_a)

    # A's evidence must not mark B.
    with pytest.raises(SkillEvolutionError) as excinfo:
        mark_evaluated_from_evidence(registry, asset_b.asset_id, evidence_a)
    assert "evidence_asset_mismatch" in str(excinfo.value)
    assert registry.get(asset_b.asset_id).status == "draft"

    # Same asset, wrong candidate hash → still rejected.
    wrong_hash = bound_evidence(pkg_a, package_hash="sha256:" + "0" * 64)
    with pytest.raises(SkillEvolutionError) as excinfo:
        mark_evaluated_from_evidence(registry, asset_a.asset_id, wrong_hash)
    assert "evidence_asset_mismatch" in str(excinfo.value)
    assert registry.get(asset_a.asset_id).status == "draft"

    # The bound record is accepted.
    mark_evaluated_from_evidence(registry, asset_a.asset_id, evidence_a)
    assert registry.get(asset_a.asset_id).status == "evaluated"


def test_evidence_wrong_skill_id_is_rejected(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    sidecars = tmp_path / "sidecars"
    candidate = make_candidate(tmp_path)
    pkg = _pkg_for(candidate)
    asset = register_asset(registry, pkg, sidecar_dir=sidecars, declared_origin="manual")

    evidence = bound_evidence(pkg)
    evidence["skill_id"] = "another-skill"
    with pytest.raises(SkillEvolutionError) as excinfo:
        mark_evaluated_from_evidence(registry, asset.asset_id, evidence)
    assert "evidence_asset_mismatch" in str(excinfo.value)


def test_failed_commit_keeps_pointer_and_leaves_active_unbound(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"

    _, v1 = ready_asset(registry, sidecars, version="1.0.1", root=tmp_path)
    _activate(registry, store, v1.asset_id, revision=0, sidecars=sidecars)
    _, v2 = ready_asset(registry, sidecars, version="1.0.2", root=tmp_path)

    with pytest.raises(BindingConflict):
        _activate(registry, store, v2.asset_id, revision=0, sidecars=sidecars)

    binding = store.get_section("skills", SKILL_ID)
    assert binding is not None and binding["asset_id"] == v1.asset_id
    assert binding["version"] == "1.0.1"
    # registry allows use, but it is not the execution pointer
    assert registry.get(v2.asset_id).status == "active"
    assert store.load()["revision"] == 1
    sidecar_v1 = json.loads((sidecars / f"{v1.asset_id}.json").read_text(encoding="utf-8"))
    assert "superseded_at" not in sidecar_v1
    assert [record["revision"] for record in _commits(state)] == [1]

    # Retry at the true revision: the unbound active entry can still be used.
    retried = _activate(registry, store, v2.asset_id, revision=1, sidecars=sidecars)
    assert retried["revision"] == 2
    assert store.get_section("skills", SKILL_ID)["asset_id"] == v2.asset_id


def test_publish_failure_keeps_pointer(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"
    _, v1 = ready_asset(registry, sidecars, version="1.0.1", root=tmp_path)

    (state / "skills").mkdir(parents=True, exist_ok=True)
    (state / "skills" / "packages").write_text("blocked", encoding="utf-8")

    with pytest.raises(SkillEvolutionError) as excinfo:
        _activate(registry, store, v1.asset_id, revision=0, sidecars=sidecars)
    assert "package_publish_failed" in str(excinfo.value)
    assert store.get_section("skills", SKILL_ID) is None
    assert store.load()["revision"] == 0
    assert not (state / "skills" / "commits").exists()


def test_successful_activation_marks_previous_sidecar_superseded(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"

    _, v1 = ready_asset(registry, sidecars, version="1.0.1", root=tmp_path)
    _activate(registry, store, v1.asset_id, revision=0, sidecars=sidecars)
    _, v2 = ready_asset(registry, sidecars, version="1.0.2", root=tmp_path)
    result = _activate(registry, store, v2.asset_id, revision=1, sidecars=sidecars)

    assert result["cleanup"] == "not_required"
    assert result["revision"] == 2
    assert store.get_section("skills", SKILL_ID)["asset_id"] == v2.asset_id
    sidecar_v1 = json.loads((sidecars / f"{v1.asset_id}.json").read_text(encoding="utf-8"))
    assert sidecar_v1["superseded_by"] == v2.asset_id
    assert sidecar_v1["superseded_revision"] == 2
    assert sidecar_v1["superseded_at"]
    # registry status of the superseded version is untouched
    assert registry.get(v1.asset_id).status == "active"

    records = _commits(state)
    assert [record["revision"] for record in records] == [1, 2]
    assert records[1]["from"] == {
        "skill_id": SKILL_ID,
        "asset_id": v1.asset_id,
        "version": "1.0.1",
        "hash": result["previous"]["package_hash"],
    }
    assert records[1]["to"]["asset_id"] == v2.asset_id
    assert records[1]["evidence_id"] == "eval-test"


def test_rollback_rejects_tampered_target_package(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"

    _, v1 = ready_asset(registry, sidecars, version="1.0.1", root=tmp_path)
    _activate(registry, store, v1.asset_id, revision=0, sidecars=sidecars)
    _, v2 = ready_asset(registry, sidecars, version="1.0.2", root=tmp_path)
    _activate(registry, store, v2.asset_id, revision=1, sidecars=sidecars)

    target = _packages_dir(state) / v1.asset_id / SKILL_ID / "SKILL.md"
    target.write_text(target.read_text(encoding="utf-8") + "\n篡改\n", encoding="utf-8")

    with pytest.raises(SkillEvolutionError) as excinfo:
        rollback_asset(
            registry,
            store,
            v2.asset_id,
            reason="tampered target",
            rolled_back_by="operator",
            expected_revision=2,
            sidecar_dir=sidecars,
        )
    assert "package_hash_mismatch" in str(excinfo.value)
    assert store.get_section("skills", SKILL_ID)["asset_id"] == v2.asset_id
    assert store.load()["revision"] == 2


def test_rollback_target_unavailable(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"

    _, v1 = ready_asset(registry, sidecars, version="1.0.1", root=tmp_path)
    _activate(registry, store, v1.asset_id, revision=0, sidecars=sidecars)
    _, v2 = ready_asset(registry, sidecars, version="1.0.2", root=tmp_path)
    _activate(registry, store, v2.asset_id, revision=1, sidecars=sidecars)

    shutil.rmtree(_packages_dir(state) / v1.asset_id)
    with pytest.raises(SkillEvolutionError) as excinfo:
        rollback_asset(
            registry,
            store,
            v2.asset_id,
            reason="missing target package",
            rolled_back_by="operator",
            expected_revision=2,
            sidecar_dir=sidecars,
        )
    assert "rollback_target_unavailable" in str(excinfo.value)
    assert store.get_section("skills", SKILL_ID)["asset_id"] == v2.asset_id


def test_rollback_then_forward_again(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"

    _, v1 = ready_asset(registry, sidecars, version="1.0.1", root=tmp_path)
    first = _activate(registry, store, v1.asset_id, revision=0, sidecars=sidecars)
    _, v2 = ready_asset(registry, sidecars, version="1.0.2", root=tmp_path)
    _activate(registry, store, v2.asset_id, revision=1, sidecars=sidecars)

    rolled = rollback_asset(
        registry,
        store,
        v2.asset_id,
        reason="regression detected",
        rolled_back_by="operator",
        expected_revision=2,
        sidecar_dir=sidecars,
    )

    assert rolled["revision"] == 3
    binding = store.get_section("skills", SKILL_ID)
    assert binding["asset_id"] == v1.asset_id
    assert binding["package_hash"] == first["entry"]["package_hash"]
    assert len(store.history_files()) >= 3
    sidecar_v2 = json.loads((sidecars / f"{v2.asset_id}.json").read_text(encoding="utf-8"))
    assert sidecar_v2["rolled_back_by"] == "operator"
    assert sidecar_v2["rolled_back_reason"] == "regression detected"
    assert sidecar_v2["rolled_back_at"]
    # target stays reusable: registry status was never rewritten
    assert registry.get(v1.asset_id).status == "active"

    records = _commits(state)
    assert [record["revision"] for record in records] == [1, 2, 3]
    assert records[2]["action"] == "rollback"
    assert records[2]["actor"] == "operator"
    assert records[2]["reason"] == "regression detected"
    assert records[2]["from"]["asset_id"] == v2.asset_id
    assert records[2]["to"]["asset_id"] == v1.asset_id

    # forward again to the previously active version (same asset, one CAS commit)
    forward = _activate(registry, store, v2.asset_id, revision=3, sidecars=sidecars)
    assert forward["revision"] == 4
    assert store.get_section("skills", SKILL_ID)["asset_id"] == v2.asset_id
    assert forward["previous"]["asset_id"] == v1.asset_id


def test_rollback_without_bootstrap_reports_target_missing(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    pkg = base_pkg()
    entry = {
        "skill_id": pkg.skill_id,
        "version": pkg.version,
        "asset_id": candidate_asset_id(pkg.skill_id, pkg.version),
        "package_hash": pkg.content_hash,
    }
    store.commit("skills", SKILL_ID, entry, expected_revision=0)

    with pytest.raises(SkillEvolutionError) as excinfo:
        rollback_asset(
            registry,
            store,
            entry["asset_id"],
            reason="no adopted predecessor",
            rolled_back_by="operator",
            expected_revision=1,
        )
    assert "rollback_target_missing" in str(excinfo.value)
    assert store.get_section("skills", SKILL_ID) == entry
    assert store.load()["revision"] == 1


def test_bootstrap_baseline_enables_rollback(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"
    pkg = base_pkg()

    boot = bootstrap_baseline(registry, store, pkg, actor="operator")
    baseline_asset = boot["asset"]
    assert baseline_asset.status == "active"
    assert baseline_asset.implementation_status == "implemented"
    assert baseline_asset.eval_state == "not_evaluated"
    assert boot["revision"] == 1
    assert store.get_section("skills", SKILL_ID)["asset_id"] == baseline_asset.asset_id

    sidecar = json.loads(
        (state / "skills" / "sidecars" / f"{baseline_asset.asset_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["declared_origin"] == "operator_baseline"
    assert sidecar["authorship"] == "operator"
    assert sidecar["approved_by"] == "operator-config"
    assert sidecar["review"]["status"] == "not_reviewed"
    assert "not model-evaluated" in sidecar["approval_note"]
    assert _commits(state)[0]["action"] == "activate"
    assert _commits(state)[0]["from"] is None

    _, v1 = ready_asset(registry, sidecars, version="1.0.1", root=tmp_path)
    _activate(registry, store, v1.asset_id, revision=1, sidecars=sidecars)

    rolled = rollback_asset(
        registry,
        store,
        v1.asset_id,
        reason="baseline restore",
        rolled_back_by="operator",
        expected_revision=2,
        sidecar_dir=sidecars,
    )
    assert rolled["entry"]["asset_id"] == baseline_asset.asset_id
    binding = store.get_section("skills", SKILL_ID)
    assert binding["asset_id"] == baseline_asset.asset_id
    assert binding["package_hash"] == pkg.content_hash
    # the registry target is real and resolvable
    assert registry.get(baseline_asset.asset_id).status == "active"


def test_orphan_history_is_ignored_for_rollback_target(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"
    pkg = base_pkg()

    boot = bootstrap_baseline(registry, store, pkg, actor="operator")
    baseline_entry = boot["entry"]
    _, v1 = ready_asset(registry, sidecars, version="1.0.1", root=tmp_path)
    _activate(registry, store, v1.asset_id, revision=1, sidecars=sidecars)

    # Orphan: a history file that was never adopted as current.
    orphan_entry = {
        "skill_id": SKILL_ID,
        "version": pkg.version,
        "asset_id": "skill-orphan-9-9-9",
        "package_hash": "sha256:" + "1" * 64,
    }
    orphan = {
        "schema_version": "1.0.0",
        "revision": 99,
        "skills": {SKILL_ID: orphan_entry},
        "mcp": {},
        "updated_at": None,
    }
    history_dir = state / "skills" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / f"99-{'2' * 64}.json").write_text(
        json.dumps(orphan, ensure_ascii=False), encoding="utf-8"
    )

    rolled = rollback_asset(
        registry,
        store,
        v1.asset_id,
        reason="orphan must not win",
        rolled_back_by="operator",
        expected_revision=2,
        sidecar_dir=sidecars,
    )
    assert rolled["entry"]["asset_id"] == baseline_entry["asset_id"]
    binding = store.get_section("skills", SKILL_ID)
    assert binding["asset_id"] == baseline_entry["asset_id"]
    assert binding["asset_id"] != "skill-orphan-9-9-9"


def test_mark_evaluated_requires_two_phases_and_honest_provenance(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    sidecars = tmp_path / "sidecars"
    candidate = make_candidate(tmp_path)
    pkg = _pkg_for(candidate)
    asset = register_asset(registry, pkg, sidecar_dir=sidecars, declared_origin="manual")
    good = bound_evidence(pkg)

    with pytest.raises(SkillEvolutionError):
        mark_evaluated_from_evidence(
            registry,
            asset.asset_id,
            {**good, "transfer": None},
        )
    with pytest.raises(SkillEvolutionError):
        mark_evaluated_from_evidence(
            registry,
            asset.asset_id,
            {**good, "regression": None},
        )
    with pytest.raises(SkillEvolutionError):
        mark_evaluated_from_evidence(
            registry,
            asset.asset_id,
            {
                **good,
                "provenance": {"kind": "live", "calls_model": False},
            },
        )
    with pytest.raises(SkillEvolutionError):
        mark_evaluated_from_evidence(
            registry, asset.asset_id, {**good, "final": "adopt"}
        )
    assert registry.get(asset.asset_id).status == "draft"
    mark_evaluated_from_evidence(registry, asset.asset_id, {**good, "final": "rollback"})
    assert registry.get(asset.asset_id).status == "evaluated"


def test_publish_package_reuses_same_hash_and_conflicts_on_other(tmp_path):
    state = tmp_path / "state"
    pkg = base_pkg()
    asset_id = candidate_asset_id(pkg.skill_id, pkg.version)

    first = publish_package(state, pkg, asset_id)
    second = publish_package(state, pkg, asset_id)
    assert first == second
    assert first == state / "skills" / "packages" / asset_id / SKILL_ID
    assert load_package(first.parent, SKILL_ID, pkg.version).content_hash == pkg.content_hash

    other = _pkg_for(make_candidate(tmp_path))
    with pytest.raises(SkillEvolutionError) as excinfo:
        publish_package(state, other, asset_id)
    assert "package_conflict" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# legacy binding migration (function level; CLI/runtime lives in its own file)
# --------------------------------------------------------------------------- #
LEGACY_ASSET_ID = "skill-repo-evidence-1.0.0"
MIGRATION_TARGET = candidate_asset_id(SKILL_ID, "1.0.0")
MCP_ENTRY = {
    "server_id": "project_kb",
    "version": "1.0.0",
    "asset_id": "mcp-project-kb-1-0-0",
    "package_hash": "sha256:" + "a" * 64,
}


_LOCK_FILES = {".lifecycle.lock", ".lock"}


def _digests(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in _LOCK_FILES
    }


def _seed_legacy_binding(state: Path):
    store = BindingStore(state)
    pkg = base_pkg()
    store.commit("mcp", "project_kb", MCP_ENTRY, expected_revision=0)
    legacy = {
        "skill_id": SKILL_ID,
        "version": pkg.version,
        "asset_id": LEGACY_ASSET_ID,
        "package_hash": pkg.content_hash,
    }
    store.commit("skills", SKILL_ID, legacy, expected_revision=1)
    assert store.load()["revision"] == 2
    return store, legacy, pkg


def test_migration_dry_run_is_read_only(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store, legacy, pkg = _seed_legacy_binding(state)
    before = _digests(tmp_path)
    before_mcp = store.load()["mcp"]

    plan = migrate_existing_binding(
        registry,
        store,
        pkg,
        existing_entry=legacy,
        target_asset_id=MIGRATION_TARGET,
        actor="operator",
        reason="migrate deployed binding",
        dry_run=True,
    )

    assert plan["status"] == "planned"
    assert plan["dry_run"] is True
    assert plan["revision"] == 2
    assert plan["from"] == legacy
    assert plan["to"] == {
        "skill_id": SKILL_ID,
        "asset_id": MIGRATION_TARGET,
        "version": pkg.version,
        "hash": pkg.content_hash,
    }
    assert plan["plan"]["register_asset"] is True
    assert plan["plan"]["publish"] == "publish"
    assert plan["plan"]["next_revision"] == 3

    assert _digests(tmp_path) == before
    assert not (state / "registry.json").exists()
    assert not (state / "skills" / "packages").exists()
    assert not (state / "skills" / "commits").exists()
    assert not (state / "skills" / "sidecars").exists()
    assert store.load()["revision"] == 2
    assert store.load()["mcp"] == before_mcp


def test_migration_apply_binds_target_and_preserves_mcp(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store, legacy, pkg = _seed_legacy_binding(state)
    before_mcp = store.load()["mcp"]
    before_mcp_json = json.dumps(before_mcp, sort_keys=True)

    result = migrate_existing_binding(
        registry,
        store,
        pkg,
        existing_entry=legacy,
        target_asset_id=MIGRATION_TARGET,
        actor="operator",
        reason="migrate deployed binding",
        dry_run=False,
    )

    assert result["status"] == "applied"
    assert result["dry_run"] is False
    assert result["revision"] == 3
    assert result["registry_status"] == "active"
    assert result["from"] == legacy
    assert result["to"] == {
        "skill_id": SKILL_ID,
        "asset_id": MIGRATION_TARGET,
        "version": pkg.version,
        "hash": pkg.content_hash,
    }
    assert result["commit_record"] == "written"

    asset = registry.get(MIGRATION_TARGET)
    assert asset.status == "active"
    assert asset.origin == "synthetic"
    assert asset.eval_state == "not_evaluated"
    assert asset.implementation_status == "implemented"

    sidecar = json.loads(
        (state / "skills" / "sidecars" / f"{MIGRATION_TARGET}.json").read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["declared_origin"] == "operator_baseline"
    assert sidecar["authorship"] == "operator"
    assert sidecar["approved_by"] == "operator-config"
    assert sidecar["review"]["status"] == "not_reviewed"
    assert "migrated existing operator binding" in sidecar["approval_note"]

    package_dir = state / "skills" / "packages" / MIGRATION_TARGET / SKILL_ID
    assert (package_dir / "manifest.json").is_file()
    assert load_package(package_dir.parent, SKILL_ID, pkg.version).content_hash == (
        pkg.content_hash
    )

    snapshot = store.load()
    assert snapshot["revision"] == 3
    assert snapshot["skills"][SKILL_ID]["asset_id"] == MIGRATION_TARGET
    # whole-snapshot CAS keeps the mcp section byte-identical
    assert snapshot["mcp"] == before_mcp
    assert json.dumps(snapshot["mcp"], sort_keys=True) == before_mcp_json

    record = json.loads(
        (state / "skills" / "commits" / "3.json").read_text(encoding="utf-8")
    )
    assert record["action"] == "migrate"
    assert record["actor"] == "operator"
    assert record["reason"] == "migrate deployed binding"
    assert record["evidence_id"] is None
    assert record["from"] == legacy
    assert record["to"] == result["to"]


def test_migration_second_apply_is_noop(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store, legacy, pkg = _seed_legacy_binding(state)
    first = migrate_existing_binding(
        registry,
        store,
        pkg,
        existing_entry=legacy,
        target_asset_id=MIGRATION_TARGET,
        actor="operator",
        reason="migrate deployed binding",
        dry_run=False,
    )
    assert first["status"] == "applied"
    commits = sorted(path.name for path in (state / "skills" / "commits").glob("*.json"))
    history = len(store.history_files())
    snapshot = store.load()

    again = migrate_existing_binding(
        registry,
        store,
        pkg,
        existing_entry=legacy,
        target_asset_id=MIGRATION_TARGET,
        actor="operator",
        reason="migrate deployed binding",
        dry_run=False,
    )
    assert again["status"] == "noop"
    assert again["revision"] == 3
    assert store.load() == snapshot
    assert sorted(
        path.name for path in (state / "skills" / "commits").glob("*.json")
    ) == commits
    assert len(store.history_files()) == history
    assert registry.get(MIGRATION_TARGET).status == "active"

    dry = migrate_existing_binding(
        registry,
        store,
        pkg,
        existing_entry=legacy,
        target_asset_id=MIGRATION_TARGET,
        actor="operator",
        reason="migrate deployed binding",
        dry_run=True,
    )
    assert dry["status"] == "noop"


def test_migration_rejects_package_mismatch(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store, legacy, _pkg = _seed_legacy_binding(state)

    tampered_root = tmp_path / "tampered-skills"
    shutil.copytree(SKILLS_ROOT / SKILL_ID, tampered_root / SKILL_ID)
    body = tampered_root / SKILL_ID / "SKILL.md"
    body.write_text(body.read_text(encoding="utf-8") + "\n篡改\n", encoding="utf-8")
    tampered = load_package(tampered_root, SKILL_ID)
    assert tampered.content_hash != legacy["package_hash"]

    before = _digests(tmp_path)
    with pytest.raises(SkillEvolutionError) as excinfo:
        migrate_existing_binding(
            registry,
            store,
            tampered,
            existing_entry=legacy,
            target_asset_id=MIGRATION_TARGET,
            actor="operator",
            reason="tampered package",
            dry_run=False,
        )
    assert "migration_package_mismatch" in str(excinfo.value)
    assert _digests(tmp_path) == before
    assert store.load()["revision"] == 2
    assert store.get_section("skills", SKILL_ID) == legacy


# --------------------------------------------------------------------------- #
# prepared intents: crash consistency around the binding commit
# --------------------------------------------------------------------------- #
def _crash_at(point: str):
    def hook(current: str) -> None:
        if current == point:
            raise RuntimeError(f"crash:{point}")

    return hook


def _commit_files(state: Path) -> list[str]:
    directory = state / "skills" / "commits"
    if not directory.is_dir():
        return []
    return sorted(path.name for path in directory.glob("*.json"))


def _intents(state: Path) -> list[dict[str, Any]]:
    directory = state / "skills" / "intents"
    if not directory.is_dir():
        return []
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]


def test_activate_after_intent_crash_leaves_pointer_and_chain_clean(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"
    boot = bootstrap_baseline(registry, store, base_pkg(), actor="operator")
    _, v1 = ready_asset(registry, sidecars, version="1.0.1", root=tmp_path)

    with pytest.raises(RuntimeError):
        activate_asset(
            registry,
            store,
            v1.asset_id,
            expected_revision=boot["revision"],
            actor="operator",
            evidence_id="eval-test",
            sidecar_dir=sidecars,
            fault_hook=_crash_at("after_intent"),
        )

    binding = store.load()
    assert binding["revision"] == 1
    assert binding["skills"][SKILL_ID]["asset_id"] == boot["asset"].asset_id
    assert _commit_files(state) == ["1.json"]
    intents = _intents(state)
    assert len(intents) == 1 and intents[0]["status"] == "prepared"

    # The CAS never happened: reconcile marks it stale and never adopts it.
    assert reconcile_commits(state, store, registry) == []
    assert _intents(state)[0]["status"] == "stale"
    assert _commit_files(state) == ["1.json"]

    # The pointer is still usable: a real activation and a rollback both work,
    # and the stale intent never enters the chain.
    activated = activate_asset(
        registry,
        store,
        v1.asset_id,
        expected_revision=1,
        actor="operator",
        evidence_id="eval-test",
        sidecar_dir=sidecars,
    )
    assert activated["revision"] == 2
    rolled = rollback_asset(
        registry,
        store,
        v1.asset_id,
        reason="stale intent must not be a target",
        rolled_back_by="operator",
        expected_revision=2,
        sidecar_dir=sidecars,
    )
    assert rolled["entry"]["asset_id"] == boot["asset"].asset_id
    assert _commit_files(state) == ["1.json", "2.json", "3.json"]
    assert _commits(state)[-1]["intent_id"] == rolled["intent_id"]


def test_activate_after_commit_crash_is_reconciled_and_rollbackable(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"
    boot = bootstrap_baseline(registry, store, base_pkg(), actor="operator")
    candidate, v1 = ready_asset(registry, sidecars, version="1.0.1", root=tmp_path)

    with pytest.raises(RuntimeError):
        activate_asset(
            registry,
            store,
            v1.asset_id,
            expected_revision=boot["revision"],
            actor="operator",
            evidence_id="eval-test",
            sidecar_dir=sidecars,
            fault_hook=_crash_at("after_commit"),
        )

    # The CAS landed; only the adoption record is missing.
    assert store.load()["revision"] == 2
    assert store.get_section("skills", SKILL_ID)["asset_id"] == v1.asset_id
    assert _commit_files(state) == ["1.json"]
    assert _intents(state)[0]["status"] == "prepared"

    adopted = reconcile_commits(state, store, registry)
    assert adopted == [
        {
            "intent_id": _intents(state)[0]["intent_id"],
            "action": "adopt",
            "revision": 2,
        }
    ]
    assert _commit_files(state) == ["1.json", "2.json"]
    record = json.loads((state / "skills" / "commits" / "2.json").read_text(encoding="utf-8"))
    assert record["action"] == "activate"
    assert record["to"] == {
        "skill_id": SKILL_ID,
        "asset_id": v1.asset_id,
        "version": candidate.version,
        "hash": candidate.content_hash,
    }
    assert record["intent_id"] == _intents(state)[0]["intent_id"]
    assert _intents(state)[0]["status"] == "committed"

    # The adopted activation is a normal chain entry: rollback reaches its from.
    rolled = rollback_asset(
        registry,
        store,
        v1.asset_id,
        reason="post-crash rollback",
        rolled_back_by="operator",
        expected_revision=2,
        sidecar_dir=sidecars,
    )
    assert rolled["entry"]["asset_id"] == boot["asset"].asset_id
    assert store.get_section("skills", SKILL_ID)["asset_id"] == boot["asset"].asset_id

    # Published package is loadable at the adopted version.
    published = state / "skills" / "packages" / v1.asset_id / SKILL_ID
    assert load_package(published.parent, SKILL_ID, candidate.version).content_hash == (
        candidate.content_hash
    )


def test_activate_after_record_crash_reconcile_is_idempotent(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"
    boot = bootstrap_baseline(registry, store, base_pkg(), actor="operator")
    _, v1 = ready_asset(registry, sidecars, version="1.0.1", root=tmp_path)

    with pytest.raises(RuntimeError):
        activate_asset(
            registry,
            store,
            v1.asset_id,
            expected_revision=boot["revision"],
            actor="operator",
            evidence_id="eval-test",
            sidecar_dir=sidecars,
            fault_hook=_crash_at("after_record"),
        )

    assert _commit_files(state) == ["1.json", "2.json"]
    assert _intents(state)[0]["status"] == "committed"
    before = {
        name: (state / "skills" / "commits" / name).read_bytes()
        for name in _commit_files(state)
    }

    assert reconcile_commits(state, store, registry) == []
    after = {
        name: (state / "skills" / "commits" / name).read_bytes()
        for name in _commit_files(state)
    }
    assert after == before
    assert _intents(state)[0]["status"] == "committed"
    assert store.load()["revision"] == 2


def test_migrate_after_commit_crash_is_reconciled_with_mcp_intact(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    pkg = base_pkg()
    store.commit("mcp", "project_kb", MCP_ENTRY, expected_revision=0)
    legacy = {
        "skill_id": SKILL_ID,
        "version": pkg.version,
        "asset_id": LEGACY_ASSET_ID,
        "package_hash": pkg.content_hash,
    }
    store.commit("skills", SKILL_ID, legacy, expected_revision=1)
    mcp_before = store.load()["mcp"]

    with pytest.raises(RuntimeError):
        migrate_existing_binding(
            registry,
            store,
            pkg,
            existing_entry=legacy,
            target_asset_id=MIGRATION_TARGET,
            actor="operator",
            reason="migrate deployed binding",
            dry_run=False,
            fault_hook=_crash_at("after_commit"),
        )

    assert store.load()["revision"] == 3
    assert store.load()["mcp"] == mcp_before
    assert _commit_files(state) == []
    assert _intents(state)[0]["action"] == "migrate"
    assert _intents(state)[0]["status"] == "prepared"

    adopted = reconcile_commits(state, store, registry)
    assert [item["action"] for item in adopted] == ["adopt"]
    assert _commit_files(state) == ["3.json"]
    record = json.loads((state / "skills" / "commits" / "3.json").read_text(encoding="utf-8"))
    assert record["action"] == "migrate"
    assert record["to"]["asset_id"] == MIGRATION_TARGET
    assert store.load()["mcp"] == mcp_before
    assert _intents(state)[0]["status"] == "committed"


# --------------------------------------------------------------------------- #
# lifecycle lock: concurrency, recursion, key matching
# --------------------------------------------------------------------------- #
def test_reconcile_blocks_until_writer_commits(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"
    boot = bootstrap_baseline(registry, store, base_pkg(), actor="operator")
    _, v1 = ready_asset(registry, sidecars, version="1.0.1", root=tmp_path)

    prepared = threading.Event()
    release = threading.Event()
    errors: dict[str, BaseException] = {}

    def hook(point: str) -> None:
        if point == "after_intent":
            prepared.set()
            if not release.wait(10):
                raise RuntimeError("writer never released")

    def writer() -> None:
        try:
            activate_asset(
                registry,
                store,
                v1.asset_id,
                expected_revision=boot["revision"],
                actor="operator",
                evidence_id="eval-test",
                sidecar_dir=sidecars,
                fault_hook=hook,
            )
        except BaseException as exc:  # pragma: no cover - asserted empty below
            errors["writer"] = exc

    thread_a = threading.Thread(target=writer)
    thread_a.start()
    assert prepared.wait(10)

    observed: dict[str, Any] = {}

    def reader() -> None:
        observed["items"] = reconcile_commits(state, store, registry)
        observed["intents"] = [item["status"] for item in _intents(state)]

    thread_b = threading.Thread(target=reader)
    thread_b.start()
    time.sleep(0.2)
    assert thread_b.is_alive()  # blocked on the lifecycle lock
    release.set()
    thread_a.join(10)
    thread_b.join(10)

    assert not thread_a.is_alive() and not thread_b.is_alive()
    assert "writer" not in errors
    assert observed["items"] == []
    assert observed["intents"] == ["committed"]
    assert _commit_files(state) == ["1.json", "2.json"]


def test_crashed_prepared_intent_is_stale_after_lock_release(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"
    boot = bootstrap_baseline(registry, store, base_pkg(), actor="operator")
    _, v1 = ready_asset(registry, sidecars, version="1.0.1", root=tmp_path)

    with pytest.raises(RuntimeError):
        activate_asset(
            registry,
            store,
            v1.asset_id,
            expected_revision=boot["revision"],
            actor="operator",
            evidence_id="eval-test",
            sidecar_dir=sidecars,
            fault_hook=_crash_at("after_intent"),
        )

    # Same pid, but the lifecycle lock is free: no active writer → stale.
    assert reconcile_commits(state, store, registry) == []
    intent = _intents(state)[0]
    assert intent["status"] == "stale"
    assert intent["reason_detail"] == "cas_not_applied"
    assert store.load()["revision"] == 1
    assert _commit_files(state) == ["1.json"]

    activated = activate_asset(
        registry,
        store,
        v1.asset_id,
        expected_revision=1,
        actor="operator",
        evidence_id="eval-test",
        sidecar_dir=sidecars,
    )
    rolled = rollback_asset(
        registry,
        store,
        v1.asset_id,
        reason="stale intent must not affect the chain",
        rolled_back_by="operator",
        expected_revision=2,
        sidecar_dir=sidecars,
    )
    assert activated["revision"] == 2
    assert rolled["entry"]["asset_id"] == boot["asset"].asset_id
    assert _commit_files(state) == ["1.json", "2.json", "3.json"]


def test_same_asset_hash_different_skill_key_not_matched(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"
    boot = bootstrap_baseline(registry, store, base_pkg(), actor="operator")
    _, v1 = ready_asset(registry, sidecars, version="1.0.1", root=tmp_path)
    activate_asset(
        registry,
        store,
        v1.asset_id,
        expected_revision=boot["revision"],
        actor="operator",
        evidence_id="eval-test",
        sidecar_dir=sidecars,
    )
    assert store.load()["revision"] == 2

    to_summary = {
        "skill_id": "skill-b",
        "asset_id": v1.asset_id,
        "version": "1.0.1",
        "hash": v1.content["package_hash"],
    }
    entry_b = {
        "skill_id": "skill-b",
        "version": "1.0.1",
        "asset_id": v1.asset_id,
        "package_hash": v1.content["package_hash"],
    }

    # Helper: only the intent's own key counts, never a scan over all skills.
    assert _binding_matches_to({"skill-b": entry_b}, {"skill_id": "skill-b", "to": to_summary})
    assert not _binding_matches_to(
        {"skill-a": {"skill_id": "skill-a", "version": "9.9.9",
                     "asset_id": "skill-a-9-9-9", "package_hash": "sha256:" + "c" * 64},
         "skill-b": entry_b},
        {"skill_id": "skill-a", "to": to_summary},
    )

    # File-level: skill-a's intent must not be adopted by skill-b's entry.
    intents_dir = state / "skills" / "intents"
    intents_dir.mkdir(parents=True, exist_ok=True)
    crafted = {
        "schema_version": "1",
        "intent_id": "activate-craftedkey",
        "action": "activate",
        "skill_id": "skill-a",
        "expected_revision": 1,
        "from": None,
        "to": to_summary,
        "actor": "operator",
        "reason": "key mismatch probe",
        "evidence_id": None,
        "owner_pid": os.getpid(),
        "owner_host": socket.gethostname(),
        "prepared_at": utc_now(),
        "status": "prepared",
        "at": utc_now(),
    }
    (intents_dir / "activate-craftedkey.json").write_text(
        json.dumps(crafted, ensure_ascii=False), encoding="utf-8"
    )
    assert reconcile_commits(state, store, registry) == []
    stored = {item["intent_id"]: item for item in _intents(state)}["activate-craftedkey"]
    assert stored["status"] == "stale"
    assert stored["reason_detail"] == "to_mismatch"
    assert _commit_files(state) == ["1.json", "2.json"]


def test_nested_lock_in_activate_is_reentrant(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"
    boot = bootstrap_baseline(registry, store, base_pkg(), actor="operator")
    _, v1 = ready_asset(registry, sidecars, version="1.0.1", root=tmp_path)

    outcome: dict[str, Any] = {}

    def nested() -> None:
        try:
            with _lifecycle_lock(state):
                outcome["result"] = activate_asset(
                    registry,
                    store,
                    v1.asset_id,
                    expected_revision=boot["revision"],
                    actor="operator",
                    evidence_id="eval-test",
                    sidecar_dir=sidecars,
                )
        except BaseException as exc:  # pragma: no cover - asserted absent
            outcome["error"] = exc

    worker = threading.Thread(target=nested)
    worker.start()
    worker.join(10)
    assert not worker.is_alive(), "activate deadlocked on its own lifecycle lock"
    assert "error" not in outcome
    assert outcome["result"]["revision"] == 2


def test_lifecycle_lock_is_released_after_exception(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    sidecars = tmp_path / "sidecars"
    boot = bootstrap_baseline(registry, store, base_pkg(), actor="operator")
    _, v1 = ready_asset(registry, sidecars, version="1.0.1", root=tmp_path)

    with pytest.raises(RuntimeError):
        activate_asset(
            registry,
            store,
            v1.asset_id,
            expected_revision=boot["revision"],
            actor="operator",
            evidence_id="eval-test",
            sidecar_dir=sidecars,
            fault_hook=_crash_at("after_intent"),
        )

    outcome: dict[str, Any] = {}

    def other_thread() -> None:
        outcome["items"] = reconcile_commits(state, store, registry)

    worker = threading.Thread(target=other_thread)
    worker.start()
    worker.join(10)
    assert not worker.is_alive(), "lifecycle lock leaked after an exception"
    assert outcome["items"] == []
    assert _intents(state)[0]["status"] == "stale"


def test_reconcile_skips_live_and_remote_young_intents(tmp_path):
    state = tmp_path / "state"
    registry = UpperRegistry(state)
    store = BindingStore(state)
    bootstrap_baseline(registry, store, base_pkg(), actor="operator")
    intents_dir = state / "skills" / "intents"
    intents_dir.mkdir(parents=True, exist_ok=True)

    def write_intent(intent_id: str, *, pid: int, host: str, age_s: int) -> None:
        stamp = (datetime.now(UTC) - timedelta(seconds=age_s)).isoformat().replace(
            "+00:00", "Z"
        )
        payload = {
            "schema_version": "1",
            "intent_id": intent_id,
            "action": "activate",
            "skill_id": SKILL_ID,
            "expected_revision": 1,
            "from": None,
            "to": {
                "skill_id": SKILL_ID,
                "asset_id": "skill-repo-evidence-1-0-1",
                "version": "1.0.1",
                "hash": "sha256:" + "b" * 64,
            },
            "actor": "operator",
            "reason": "writer probe",
            "evidence_id": None,
            "owner_pid": pid,
            "owner_host": host,
            "prepared_at": stamp,
            "status": "prepared",
            "at": stamp,
        }
        (intents_dir / f"{intent_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    write_intent("live", pid=os.getppid(), host=socket.gethostname(), age_s=0)
    write_intent("remote-young", pid=os.getppid(), host="another-host", age_s=1)
    write_intent("remote-old", pid=os.getppid(), host="another-host", age_s=400)

    results = reconcile_commits(state, store, registry)
    assert [item["skipped"] for item in results] == ["active_writer", "active_writer"]

    state_by_id = {item["intent_id"]: item for item in _intents(state)}
    assert state_by_id["live"]["status"] == "prepared"
    assert state_by_id["remote-young"]["status"] == "prepared"
    assert state_by_id["remote-old"]["status"] == "stale"
    assert state_by_id["remote-old"]["reason_detail"] == "prepared_timeout"
    assert store.load()["revision"] == 1
    assert _commit_files(state) == ["1.json"]
