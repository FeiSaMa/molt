"""Skill/MCP runtime API tests (slice C). Not executed by the C implementer.

Coverage:
* legacy contracts and ROUTES stay at 9 + 6 + 6 paths / 24 routes;
* new routes require Bearer, the new OpenAPI document is public;
* runtime status never leaks paths, commands or environment values;
* catalog / select / context-check projections;
* manual MCP tools + call against a real sandboxed server (skipped without bwrap);
* ``skill.loaded`` and MCP ``tool.call`` / ``tool.result`` fields;
* legacy turn event order is unchanged when the runtime is off;
* budget exhaustion emits ``tool_budget_exceeded`` and writes no history.
"""

from __future__ import annotations

import http.client
import json
import shutil
import threading
from pathlib import Path
from typing import Any

import pytest

from molt.agent.chat import ChatReply, MockAdapter, ToolCall
from molt.agent.errors import ErrorCode
from molt.agent.probe import build_index
from molt.agent.runtime import (
    AgentRuntime,
    AgentRuntimeError,
    McpProfileSnapshot,
    McpSurface,
    TurnSnapshot,
)
from molt.agent.server import (
    ROUTES,
    SKILL_MCP_ROUTES,
    AgentApp,
    create_server,
    load_openapi,
)
from molt.agent.store import SessionStore, new_id
from molt.agent.tools import ToolOutcome
from molt.agent.turn import TurnRunner
from molt.mcp.allowlist import McpAllowlist
from molt.mcp.jsonrpc import McpUnavailable
from molt.mcp.profile import build_profile_asset
from molt.skills import BindingStore, CaptureAdapter, load_package
from molt.upper.registry import UpperRegistry

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token-skill-mcp"
LEGACY_PATHS = {
    "/api/health",
    "/api/openapi.json",
    "/api/experiments",
    "/api/experiments/{experiment_id}",
    "/api/tasks",
    "/api/sessions",
    "/api/sessions/{session_id}",
    "/api/sessions/{session_id}/messages",
    "/api/sessions/{session_id}/events",
}
LAB_PATHS = {
    "/api/lab/openapi.json",
    "/api/lab/capabilities",
    "/api/lab/tasks",
    "/api/lab/runs",
    "/api/lab/runs/{run_id}",
    "/api/lab/runs/{run_id}/events",
}
UPPER_PATHS = {
    "/api/upper/graph",
    "/api/upper/assets",
    "/api/upper/replay",
    "/api/upper/evaluations/{evaluation_id}",
    "/api/upper/branches/{branch_id}/switch",
    "/api/upper/rollback",
}
NEW_PATHS = {
    "/api/runtime/status",
    "/api/skills/catalog",
    "/api/skills/effective",
    "/api/skills/select",
    "/api/skills/context-check",
    "/api/skills/propose",
    "/api/skills/evaluate",
    "/api/skills/approve",
    "/api/skills/activate",
    "/api/skills/rollback",
    "/api/skills/evaluations",
    "/api/skills/evaluations/{evaluation_id}",
    "/api/mcp/tools",
    "/api/mcp/call",
    "/api/sessions/{session_id}/runtime",
    "/api/skill-mcp/openapi.json",
}


def _json(body: bytes) -> Any:
    return json.loads(body.decode("utf-8"))


def _bind(tmp_path: Path, *, mcp: bool = False) -> tuple[Any, Path]:
    state = tmp_path / "skill-state"
    package = load_package(ROOT / "skills", "repo-evidence")
    store = BindingStore(state, repo_root=ROOT)
    revision = store.commit(
        "skills",
        package.skill_id,
        {
            "skill_id": package.skill_id,
            "version": package.version,
            "asset_id": "operator_baseline",
            "package_hash": package.content_hash,
        },
        expected_revision=0,
    )["revision"]
    if mcp:
        allowlist = McpAllowlist.load(
            ROOT / "molt" / "mcp" / "allowlist.default.json", repo_root=ROOT
        )
        spec = allowlist.get("project_kb")
        asset = build_profile_asset(spec, repo_root=ROOT)
        store.commit(
            "mcp",
            "project_kb",
            {
                "server_id": "project_kb",
                "version": asset.version,
                "asset_id": asset.asset_id,
                "package_hash": asset.content["artifact_hash"],
            },
            expected_revision=revision,
        )
    return package, state


