"""D6：快照事务、kill -9 恢复、读者校验、revert。"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from molt.core.errors import HashMismatch, StaleRead, TxLocked, ValidationError
from molt.core.snapshot import build_snapshot
from molt.core.store import StateStore
from molt.core.types import Genome

ROOT = Path(__file__).resolve().parents[1]
FIXED = "2026-01-01T00:00:00Z"


def load_genome() -> Genome:
    data = json.loads((ROOT / "shells/code-repair/genomes/v0.json").read_text(encoding="utf-8"))
    return Genome.from_dict(data)


def make_roots(tmp_path: Path) -> dict[str, Path]:
    roots = {
        "molt/core": tmp_path / "repo" / "molt" / "core",
        "shells/code-repair": tmp_path / "repo" / "shells" / "code-repair",
    }
    for name, path in roots.items():
        path.mkdir(parents=True, exist_ok=True)
        (path / "file.py").write_text(f"# {name}\n", encoding="utf-8")
    return roots


def test_adopt_writes_everything_and_pointer_last(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    roots = make_roots(tmp_path)
    archive = store.adopt(
        load_genome(), roots, result_refs=["result-1"], note="test", adopted_at=FIXED
    )
    pointer = store.current()
    assert pointer is not None
    assert pointer["generation"] == 1
    assert pointer["archive_id"] == archive.archive_id
    snapshot = store.read_snapshot(pointer["snapshot_id"])
    assert set(snapshot.files) == set(roots)
    assert store.read_current()["generation"] == 1
    assert (store.root / "archive" / f"{archive.archive_id}.json").is_file()
    assert not list((store.root / "journal").glob("*.json"))


def test_second_generation_keeps_parent(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    roots = make_roots(tmp_path)
    genome = load_genome()
    first = store.adopt(genome, roots, result_refs=["r1"], note="a", adopted_at=FIXED)
    (roots["molt/core"] / "file.py").write_text("# changed\n", encoding="utf-8")
    second = store.adopt(genome, roots, result_refs=["r2"], note="b", adopted_at=FIXED)
    assert second.generation == 2
    assert second.parent_id == genome.genome_id
    assert first.archive_id != second.archive_id
    assert store.read_current()["generation"] == 2


def test_reader_detects_tampered_generation(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    roots = make_roots(tmp_path)
    store.adopt(load_genome(), roots, result_refs=["r1"], note="", adopted_at=FIXED)
    target = store.root / "generations" / "00000001" / "molt" / "core" / "file.py"
    target.write_text("# tampered\n", encoding="utf-8")
    with pytest.raises(StaleRead):
        store.read_current(retries=1)


def test_revert_restores_content_at_new_generation(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    roots = make_roots(tmp_path)
    first = store.adopt(load_genome(), roots, result_refs=["r1"], note="v1", adopted_at=FIXED)
    (roots["molt/core"] / "file.py").write_text("# v2\n", encoding="utf-8")
    store.adopt(load_genome(), roots, result_refs=["r2"], note="v2", adopted_at=FIXED)
    reverted = store.revert(first.archive_id, adopted_at=FIXED)
    assert reverted.generation == 3
    restored = store.root / "generations" / "00000003" / "molt" / "core" / "file.py"
    assert restored.read_text(encoding="utf-8") == "# molt/core\n"
    assert store.read_current()["archive_id"] == reverted.archive_id


def test_kill_9_mid_transaction_recovers_idempotently(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    roots = make_roots(tmp_path)
    script = f"""
import os, signal, sys
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})
from molt.core.store import StateStore
from molt.core.types import Genome
import json
store = StateStore(Path({str(tmp_path / "state")!r}))
genome = Genome.from_dict(json.loads(Path({str(ROOT / "shells/code-repair/genomes/v0.json")!r}).read_text()))
roots = {{'molt/core': Path({str(roots["molt/core"])!r}), 'shells/code-repair': Path({str(roots["shells/code-repair"])!r})}}
store._commit_pointer = lambda pointer: os.kill(os.getpid(), signal.SIGKILL)
store.adopt(genome, roots, result_refs=['r1'], note='crash', adopted_at='{FIXED}')
"""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=False
    )
    assert completed.returncode == -signal.SIGKILL
    assert list((store.root / "journal").glob("*.json")), "被杀后应留下 journal"

    actions = store.recover()
    assert actions == ["completed:1"]
    assert store.read_current()["generation"] == 1
    assert store.recover() == []
    assert store.read_current()["generation"] == 1


def _kill_mid_transaction(tmp_path: Path, roots: dict[str, Path]) -> subprocess.CompletedProcess:
    script = f"""
