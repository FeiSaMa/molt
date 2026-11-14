"""Skill administration API and D2/D4 boundary tests (not executed here).

Covers:
* propose requires provider or manual_body;
* evaluate requires manual_outputs when no real provider, and can produce
  keep/reject/rollback;
* approve → activate increments the binding revision and matches the hash;
* rollback restores the previous managed version and keeps history;
* activate rejects a same skill_id@version identity with a different hash;
* new routes need Bearer, the new OpenAPI is public, old contracts stay 9/6/6;
* runtime on but no MCP tools keeps the legacy single-tool + tool_choice=none path.
"""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from molt.agent.chat import ChatReply, MockAdapter, ToolCall
from molt.agent.probe import build_index
from molt.agent.runtime import AgentRuntime
from molt.agent.server import AgentApp, create_server, load_openapi
from molt.agent.skill_admin import SkillAdmin, SkillAdminError
from molt.agent.store import SessionStore, new_id
from molt.agent.turn import TurnRunner
from molt.skills import BindingStore, CaptureAdapter, load_package
from molt.skills.eval_tasks import load_tasks
from molt.skills.lifecycle import activate_asset, bootstrap_baseline
from molt.upper.types import UpperAsset

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token-skill-admin"
MANUAL_BODY = """# 仓库证据与零写入验收（修订版）

- 执行前后各取一次树快照并逐项比对；`git status --porcelain` 只是必要条件。
- replay 必须带 `replay_of_run_id`，并记录 `calls_model`。
"""


def _admin(tmp_path: Path, adapter: Any | None = None) -> tuple[SkillAdmin, BindingStore]:
    skill_state = tmp_path / "skill-state"
    binding = BindingStore(skill_state, repo_root=ROOT)
    admin = SkillAdmin(
        ROOT,
        ROOT / "skills",
        skill_state,
        skill_state / "skill-registry",
        adapter if adapter is not None else MockAdapter(),
        binding,
    )
    return admin, binding


def _propose(admin: SkillAdmin, *, version: str | None = None) -> dict[str, Any]:
    return admin.propose(
        "repo-evidence",
        "补充零写入与标注规则",
        manual_body=MANUAL_BODY,
        version=version,
    )


def _good(task: Any) -> str:
    tokens = "；".join(task.expect["must_include"])
    return f"结论：{tokens}。零写入 树快照 replay_of_run_id calls_model。"


def _manual_outputs(outcome: str) -> dict[str, str]:
    tasks = load_tasks(ROOT / "skills_eval")
    transfer = [task for task in tasks if task.phase == "transfer"]
    regression = [task for task in tasks if task.phase == "regression"]
    responses: dict[str, str] = {}
    for index, task in enumerate(transfer):
        baseline_ok = index != 0
        if outcome in ("keep", "rollback"):
            baseline_ok = index != 0
        else:
            baseline_ok = True
        responses[f"baseline:{task.task_id}"] = _good(task) if baseline_ok else "未验证。"
        responses[f"candidate:{task.task_id}"] = _good(task)
    for task in regression:
        responses[f"baseline:{task.task_id}"] = _good(task)
        responses[f"candidate:{task.task_id}"] = _good(task)
    if outcome == "rollback":
        responses[f"candidate:{regression[-1].task_id}"] = "未验证。"
    return responses


def test_skill_admin_state_derives_from_skill_state(tmp_path: Path) -> None:
    admin, binding = _admin(tmp_path)
    assert admin.state_dir == (tmp_path / "skill-state").resolve()
    assert binding.path == admin.state_dir / "skills" / "current.json"
    assert admin.registry_state == admin.state_dir / "skill-registry"
    assert admin.candidate_root == admin.state_dir / "skill-candidates"
    assert admin._index_path == admin.state_dir / "skill-admin" / "evaluations.json"


def test_propose_requires_provider_or_manual_body(tmp_path: Path) -> None:
    admin, _ = _admin(tmp_path)
    with pytest.raises(SkillAdminError) as excinfo:
        admin.propose("repo-evidence", "补充零写入", version="1.0.9")
    assert excinfo.value.code == "provider_unavailable"

    result = admin.propose(
        "repo-evidence", "补充零写入", manual_body=MANUAL_BODY, version="1.0.9"
    )
    assert result["origin"] == "manual"
    assert result["calls_model"] is False
    assert result["asset_id"].startswith("skill-repo-evidence-1-0-9")