def _runtime(tmp_path: Path, *, enabled: bool = True, mcp: bool = False) -> AgentRuntime:
    package, state = _bind(tmp_path, mcp=mcp)
    del package
    allowlist = ROOT / "molt" / "mcp" / "allowlist.default.json" if mcp else None
    return AgentRuntime(
        ROOT,
        ROOT / "skills",
        state,
        allowlist,
        MockAdapter(),
        enabled=enabled,
        mcp_registry_state=tmp_path / "upper",
    )


def _registry_with_two_versions(tmp_path: Path):
    """v1 stays draft; v2 is evaluated/approved/active. Return (spec, v1, v2, root)."""

    allowlist = McpAllowlist.load(
        ROOT / "molt" / "mcp" / "allowlist.default.json", repo_root=ROOT
    )
    spec = allowlist.get("project_kb")
    v1 = build_profile_asset(spec, repo_root=ROOT)
    v1.asset_id = "mcp-profile-project_kb-v1"
    v1.version = "1.0.0"
    v2 = build_profile_asset(spec, repo_root=ROOT)
    v2.asset_id = "mcp-profile-project_kb-v2"
    v2.version = "2.0.0"
    root = tmp_path / "registry"
    registry = UpperRegistry(root, repo_root=ROOT)
    registry.register(v1)
    registry.register(v2)
    registry.mark_evaluated(v2.asset_id)
    registry.approve(v2.asset_id, approved_by="ops-reviewer", approval_level="strict")
    registry.activate(v2.asset_id)
    return spec, v1, v2, root


def _binding_state(tmp_path: Path, asset: Any) -> tuple[Path, BindingStore]:
    state = tmp_path / "skill-state"
    store = BindingStore(state, repo_root=ROOT)
    package = load_package(ROOT / "skills", "repo-evidence")
    revision = store.commit(
        "skills",
        package.skill_id,
        {
            "skill_id": package.skill_id,
            "version": package.version,
            "asset_id": "operator_baseline",
            "package_hash": package.content_hash,
        },
        expected_revision=0,
    )["revision"]
    store.commit(
        "mcp",
        "project_kb",
        {
            "server_id": "project_kb",
            "version": asset.version,
            "asset_id": asset.asset_id,
            "package_hash": asset.content["artifact_hash"],
        },
        expected_revision=revision,
    )
    return state, store


def _runtime_for_registry(
    tmp_path: Path, state: Path, registry_root: Path
) -> AgentRuntime:
    return AgentRuntime(
        ROOT,
        ROOT / "skills",
        state,
        ROOT / "molt" / "mcp" / "allowlist.default.json",
        MockAdapter(),
        enabled=True,
        mcp_registry_state=registry_root,
    )


class Harness:
    def __init__(self, runtime: AgentRuntime | None) -> None:
        self.app = AgentApp(
            index=build_index(ROOT),
            adapter=MockAdapter(),
            token=TOKEN,
            allowed_origins=(),
            openapi=load_openapi(ROOT),
            data_dir=ROOT / "web" / "data",
            runtime=runtime,
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
        self,
        method: str,
        path: str,
        *,
        body: object | None = None,
        token: str | None = TOKEN,
    ) -> tuple[int, dict[str, str], bytes]:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, {key: value for key, value in response.getheaders()}, raw


@pytest.fixture()
def runtime(tmp_path: Path) -> AgentRuntime:
    runtime = _runtime(tmp_path)
    yield runtime
    runtime.close()


def test_legacy_contracts_and_routes_unchanged() -> None:
    legacy = json.loads((ROOT / "contracts/openapi-v1.json").read_text())["paths"]
    lab = json.loads((ROOT / "contracts/openapi-lab-v1.json").read_text())["paths"]
    assert set(legacy) == LEGACY_PATHS
    assert set(lab) == LAB_PATHS
    assert (
        set(json.loads((ROOT / "contracts/openapi-upper-v1.json").read_text())["paths"])
        == UPPER_PATHS
    )
    assert len(ROUTES) == 24
    assert len(SKILL_MCP_ROUTES) == 16


