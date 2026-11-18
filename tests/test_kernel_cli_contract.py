from __future__ import annotations

import os
import stat
from pathlib import Path

from molt.cli import main
from molt.core import write_policy
from molt.core.events import EventLog, make_event
from molt.core.fsutil import atomic_write_text
from molt.core.ledger import Ledger
from molt.core.store import StateStore


def _tree_snapshot(root: Path) -> tuple[tuple[str, int, str, bytes | str], ...]:
    entries = []
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        mode = stat.S_IMODE(info.st_mode)
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            value: bytes | str = os.readlink(path)
            kind = "link"
        elif path.is_file():
            value = path.read_bytes()
            kind = "file"
        else:
            value = ""
            kind = "dir"
        entries.append((rel, mode, kind, value))
    return tuple(entries)


def test_default_backend_remains_v1_without_kernel_service(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MOLT_KERNEL_BACKEND", raising=False)
    monkeypatch.delenv("MOLT_KERNEL_SOCKET", raising=False)
    state = tmp_path / "v1-state"

    assert main(
        [
            "search",
            "--runtime",
            "fixture",
            "--task",
            "smoke-add",
            "--state",
            str(state),
        ]
    ) == 0
    assert (state / "runs").is_dir()


def test_kernel_backend_without_service_fails_without_v1_fallback(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MOLT_KERNEL_BACKEND", "kernel")
    monkeypatch.delenv("MOLT_KERNEL_SOCKET", raising=False)
    state = tmp_path / "must-stay-absent"

    assert main(
        [
            "search",
            "--runtime",
            "fixture",
            "--task",
            "smoke-add",
            "--state",
            str(state),
        ]
    ) == 2
    assert "requires MOLT_KERNEL_SOCKET" in capsys.readouterr().err
    assert not state.exists()


def test_kernel_backend_rejects_unvalidated_platform_before_state_access(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MOLT_KERNEL_BACKEND", "kernel")
    monkeypatch.setenv("MOLT_KERNEL_SOCKET", str(tmp_path / "socket"))
    monkeypatch.setattr("molt.cli.platform.machine", lambda: "aarch64")
    state = tmp_path / "must-stay-absent"

    assert main(
        ["search", "--runtime", "fixture", "--task", "smoke-add", "--state", str(state)]
    ) == 2
    assert "Linux x86_64" in capsys.readouterr().err
    assert not state.exists()


def test_r_release_rejects_v1_writes_without_changing_source_tree(tmp_path, monkeypatch, capsys):
    source = tmp_path / "legacy-state"
    store = StateStore(source)
    store.ensure()
    (source / "runs" / "keep.json").write_bytes(b"preserve\n")
    (source / "runs" / "keep.json").chmod(0o640)
    (source / "operator-link").symlink_to("runs/keep.json")
    before = _tree_snapshot(source)
    monkeypatch.setattr(write_policy, "DEFAULT_BACKEND", "kernel")
    monkeypatch.setenv("MOLT_KERNEL_BACKEND", "v1")
    monkeypatch.delenv("MOLT_KERNEL_SOCKET", raising=False)

    commands = [
        ["search", "--runtime", "fixture", "--task", "smoke-add", "--state", str(source)],
        ["adopt", "--state", str(source), "--genome", "shells/code-repair/genomes/v0.json"],
        ["revert", "--state", str(source), "--to", "missing"],
        ["doctor", "--recover", "--state", str(source)],
        ["demo", "--mode", "replay", "--state", str(source)],
        ["calibrate", "--scenario", "demo", "--state", str(source)],
    ]
    for command in commands:
        assert main(command) == 2
        capsys.readouterr()
        assert _tree_snapshot(source) == before

    for mutation in (
        store.ensure,
        store.recover,
        lambda: Ledger(source).append("owner", {"cost_usd": 0}),
        lambda: EventLog(source).append(
            make_event(
                scope="search",
                event_type="run.started",
                payload={"genome_id": "g", "task_id": "t"},
            )
        ),
        lambda: atomic_write_text(source / "runs" / "new.json", "{}"),
    ):
        try:
            mutation()
        except PermissionError:
            pass
        else:
            raise AssertionError("v1 mutation was not rejected")
        assert _tree_snapshot(source) == before


def test_r_release_read_only_audit_and_migration_preview_keep_source_usable(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "legacy-state"
    StateStore(source).ensure()
    (source / "runs" / "existing.json").write_text("{}", encoding="utf-8")
    before = _tree_snapshot(source)
    monkeypatch.setattr(write_policy, "DEFAULT_BACKEND", "kernel")
    monkeypatch.setenv("MOLT_KERNEL_BACKEND", "v1")

    assert main(["audit", "--scenario", "demo", "--state", str(source)]) == 1
    capsys.readouterr()
    assert _tree_snapshot(source) == before
    assert main(["migrate", "--state", str(source)]) == 2
    capsys.readouterr()
    assert _tree_snapshot(source) == before