import os, signal, sys, json
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})
from molt.core.store import StateStore
from molt.core.types import Genome
store = StateStore(Path({str(tmp_path / "state")!r}))
genome = Genome.from_dict(json.loads(Path({str(ROOT / "shells/code-repair/genomes/v0.json")!r}).read_text()))
roots = {{'molt/core': Path({str(roots["molt/core"])!r}), 'shells/code-repair': Path({str(roots["shells/code-repair"])!r})}}
store._commit_pointer = lambda pointer: os.kill(os.getpid(), signal.SIGKILL)
store.adopt(genome, roots, result_refs=['r1'], note='crash', adopted_at='{FIXED}')
"""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=False
    )
    assert completed.returncode == -signal.SIGKILL
    return completed


def test_adopt_auto_recovers_after_sigkill(tmp_path: Path) -> None:
    roots = make_roots(tmp_path)
    _kill_mid_transaction(tmp_path, roots)
    store = StateStore(tmp_path / "state")
    assert list((store.root / "journal").glob("*.json")), "被杀后应留下 journal"

    archive = store.adopt(
        load_genome(), roots, result_refs=["r2"], note="after-crash", adopted_at=FIXED
    )
    assert store.last_recovery == ["completed:1"]
    assert archive.generation == 2
    assert store.current()["generation"] == 2
    assert not list((store.root / "journal").glob("*.json"))
    assert store.read_current()["generation"] == 2
    assert store.recover() == []


def test_adopt_without_journal_reports_no_recovery(tmp_path: Path) -> None:
    roots = make_roots(tmp_path)
    store = StateStore(tmp_path / "state")
    store.adopt(load_genome(), roots, result_refs=["r1"], note="", adopted_at=FIXED)
    assert store.last_recovery == []
    assert store.recover() == []


def test_incomplete_journal_aborts_to_old_state(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    store.ensure()
    journal = store.root / "journal" / "00000001.json"
    journal.write_text(
        json.dumps(
            {
                "generation": 1,
                "snapshot_id": "snap-missing",
                "archive_id": "arc-missing",
                "genome_id": "g",
                "tree_hash": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    actions = store.recover()
    assert actions == ["aborted:1"]
    assert store.current() is None
    assert store.recover() == []


def test_missing_artifact_root_rejected(tmp_path: Path) -> None:
    roots = make_roots(tmp_path)
    with pytest.raises(HashMismatch):
        build_snapshot(
            {**roots, "missing": tmp_path / "nope"},
            generation=1,
            created_at=FIXED,
            snapshot_id="snap",
        )


def test_symlink_in_root_rejected(tmp_path: Path) -> None:
    roots = make_roots(tmp_path)
    (roots["molt/core"] / "link.py").symlink_to(roots["molt/core"] / "file.py")
    with pytest.raises(HashMismatch):
        build_snapshot(roots, generation=1, created_at=FIXED, snapshot_id="snap")


def test_snapshot_files_must_match_roots() -> None:
    payload = {
        "schema_version": "1.0.0",
        "snapshot_id": "snap",
        "generation": 1,
        "created_at": FIXED,
        "roots": ["a", "b"],
        "tree_hash": "a" * 64,
        "files": {"a": {"x": "b" * 64}},
        "ext": {},
    }
    with pytest.raises(ValidationError):
        from molt.core.types import Snapshot

        Snapshot.from_dict(payload)


def test_lock_contention_fails_immediately(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    roots = make_roots(tmp_path)
    with store.lock():
        with pytest.raises(TxLocked):
            store.adopt(load_genome(), roots, result_refs=["r1"], note="", adopted_at=FIXED)


def test_adopt_rolls_back_incomplete_generation(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    make_roots(tmp_path)
    store.ensure()
    # 人为留下一个临时 generation 目录，recover 必须清掉
    tmp = store.root / "generations" / ".tmp-999-00000001"
    tmp.mkdir(parents=True)
    (tmp / "junk").write_text("x", encoding="utf-8")
    assert store.recover() == ["removed-tmp:.tmp-999-00000001"]
    assert not tmp.exists()
