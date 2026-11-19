"""P1：``svc-config`` 的 adopt 闭环——artifact roots 含单文件 root。

``molt/shell_bundle.py`` 是 shell 装配的一部分，必须以文件 root 进快照；
``copy_generation`` 用 ``shutil.copy2`` 保留 mode，symlink 仍然拒绝。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from molt.core.errors import HashMismatch
from molt.core.hash import sha256_file
from molt.core.snapshot import build_snapshot, copy_generation, verify_generation
from molt.core.store import StateStore
from molt.core.types import Snapshot

ROOT = Path(__file__).resolve().parents[1]
SVC_GENOME = "shells/svc-config/genomes/v0.json"
SVC_MANIFEST = "shells/svc-config/manifest.json"
DEMO_GENOME = "shells/code-repair/genomes/v0.json"
DEMO_MANIFEST = "shells/code-repair/manifest.json"


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(ROOT / "tests/run_cli.py"), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


def test_svc_config_adopt_supports_file_root(tmp_path: Path) -> None:
    state = tmp_path / "state"
    completed = _run_cli(
        "adopt",
        "--state",
        str(state),
        "--genome",
        SVC_GENOME,
        "--manifest",
        SVC_MANIFEST,
    )
    assert completed.returncode == 0, completed.stderr
    assert "adopt ok" in completed.stdout
    assert "generation=1" in completed.stdout

    archives = sorted((state / "archive").glob("*.json"))
    assert len(archives) == 1
    archive = json.loads(archives[0].read_text(encoding="utf-8"))
    assert archive["generation"] == 1

    snapshots = sorted((state / "snapshots").glob("*.json"))
    assert len(snapshots) == 1
    payload = json.loads(snapshots[0].read_text(encoding="utf-8"))
    snapshot = Snapshot.from_dict(payload)  # 双校验：files/tree_hash 自洽
    assert "molt/shell_bundle.py" in snapshot.roots
    expected = sha256_file(ROOT / "molt/shell_bundle.py")
    assert snapshot.files["molt/shell_bundle.py"] == {"shell_bundle.py": expected}

    generation_file = state / "generations" / "00000001" / "molt" / "shell_bundle.py"
    assert generation_file.is_file()
    assert sha256_file(generation_file) == expected
    source_mode = (ROOT / "molt/shell_bundle.py").stat().st_mode & 0o777
    assert generation_file.stat().st_mode & 0o777 == source_mode
    assert verify_generation(state / "generations" / "00000001", snapshot)

    # revert 幂等：同一 archive 连续两次都成功，generation 递增且内容可校验。
    for generation in (2, 3):
        reverted = _run_cli(
            "revert",
            "--state",
            str(state),
            "--to",
            archive["archive_id"],
        )
        assert reverted.returncode == 0, reverted.stderr
        assert f"generation={generation}" in reverted.stdout
        assert verify_generation(state / "generations" / f"{generation:08d}", snapshot)
    pointer = json.loads((state / "current.json").read_text(encoding="utf-8"))
    assert pointer["generation"] == 3
    assert StateStore(state).read_snapshot(snapshot.snapshot_id).tree_hash == snapshot.tree_hash


def test_code_repair_adopt_still_works(tmp_path: Path) -> None:
    state = tmp_path / "state"
    completed = _run_cli(
        "adopt",
        "--state",
        str(state),
        "--genome",
        DEMO_GENOME,
        "--manifest",
        DEMO_MANIFEST,
    )
    assert completed.returncode == 0, completed.stderr
    assert "generation=1" in completed.stdout
    snapshots = sorted((state / "snapshots").glob("*.json"))
    payload = json.loads(snapshots[0].read_text(encoding="utf-8"))
    assert payload["roots"] == ["molt/core", "schemas/v1", "shells/code-repair"]
    assert verify_generation(state / "generations" / "00000001", Snapshot.from_dict(payload))


def test_file_root_symlink_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real.py"
    real.write_text("x = 1\n", encoding="utf-8")
    link = tmp_path / "link.py"
    link.symlink_to(real)
    roots = {"link.py": link}
    with pytest.raises(HashMismatch):
        build_snapshot(
            roots,
            generation=1,
            created_at="2026-01-01T00:00:00Z",
            snapshot_id="snap-test",
        )
    with pytest.raises(HashMismatch):
        copy_generation(roots, tmp_path / "generation")