def test_evaluate_requires_manual_outputs_without_provider(tmp_path: Path) -> None:
    admin, _ = _admin(tmp_path)
    candidate = _propose(admin)
    with pytest.raises(SkillAdminError) as excinfo:
        admin.evaluate(candidate["asset_id"])
    assert excinfo.value.code == "provider_unavailable"

    result = admin.evaluate(candidate["asset_id"], manual_outputs=_manual_outputs("keep"))
    assert result["mode"] == "manual"
    assert result["calls_model"] is False
    assert result["evidence_id"]
    assert set(result["transfer"]) == {"verdict", "reason", "metrics"}


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [("keep", "keep"), ("reject", "reject"), ("rollback", "rollback")],
)
def test_evaluate_manual_outputs_can_produce_each_final(
    tmp_path: Path, outcome: str, expected: str
) -> None:
    admin, _ = _admin(tmp_path)
    candidate = _propose(admin)
    result = admin.evaluate(
        candidate["asset_id"], manual_outputs=_manual_outputs(outcome)
    )
    assert result["final"] == expected
    assert result["mode"] == "manual"
    assert result["calls_model"] is False


def test_approve_activate_updates_binding(tmp_path: Path) -> None:
    admin, binding = _admin(tmp_path)
    candidate = _propose(admin)
    evaluation = admin.evaluate(
        candidate["asset_id"], manual_outputs=_manual_outputs("keep")
    )

    with pytest.raises(SkillAdminError) as excinfo:
        admin.activate(
            candidate["asset_id"], 0, actor="ops-reviewer", evidence_id=evaluation["evidence_id"]
        )
    assert excinfo.value.code == "skill_state_conflict"

    approved = admin.approve(
        candidate["asset_id"],
        "ops-reviewer",
        evidence_id=evaluation["evidence_id"],
        note="manual keep",
    )
    assert approved["status"] == "approved"

    activated = admin.activate(
        candidate["asset_id"], 0, actor="ops-reviewer", evidence_id=evaluation["evidence_id"]
    )
    assert activated["revision"] == 1
    assert activated["package_hash"] == candidate["package_hash"]
    assert activated["commit_record_id"] == "1"
    entry = binding.get_section("skills", "repo-evidence")
    assert entry["asset_id"] == candidate["asset_id"]
    assert entry["package_hash"] == candidate["package_hash"]


def test_rollback_restores_previous_managed_version(tmp_path: Path) -> None:
    admin, binding = _admin(tmp_path)
    first = _propose(admin, version="1.0.1")
    first_eval = admin.evaluate(first["asset_id"], manual_outputs=_manual_outputs("keep"))
    admin.approve(first["asset_id"], "ops-reviewer", evidence_id=first_eval["evidence_id"], note="first")
    assert (
        admin.activate(
            first["asset_id"], 0, actor="ops-reviewer", evidence_id=first_eval["evidence_id"]
        )["revision"]
        == 1
    )

    second = _propose(admin, version="1.0.2")
    second_eval = admin.evaluate(second["asset_id"], manual_outputs=_manual_outputs("keep"))
    admin.approve(second["asset_id"], "ops-reviewer", evidence_id=second_eval["evidence_id"], note="second")
    assert (
        admin.activate(
            second["asset_id"], 1, actor="ops-reviewer", evidence_id=second_eval["evidence_id"]
        )["revision"]
        == 2
    )

    rolled = admin.rollback(
        reason="regression against candidate",
        rolled_back_by="ops-reviewer",
        expected_revision=2,
        evidence_id=second_eval["evidence_id"],
    )
    assert rolled["target_asset_id"] == first["asset_id"]
    assert rolled["version"] == "1.0.1"
    assert rolled["revision"] == 3
    assert rolled["commit_record_id"] == "3"
    entry = binding.get_section("skills", "repo-evidence")
    assert entry["asset_id"] == first["asset_id"]
    assert entry["package_hash"] == first["package_hash"]
    assert len(binding.history_files()) >= 3