def test_new_routes_require_bearer_and_openapi_is_public(runtime: AgentRuntime) -> None:
    harness = Harness(runtime)
    try:
        for method, path in (
            ("GET", "/api/runtime/status"),
            ("GET", "/api/skills/catalog"),
            ("GET", "/api/skills/effective"),
            ("GET", "/api/mcp/tools"),
            ("POST", "/api/skills/select"),
            ("POST", "/api/skills/context-check"),
            ("POST", "/api/mcp/call"),
            ("POST", "/api/sessions/sess-x/runtime"),
        ):
            status, headers, _ = harness.request(
                method, path, token=None, body={} if method == "POST" else None
            )
            assert status == 401, (method, path)
            assert headers["WWW-Authenticate"] == "Bearer"

        status, _, body = harness.request("GET", "/api/skill-mcp/openapi.json", token=None)
        assert status == 200
        document = _json(body)
        assert set(document["paths"]) == NEW_PATHS
        assert document["paths"]["/api/skill-mcp/openapi.json"]["get"]["security"] == []
        assert document["security"] == [{"bearerAuth": []}]
    finally:
        harness.close()


def test_runtime_status_projection_has_no_paths(runtime: AgentRuntime, tmp_path: Path) -> None:
    harness = Harness(runtime)
    try:
        status, _, body = harness.request("GET", "/api/runtime/status")
        assert status == 200
        payload = _json(body)
        assert payload["enabled"] is True
        assert payload["skills"] == {"available": True, "reason": "ok"}
        assert payload["mcp"] == {
            "available": False,
            "reason": "not_configured",
            "approval_source": None,
            "approved": False,
        }
        assert payload["provider"] == {"kind": "mock"}
        assert payload["binding"]["revision"] == 1
        assert set(payload["binding"]["skills"]["repo-evidence"]) == {
            "skill_id",
            "version",
            "asset_id",
            "package_hash",
        }
        serialized = body.decode()
        for marker in (str(tmp_path), "command", "MOLT_CHAT", "allowlist.default.json", "/home/"):
            assert marker not in serialized
    finally:
        harness.close()


def test_catalog_select_and_context_check(runtime: AgentRuntime) -> None:
    harness = Harness(runtime)
    try:
        status, _, body = harness.request("GET", "/api/skills/catalog")
        assert status == 200
        catalog = _json(body)
        assert catalog["revision"] == 1
        entry = catalog["skills"][0]
        assert entry["skill_id"] == "repo-evidence"
        assert entry["available"] is True
        assert entry["triggers"]["keywords"]
        assert "mcp:project_kb:search_docs" in entry["permissions"]["tools"]
        assert "source" in entry and "count" in entry["source"]
        package = load_package(ROOT / "skills", "repo-evidence")
        assert package.body not in body.decode()

        status, _, body = harness.request("GET", "/api/skills/effective")
        assert status == 200
        effective = _json(body)
        assert effective["revision"] == 1
        assert effective["skills"]["repo-evidence"]["package_hash"] == package.content_hash

        status, _, body = harness.request(
            "POST", "/api/skills/select", body={"task_text": "如何验证这次改动零写入？"}
        )
        assert status == 200
        selected = _json(body)
        assert selected["selected"] is True
        assert selected["selection"]["skill_id"] == "repo-evidence"
        assert selected["mode"] == "deterministic-exact-prefix"

        status, _, body = harness.request(
            "POST", "/api/skills/select", body={"task_text": "今天天气如何"}
        )
        assert status == 200
        assert _json(body)["selected"] is False

        status, _, body = harness.request(
            "POST", "/api/skills/context-check", body={"task_text": "如何验证这次改动零写入？"}
        )
        assert status == 200
        evidence = _json(body)
        assert evidence["evidence_kind"] == "context_loaded"
        assert evidence["calls_model"] is False
        assert evidence["mode"] == "manual"
        assert evidence["contains_body"] is True
        assert evidence["content_hash"] == package.content_hash
        assert "improved" not in evidence
    finally:
        harness.close()


