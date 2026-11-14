"""Lab execution tests: fail-closed gate, workspace safety, mock two-round flow."""

from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from molt.agent.chat import ChatReply, MockAdapter
from molt.agent.lab import LabBusy, LabManager, LabUnavailable
from molt.agent.probe import build_index
from molt.agent.server import AgentApp, create_server, load_openapi
from molt.agent.workspace import PathRejected, SandboxStatus, Workspace, probe_sandbox

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token-lab-only"


def _request(port: int, method: str, path: str, body=None, token: str | None = TOKEN):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": f"127.0.0.1:{port}"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    raw = None
    if body is not None:
        raw = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=raw, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response.status, payload


class LabHarness:
    def __init__(self, tmp_path: Path, sandbox: SandboxStatus | None = None) -> None:
        adapter = MockAdapter()
        lab = LabManager(adapter, lab_root=tmp_path, sandbox=sandbox)
        self.app = AgentApp(
            index=build_index(ROOT),
            adapter=adapter,
            token=TOKEN,
            allowed_origins=(),
            openapi=load_openapi(ROOT),
            data_dir=ROOT / "web" / "data",
            root=ROOT,
            lab=lab,
        )
        self.server = create_server(self.app, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def test_workspace_rejects_escape_and_symlink(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path, "path-test")
    try:
        workspace.write("calc.py", "print(1)\n")
        assert workspace.read("calc.py") == "print(1)\n"
        for name in ("../outside.py", "/etc/passwd", "a/../../b.py"):
            with pytest.raises(PathRejected):
                workspace.write(name, "x")
        outside = tmp_path / "outside.py"
        outside.write_text("secret", encoding="utf-8")
        (workspace.root / "link.py").symlink_to(outside)
        with pytest.raises(PathRejected):
            workspace.read("link.py")
    finally:
        workspace.cleanup()


def test_lab_fails_closed_without_sandbox(tmp_path: Path) -> None:
    sandbox = SandboxStatus(
        available=False,
        backend="bwrap",
        reason="bwrap_not_installed",
        detail="test",
    )
    lab = LabManager(MockAdapter(), lab_root=tmp_path, sandbox=sandbox)
    assert lab.capabilities()["execution"]["available"] is False
    with pytest.raises(LabUnavailable):
        lab.create("calc-v1")


def test_lab_mock_two_round_flow(tmp_path: Path) -> None:
    sandbox = probe_sandbox()
    if not sandbox.available:
        pytest.skip(f"bwrap unavailable: {sandbox.reason}")
    lab = LabManager(MockAdapter(), lab_root=tmp_path, sandbox=sandbox)
    run = lab.create("calc-v1")
    for _ in range(200):
        if run.status in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.05)
    assert run.status == "completed", run.error
    assert run.result is not None
    assert run.result["round1"]["passed"] is False
    assert run.result["round2"]["passed"] is True
    assert run.result["adopted"] is True
    assert run.result["provenance"]["patch_source"] == "mock-deterministic"
    assert run.result["provenance"]["real_model"] is False
    assert "+    return a + b" in run.result["diff"]
    assert run.result["provenance"]["historical"] is False


def test_lab_api_fail_closed_and_auth(tmp_path: Path) -> None:
    sandbox = SandboxStatus(
        available=False,
        backend="bwrap",
        reason="bwrap_not_installed",
        detail="test",
    )
    harness = LabHarness(tmp_path, sandbox=sandbox)
    try:
        assert _request(harness.port, "GET", "/api/lab/capabilities", token=None)[0] == 401
        status, body = _request(harness.port, "GET", "/api/lab/capabilities")
        assert status == 200
        assert json.loads(body)["execution"]["available"] is False
        status, body = _request(
            harness.port, "POST", "/api/lab/runs", {"task_id": "calc-v1"}
        )
        assert status == 503
        assert json.loads(body)["error"]["details"]["reason"] == "isolation_unavailable"
        status, body = _request(harness.port, "GET", "/api/lab/openapi.json", token=None)
        assert status == 200
        assert "/api/lab/runs" in json.loads(body)["paths"]
    finally:
        harness.close()