def test_activate_rejects_identity_conflict(tmp_path: Path) -> None:
    admin, _ = _admin(tmp_path)
    candidate = _propose(admin)
    evaluation = admin.evaluate(
        candidate["asset_id"], manual_outputs=_manual_outputs("keep")
    )
    admin.approve(
        candidate["asset_id"], "ops-reviewer", evidence_id=evaluation["evidence_id"], note="keep"
    )

    conflict = UpperAsset(
        asset_id="skill-repo-evidence-1-0-1-conflict",
        kind="skill",
        name="repo-evidence",
        version="1.0.1",
        source="test",
        family="molt-repository",
        created_by="operator",
        status="draft",
        content={"package_hash": "sha256:" + "0" * 64},
    )
    admin.registry.register(conflict)

    with pytest.raises(SkillAdminError) as excinfo:
        admin.activate(
            candidate["asset_id"], 0, actor="ops-reviewer", evidence_id=evaluation["evidence_id"]
        )
    assert excinfo.value.code == "skill_identity_conflict"


def _candidate_with_missing_commit(
    tmp_path: Path,
) -> tuple[SkillAdmin, BindingStore, str, int]:
    admin, binding = _admin(tmp_path)
    repo_package = load_package(ROOT / "skills", "repo-evidence")
    base = bootstrap_baseline(
        admin.registry, binding, repo_package, actor="ops-reviewer", expected_revision=0
    )
    candidate = _propose(admin, version="1.0.1")
    evidence = admin.evaluate(candidate["asset_id"], manual_outputs=_manual_outputs("keep"))
    admin.approve(
        candidate["asset_id"], "ops-reviewer", evidence_id=evidence["evidence_id"]
    )

    def hook(point: str) -> None:
        if point == "after_commit":
            raise RuntimeError("crash:after_commit")

    with pytest.raises(RuntimeError):
        activate_asset(
            admin.registry,
            binding,
            candidate["asset_id"],
            expected_revision=base["revision"],
            actor="ops-reviewer",
            evidence_id=evidence["evidence_id"],
            sidecar_dir=admin.sidecar_dir,
            fault_hook=hook,
        )
    revision = base["revision"] + 1
    assert binding.load()["revision"] == revision
    assert not (admin.state_dir / "skills" / "commits" / f"{revision}.json").exists()
    return admin, binding, evidence["evidence_id"], revision


def test_http_activate_reconciles_missing_commit(tmp_path: Path) -> None:
    admin, binding, evidence_id, revision = _candidate_with_missing_commit(tmp_path)
    harness = _Harness(admin)
    try:
        status, raw = harness.request(
            "POST",
            "/api/skills/activate",
            body={
                "asset_id": "skill-repo-evidence-1-0-1",
                "expected_revision": revision,
                "actor": "ops-reviewer",
                "evidence_id": evidence_id,
            },
        )
        assert status == 200
        payload = json.loads(raw)
        assert payload["reconciled"]
        assert binding.load()["revision"] == revision + 1
    finally:
        harness.close()


def test_http_rollback_reconciles_missing_commit(tmp_path: Path) -> None:
    admin, binding, evidence_id, revision = _candidate_with_missing_commit(tmp_path)
    harness = _Harness(admin)
    try:
        status, raw = harness.request(
            "POST",
            "/api/skills/rollback",
            body={
                "reason": "recover missing commit",
                "rolled_back_by": "ops-reviewer",
                "expected_revision": revision,
                "evidence_id": evidence_id,
            },
        )
        assert status == 200
        payload = json.loads(raw)
        assert payload["reconciled"]
        assert binding.load()["skills"]["repo-evidence"]["asset_id"] == (
            "skill-repo-evidence-1-0-0"
        )
    finally:
        harness.close()


def test_approve_rejects_non_keep_and_mismatched_evidence(tmp_path: Path) -> None:
    admin, _ = _admin(tmp_path)
    candidate = _propose(admin)
    rejected = admin.evaluate(
        candidate["asset_id"], manual_outputs=_manual_outputs("reject")
    )
    assert rejected["final"] == "reject"
    with pytest.raises(SkillAdminError) as excinfo:
        admin.approve(
            candidate["asset_id"], "ops-reviewer", evidence_id=rejected["evidence_id"]
        )
    assert excinfo.value.code == "skill_not_keep"

    admin2, _ = _admin(tmp_path / "mismatch")
    candidate2 = _propose(admin2)
    kept = admin2.evaluate(
        candidate2["asset_id"], manual_outputs=_manual_outputs("keep")
    )
    path = (
        admin2.state_dir
        / "skills"
        / "evaluations"
        / f"{kept['evidence_id']}.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["asset_id"] = "someone-else"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SkillAdminError) as excinfo:
        admin2.approve(
            candidate2["asset_id"], "ops-reviewer", evidence_id=kept["evidence_id"]
        )
    assert excinfo.value.code == "skill_evidence_mismatch"


class _RecordingAdapter:
    name = "recording"
    model = "none"
    real_model = False
    adapter_ready = True
    provider_error = None

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages, tools, tool_choice):  # noqa: ANN001
        self.calls.append(
            {"messages": messages, "tools": tools, "tool_choice": tool_choice}
        )
        return ChatReply(content="ok")