def test_session_runtime_switch_and_disabled_conflict(tmp_path: Path) -> None:
    enabled = _runtime(tmp_path)
    disabled = _runtime(tmp_path / "disabled", enabled=False)
    harness = Harness(enabled)
    try:
        status, _, body = harness.request("POST", "/api/sessions", body={})
        assert status == 201
        session_id = _json(body)["session_id"]

        status, _, body = harness.request(
            "POST", f"/api/sessions/{session_id}/runtime", body={"mode": "active"}
        )
        assert status == 200
        assert _json(body) == {
            "session_id": session_id,
            "mode": "active",
            "runtime_enabled": True,
        }

        status, _, _ = harness.request(
            "POST", f"/api/sessions/{session_id}/runtime", body={"mode": "sideways"}
        )
        assert status == 400

        status, _, _ = harness.request(
            "POST", f"/api/sessions/{session_id}/runtime", body={"mode": "active", "extra": 1}
        )
        assert status == 400
    finally:
        harness.close()

    harness = Harness(disabled)
    try:
        status, _, body = harness.request("POST", "/api/sessions", body={})
        session_id = _json(body)["session_id"]
        status, _, body = harness.request(
            "POST", f"/api/sessions/{session_id}/runtime", body={"mode": "active"}
        )
        assert status == 409
        assert _json(body)["error"]["details"]["reason"] == "runtime_disabled"
        status, _, _ = harness.request(
            "POST", f"/api/sessions/{session_id}/runtime", body={"mode": "off"}
        )
        assert status == 200
    finally:
        harness.close()
        disabled.close()


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap not installed")
def test_mcp_tools_and_manual_call_real(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, mcp=True)
    harness = Harness(runtime)
    try:
        status, _, body = harness.request("GET", "/api/mcp/tools")
        assert status == 200
        catalog = _json(body)
        assert catalog["calls_model"] is False
        server = catalog["servers"][0]
        assert server["server_id"] == "project_kb"
        assert server["available"] is True
        assert server["binding_revision"] == 2
        assert server["approval_source"] == "manual_operator_config"
        assert server["approved"] is False
        assert server["approval_note"] == "manual operator configuration; not model-evaluated"
        names = {tool["name"] for tool in server["tools"]}
        assert {"search_docs", "get_doc"} <= names
        search = next(tool for tool in server["tools"] if tool["name"] == "search_docs")
        assert search["namespaced_name"] == "mcp__project_kb__search_docs"
        assert search["input_schema"]["type"] == "object"

        status, _, body = harness.request(
            "POST",
            "/api/mcp/call",
            body={
                "server_id": "project_kb",
                "tool": "get_doc",
                "arguments": {"doc_id": "zero-write"},
            },
        )
        assert status == 200
        result = _json(body)
        assert result["status"] == "ok"
        assert result["calls_model"] is False
        assert result["result"]["content"]

        status, _, body = harness.request(
            "POST", "/api/mcp/call", body={"server_id": "project_kb", "tool": "rm_rf"}
        )
        assert status == 400
        assert _json(body)["error"]["details"]["reason"] == "tool_not_allowed"
    except McpUnavailable:
        pytest.skip("bwrap unavailable at runtime")
    finally:
        harness.close()


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap not installed")
def test_dead_mcp_client_is_evicted_and_recreated(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, mcp=True)
    try:
        runtime.call_mcp_tool("project_kb", "get_doc", {"doc_id": "zero-write"})
        first = runtime._clients["project_kb"]
        proc = first._proc
        assert proc is not None
        proc.kill()
        proc.wait(timeout=5)
        assert runtime._client_alive(first) is False

        result = runtime.call_mcp_tool("project_kb", "get_doc", {"doc_id": "zero-write"})
        assert result["content"]
        second = runtime._clients["project_kb"]
        assert second is not first
        assert first not in runtime._clients.values()
    finally:
        runtime.close()


