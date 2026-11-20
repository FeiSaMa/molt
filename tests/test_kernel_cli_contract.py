"""Generic CLI failure, UDS and read-only v1 contracts; synthetic data only."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from test_kernel_transactions import TS, _binary, _run, _server
from test_v1_compatibility import _snapshot, _write, build_compatibility_fixture

from molt.cli import main
from molt.core import write_policy
from molt.core.events import EventLog, make_event
from molt.core.fsutil import atomic_write_text
from molt.core.ledger import Ledger
from molt.core.store import StateStore
from molt.kernel_client import KernelClient


def _tree_snapshot(root: Path) -> tuple:
    entries = []
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        value = os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else ""
        entries.append((str(path.relative_to(root)), stat.S_IMODE(info.st_mode), value))
    return tuple(entries)


@pytest.mark.parametrize("command", [["current"], ["commit", "--manifest", "sha256:" + "a" * 64,
                                                      "--request-id", "same-id", "--at", TS]])
@pytest.mark.parametrize("explicit_backend", [False, True])
def test_no_service_never_falls_back(tmp_path, monkeypatch, capsys, command, explicit_backend):
    monkeypatch.setattr(write_policy, "DEFAULT_BACKEND", "kernel")
    monkeypatch.delenv("MOLT_KERNEL_SOCKET", raising=False)
    monkeypatch.delenv("MOLT_KERNEL_BACKEND", raising=False)
    if explicit_backend:
        monkeypatch.setenv("MOLT_KERNEL_BACKEND", "kernel")
    before = _tree_snapshot(tmp_path)
    assert main(command) == 2
    assert "requires MOLT_KERNEL_SOCKET" in capsys.readouterr().err
    assert _tree_snapshot(tmp_path) == before
    monkeypatch.setenv("MOLT_KERNEL_SOCKET", str(tmp_path / "absent.sock"))
    assert main(command) == 1
    error = capsys.readouterr().err
    if command[0] == "commit":
        assert "outcome unknown" in error and "same-id" in error
    assert _tree_snapshot(tmp_path) == before


def test_kernel_rejects_platform_before_file_or_socket_access(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MOLT_KERNEL_BACKEND", "kernel")
    monkeypatch.setattr("molt.cli.platform.machine", lambda: "aarch64")
    assert main(["object", "put", str(tmp_path / "absent")]) == 2
    assert "Linux x86_64" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_v1_writes_are_rejected_even_when_legacy_default_is_patched(tmp_path, monkeypatch, capsys):
    source = tmp_path / "legacy"
    build_compatibility_fixture(source)
    (source / "operator-link").symlink_to("runs")
    store = StateStore(source)
    before = _tree_snapshot(source)
    monkeypatch.setenv("MOLT_KERNEL_BACKEND", "v1")
    monkeypatch.setattr(write_policy, "DEFAULT_BACKEND", "kernel")
    commands = [
        ["commit", "--manifest", "unused", "--request-id", "unused", "--at", TS],
        ["recover", "--state", str(source)],
        ["object", "put", str(source / "unused")],
        ["manifest", "create", str(source / "unused")],
        ["checkpoint", "export", "--state", str(source), "--out", str(source / "unused"),
         "--created-at", TS],
    ]
    for command in commands:
        assert main(command) == 2
        assert "read-only" in capsys.readouterr().err
        assert _tree_snapshot(source) == before
    for mutation in (
        store.ensure, store.recover,
        lambda: Ledger(source).append("owner", {"cost_usd": 0}),
        lambda: EventLog(source).append(make_event(scope="search", event_type="run.started",
                                                   payload={"genome_id": "g", "task_id": "t"})),
        lambda: atomic_write_text(source / "runs" / "new.json", "{}"),
    ):
        with pytest.raises(PermissionError):
            mutation()
        assert _tree_snapshot(source) == before


def test_generic_cli_real_socket_transactions(tmp_path, monkeypatch, capsys):
    binary = _binary()
    state = tmp_path / "kernel"
    _run(binary, "init", "--state", state)
    monkeypatch.setenv("MOLT_KERNEL_BACKEND", "kernel")
    payload = tmp_path / "synthetic.json"
    payload.write_bytes(b'{"provenance":"synthetic","value":1}')
    with _server(binary, state) as socket:
        monkeypatch.setenv("MOLT_KERNEL_SOCKET", socket)
        assert main(["current"]) == 0
        assert json.loads(capsys.readouterr().out) is None
        assert main(["object", "put", str(payload)]) == 0
        digest = json.loads(capsys.readouterr().out)["hash"]
        roots = tmp_path / "roots.json"
        _write(roots, {"workspace": {"synthetic.json": {
            "type": "file", "mode": 0o600, "size": payload.stat().st_size, "sha256": digest,
        }}})
        assert main(["manifest", "create", str(roots)]) == 0
        manifest = json.loads(capsys.readouterr().out)["hash"]
        assert main(["manifest", "get", manifest]) == 0
        assert json.loads(capsys.readouterr().out)["roots"] == json.loads(roots.read_text())
        command = ["commit", "--manifest", manifest, "--request-id", "cli-synthetic", "--at", TS]
        assert main(command) == 0
        first = json.loads(capsys.readouterr().out)
        assert first["generation"] == 1
        assert main(command) == 0
        assert json.loads(capsys.readouterr().out) == first
        assert main(command[:-1] + ["2026-11-19T00:00:01Z"]) == 1
        error = capsys.readouterr().err
        assert "request_conflict" in error and "outcome unknown" not in error
        assert main(["inspect"]) == 0
        assert json.loads(capsys.readouterr().out)["current"] == first
        assert main(["request-status", "cli-synthetic"]) == 0
        assert json.loads(capsys.readouterr().out) == {"status": "committed", "result": first}
        assert main(["request-status", "never-submitted"]) == 0
        assert json.loads(capsys.readouterr().out) == {
            "status": "not_found", "retry_with_same_request_id": True,
        }
        output = tmp_path / "download.json"
        assert main(["object", "get", digest, "--out", str(output)]) == 0
        assert output.read_bytes() == payload.read_bytes()
        capsys.readouterr()
        output.write_bytes(b"preserve")
        assert main(["object", "get", digest, "--out", str(output)]) == 1
        assert output.read_bytes() == b"preserve"
        capsys.readouterr()
        assert main(["audit"]) == 0
        assert json.loads(capsys.readouterr().out)["ok"] is True
    with _server(binary, state) as socket:
        assert main(["--socket", socket, "request-status", "cli-synthetic"]) == 0
        assert json.loads(capsys.readouterr().out)["result"] == first
        assert main(["--socket", socket, *command]) == 0
        assert json.loads(capsys.readouterr().out) == first


def test_cli_v1_audit_and_explicit_migration_preserve_source(tmp_path, monkeypatch, capsys):
    binary = _binary()
    source = tmp_path / "legacy"
    scenario = build_compatibility_fixture(source)
    scenario_file = tmp_path / "scenario.json"
    _write(scenario_file, scenario.to_dict())
    before = _snapshot(source)
    monkeypatch.setenv("MOLT_KERNEL_BACKEND", "v1")
    assert main(["audit", "--state", str(source), "--scenario", str(scenario_file)]) == 0
    assert json.loads(capsys.readouterr().out)["runs"] == 4
    assert _snapshot(source) == before
    kernel = tmp_path / "kernel"
    _run(binary, "init", "--state", kernel)
    with _server(binary, kernel) as socket:
        monkeypatch.setenv("MOLT_KERNEL_SOCKET", socket)
        assert main(["migrate", "--state", str(source)]) == 0
        assert "dry-run" in capsys.readouterr().out
        assert _snapshot(source) == before
        with KernelClient(socket) as client:
            assert client.current() is None
        assert main(["migrate", "--state", str(source), "--apply"]) == 2
        capsys.readouterr()
        command = ["migrate", "--state", str(source), "--apply", "--migration-id", "cli-migration"]
        assert main(command) == 0
        assert "generation=1" in capsys.readouterr().out
        assert main(command) == 0
        assert "unchanged=true" in capsys.readouterr().out
        assert _snapshot(source) == before
        _write(source / "note.json", {"provenance": "synthetic", "changed": True})
        changed = _snapshot(source)
        assert main(command) == 1
        assert "different source snapshot" in capsys.readouterr().err
        assert _snapshot(source) == changed
        with KernelClient(socket) as client:
            assert client.current()["generation"] == 1
    run_file = source / "runs" / "synthetic-baseline-search.json"
    record = json.loads(run_file.read_text())
    record["usage"]["cost_usd"] = 0.8
    _write(run_file, record)
    tampered = _snapshot(source)
    assert main(["audit", "--state", str(source), "--scenario", str(scenario_file)]) == 1
    assert _snapshot(source) == tampered


def test_cli_lost_commit_response_requires_original_id(tmp_path, monkeypatch, capsys):
    from test_kernel_transactions import _manifest

    binary = _binary()
    state = tmp_path / "kernel"
    _run(binary, "init", "--state", state)
    monkeypatch.setenv("MOLT_KERNEL_BACKEND", "kernel")
    original_call = KernelClient.call
    with _server(binary, state) as socket:
        monkeypatch.setenv("MOLT_KERNEL_SOCKET", socket)
        with KernelClient(socket) as client:
            manifest, _ = _manifest(client, b'{"provenance":"synthetic"}')

        def lose_response(self, method, params=None):
            result = original_call(self, method, params)
            if method == "generation.commit":
                raise ConnectionError("synthetic response loss after durable commit")
            return result

        command = ["commit", "--manifest", manifest, "--request-id", "lost-cli", "--at", TS]
        monkeypatch.setattr(KernelClient, "call", lose_response)
        assert main(command) == 1
        error = capsys.readouterr().err
        assert "outcome unknown" in error and "request-status 'lost-cli'" in error
        monkeypatch.setattr(KernelClient, "call", original_call)
    with _server(binary, state) as socket:
        monkeypatch.setenv("MOLT_KERNEL_SOCKET", socket)
        assert main(["request-status", "lost-cli"]) == 0
        status = json.loads(capsys.readouterr().out)
        assert status["status"] == "committed" and status["result"]["generation"] == 1
        assert main(command) == 0
        assert json.loads(capsys.readouterr().out) == status["result"]


def test_cli_loads_without_competition_modules(tmp_path):
    import subprocess
    import sys

    import molt

    # An isolated interpreter makes cached imports unable to mask dependencies.
    code = '''
import importlib.abc
import sys
sys.path.insert(0, sys.argv[1])
class RejectCompetition(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"molt.demo", "molt.engine", "shells"} or fullname.startswith(("molt.adapters", "shells.")):
            raise AssertionError("retired dependency: " + fullname)
sys.meta_path.insert(0, RejectCompetition())
from molt.cli import build_parser, main
commands = build_parser()._subparsers._group_actions[0].choices
for name in ("demo", "search", "propose", "calibrate", "genome", "scenario", "gate", "adopt", "revert"):
    assert name not in commands, name
assert main([]) == 0
'''
    result = subprocess.run([sys.executable, "-I", "-c", code, str(Path(molt.__file__).resolve().parent.parent)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
