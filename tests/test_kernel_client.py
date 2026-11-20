"""Real Unix-socket coverage for the dependency-free Python kernel client."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from molt.cli import main as cli_main
from molt.core import write_policy
from molt.kernel_client import KernelClient, KernelError
from molt.kernel_mirror import mirror_state, preview_state, state_roots

ROOT = Path(os.environ.get("MOLT_TEST_DATA_ROOT", Path(__file__).resolve().parents[1])).resolve()
TS = "2026-11-16T00:00:00Z"


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _wait_for_server(executable: Path, state: Path, socket_path: Path) -> subprocess.Popen:
    server = subprocess.Popen(
        [executable, "serve", "--state", state],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if server.poll() is not None:
            stderr = server.stderr.read().decode() if server.stderr else ""
            pytest.fail(f"kernel server exited during startup: {stderr}")
        try:
            with KernelClient(str(socket_path), timeout=0.2):
                return server
        except (OSError, ConnectionError):
            time.sleep(0.01)
    server.terminate()
    server.wait(timeout=5)
    pytest.fail("kernel server startup timed out")


@pytest.mark.integration
def test_cli_backup_restores_synthetic_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    from test_kernel_transactions import _binary, _commit, _manifest, _run, _server

    executable = _binary()
    monkeypatch.setenv("MOLT_KERNEL_BACKEND", "kernel")
    monkeypatch.setenv("MOLT_KERNEL_BIN", str(executable))
    source = tmp_path / "source"
    _run(executable, "init", "--state", source)
    with _server(executable, source) as socket:
        monkeypatch.setenv("MOLT_KERNEL_SOCKET", socket)
        with KernelClient(socket) as client:
            manifest, digest = _manifest(client, b'{"provenance":"synthetic"}')
            expected = _commit(client, "cli-backup", manifest)
            identity = client.inspect()["state_id"]
        assert cli_main(["audit"]) == 0
        capsys.readouterr()
    checkpoint = tmp_path / "checkpoint.json"
    assert cli_main(["checkpoint", "export", "--state", str(source), "--out", str(checkpoint),
                     "--created-at", TS]) == 0
    capsys.readouterr()
    before_checkpoint = checkpoint.read_bytes()
    assert cli_main(["checkpoint", "export", "--state", str(source), "--out", str(checkpoint),
                     "--created-at", TS]) == 1
    assert checkpoint.read_bytes() == before_checkpoint
    capsys.readouterr()
    backup = tmp_path / "backup"
    shutil.copytree(source, backup, ignore=shutil.ignore_patterns("kernel.sock"))
    restored = tmp_path / "restored"
    shutil.copytree(backup, restored)
    # Original path is unavailable; restore must be independent of it.
    monkeypatch.setenv("MOLT_KERNEL_SOCKET", str(tmp_path / "unavailable.sock"))
    assert cli_main(["recover", "--state", str(restored)]) == 0
    assert cli_main(["audit", "--state", str(restored), "--checkpoint", str(checkpoint)]) == 0
    capsys.readouterr()
    with _server(executable, restored) as socket:
        with KernelClient(socket) as client:
            assert client.current() == expected
            assert client.inspect()["state_id"] == identity
            assert client.get_object(digest) == b'{"provenance":"synthetic"}'
            assert _commit(client, "cli-backup", manifest) == expected
        monkeypatch.setenv("MOLT_KERNEL_SOCKET", socket)
        assert cli_main(["audit"]) == 0


@pytest.mark.integration
def test_kernel_server_lock_and_stale_socket_recovery(tmp_path: Path) -> None:
    binary = os.environ.get("MOLT_KERNEL_BIN")
    if not binary:
        pytest.skip("set MOLT_KERNEL_BIN to a built molt-kernel executable")
    executable = Path(binary).resolve()
    state = tmp_path / "service-state"
    state.mkdir()
    subprocess.run([executable, "init", "--state", state], check=True, capture_output=True)
    socket_path = state / "kernel.sock"
    server = _wait_for_server(executable, state, socket_path)
    try:
        ready = subprocess.run(
            [executable, "ping", "--socket", socket_path], capture_output=True, text=True
        )
        assert ready.returncode == 0
        assert json.loads(ready.stdout)["ok"] is True
        duplicate = subprocess.run(
            [executable, "serve", "--state", state], capture_output=True, text=True
        )
        assert duplicate.returncode == 1
        assert "already running" in duplicate.stderr
        assert socket_path.exists()
    finally:
        server.terminate()
        server.wait(timeout=5)
    unavailable = subprocess.run(
        [executable, "ping", "--socket", socket_path], capture_output=True, text=True
    )
    assert unavailable.returncode == 1

    restarted = _wait_for_server(executable, state, socket_path)
    try:
        assert restarted.poll() is None
        with KernelClient(str(socket_path), timeout=1) as client:
            assert client.current() is None
    finally:
        restarted.terminate()
        restarted.wait(timeout=5)

    protected_state = tmp_path / "protected-state"
    protected_state.mkdir()
    subprocess.run(
        [executable, "init", "--state", protected_state], check=True, capture_output=True
    )
    protected_socket = protected_state / "kernel.sock"
    protected_socket.write_text("keep", encoding="utf-8")
    occupied = subprocess.run(
        [executable, "serve", "--state", protected_state], capture_output=True, text=True
    )
    assert occupied.returncode == 1
    assert "not a socket" in occupied.stderr
    assert protected_socket.read_text(encoding="utf-8") == "keep"

    symlink_state = tmp_path / "symlink-lock-state"
    symlink_state.mkdir()
    subprocess.run(
        [executable, "init", "--state", symlink_state], check=True, capture_output=True
    )
    outside_lock = tmp_path / "outside-lock"
    outside_lock.write_text("keep", encoding="utf-8")
    (symlink_state / "SERVER_LOCK").symlink_to(outside_lock)
    unsafe = subprocess.run(
        [executable, "serve", "--state", symlink_state], capture_output=True, text=True
    )
    assert unsafe.returncode == 1
    assert outside_lock.read_text(encoding="utf-8") == "keep"


@pytest.mark.integration
def test_python_client_roundtrips_real_kernel_server(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(write_policy, "DEFAULT_BACKEND", "v1")
    binary = os.environ.get("MOLT_KERNEL_BIN")
    if not binary:
        pytest.skip("set MOLT_KERNEL_BIN to a built molt-kernel executable")
    executable = Path(binary).resolve()
    if not hasattr(__import__("socket"), "AF_UNIX"):
        pytest.skip("Unix-domain sockets are unavailable")
    state = tmp_path / "state"
    state.mkdir()
    subprocess.run([executable, "init", "--state", state], check=True, capture_output=True)
    server = subprocess.Popen(
        [executable, "serve", "--state", state],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    socket_path = state / "kernel.sock"
    deadline = time.monotonic() + 5
    while not socket_path.exists():
        if server.poll() is not None:
            stderr = server.stderr.read().decode() if server.stderr else ""
            pytest.fail(f"kernel server exited during startup: {stderr}")
        if time.monotonic() >= deadline:
            pytest.fail("kernel server startup timed out")
        time.sleep(0.01)

    try:
        payload = b"python-client-e2e"
        payload_hash = _digest(payload)
        manifest_body = {"roots": {"workspace": {"file.txt": {
            "type": "file", "mode": 420, "size": len(payload), "sha256": payload_hash,
        }}}, "tree_hash": None}
        manifest_body["tree_hash"] = _digest(_canonical(manifest_body["roots"]))
        manifest = _canonical(manifest_body)
        manifest_hash = _digest(manifest)

        with KernelClient(str(socket_path), timeout=5) as client:
            large_object = bytes(range(251)) * 5000
            large_hash = _digest(large_object)
            assert client.upload_object(large_object) == large_hash
            assert not list(state.glob(".upload-*"))
            assert client.call("object.put", {"data": list(payload)})["hash"] == payload_hash
            assert client.get_object(payload_hash) == payload
            assert client.call("object.put", {"data": list(manifest)})["hash"] == manifest_hash
            roots = manifest_body["roots"]
            assert client.create_manifest(roots) == manifest_hash
            assert client.get_manifest(manifest_hash)["roots"] == roots
            result = client.call(
                "generation.commit",
                {"request_id": "python-e2e", "manifest_hash": manifest_hash, "ts": TS},
            )
            assert result["generation"] == 1
            assert client.call("current") == result
            assert client.request_status("python-e2e")["result"] == result
            assert client.call(
                "generation.commit",
                {"request_id": "python-e2e", "manifest_hash": manifest_hash, "ts": TS},
            ) == result
            with pytest.raises(KernelError, match="request_conflict"):
                client.call(
                    "generation.commit",
                    {
                        "request_id": "python-e2e",
                        "manifest_hash": manifest_hash,
                        "ts": "2026-11-16T00:00:01Z",
                    },
                )

        legacy = tmp_path / "legacy-state"
        (legacy / "runs").mkdir(parents=True)
        (legacy / "runs" / "run-1.json").write_text('{"run_id":"run-1"}\n')
        (legacy / "work").mkdir()
        (legacy / "work" / "scratch").write_text("excluded")
        first_mirror = mirror_state(legacy, str(socket_path))
        second_mirror = mirror_state(legacy, str(socket_path))
        assert first_mirror["generation"] == 2
        assert first_mirror["files"] == 1
        assert first_mirror["unchanged"] is False
        assert second_mirror["generation"] == 2
        assert second_mirror["unchanged"] is True
        with KernelClient(str(socket_path)) as client:
            before_migration = client.current()
        preview = preview_state(legacy, str(socket_path))
        assert preview["state_id"]
        assert preview["source_files"] == 1
        assert preview["source_bytes"] == len(b'{"run_id":"run-1"}\n')
        assert preview["differences"] == []
        with KernelClient(str(socket_path)) as client:
            assert client.current() == before_migration

        source_before_migration = state_roots(legacy)
        applied = mirror_state(legacy, str(socket_path), migration_id="initial-import")
        retried = mirror_state(legacy, str(socket_path), migration_id="initial-import")
        assert applied["generation"] == 3
        assert retried["generation"] == 3
        assert retried["unchanged"] is True
        assert state_roots(legacy) == source_before_migration
        source_before_change = state_roots(legacy)
        (legacy / "runs" / "run-1.json").write_text('{"run_id":"changed"}\n')
        changed_preview = preview_state(legacy, str(socket_path))
        assert changed_preview["differences"] == [
            {
                "path": "runs/run-1.json",
                "source_sha256": _digest(b'{"run_id":"changed"}\n'),
                "current_sha256": _digest(b'{"run_id":"run-1"}\n'),
                "source_size": len(b'{"run_id":"changed"}\n'),
                "current_size": len(b'{"run_id":"run-1"}\n'),
                "change": "changed",
            }
        ]
        with pytest.raises(ValueError, match="different source snapshot"):
            mirror_state(legacy, str(socket_path), migration_id="initial-import")
        assert mirror_state(legacy, str(socket_path), migration_id="changed-import")["generation"] == 4
        assert state_roots(legacy) != source_before_change

    finally:
        server.kill()
        server.wait(timeout=5)