def test_lab_api_mock_flow(tmp_path: Path) -> None:
    sandbox = probe_sandbox()
    if not sandbox.available:
        pytest.skip(f"bwrap unavailable: {sandbox.reason}")
    harness = LabHarness(tmp_path, sandbox=sandbox)
    try:
        status, body = _request(
            harness.port, "POST", "/api/lab/runs", {"task_id": "calc-v1"}
        )
        assert status == 202
        run_id = json.loads(body)["run_id"]
        for _ in range(200):
            status, body = _request(harness.port, "GET", f"/api/lab/runs/{run_id}")
            result = json.loads(body)
            if result["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        assert result["status"] == "completed", result.get("error")
        assert result["result"]["adopted"] is True
        status, body = _request(
            harness.port, "GET", f"/api/lab/runs/{run_id}/events", token=TOKEN
        )
        assert status == 200
        text = body.decode("utf-8")
        assert "event: round1.completed" in text
        assert "event: evolution.diff" in text
        assert "event: round2.completed" in text
        assert "event: done" in text
        assert _request(
            harness.port, "DELETE", f"/api/lab/runs/{run_id}"
        )[0] == 204
    finally:
        harness.close()


def test_lab_cancel_slow_run(tmp_path: Path) -> None:
    sandbox = probe_sandbox()
    if not sandbox.available:
        pytest.skip(f"bwrap unavailable: {sandbox.reason}")
    lab = LabManager(MockAdapter(), lab_root=tmp_path, sandbox=sandbox)
    run = lab.create("slow-v1")
    time.sleep(0.4)
    assert lab.cancel(run.run_id) is True
    for _ in range(100):
        if run.status == "cancelled":
            break
        time.sleep(0.05)
    assert run.status == "cancelled", run.error
    assert any(event.event == "run.cancelled" for event in run.events)
    assert any(event.event == "done" for event in run.events)


class ScriptedRealAdapter:
    name = "scripted-real"
    model = "scripted-real"
    real_model = True
    adapter_ready = True
    provider_error = None

    def complete(self, messages, tools, tool_choice):  # noqa: ANN001
        return ChatReply(
            content='{"file":"calc.py","old":"    return a - b\\n","new":"    return a + b\\n"}'
        )


def test_lab_provider_patch_path(tmp_path: Path) -> None:
    sandbox = probe_sandbox()
    if not sandbox.available:
        pytest.skip(f"bwrap unavailable: {sandbox.reason}")
    lab = LabManager(ScriptedRealAdapter(), lab_root=tmp_path, sandbox=sandbox)
    run = lab.create("calc-v1")
    for _ in range(200):
        if run.status in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.05)
    assert run.status == "completed", run.error
    assert run.result["provenance"]["patch_source"] == "provider"
    assert run.result["provenance"]["real_model"] is True
    assert run.result["round2"]["passed"] is True


def test_lab_rejects_concurrent_run(tmp_path: Path) -> None:
    sandbox = probe_sandbox()
    if not sandbox.available:
        pytest.skip(f"bwrap unavailable: {sandbox.reason}")
    lab = LabManager(MockAdapter(), lab_root=tmp_path, sandbox=sandbox)
    first = lab.create("slow-v1")
    try:
        with pytest.raises(LabBusy):
            lab.create("calc-v1")
    finally:
        lab.cancel(first.run_id)


def test_lab_command_timeout_fails_closed(tmp_path: Path) -> None:
    sandbox = probe_sandbox()
    if not sandbox.available:
        pytest.skip(f"bwrap unavailable: {sandbox.reason}")
    lab = LabManager(
        MockAdapter(), lab_root=tmp_path, sandbox=sandbox, command_timeout=0.05
    )
    run = lab.create("slow-v1")
    for _ in range(100):
        if run.status in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.05)
    assert run.status == "failed"
    assert run.error and run.error["code"] == "command_timeout"
    assert [item for item in tmp_path.iterdir() if item.name != "runs"] == []
    if (tmp_path / "runs").exists():
        assert all(item.suffix == ".json" for item in (tmp_path / "runs").iterdir())


def test_lab_cleans_workspace_after_success(tmp_path: Path) -> None:
    sandbox = probe_sandbox()
    if not sandbox.available:
        pytest.skip(f"bwrap unavailable: {sandbox.reason}")
    lab = LabManager(MockAdapter(), lab_root=tmp_path, sandbox=sandbox)
    run = lab.create("calc-v1")
    for _ in range(100):
        if run.status in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.05)
    assert run.status == "completed"
    assert [item for item in tmp_path.iterdir() if item.name != "runs"] == []
    if (tmp_path / "runs").exists():
        assert all(item.suffix == ".json" for item in (tmp_path / "runs").iterdir())