def test_registry_selects_binding_v1_draft_not_active_v2(tmp_path: Path) -> None:
    spec, v1, v2, registry_root = _registry_with_two_versions(tmp_path)
    del spec, v2
    state, store = _binding_state(tmp_path, v1)
    runtime = _runtime_for_registry(tmp_path, state, registry_root)
    try:
        catalog = runtime.mcp_tool_catalog()["servers"][0]
        assert catalog["available"] is False
        assert catalog["reason"] == "mcp_profile_invalid"
        assert catalog["binding_revision"] == store.load()["revision"]
    finally:
        runtime.close()


def test_registry_selects_binding_active_v2(tmp_path: Path) -> None:
    spec, v1, v2, registry_root = _registry_with_two_versions(tmp_path)
    del v1
    state, store = _binding_state(tmp_path, v2)
    runtime = _runtime_for_registry(tmp_path, state, registry_root)
    try:
        profile = runtime._profile_from_snapshot(store.load(), spec)
        assert profile.asset_id == v2.asset_id
        assert profile.version == "2.0.0"
        assert profile.approval_source == "registry"
        assert profile.approved is True
    finally:
        runtime.close()


def test_registry_asset_not_found(tmp_path: Path) -> None:
    spec, v1, v2, registry_root = _registry_with_two_versions(tmp_path)
    del v1, v2
    unregistered = build_profile_asset(spec, repo_root=ROOT)
    state, store = _binding_state(tmp_path, unregistered)
    runtime = _runtime_for_registry(tmp_path, state, registry_root)
    try:
        with pytest.raises(AgentRuntimeError) as excinfo:
            runtime._profile_from_snapshot(store.load(), spec)
        assert excinfo.value.code == "mcp_asset_not_found"
    finally:
        runtime.close()


def test_registry_missing_falls_back_to_operator_config(tmp_path: Path) -> None:
    allowlist = McpAllowlist.load(
        ROOT / "molt" / "mcp" / "allowlist.default.json", repo_root=ROOT
    )
    spec = allowlist.get("project_kb")
    asset = build_profile_asset(spec, repo_root=ROOT)
    state, store = _binding_state(tmp_path, asset)
    runtime = _runtime_for_registry(tmp_path, state, tmp_path / "empty-registry")
    try:
        profile = runtime._profile_from_snapshot(store.load(), spec)
        assert profile.approval_source == "manual_operator_config"
        assert profile.approved is False
        assert profile.approved_by == "operator-config"
        assert profile.approval_note == (
            "manual operator configuration; not model-evaluated"
        )
    finally:
        runtime.close()


def test_binding_missing_asset_id_is_incomplete(tmp_path: Path) -> None:
    allowlist = McpAllowlist.load(
        ROOT / "molt" / "mcp" / "allowlist.default.json", repo_root=ROOT
    )
    spec = allowlist.get("project_kb")
    runtime = _runtime_for_registry(
        tmp_path, tmp_path / "skill-state", tmp_path / "empty-registry"
    )
    snapshot = {
        "revision": 3,
        "mcp": {
            "project_kb": {
                "server_id": "project_kb",
                "version": "1.0.0",
                "package_hash": "sha256:" + "0" * 64,
            }
        },
    }
    try:
        with pytest.raises(AgentRuntimeError) as excinfo:
            runtime._profile_from_snapshot(snapshot, spec)
        assert excinfo.value.code == "mcp_binding_incomplete"
    finally:
        runtime.close()


class StaticAdapter:
    name = "static"
    model = "static"
    real_model = True
    adapter_ready = True
    provider_error = None

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages, tools, tool_choice):  # noqa: ANN001
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        return ChatReply(content="静态回复")


