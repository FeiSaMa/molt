"""Synthetic opaque-object transactions over real UDS; no competition data required.

This file must also run when copied outside the checkout with an installed wheel.
It tests storage evidence, not the truth of any external business action.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from molt.kernel_client import KernelClient, KernelError

TS = "2026-11-19T00:00:00Z"


def test_reject_legacy_tree_without_writes(tmp_path, monkeypatch):
    binary = _binary()
    source = tmp_path / "legacy"
    (source / "runs").mkdir(parents=True)
    (source / "runs" / "record.json").write_bytes(b'{"provenance":"synthetic"}\n')
    (source / "records").symlink_to("runs")

    def snapshot():
        return {
            str(path.relative_to(source)): (
                path.lstat().st_mode,
                os.readlink(path) if path.is_symlink() else
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
            )
            for path in source.rglob("*")
        }

    def reject(executable, command, expected, diagnostic):
        done = subprocess.run(
            [*executable, *command], cwd=tmp_path, capture_output=True, text=True, timeout=10,
        )
        assert done.returncode == expected, done.stderr
        assert diagnostic in done.stderr
        assert snapshot() == before

    before = snapshot()
    cli = [sys.executable, "-m", "molt"]
    monkeypatch.setenv("MOLT_KERNEL_BIN", str(binary))
    monkeypatch.setenv("MOLT_KERNEL_BACKEND", "v1")
    reject(cli, ["current"], 2, "MOLT_KERNEL_BACKEND=v1 is retired")
    reject(cli, ["recover", "--state", source], 2, "MOLT_KERNEL_BACKEND=v1 is retired")
    monkeypatch.delenv("MOLT_KERNEL_BACKEND")
    reject(cli, ["migrate", "--state", source], 2, "invalid choice")
    reject(cli, ["audit", "--scenario", "legacy", "--state", source], 2, "unrecognized arguments")
    for action in ("audit", "recover"):
        reject(cli, [action, "--state", source], 1, "not an initialized kernel state")
    reject([binary], ["init", "--state", source], 1, "state directory is not empty")
    reject([binary], ["serve", "--state", source], 1, "not an initialized kernel state")


def _binary() -> Path:
    value = os.environ.get("MOLT_KERNEL_BIN")
    if not value:
        pytest.skip("set MOLT_KERNEL_BIN to a freshly built molt-kernel")
    binary = Path(value).resolve()
    assert binary.is_file(), binary
    return binary


def _run(binary: Path, *args: str | Path) -> subprocess.CompletedProcess:
    return subprocess.run([binary, *args], check=True, capture_output=True, timeout=15)


@contextmanager
def _server(binary: Path, state: Path):
    process = subprocess.Popen(
        [binary, "serve", "--state", state],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    socket = str(state / "kernel.sock")
    try:
        deadline = time.monotonic() + 5
        while True:
            if process.poll() is not None:
                pytest.fail(f"kernel exited: {process.stderr.read().decode()}")
            try:
                with KernelClient(socket, timeout=0.2):
                    break
            except (OSError, ConnectionError):
                if time.monotonic() >= deadline:
                    pytest.fail("kernel startup timed out")
                time.sleep(0.01)
        yield socket
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stderr.close()


def _manifest(client: KernelClient, payload: bytes) -> tuple[str, str]:
    digest = client.upload_object(payload, chunk_size=1024)
    assert digest == "sha256:" + hashlib.sha256(payload).hexdigest()
    roots = {"workspace": {"synthetic.json": {
        "type": "file", "mode": 0o600, "size": len(payload), "sha256": digest,
    }}}
    manifest = client.create_manifest(roots)
    assert client.get_manifest(manifest)["roots"] == roots
    assert client.get_object(digest) == payload
    return manifest, digest


def _commit(client: KernelClient, request_id: str, manifest: str) -> dict:
    return client.call("generation.commit", {
        "request_id": request_id, "manifest_hash": manifest, "ts": TS,
    })


@pytest.mark.integration
def test_synthetic_transactions_and_restart(tmp_path: Path) -> None:
    binary = _binary()
    state = tmp_path / "state"
    _run(binary, "init", "--state", state)
    payload = b'{"provenance":"synthetic","value":1}'
    with _server(binary, state) as socket:
        with KernelClient(socket, timeout=2) as client:
            assert client.current() is None
            assert client.request_status("absent") == {
                "status": "not_found", "retry_with_same_request_id": True,
            }
            # Exercise streaming above a single chunk; bounded at 1.255 MB.
            large = bytes(range(251)) * 5000
            large_hash = client.upload_object(large)
            assert client.get_object(large_hash) == large
            assert not list(state.glob(".upload-*"))
            manifest, digest = _manifest(client, payload)
            first = _commit(client, "synthetic-1", manifest)
            assert first["generation"] == 1
            assert client.current() == first
            assert client.request_status("synthetic-1") == {"status": "committed", "result": first}
            assert _commit(client, "synthetic-1", manifest) == first
            second_manifest, _ = _manifest(client, b'{"provenance":"synthetic","value":2}')
            with pytest.raises(KernelError) as error:
                _commit(client, "synthetic-1", second_manifest)
            assert error.value.code == "request_conflict"
            assert client.current() == first
            second = _commit(client, "synthetic-2", second_manifest)
            assert second["generation"] == 2
    with _server(binary, state) as socket:
        with KernelClient(socket, timeout=2) as client:
            assert client.current() == second
            assert client.request_status("synthetic-1")["result"] == first
            assert _commit(client, "synthetic-2", second_manifest) == second
            assert client.get_object(digest) == payload
    _run(binary, "audit", "--state", state)


@pytest.mark.integration
def test_lost_response_queries_same_request_after_restart(tmp_path: Path) -> None:
    binary = _binary()
    state = tmp_path / "state"
    _run(binary, "init", "--state", state)
    with _server(binary, state) as socket:
        with KernelClient(socket, timeout=2) as client:
            manifest, _ = _manifest(client, b'{"provenance":"synthetic","action":"opaque"}')
            # Send a valid framed commit, then close without consuming its response.
            # This deliberately leaves the caller uncertain; it must query this ID.
            frame = json.dumps({
                "jsonrpc": "2.0", "id": 100, "method": "generation.commit",
                "params": {"request_id": "lost-response", "manifest_hash": manifest, "ts": TS},
            }).encode()
            client.sock.sendall(struct.pack(">I", len(frame)) + frame)
        # Establish that the server processed the request before stopping it. The
        # consumer's recovery below still starts from an unobserved response.
        deadline = time.monotonic() + 5
        with KernelClient(socket, timeout=2) as observer:
            while observer.request_status("lost-response")["status"] != "committed":
                assert time.monotonic() < deadline, "request never committed"
                time.sleep(0.01)
    with _server(binary, state) as socket:
        with KernelClient(socket, timeout=2) as client:
            status = client.request_status("lost-response")
            assert status["status"] == "committed"
            assert status["result"]["generation"] == 1
            assert _commit(client, "lost-response", manifest) == status["result"]
            assert client.current()["generation"] == 1
    _run(binary, "audit", "--state", state)


@pytest.mark.integration
def test_synthetic_backup_checkpoint_and_restore(tmp_path: Path) -> None:
    binary = _binary()
    source = tmp_path / "source"
    _run(binary, "init", "--state", source)
    payload = b'{"provenance":"synthetic","note":"backup fixture"}'
    with _server(binary, source) as socket:
        with KernelClient(socket, timeout=2) as client:
            manifest, digest = _manifest(client, payload)
            expected = _commit(client, "backup-synthetic", manifest)
            identity = client.inspect()["state_id"]
    checkpoint = tmp_path / "checkpoint.json"
    _run(binary, "checkpoint", "export", "--state", source, "--out", checkpoint,
         "--created-at", TS)
    restored = tmp_path / "restored"
    shutil.copytree(source, restored, ignore=shutil.ignore_patterns("kernel.sock"))
    # Only one synthetic test directory is renamed, never user files or asset batches.
    source.rename(tmp_path / "offline-source")
    _run(binary, "audit", "--state", restored, "--checkpoint", checkpoint)
    with _server(binary, restored) as socket:
        with KernelClient(socket, timeout=2) as client:
            assert client.current() == expected
            assert client.inspect()["state_id"] == identity
            assert client.get_object(digest) == payload
            assert client.get_manifest(manifest)["roots"]["workspace"]["synthetic.json"]["sha256"] == digest
            assert _commit(client, "backup-synthetic", manifest) == expected
    _run(binary, "audit", "--state", restored, "--checkpoint", checkpoint)


@pytest.mark.integration
def test_synthetic_single_writer_and_socket_guards(tmp_path: Path) -> None:
    binary = _binary()
    state = tmp_path / "state"
    _run(binary, "init", "--state", state)
    with _server(binary, state) as socket:
        assert Path(socket).stat().st_mode & 0o777 == 0o600
        duplicate = subprocess.run(
            [binary, "serve", "--state", state], capture_output=True, timeout=5,
        )
        assert duplicate.returncode == 1
        assert b"already running" in duplicate.stderr
        assert json.loads(_run(binary, "ping", "--socket", socket).stdout)["ok"] is True
    with _server(binary, state) as socket:
        with KernelClient(socket, timeout=2) as client:
            assert client.current() is None
    protected = tmp_path / "protected"
    _run(binary, "init", "--state", protected)
    sentinel = protected / "kernel.sock"
    sentinel.write_text("synthetic sentinel")
    failed = subprocess.run(
        [binary, "serve", "--state", protected], capture_output=True, timeout=5,
    )
    assert failed.returncode == 1
    assert b"not a socket" in failed.stderr
    assert sentinel.read_text() == "synthetic sentinel"
