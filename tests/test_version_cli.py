from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from test_kernel_transactions import _binary, _run, _server

from molt.core.fsutil import exclusive_lock

pytestmark = pytest.mark.integration


def _cli(workspace, *args, expected=0):
    process = subprocess.run(
        [sys.executable, "-m", "molt", "version", "--workspace", workspace, *map(str, args)],
        check=False, capture_output=True, text=True, timeout=15,
    )
    assert process.returncode == expected, process.stderr
    return json.loads(process.stdout) if expected == 0 else process.stderr


def _files(tmp_path, *, prefix="", format="plain", tool_format="plain"):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"reply_format": format, "prefix": prefix}))
    tool = tmp_path / "tool.json"
    tool.write_text(json.dumps({"tool": "echo", "output_format": tool_format}))
    return "--config", config, "--tool", tool


def _commit(workspace, files, generation, *args, **options):
    return _cli(workspace, "commit", *files, "--expect-generation", generation, *args, **options)


def _fingerprint(workspace):
    return {str(path.relative_to(workspace)): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}


def test_version_cli_workflow_and_conflicting_writers(tmp_path):
    kernel = _binary()
    workspace = tmp_path / "v"
    _cli(workspace, "init", "--kernel", kernel)
    _cli(workspace, "init", "--kernel", kernel, expected=2)
    with _server(kernel, workspace / "state"):
        empty = _cli(workspace, "status")
        assert empty["generation"] == 0 and empty["provenance"] == "local"
        first = _commit(workspace, _files(tmp_path), 0)
        initial = first["snapshot"]
        original_event = _cli(workspace, "audit", first["event"])
        assert original_event["evidence"]["provenance"] == "local"
        before_reads = _fingerprint(workspace)
        descriptor = _cli(workspace, "show", initial)["descriptor"]
        assert {"config", "tool", "dependency", "parents"} == set(descriptor)
        assert _cli(workspace, "history")[0]["event"] == first["event"]
        pin = tmp_path / "pin.json"
        pin.write_text(json.dumps(_cli(workspace, "pin")))
        assert _cli(workspace, "run", "hi")["output"] == "hi"
        assert _fingerprint(workspace) == before_reads

        _cli(workspace, "branch", "topic", initial, "--expect-generation", 1)
        rejected = _commit(workspace, _files(tmp_path, format="json"), 2, "--expect-head", initial, expected=2)
        assert "incompatible" in rejected
        assert _cli(workspace, "status")["generation"] == 2

        files = _files(tmp_path, prefix="main: ")
        command = [
            sys.executable, "-m", "molt", "version", "--workspace", str(workspace), "commit",
            *map(str, files), "--expect-generation", "2", "--expect-head", initial,
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            writers = [pool.submit(subprocess.run, command, capture_output=True, timeout=15) for _ in range(2)]
            outcomes = [writer.result() for writer in writers]
        assert sorted(outcome.returncode for outcome in outcomes) == [0, 3]
        ours = next(json.loads(outcome.stdout) for outcome in outcomes if outcome.returncode == 0)
        assert _cli(workspace, "status")["generation"] == 3
        _commit(workspace, files, 2, "--expect-head", initial, expected=3)
        theirs = _commit(
            workspace, _files(tmp_path, tool_format="config"), 3, "--branch", "topic", "--expect-head", initial,
        )
        merged = _cli(
            workspace, "merge", "main", "topic", "--expect-generation", 4,
            "--expect-target", ours["snapshot"], "--expect-source", theirs["snapshot"],
        )
        selected = _cli(workspace, "switch", "main", "--expect-generation", 5, "--expect-head", merged["snapshot"])
        merged_pin = tmp_path / "merged.json"
        merged_pin.write_text(json.dumps(_cli(workspace, "pin")))
        assert _cli(workspace, "show", selected["snapshot"])["descriptor"]["parents"] == [
            ours["snapshot"], theirs["snapshot"],
        ]
        assert _cli(workspace, "run", "hi")["output"] == "main: hi"
        assert _cli(workspace, "run", "hi", "--pin", pin)["output"] == "hi"
        bad = _commit(workspace, _files(tmp_path, prefix="topic: ", tool_format="config"), 6,
                      "--branch", "topic", "--expect-head", theirs["snapshot"])
        assert "merge conflict" in _cli(
            workspace, "merge", "main", "topic", "--expect-generation", 7,
            "--expect-target", merged["snapshot"], "--expect-source", bad["snapshot"], expected=3,
        )
        rolled = _cli(workspace, "rollback", initial, "--expect-generation", 7)
        assert rolled["current"]["generation"] == 8
        assert _cli(workspace, "run", "hi")["output"] == "hi"
        assert _cli(workspace, "run", "hi", "--pin", merged_pin)["output"] == "main: hi"
        audit = _cli(workspace, "audit", merged["event"])
        _cli(workspace, "digest", merged["event"], "--model", tmp_path / "missing.gguf",
             "--model-id", "missing", expected=1)
        assert _cli(workspace, "audit", merged["event"]) == audit
        assert _cli(workspace, "status")["generation"] == 8
        digest = _cli(workspace, "digest", merged["event"])
        assert digest["digest"]["inputs"] == [merged["event"]]
        assert _cli(workspace, "audit", merged["event"])["evidence"] == audit["evidence"]


@pytest.mark.parametrize(("point", "durable"), [("log.fsynced", False), ("head.durable", True)])
@pytest.mark.parametrize("managed", [False, True], ids=["echo", "files"])
def test_original_request_after_kernel_disconnect(tmp_path, point, durable, managed):
    kernel = _binary()
    workspace = tmp_path / "v"
    _cli(workspace, "init", "--kernel", kernel, "--synthetic")
    state = workspace / "state"
    pin = tmp_path / "original-pin.json"
    if managed:
        edits, exports, manifest = (tmp_path / name for name in ("edits", "exports", "files.json"))
        edits.mkdir()
        exports.mkdir(mode=0o700)
        manifest.write_text('["opencode.json","tool.txt"]')

        def inputs(label):
            (edits / "opencode.json").write_text(label)
            (edits / "tool.txt").write_text(label)
            return ("--root", edits, "--manifest", manifest, "--reason", label, "--reason-source", "test")

        def commit(target, files, generation, *args, expected=0):
            return _cli(target, "commit-files", *files, "--expect-generation", generation, *args,
                        expected=expected)
    else:
        inputs = lambda label: _files(  # noqa: E731
            tmp_path, format="plain" if label == "initial" else "json",
            tool_format="plain" if label == "initial" else "json",
        )
        commit = _commit
    with _server(kernel, state):
        first = commit(workspace, inputs("initial"), 0)
        if managed:
            old_tree = Path(_cli(workspace, "read-tree", "--into", exports)["tree"])
        else:
            pin.write_text(json.dumps(_cli(workspace, "pin")))
    checkpoint = tmp_path / "checkpoint.json"
    _run(
        kernel, "checkpoint", "export", "--state", state, "--out", checkpoint,
        "--created-at", "2026-11-20T00:00:00Z",
    )
    marker = tmp_path / "pause"
    env = {**os.environ, "MOLT_KERNEL_PAUSE_AT": point, "MOLT_KERNEL_PAUSE_MARKER": str(marker)}
    files = inputs("changed")
    with subprocess.Popen(
        [sys.executable, "-m", "molt", "version", "--workspace", workspace, "serve", "--kernel", kernel],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ) as server:
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    ping = subprocess.run(
                        [kernel, "ping", "--socket", state / "kernel.sock"], capture_output=True, timeout=1,
                    )
                    if ping.returncode == 0:
                        break
                    assert server.poll() is None
                    time.sleep(.02)
                else:
                    pytest.fail("fault-injection service did not start")
                write = pool.submit(
                    commit, workspace, files, 1, "--expect-head", first["snapshot"], "--activate",
                    expected=4,
                )
                while not marker.exists():
                    assert not write.done() and time.monotonic() < deadline
                    time.sleep(.01)
                request = json.loads((workspace / "broker.json").read_text())["pending"]["params"]["request_id"]
            finally:
                server.kill()
                server.wait(timeout=5)
            assert request in write.result(timeout=6)

    # Test both a restart of the original and one restored from a stopped workspace.
    backup = tmp_path / "backup"
    shutil.copytree(workspace, backup, ignore=shutil.ignore_patterns("kernel.sock"))
    restore = tmp_path / "restore"
    shutil.copytree(backup, restore)
    for target in (workspace, restore):
        with _server(kernel, target / "state"):
            pending = _cli(target, "status")
            assert pending["pending_request"] == request
            status = _cli(target, "request-status", request)
            assert status["status"] == ("committed" if durable else "not_found")
            commit(target, files, 1, "--expect-head", first["snapshot"], expected=4)
            recovered = _cli(target, "recover")
            assert recovered["generation"] == 2
            assert _cli(target, "recover") == recovered
            assert _cli(target, "request-status", request) == {"status": "committed", "result": recovered}
            history = _cli(target, "history")
            assert [event["generation"] for event in history] == [1, 2]
            assert _cli(target, "audit", first["event"])["evidence"]["provenance"] == "synthetic"
            if managed:
                tree = Path(_cli(target, "read-tree", "--into", exports)["tree"])
                for path in ("opencode.json", "tool.txt"):
                    assert (tree / path).read_text() == "changed"
                    assert (old_tree / path).read_text() == "initial"
                event = _cli(target, "audit", history[-1]["event"])["evidence"]
                assert event["reason"] == {"text": "changed", "source": "test"}
            else:
                assert json.loads(_cli(target, "run", "hi")["output"]) == {"echo": "hi"}
                assert _cli(target, "run", "hi", "--pin", pin)["output"] == "hi"
        report = _run(kernel, "audit", "--state", target / "state", "--checkpoint", checkpoint)
        assert json.loads(report.stdout)["checkpoint_verified"]


def test_workspace_identity_and_permissions(tmp_path):
    kernel = _binary()
    workspace, other = tmp_path / "v", tmp_path / "other"
    _cli(workspace, "init", "--kernel", kernel)
    _cli(other, "init", "--kernel", kernel)
    with _server(kernel, other / "state"):
        (workspace / "state" / "kernel.sock").symlink_to(other / "state" / "kernel.sock")
        assert "identity mismatch" in _cli(workspace, "status", expected=2)
        assert _cli(other, "status")["generation"] == 0
        _cli(other, "serve", "--kernel", kernel, expected=1)
        assert _cli(other, "status")["generation"] == 0
        with exclusive_lock(other / "broker.lock"):
            _cli(other, "status", expected=3)
    other.chmod(0o755)
    assert "private" in _cli(other, "serve", "--kernel", kernel, expected=2)


def test_managed_file_capture_and_merge(tmp_path):
    kernel = _binary()
    workspace, edits, manifest = (tmp_path / name for name in ("v", "edits", "files.json"))
    edits.mkdir()
    config, tool = (edits / name for name in ("opencode.json", "tool.txt"))
    config.write_bytes(b'{"model":"local/example"}\n')
    tool.write_bytes(b"tool\r\n")
    paths = [config.name, tool.name]

    def commit(generation, *args, listed=paths, expected=0):
        manifest.write_text(json.dumps(listed))
        return _cli(workspace, "commit-files", "--root", edits, "--manifest", manifest,
                    "--expect-generation", generation, *args, expected=expected)

    _cli(workspace, "init", "--kernel", kernel, "--synthetic")
    with _server(kernel, workspace / "state"):
        for bad_path in ("../v/journal.json", "/tmp/file", "a//b", ".env.local"):
            assert "invalid managed path" in commit(0, listed=[bad_path], expected=2)
        link = edits / "link"
        link.symlink_to(config)
        assert "cannot capture" in commit(0, listed=["link"], expected=2)
        os.mkfifo(edits / "fifo")
        assert "regular file" in commit(0, listed=["fifo"], expected=2)
        assert "duplicate" in commit(0, listed=[config.name] * 2, expected=2)
        first = commit(0, "--reason", "baseline", "--reason-source", "operator")
        head = first["snapshot"]
        assert _cli(workspace, "audit", first["event"])["evidence"]["reason"] == {
            "text": "baseline", "source": "operator",
        }
        assert commit(1, "--expect-head", head)["changed"] is False
        assert "stale generation" in commit(0, "--expect-head", head, expected=3)
        _cli(workspace, "branch", "topic", head, "--expect-generation", 1)
        config.write_bytes(b'{"model":"local/changed"}\n')
        ours = commit(2, "--expect-head", head)["snapshot"]
        config.write_bytes(b'{"model":"local/example"}\n')
        tool.write_bytes(b"changed\r\n")
        theirs = commit(3, "--branch", "topic", "--expect-head", head)["snapshot"]
        merged = _cli(workspace, "merge", "main", "topic", "--expect-target", ours,
                      "--expect-source", theirs, "--expect-generation", 4, "--activate")
        show = _cli(workspace, "show", merged["snapshot"])
        assert show["descriptor"]["parents"] == [ours, theirs]
        assert show["files"] == {
            config.name: _cli(workspace, "show", ours)["files"][config.name],
            tool.name: _cli(workspace, "show", theirs)["files"][tool.name],
        }
        _cli(workspace, "branch", "delete", merged["snapshot"], "--expect-generation", 5)
        deleted = commit(6, "--branch", "delete", "--expect-head", merged["snapshot"],
                         listed=[config.name])["snapshot"]
        tool.write_bytes(b"changed again\n")
        modified = commit(7, "--expect-head", merged["snapshot"])["snapshot"]
        before = _cli(workspace, "status")
        assert "merge conflict in tool.txt" in _cli(
            workspace, "merge", "main", "delete", "--expect-target", modified,
            "--expect-source", deleted, "--expect-generation", 8, expected=3,
        )
        assert _cli(workspace, "status") == before


def test_managed_reader_survives_switch_restart_and_failed_preparation(tmp_path, monkeypatch):
    from molt.version_files import publish_tree

    kernel = _binary()
    workspace, edits, exports = (tmp_path / name for name in ("v", "edits", "out"))
    edits.mkdir()
    exports.mkdir(mode=0o700)
    manifest = tmp_path / "files.json"
    manifest.write_text('["opencode.json","nested/tool.txt"]')
    (edits / "nested").mkdir()

    def edit(label):
        (edits / "opencode.json").write_text(label)
        (edits / "nested/tool.txt").write_text(label)

    def commit(generation, *args):
        return _cli(workspace, "commit-files", "--root", edits, "--manifest", manifest,
                    "--expect-generation", generation, *args)["snapshot"]

    def read(tree):
        return tuple((tree / path).read_text() for path in json.loads(manifest.read_text()))

    _cli(workspace, "init", "--kernel", kernel)
    with _server(kernel, workspace / "state"):
        edit("old")
        old = commit(0)
        edit("new")
        new = commit(1, "--expect-head", old)
        edit("dirty")
        entered, resume = Event(), Event()

        def pinned_reader():
            result = _cli(workspace, "read-tree", "--into", exports)
            tree = Path(result["tree"])
            assert result["snapshot"] == old and tree.stat().st_mode & 0o777 == 0o500
            entered.set()
            assert resume.wait(5)
            return read(tree)

        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(pinned_reader)
            try:
                assert entered.wait(5)
                _cli(workspace, "switch", "main", "--expect-head", new, "--expect-generation", 2)
                active = _cli(workspace, "read-tree", "--into", exports)
                assert active["snapshot"] == new and read(Path(active["tree"])) == ("new", "new")
            finally:
                resume.set()
            assert pending.result(timeout=5) == ("old", "old")

        before = set(exports.glob("*/project"))
        original = Path.write_bytes

        def interrupted(path, data):
            if path.name == "tool.txt":
                raise OSError("injected preparation failure")
            return original(path, data)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "write_bytes", interrupted)
            with pytest.raises(OSError, match="injected preparation"):
                publish_tree(exports, {"opencode.json": b"old", "nested/tool.txt": b"old"})
        assert set(exports.glob("*/project")) == before
        retry = _cli(workspace, "export", old, "--into", exports)
        assert read(Path(retry["tree"])) == ("old", "old")
        _cli(workspace, "rollback", old, "--expect-generation", 3)

    with _server(kernel, workspace / "state"):
        active = _cli(workspace, "read-tree", "--into", exports)
        assert active["snapshot"] == old and read(Path(active["tree"])) == ("old", "old")
    assert read(edits) == ("dirty", "dirty")