def _run_turn(runtime: AgentRuntime, task: str) -> tuple[CaptureAdapter, Any]:
    adapter = CaptureAdapter(_RecordingAdapter())
    store = SessionStore(provider="test")
    session = store.create()
    runtime.set_mode(session.session_id, "active")
    TurnRunner(store, build_index(ROOT), adapter, runtime=runtime).run(
        session, new_id("turn"), task
    )
    return adapter, session


def test_activate_candidate_changes_next_turn_and_rollback_restores(
    tmp_path: Path,
) -> None:
    admin, binding = _admin(tmp_path)
    repo_package = load_package(ROOT / "skills", "repo-evidence")
    baseline_activation = bootstrap_baseline(
        admin.registry,
        binding,
        repo_package,
        actor="ops-reviewer",
        expected_revision=0,
    )
    baseline = {"asset_id": baseline_activation["entry"]["asset_id"]}

    candidate = _propose(admin, version="1.0.1")
    candidate_eval = admin.evaluate(
        candidate["asset_id"], manual_outputs=_manual_outputs("keep")
    )
    admin.approve(
        candidate["asset_id"], "ops-reviewer", evidence_id=candidate_eval["evidence_id"]
    )
    candidate_activation = admin.activate(
        candidate["asset_id"],
        baseline_activation["revision"],
        actor="ops-reviewer",
        evidence_id=candidate_eval["evidence_id"],
    )
    assert candidate_activation["revision"] > baseline_activation["revision"]

    runtime = AgentRuntime(ROOT, ROOT / "skills", admin.state_dir, None, None, enabled=True)
    try:
        adapter, session = _run_turn(runtime, "如何验证这次改动零写入？")
        loaded = next(event.data for event in session.events if event.event == "skill.loaded")
        assert loaded["version"] == "1.0.1"
        assert MANUAL_BODY.strip() in adapter.calls[0]["messages"][0]["content"]

        rolled = admin.rollback(
            reason="restore baseline",
            rolled_back_by="ops-reviewer",
            expected_revision=candidate_activation["revision"],
            evidence_id=candidate_eval["evidence_id"],
        )
        assert rolled["target_asset_id"] == baseline["asset_id"]

        adapter2, session2 = _run_turn(runtime, "如何验证这次改动零写入？")
        loaded2 = next(event.data for event in session2.events if event.event == "skill.loaded")
        assert loaded2["version"] == "1.0.0"
        assert repo_package.body.strip() in adapter2.calls[0]["messages"][0]["content"]
    finally:
        runtime.close()

    commit = (
        admin.state_dir
        / "skills"
        / "commits"
        / f"{candidate_activation['revision']}.json"
    )
    assert commit.is_file()
    record = json.loads(commit.read_text(encoding="utf-8"))
    assert {"actor", "reason", "from", "to"} <= set(record)
    assert "evidence_id" in record or "evidence" in record


def test_approve_and_activate_require_audit_fields(tmp_path: Path) -> None:
    admin, _ = _admin(tmp_path)
    harness = _Harness(admin)
    try:
        status, _ = harness.request(
            "POST",
            "/api/skills/approve",
            body={"asset_id": "skill-x", "approved_by": "ops-reviewer"},
        )
        assert status == 400
        status, _ = harness.request(
            "POST",
            "/api/skills/activate",
            body={"asset_id": "skill-x", "expected_revision": 0},
        )
        assert status == 400
    finally:
        harness.close()


