from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_kernel_transactions import _binary, _server
from test_version_cli import _cli

pytestmark = pytest.mark.integration


def _project(config, *args, expected=0):
    process = subprocess.run(
        [sys.executable, "-m", "molt", "project", "--config", config, *map(str, args)],
        capture_output=True, text=True, timeout=15,
    )
    assert process.returncode == expected, process.stderr
    return process.stderr if expected else json.loads(process.stdout)


def _setup(root, kernel):
    workspace = root / "v"
    _cli(workspace, "init", "--kernel", kernel, "--synthetic")
    for name in ("edit", "export"):
        (root / name).mkdir(mode=0o700)
    config = root / "project.json"
    config.write_text(json.dumps({
        "workspace": "v", "edits": "edit", "exports": "export",
        "files": ["opencode.json", ".opencode/agents/demo.md"],
    }))
    return workspace, config


def test_project_workflow_preserves_dirty_edits_and_pins(tmp_path):
    kernel = _binary()
    workspace, config = _setup(tmp_path, kernel)
    with _server(kernel, workspace / "state"):
        first = _project(config, "begin")
        edit = Path(first["edit"]) / "opencode.json"
        assert first["generation"] == 0 and first["head"] is None
        assert _project(config, "save", "--reason", "empty")["changed"] is False
        edit.write_text("first")
        assert _project(config, "preview")["changes"] == [{"path": edit.name, "operation": "add"}]
        initial = _project(config, "save", "--reason", "initial")
        pinned = Path(_project(config, "read-tree")["tree"]) / edit.name
        assert not pinned.stat().st_mode & 0o222
        assert _project(config, "save", "--reason", "unchanged")["changed"] is False
        _cli(workspace, "branch", "trial", initial["snapshot"], "--expect-generation", 1)

        fresh = _project(config, "begin")
        edit = Path(fresh["edit"]) / edit.name
        edit.write_text("second")
        assert "未保存" in _project(config, "switch", "trial", expected=3)
        assert "未保存" in _project(config, "begin", expected=3)
        assert _project(config, "preview")["changes"] == [{"path": edit.name, "operation": "modify"}]
        second = _project(config, "save", "--reason", "second", "--reason-source", "test")
        evidence = _cli(workspace, "audit", second["event"])["evidence"]
        assert evidence["reason"] == {"text": "second", "source": "test"}
        newer = Path(_project(config, "read-tree")["tree"]) / edit.name
        _project(config, "switch", "trial")
        switched = Path(_project(config, "read-tree")["tree"]) / edit.name
        assert switched.read_text() == pinned.read_text() == "first"
        assert newer.read_text() == edit.read_text() == "second"

        edit.write_text("uncommitted")
        assert "stale generation" in _project(config, "save", "--reason", "stale", expected=3)
        assert edit.read_text() == "uncommitted"
        trial = _project(config, "begin", "--branch", "trial", "--keep-old")
        assert trial["previous_edit"] == str(edit.parent)
        assert (Path(trial["edit"]) / edit.name).read_text() == "first"
        assert edit.read_text() == "uncommitted"
        _project(config, "rollback", second["snapshot"])
        rolled_back = Path(_project(config, "read-tree")["tree"]) / edit.name
        assert rolled_back.read_text() == "second"
        assert pinned.read_text() == "first"
        assert "不支持" in _project(config, "opencode", "--", "run", "--server=example", expected=2)


@pytest.mark.parametrize("point", ["log.fsynced", "head.durable"])
def test_project_recovers_exact_request_and_retains_later_edits(tmp_path, point):
    kernel = _binary()
    workspace, config = _setup(tmp_path, kernel)
    state = workspace / "state"
    marker = tmp_path / "pause"
    env = {**os.environ, "MOLT_KERNEL_PAUSE_AT": point, "MOLT_KERNEL_PAUSE_MARKER": str(marker)}

    with subprocess.Popen(
        [kernel, "serve", "--state", state], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ) as server, ThreadPoolExecutor(max_workers=1) as pool:
        try:
            deadline = time.monotonic() + 5
            while subprocess.run(
                [kernel, "ping", "--socket", state / "kernel.sock"], capture_output=True, timeout=1,
            ).returncode:
                assert server.poll() is None and time.monotonic() < deadline
                time.sleep(.02)
            edit = Path(_project(config, "begin")["edit"]) / "opencode.json"
            edit.write_text("captured")
            save = pool.submit(_project, config, "save", "--reason", "durable", expected=4)
            while not marker.exists():
                assert not save.done() and time.monotonic() < deadline
                time.sleep(.01)
            session = json.loads((workspace / "project-session.json").read_text())
            request_id = session["pending"]["request_id"]
            broker = json.loads((workspace / "broker.json").read_text())
            assert broker["pending"]["params"]["request_id"] == request_id
        finally:
            server.kill()
            server.wait(timeout=5)
        assert request_id in save.result(timeout=5)

    edit.write_text("edited during recovery")
    with _server(kernel, state):
        assert request_id in _project(config, "save", "--reason", "retry", expected=4)
        recovery = _project(config, "recover")
        assert recovery["request_id"] == request_id
        assert recovery["status"] == "committed"
        assert _cli(workspace, "request-status", request_id)["result"] == recovery["current"]
        assert _project(config, "preview")["changes"] == [{"path": edit.name, "operation": "modify"}]
        tree = Path(_project(config, "read-tree")["tree"])
        assert (tree / edit.name).read_text() == "captured"
        assert edit.read_text() == "edited during recovery"
        assert _project(config, "recover")["current"]["generation"] == 1
        assert len(_cli(workspace, "history")) == 1
