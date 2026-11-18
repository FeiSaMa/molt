"""Real Unix-socket coverage for the dependency-free Python kernel client."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from molt.cli import main as cli_main
from molt.core.audit import audit_state
from molt.core.store import StateStore
from molt.demo import load_demo_scenario
from molt.kernel_client import KernelClient, KernelError
from molt.kernel_mirror import materialize_current, mirror_state, preview_state, state_roots

ROOT = Path(__file__).resolve().parents[1]
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
def test_python_client_roundtrips_real_kernel_server(tmp_path: Path, monkeypatch, capsys) -> None:
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

        cli_state = tmp_path / "cli-state"
        monkeypatch.setenv("MOLT_KERNEL_SOCKET", str(socket_path))
        assert cli_main(
            [
                "search",
                "--runtime",
                "fixture",
                "--task",
                "smoke-add",
                "--state",
                str(cli_state),
            ]
        ) == 0
        assert (cli_state / "runs").is_dir()
        assert mirror_state(cli_state, str(socket_path))["unchanged"] is True
        fixture_projection = tmp_path / "fixture-projection"
        materialize_current(fixture_projection, str(socket_path))
        fixture_projected_state = fixture_projection / "state"
        assert state_roots(cli_state)[0] == state_roots(fixture_projected_state)[0]

        replay_state = tmp_path / "replay-state"
        assert cli_main(
            ["demo", "--mode", "replay", "--state", str(replay_state), "--max-wall", "180"]
        ) == 0
        projected = tmp_path / "projected-state"
        materialized = materialize_current(projected, str(socket_path))
        assert materialized["files"] > 0
        projected_state = projected / "state"
        assert state_roots(replay_state)[0] == state_roots(projected_state)[0]
        scenario = load_demo_scenario(ROOT)
        assert audit_state(StateStore(replay_state), scenario) == audit_state(
            StateStore(projected_state), scenario
        )

        primary_path = tmp_path / "kernel-primary-state"
        monkeypatch.setenv("MOLT_KERNEL_BACKEND", "kernel")
        assert cli_main(
            [
                "search",
                "--runtime",
                "fixture",
                "--task",
                "smoke-add",
                "--state",
                str(primary_path),
            ]
        ) == 0
        assert cli_main(
            [
                "propose",
                "--offline",
                "--runtime",
                "fixture",
                "--state",
                str(primary_path),
            ]
        ) == 0
        assert cli_main(
            [
                "adopt",
                "--state",
                str(primary_path),
                "--genome",
                str(ROOT / "shells/code-repair/genomes/v0.json"),
                "--at",
                TS,
            ]
        ) == 0
        adopted = capsys.readouterr().out
        archive_id = next(
            token.split("=", 1)[1]
            for token in adopted.split()
            if token.startswith("archive=")
        )
        assert cli_main(
            ["revert", "--state", str(primary_path), "--to", archive_id, "--at", TS]
        ) == 0
        assert not primary_path.exists()
        primary_projection = tmp_path / "primary-projection"
        materialize_current(primary_projection, str(socket_path))
        assert (primary_projection / "state" / "runs").is_dir()
        assert (primary_projection / "state" / "proposals").is_dir()
        assert (primary_projection / "state" / "archive").is_dir()

        calibration_path = tmp_path / "kernel-primary-calibration"
        assert cli_main(
            [
                "calibrate",
                "--scenario",
                "demo",
                "--min-pass",
                "3",
                "--state",
                str(calibration_path),
            ]
        ) == 0
        assert not calibration_path.exists()
        calibration_projection = tmp_path / "calibration-projection"
        materialize_current(calibration_projection, str(socket_path))
        assert (calibration_projection / "state" / "runs").is_dir()

        recovery_path = tmp_path / "kernel-primary-recovery"
        assert cli_main(
            ["doctor", "--recover", "--state", str(recovery_path)]
        ) == 0
        assert not recovery_path.exists()

        isolated_state = tmp_path / "isolated-kernel-state"
        isolated_state.mkdir()
        subprocess.run(
            [executable, "init", "--state", isolated_state], check=True, capture_output=True
        )
        isolated_server = subprocess.Popen(
            [executable, "serve", "--state", isolated_state],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        isolated_socket = isolated_state / "kernel.sock"
        try:
            deadline = time.monotonic() + 5
            while not isolated_socket.exists():
                if isolated_server.poll() is not None:
                    stderr = isolated_server.stderr.read().decode() if isolated_server.stderr else ""
                    pytest.fail(f"isolated kernel server exited during startup: {stderr}")
                if time.monotonic() >= deadline:
                    pytest.fail("isolated kernel server startup timed out")
                time.sleep(0.01)
            monkeypatch.setenv("MOLT_KERNEL_SOCKET", str(isolated_socket))
            primary_demo_path = tmp_path / "kernel-primary-demo"
            assert cli_main(
                ["demo", "--mode", "replay", "--state", str(primary_demo_path)]
            ) == 0
            assert cli_main(
                ["audit", "--scenario", "demo", "--state", str(primary_demo_path)]
            ) == 0
            primary_demo_projection = tmp_path / "kernel-primary-demo-projection"
            materialize_current(primary_demo_projection, str(isolated_socket))
            audit_state(
                StateStore(primary_demo_projection / "state"),
                load_demo_scenario(ROOT),
            )
        finally:
            isolated_server.kill()
            isolated_server.wait(timeout=5)

        existing_state = tmp_path / "existing-v1-state"
        existing_state.mkdir()
        sentinel = existing_state / "keep.txt"
        sentinel.write_text("v1")
        assert cli_main(
            [
                "search",
                "--runtime",
                "fixture",
                "--task",
                "smoke-add",
                "--state",
                str(existing_state),
            ]
        ) == 2
        assert sentinel.read_text() == "v1"
    finally:
        server.kill()
        server.wait(timeout=5)