def test_skill_loaded_event_and_captured_system_prompt(tmp_path: Path) -> None:
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap not installed")
    runtime = _runtime(tmp_path, mcp=True)
    adapter = CaptureAdapter(StaticAdapter())
    store = SessionStore(provider="test")
    session = store.create()
    runtime.set_mode(session.session_id, "active")
    runner = TurnRunner(store, build_index(ROOT), adapter, runtime=runtime)
    turn_id = new_id("turn")

    runner.run(session, turn_id, "如何验证这次改动零写入？")

    events = [event.event for event in session.events]
    assert "skill.loaded" in events
    assert events[-1] == "done"
    loaded = next(event for event in session.events if event.event == "skill.loaded")
    assert set(loaded.data) == {
        "turn_id",
        "skill_id",
        "version",
        "content_hash",
        "reason",
        "binding_revision",
        "mode",
    }
    assert loaded.data["skill_id"] == "repo-evidence"
    assert loaded.data["mode"] == "active"
    assert loaded.data["binding_revision"] == runtime.binding_public()["revision"] == 2

    package = load_package(ROOT / "skills", "repo-evidence")
    first_call = adapter.calls[0]
    system = first_call["messages"][0]
    assert system["role"] == "system"
    assert package.body.strip() in system["content"]
    assert package.content_hash in system["content"]
    assert first_call["tool_choice"] == "auto"
    runtime.close()


class _ToolThenContent:
    name = "tool-then-content"
    model = "static"
    real_model = False
    adapter_ready = True
    provider_error = None

    def complete(self, messages, tools, tool_choice):  # noqa: ANN001
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


def test_legacy_turn_event_order_unchanged_without_runtime() -> None:
    store = SessionStore(provider="test")
    session = store.create()
    runner = TurnRunner(store, build_index(ROOT), _ToolThenContent())
    runner.run(session, new_id("turn"), "看看实验")
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
    assert [event.data["state"] for event in session.events if event.event == "status"] == [
        "thinking",
        "tool",
        "responding",
    ]
    tool_result = next(event for event in session.events if event.event == "tool.result")
    assert "source" not in tool_result.data
    assert "server" not in tool_result.data


class _AlwaysThreeCalls:
    name = "three-calls"
    model = "static"
    real_model = False
    adapter_ready = True
    provider_error = None

    def complete(self, messages, tools, tool_choice):  # noqa: ANN001
        return ChatReply(
            tool_calls=[
                ToolCall(
                    call_id=f"call-{index}",
                    name="mcp__fake__echo",
                    arguments=json.dumps({"index": index}),
                )
                for index in range(3)
            ]
        )


class _FakeRuntime:
    enabled = True

    def __init__(self, *, revision: int = 7) -> None:
        self.revision = revision
        self._specs = (
            {
                "type": "function",
                "function": {"name": "mcp__fake__echo", "description": "", "parameters": {}},
            },
        )

    def enabled_for(self, session_id: str) -> bool:
        return True

    def begin_turn(self, task_text: str) -> TurnSnapshot:
        profile = McpProfileSnapshot(
            server_id="fake",
            asset_id="mcp-profile-fake",
            version="1.0.0",
            artifact_hash="sha256:" + "0" * 64,
            binding_revision=self.revision,
            approval_source="manual_operator_config",
            approved=False,
            approved_by="operator-config",
            approval_note="manual operator configuration; not model-evaluated",
        )
        surface = McpSurface(
            available=True,
            reason="ok",
            profiles={"fake": profile},
            specs=self._specs,
            servers=("fake",),
        )
        return TurnSnapshot(
            revision=self.revision,
            skill=None,
            mcp=surface,
            allowed_tools=("mcp__fake__echo",),
            specs=self._specs,
        )

    def clear_turn(self) -> None:
        return None

    def load_skill(self, task_text: str):  # noqa: ANN001
        return None

    def tool_specs(self, snapshot: TurnSnapshot | None = None):  # noqa: ANN001
        return list(self._specs)

    def describe_tool(self, name: str) -> dict[str, Any]:
        return {"source": "mcp", "server": "fake", "tool": "echo", "name": name}

    def execute_tool(
        self, snapshot: TurnSnapshot, name: str, arguments: Any
    ) -> ToolOutcome:
        return ToolOutcome(status="ok", result={"content": []})


