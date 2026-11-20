from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_kernel_transactions import _binary, _server
from test_version_cli import _cli

from molt.cli import main
from molt.kernel_client import KernelClient

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


def test_generic_reader_pins_three_files_and_audits_after_restore(tmp_path):
    kernel = _binary()
    original = tmp_path / "original"
    original.mkdir()
    workspace, config = _setup(original, kernel)
    files = ("config.json", "prompt.txt", "tools.json")
    config.write_text(json.dumps(json.loads(config.read_text()) | {"files": files}))
    versions = [
        dict(zip(files, (b'{"mode":"short"}\n', b"A prompt\n", b'{"tools":["echo"]}\n'), strict=True)),
        dict(zip(files, (b'{"mode":"full"}\n', b"B prompt\n", b'{"tools":["echo","count"]}\n'), strict=True)),
    ]

    def read(pin):
        return {name: (Path(pin["tree"]) / name).read_bytes() for name in files}

    with _server(kernel, workspace / "state"):
        edit = Path(_project(config, "begin")["edit"])
        receipts = []
        for content, reason in zip(versions, ("baseline", "enable counting"), strict=True):
            for name, value in content.items():
                (edit / name).write_bytes(value)
            receipts.append(_project(config, "save", "--reason", reason, "--reason-source", "synthetic-reader"))
        a, b = receipts
        pinned_b = _project(config, "read-tree")
        assert pinned_b["snapshot"] == b["snapshot"]
        _project(config, "rollback", a["snapshot"])
        pinned_a = _project(config, "read-tree")
        assert pinned_a["snapshot"] == a["snapshot"]
        assert read(pinned_b) == versions[1]
        assert read(pinned_a) == versions[0]
        audited = _cli(workspace, "audit", b["event"])
        assert audited["evidence"]["reason"] == {"text": "enable counting", "source": "synthetic-reader"}
        (edit / "prompt.txt").write_bytes(b"unsaved draft\n")

    backup = tmp_path / "backup"
    original.rename(backup)
    assert not original.exists()
    restored = tmp_path / "restored"
    shutil.copytree(backup, restored, ignore=shutil.ignore_patterns("kernel.sock"))
    restored_workspace, restored_config = restored / "v", restored / "project.json"
    session = restored_workspace / "project-session.json"
    before = session.read_bytes()
    with _server(kernel, restored_workspace / "state"):
        assert "项目配置与已有编辑会话不符" in _project(restored_config, "preview", expected=2)
        assert session.read_bytes() == before
        assert _cli(restored_workspace, "audit", b["event"]) == audited
        retained_edit = restored / edit.relative_to(original) / "prompt.txt"
        assert retained_edit.read_bytes() == b"unsaved draft\n"
        retained_export = restored / Path(pinned_b["tree"]).relative_to(original)
        assert read({"tree": retained_export}) == versions[1]

    # Make a fresh project binding on a working copy. Keep the complete backup.
    working = tmp_path / "working"
    shutil.copytree(restored, working, ignore=shutil.ignore_patterns("kernel.sock", "project-session.json"))
    with _server(kernel, working / "v" / "state"):
        current = _project(working / "project.json", "read-tree")
        assert current["snapshot"] == a["snapshot"] and read(current) == versions[0]
        fresh = Path(_project(working / "project.json", "begin")["edit"])
        (fresh / "prompt.txt").write_bytes(retained_edit.read_bytes())
        assert _project(working / "project.json", "preview")["changes"] == [
            {"path": "prompt.txt", "operation": "modify"},
        ]
    assert (backup / "v" / "project-session.json").read_bytes() == before


def test_project_recovers_lost_reply_with_release_kernel(tmp_path, monkeypatch, capsys):
    kernel = _binary()
    workspace, config = _setup(tmp_path, kernel)
    with _server(kernel, workspace / "state"):
        edit = Path(_project(config, "begin")["edit"]) / "opencode.json"
        edit.write_bytes(b"captured")
        call = KernelClient.call
        observed = []

        def lose_reply(client, method, params=None):
            result = call(client, method, params)
            if method == "generation.commit":
                observed.append(params["request_id"])
                raise ConnectionResetError("synthetic lost commit reply")
            return result

        with monkeypatch.context() as patch:
            patch.setattr(KernelClient, "call", lose_reply)
            assert main(["project", "--config", str(config), "save", "--reason", "retained reason"]) == 4
        request_id, = observed
        assert request_id in capsys.readouterr().err
        receipt = _project(config, "recover")
        assert receipt["request_id"] == request_id
        assert receipt["status"] == "committed"
        assert _cli(workspace, "request-status", request_id)["result"] == receipt["current"]
        assert len(_cli(workspace, "history")) == 1
        assert _cli(workspace, "audit", receipt["event"])["evidence"]["reason"]["text"] == "retained reason"


@pytest.mark.parametrize("point", ["log.fsynced", "head.durable"])
@pytest.mark.parametrize("relocate", [False, True], ids=["same-path", "old-path-unavailable"])
def test_project_recovers_exact_request_and_retains_later_edits(tmp_path, point, relocate):
    kernel = _binary()
    original = tmp_path / "original"
    original.mkdir()
    workspace, config = _setup(original, kernel)
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
    if relocate:
        backup = tmp_path / "backup"
        original.rename(backup)
        restored = tmp_path / "restored"
        shutil.copytree(backup, restored, ignore=shutil.ignore_patterns("kernel.sock"))
        workspace = restored / "v"
        config = restored / "project.json"
        saved_session = (workspace / "project-session.json").read_bytes()
        saved_intent = (workspace / "broker.json").read_bytes()
        assert not original.exists()
        with _server(kernel, workspace / "state"):
            assert "项目配置与已有编辑会话不符" in _project(config, "recover", expected=2)
            assert (workspace / "project-session.json").read_bytes() == saved_session
            assert (workspace / "broker.json").read_bytes() == saved_intent
            committed = _cli(workspace, "recover")
            assert _cli(workspace, "request-status", request_id)["result"] == committed
            assert _cli(workspace, "recover") == committed
            assert len(_cli(workspace, "history")) == 1
            evidence = _cli(workspace, "audit", session["pending"]["event"])["evidence"]
            assert evidence["reason"]["text"] == "durable"
        working = tmp_path / "working"
        shutil.copytree(restored, working, ignore=shutil.ignore_patterns("kernel.sock", "project-session.json"))
        with _server(kernel, working / "v" / "state"):
            new_config = working / "project.json"
            pinned = Path(_project(new_config, "read-tree")["tree"]) / edit.name
            assert pinned.read_text() == "captured"
            copied_edit = backup / edit.relative_to(original)
            fresh = Path(_project(new_config, "begin")["edit"]) / edit.name
            fresh.write_bytes(copied_edit.read_bytes())
            assert _project(new_config, "preview")["changes"] == [{"path": edit.name, "operation": "modify"}]
            assert copied_edit.read_text() == "edited during recovery"
        assert (backup / "v" / "broker.json").read_bytes() == saved_intent
        assert (backup / "v" / "project-session.json").read_bytes() == saved_session
        return

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