class _Harness:
    def __init__(self, admin: SkillAdmin) -> None:
        self.app = AgentApp(
            index=build_index(ROOT),
            adapter=MockAdapter(),
            token=TOKEN,
            allowed_origins=(),
            openapi=load_openapi(ROOT),
            data_dir=ROOT / "web" / "data",
            skill_admin=admin,
        )
        self.server = create_server(self.app, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.app.close()

    def request(
        self, method: str, path: str, *, body: object | None = None, token: str | None = TOKEN
    ) -> tuple[int, bytes]:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, raw


def test_new_routes_require_bearer_and_openapi_is_public(tmp_path: Path) -> None:
    admin, _ = _admin(tmp_path)
    harness = _Harness(admin)
    try:
        for method, path in (
            ("GET", "/api/skills/evaluations"),
            ("GET", "/api/skills/evaluations/eval-x"),
            ("POST", "/api/skills/propose"),
            ("POST", "/api/skills/evaluate"),
            ("POST", "/api/skills/approve"),
            ("POST", "/api/skills/activate"),
            ("POST", "/api/skills/rollback"),
        ):
            body = {} if method == "POST" else None
            status, _ = harness.request(method, path, body=body, token=None)
            assert status == 401, (method, path)

        status, raw = harness.request(
            "GET", "/api/skill-mcp/openapi.json", token=None
        )
        assert status == 200
        document = json.loads(raw)
        assert len(document["paths"]) == 16
        assert document["paths"]["/api/skill-mcp/openapi.json"]["get"]["security"] == []
    finally:
        harness.close()


def test_legacy_contracts_still_9_6_6() -> None:
    assert len(json.loads((ROOT / "contracts/openapi-v1.json").read_text())["paths"]) == 9
    assert (
        len(json.loads((ROOT / "contracts/openapi-lab-v1.json").read_text())["paths"]) == 6
    )
    assert (
        len(json.loads((ROOT / "contracts/openapi-upper-v1.json").read_text())["paths"]) == 6
    )


class _ToolThenContent:
    name = "tool-then-content"
    model = "static"
    real_model = False
    adapter_ready = True
    provider_error = None

    def __init__(self) -> None:
        self.choices: list[str] = []

    def complete(self, messages, tools, tool_choice):  # noqa: ANN001
        self.choices.append(tool_choice)
        if tool_choice == "auto":
            return ChatReply(
                tool_calls=[
                    ToolCall(
                        call_id="call-legacy-1",
                        name="experiments_get",
                        arguments=json.dumps({"experiment_id": "2c-memory-v3"}),
                    )
                ]
            )
        return ChatReply(content="旧路径完成")


def _runtime_with_bound_skill(tmp_path: Path, adapter: Any) -> AgentRuntime:
    state = tmp_path / "runtime-state"
    package = load_package(ROOT / "skills", "repo-evidence")
    BindingStore(state, repo_root=ROOT).commit(
        "skills",
        package.skill_id,
        {
            "skill_id": package.skill_id,
            "version": package.version,
            "asset_id": "operator_baseline",
            "package_hash": package.content_hash,
        },
        expected_revision=0,
    )
    return AgentRuntime(
        ROOT,
        ROOT / "skills",
        state,
        None,
        adapter,
        enabled=True,
    )


def test_runtime_off_keeps_old_event_order(tmp_path: Path) -> None:
    adapter = _ToolThenContent()
    store = SessionStore(provider="test")
    session = store.create()
    TurnRunner(store, build_index(ROOT), adapter).run(session, new_id("turn"), "看看实验")
    assert [event.event for event in session.events] == [
        "turn.started",
        "status",
        "tool.call",
        "status",
        "tool.result",
        "status",
        "message.delta",
        "message.completed",
        "turn.completed",
        "done",
    ]
    assert adapter.choices == ["auto", "none"]


def test_runtime_without_mcp_tools_uses_single_tool_second_call(tmp_path: Path) -> None:
    adapter = _ToolThenContent()
    runtime = _runtime_with_bound_skill(tmp_path, adapter)
    store = SessionStore(provider="test")
    session = store.create()
    runtime.set_mode(session.session_id, "active")
    TurnRunner(store, build_index(ROOT), adapter, runtime=runtime).run(
        session, new_id("turn"), "如何验证这次改动零写入？"
    )
    events = [event.event for event in session.events]
    assert "skill.loaded" in events
    assert "runtime.warning" in events
    assert events.count("tool.call") == 1
    assert adapter.choices == ["auto", "none"]
    tool_result = next(event for event in session.events if event.event == "tool.result")
    assert "source" not in tool_result.data
    runtime.close()