def test_tool_budget_exceeded_writes_no_history() -> None:
    store = SessionStore(provider="test")
    session = store.create()
    runner = TurnRunner(store, build_index(ROOT), _AlwaysThreeCalls(), runtime=_FakeRuntime())
    runner.run(session, new_id("turn"), "触发预算")

    events = [event.event for event in session.events]
    assert events[0] == "turn.started"
    assert "tool.call" in events
    assert "done" not in events
    assert session.messages == []
    error = next(event for event in session.events if event.event == "error")
    assert error.data["code"] == ErrorCode.TOOL_BUDGET_EXCEEDED.value
    tool_calls = [event for event in session.events if event.event == "tool.call"]
    assert len(tool_calls) == 3
    first = tool_calls[0].data
    assert first["source"] == "mcp"
    assert first["server"] == "fake"
    assert first["tool"] == "echo"
    assert first["name"] == "mcp__fake__echo"
    assert first["binding_revision"] == 7


def test_active_turn_without_mcp_config_warns_and_finishes(tmp_path: Path) -> None:
    """No allowlist is an explicit degraded mode: warning, skill context only."""

    runtime = _runtime(tmp_path)
    store = SessionStore(provider="test")
    session = store.create()
    runtime.set_mode(session.session_id, "active")
    runner = TurnRunner(store, build_index(ROOT), StaticAdapter(), runtime=runtime)
    runner.run(session, new_id("turn"), "如何验证这次改动零写入？")

    events = [event.event for event in session.events]
    assert "runtime.warning" in events
    assert "done" in events
    assert not any(event.event == "error" for event in session.events)
    assert len(session.messages) == 2
    runtime.close()


def test_active_turn_with_broken_mcp_config_errors(tmp_path: Path) -> None:
    """Allowlist configured but no mcp binding: fail closed, no history."""

    package, state = _bind(tmp_path, mcp=False)
    del package
    runtime = AgentRuntime(
        ROOT,
        ROOT / "skills",
        state,
        ROOT / "molt" / "mcp" / "allowlist.default.json",
        MockAdapter(),
        enabled=True,
        mcp_registry_state=tmp_path / "upper",
    )
    store = SessionStore(provider="test")
    session = store.create()
    runtime.set_mode(session.session_id, "active")
    runner = TurnRunner(store, build_index(ROOT), StaticAdapter(), runtime=runtime)
    runner.run(session, new_id("turn"), "如何验证这次改动零写入？")

    events = [event.event for event in session.events]
    assert "done" not in events
    assert session.messages == []
    error = next(event for event in session.events if event.event == "error")
    assert error.data["code"] == ErrorCode.MCP_UNAVAILABLE.value
    runtime.close()


class _OneMcpCall:
    name = "one-mcp-call"
    model = "static"
    real_model = False
    adapter_ready = True
    provider_error = None

    def __init__(self) -> None:
        self.round = 0

    def complete(self, messages, tools, tool_choice):  # noqa: ANN001
        self.round += 1
        if self.round == 1:
            return ChatReply(
                tool_calls=[
                    ToolCall(
                        call_id="call-mcp-1",
                        name="mcp__fake__echo",
                        arguments=json.dumps({"q": "零写入"}),
                    )
                ]
            )
        return ChatReply(content="工具完成")


def test_mcp_tool_result_fields() -> None:
    store = SessionStore(provider="test")
    session = store.create()
    runner = TurnRunner(store, build_index(ROOT), _OneMcpCall(), runtime=_FakeRuntime())
    runner.run(session, new_id("turn"), "调用工具")

    call = next(event for event in session.events if event.event == "tool.call")
    assert call.data["source"] == "mcp"
    assert call.data["server"] == "fake"
    assert call.data["tool"] == "echo"
    assert call.data["name"] == "mcp__fake__echo"
    assert call.data["arguments"] == {"q": "零写入"}
    result = next(event for event in session.events if event.event == "tool.result")
    assert result.data["source"] == "mcp"
    assert result.data["server"] == "fake"
    assert result.data["tool"] == "echo"
    assert result.data["status"] == "ok"
    assert result.data["binding_revision"] == 7
    assert [event.event for event in session.events][-1] == "done"
