"""状态存储与采纳/回退事务。

事务语义：
- 独占 flock ``state/lock``，拿不到立即 ``tx_locked``，不排队、不重试；
- journal 用 ``O_EXCL`` 创建，写 root 顺序固定，指针最后；
- 中途失败或进程被杀，``recover()`` 幂等恢复到旧态或新态；
- 读者校验「指针 + generation + tree hash」，有界重试 3 次，超限 ``stale_read``。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from molt.core.archive import ArchiveStore
from molt.core.errors import StaleRead, TxLocked, ValidationError
from molt.core.fsutil import atomic_write_text, exclusive_lock, fsync_dir
from molt.core.hash import canonical_json_text, content_hash, tree_files, tree_hash_from_files
from molt.core.snapshot import build_snapshot, copy_generation, verify_generation
from molt.core.types import (
    Archive,
    GateDecision,
    Genome,
    Proposal,
    Result,
    Run,
    Snapshot,
)

READ_RETRIES = 3
READ_RETRY_SLEEP_S = 0.01


class StateStore:
    """``state/`` 的唯一写入口。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- 目录 ------------------------------------------------------------
    def ensure(self) -> None:
        for sub in (
            "journal",
            "generations",
            "snapshots",
            "archive",
            "events",
            "ledger",
            "runs",
            "results",
            "decisions",
            "proposals",
            "genomes",
        ):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    @property
    def lock_path(self) -> Path:
        return self.root / "lock"

    @property
    def pointer_path(self) -> Path:
        return self.root / "current.json"

    def lock(self, *, blocking: bool = False) -> Any:
        return _Lock(self.lock_path, blocking=blocking)

    @property
    def archive(self) -> ArchiveStore:
        return ArchiveStore(self.root)

    # -- 简单记录 --------------------------------------------------------
    def _write_json(self, path: Path, data: Mapping[str, Any]) -> None:
        atomic_write_text(path, canonical_json_text(dict(data)))

    def write_run(self, record: Run) -> Path:
        path = self.root / "runs" / f"{record.run_id}.json"
        self._write_json(path, record.to_dict())
        return path

    def write_genome(self, record: Genome) -> Path:
        path = self.root / "genomes" / f"{_safe_name(record.genome_id)}.json"
        self._write_json(path, record.to_dict())
        return path

    def read_genome(self, genome_id: str) -> Genome:
        return Genome.from_dict(
            self._read_json(self.root / "genomes" / f"{_safe_name(genome_id)}.json")
        )

    def list_genomes(self) -> list[Genome]:
        return self._list_records("genomes", Genome)

    def read_run(self, run_id: str) -> Run:
        return Run.from_dict(self._read_json(self.root / "runs" / f"{run_id}.json"))

    def list_runs(self) -> list[Run]:
        return self._list_records("runs", Run)

    def write_result(self, record: Result) -> Path:
        path = self.root / "results" / f"{record.result_id}.json"
        self._write_json(path, record.to_dict())
        return path

    def read_result(self, result_id: str) -> Result:
        return Result.from_dict(self._read_json(self.root / "results" / f"{result_id}.json"))

    def list_results(self) -> list[Result]:
        return self._list_records("results", Result)

    def write_decision(self, record: GateDecision) -> Path:
        path = self.root / "decisions" / f"{record.decision_id}.json"
        self._write_json(path, record.to_dict())
        return path

    def read_decision(self, decision_id: str) -> GateDecision:
        return GateDecision.from_dict(
            self._read_json(self.root / "decisions" / f"{decision_id}.json")
        )

    def list_decisions(self) -> list[GateDecision]:
        return self._list_records("decisions", GateDecision)

    def write_proposal(self, record: Proposal) -> Path:
        path = self.root / "proposals" / f"{record.proposal_id}.json"
        self._write_json(path, record.to_dict())
        return path

    def read_proposal(self, proposal_id: str) -> Proposal:
        return Proposal.from_dict(self._read_json(self.root / "proposals" / f"{proposal_id}.json"))

    def snapshots(self) -> list[Snapshot]:
        return self._list_records("snapshots", Snapshot)

    def read_snapshot(self, snapshot_id: str) -> Snapshot:
        return Snapshot.from_dict(self._read_json(self.root / "snapshots" / f"{snapshot_id}.json"))

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise ValidationError(f"记录不存在: {path}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValidationError(f"记录必须是 object: {path}")
        return value

    def _list_records(self, sub: str, record_cls: type) -> list[Any]:
        directory = self.root / sub
        if not directory.exists():
            return []
        return [
            record_cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(directory.glob("*.json"))
        ]

    # -- 指针与读者 ------------------------------------------------------
    def current(self) -> dict[str, Any] | None:
        if not self.pointer_path.exists():
            return None
        value = json.loads(self.pointer_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValidationError("current.json 必须是 object")
        return value

    def read_current(self, *, retries: int = READ_RETRIES) -> dict[str, Any] | None:
        """校验指针 + generation + tree hash；不一致有界重试。"""
        last_error = ""
        for attempt in range(retries + 1):
            pointer = self.current()
            if pointer is None:
                return None
            try:
                snapshot = self.read_snapshot(str(pointer["snapshot_id"]))
                generation_dir = self.root / "generations" / f"{int(pointer['generation']):08d}"
                if snapshot.tree_hash != pointer["tree_hash"]:
                    last_error = "snapshot.tree_hash 与指针不一致"
                elif not generation_dir.exists():
                    last_error = "generation 目录不存在"
                elif not verify_generation(generation_dir, snapshot):
                    last_error = "generation 目录树哈希与快照不一致"
                else:
                    return pointer
            except (KeyError, ValueError, ValidationError, OSError) as exc:
                last_error = str(exc)
            if attempt < retries:
                time.sleep(READ_RETRY_SLEEP_S)
        raise StaleRead(f"读取到不一致状态（重试 {retries} 次）：{last_error}")

    # -- 事务 ------------------------------------------------------------
    def _journal_path(self, generation: int) -> Path:
        return self.root / "journal" / f"{generation:08d}.json"

    def _write_journal(self, entry: dict[str, Any]) -> Path:
        path = self._journal_path(int(entry["generation"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError as exc:
            raise TxLocked(
                f"journal 已存在（generation={entry['generation']}），先运行 doctor --recover"
            ) from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(canonical_json_text(entry))
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            if path.exists():
                path.unlink()
            raise
        fsync_dir(path.parent)
        return path

    def _commit_pointer(self, pointer: dict[str, Any]) -> None:
        self._write_json(self.pointer_path, pointer)

    def adopt(
        self,
        genome: Genome,
        roots: Mapping[str, Path],
        *,
        result_refs: list[str],
        note: str,
        adopted_at: str,
        lock_hold_s: float = 0.0,
    ) -> Archive:
        """单写者单事务：快照 → generation → snapshot/archive → 指针。"""
        self.ensure()
        with _Lock(self.lock_path, blocking=False):
            pointer = self.current()
            generation = int(pointer["generation"]) + 1 if pointer else 1
            snapshot = build_snapshot(
                roots,
                generation=generation,
                created_at=adopted_at,
                snapshot_id=f"snap-{_short_id(roots, generation)}",
            )
            archive_id = f"arc-{generation:04d}-{genome.genome_id.split(':')[-1][:8]}"
            archive = Archive.from_dict(
                {
                    "schema_version": "1.0.0",
                    "archive_id": archive_id,
                    "generation": generation,
                    "genome_id": genome.genome_id,
                    "parent_id": pointer["genome_id"] if pointer else None,
                    "adopted_at": adopted_at,
                    "snapshot_id": snapshot.snapshot_id,
                    "result_refs": list(result_refs),
                    "note": note,
                    "ext": {},
                }
            )
            journal = self._write_journal(
                {
                    "generation": generation,
                    "snapshot_id": snapshot.snapshot_id,
                    "archive_id": archive_id,
                    "genome_id": genome.genome_id,
                    "tree_hash": snapshot.tree_hash,
                }
            )
            self._materialize_generation(roots, generation, snapshot)
            self._write_json(
                self.root / "snapshots" / f"{snapshot.snapshot_id}.json", snapshot.to_dict()
            )
            self.archive.commit(archive)
            self._commit_pointer(
                {
                    "generation": generation,
                    "snapshot_id": snapshot.snapshot_id,
                    "genome_id": genome.genome_id,
                    "archive_id": archive_id,
                    "tree_hash": snapshot.tree_hash,
                    "adopted_at": adopted_at,
                }
            )
            journal.unlink()
            fsync_dir(journal.parent)
            if lock_hold_s:
                time.sleep(lock_hold_s)
            return archive

    def _materialize_generation(
        self, roots: Mapping[str, Path], generation: int, snapshot: Snapshot
    ) -> None:
        final = self.root / "generations" / f"{generation:08d}"
        tmp = self.root / "generations" / f".tmp-{os.getpid()}-{generation:08d}"
        copy_generation(roots, tmp)
        if not verify_generation(tmp, snapshot):
            shutil.rmtree(tmp, ignore_errors=True)
            raise StaleRead("复制后的 generation 树哈希与快照不一致")
        if final.exists():
            shutil.rmtree(final)
        os.replace(tmp, final)
        fsync_dir(final.parent)

    def revert(
        self,
        archive_id: str,
        *,
        adopted_at: str,
        note: str = "revert",
    ) -> Archive:
        """把指针切回某次归档的内容；生成新 generation，旧内容不改。"""
        self.ensure()
        with _Lock(self.lock_path, blocking=False):
            pointer = self.current()
            target = self.archive.get(archive_id)
            snapshot = self.read_snapshot(target.snapshot_id)
            source = self.root / "generations" / f"{snapshot.generation:08d}"
            if not source.exists() or not verify_generation(source, snapshot):
                raise StaleRead(f"归档 generation 不可用: {archive_id}")
            generation = int(pointer["generation"]) + 1 if pointer else 1
            new_archive_id = f"arc-{generation:04d}-revert-{target.archive_id[-8:]}"
            journal = self._write_journal(
                {
                    "generation": generation,
                    "snapshot_id": snapshot.snapshot_id,
                    "archive_id": new_archive_id,
                    "genome_id": target.genome_id,
                    "tree_hash": snapshot.tree_hash,
                }
            )
            final = self.root / "generations" / f"{generation:08d}"
            tmp = self.root / "generations" / f".tmp-{os.getpid()}-{generation:08d}"
            shutil.copytree(source, tmp)
            if not verify_generation(tmp, snapshot):
                shutil.rmtree(tmp, ignore_errors=True)
                raise StaleRead("revert 复制后的树哈希不一致")
            os.replace(tmp, final)
            fsync_dir(final.parent)
            record = Archive.from_dict(
                {
                    "schema_version": "1.0.0",
                    "archive_id": new_archive_id,
                    "generation": generation,
                    "genome_id": target.genome_id,
                    "parent_id": pointer["genome_id"] if pointer else None,
                    "adopted_at": adopted_at,
                    "snapshot_id": snapshot.snapshot_id,
                    "result_refs": list(target.result_refs),
                    "note": note,
                    "ext": {"reverted_from": archive_id},
                }
            )
            self.archive.commit(record)
            self._commit_pointer(
                {
                    "generation": generation,
                    "snapshot_id": snapshot.snapshot_id,
                    "genome_id": target.genome_id,
                    "archive_id": new_archive_id,
                    "tree_hash": snapshot.tree_hash,
                    "adopted_at": adopted_at,
                }
            )
            journal.unlink()
            fsync_dir(journal.parent)
            return record

    def recover(self) -> list[str]:
        """恢复未完成事务；幂等，结果只会是旧态或新态。"""
        self.ensure()
        actions: list[str] = []
        with _Lock(self.lock_path, blocking=False):
            for tmp in sorted((self.root / "generations").glob(".tmp-*")):
                shutil.rmtree(tmp, ignore_errors=True)
                actions.append(f"removed-tmp:{tmp.name}")
            current = self.current()
            current_gen = int(current["generation"]) if current else 0
            for journal_path in sorted((self.root / "journal").glob("*.json")):
                entry = json.loads(journal_path.read_text(encoding="utf-8"))
                generation = int(entry["generation"])
                if generation <= current_gen:
                    journal_path.unlink()
                    actions.append(f"already-committed:{generation}")
                    continue
                generation_dir = self.root / "generations" / f"{generation:08d}"
                snapshot_path = self.root / "snapshots" / f"{entry['snapshot_id']}.json"
                archive_path = self.root / "archive" / f"{entry['archive_id']}.json"
                complete = (
                    generation_dir.exists() and snapshot_path.exists() and archive_path.exists()
                )
                if complete:
                    snapshot = self.read_snapshot(entry["snapshot_id"])
                    if verify_generation(generation_dir, snapshot):
                        self._commit_pointer(
                            {
                                "generation": generation,
                                "snapshot_id": entry["snapshot_id"],
                                "genome_id": entry["genome_id"],
                                "archive_id": entry["archive_id"],
                                "tree_hash": snapshot.tree_hash,
                            }
                        )
                        journal_path.unlink()
                        actions.append(f"completed:{generation}")
                        current_gen = generation
                        continue
                shutil.rmtree(generation_dir, ignore_errors=True)
                for path in (snapshot_path, archive_path):
                    if path.exists():
                        path.unlink()
                journal_path.unlink()
                actions.append(f"aborted:{generation}")
            fsync_dir(self.root / "journal")
        return actions


class _Lock:
    """``StateStore.lock()`` 的上下文包装。"""

    def __init__(self, path: Path, *, blocking: bool) -> None:
        self.path = path
        self.blocking = blocking
        self._ctx: Any = None

    def __enter__(self) -> _Lock:
        self._ctx = exclusive_lock(self.path, blocking=self.blocking)
        try:
            self._ctx.__enter__()
        except BlockingIOError as exc:
            raise TxLocked("state 写锁被占用（tx_locked）") from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._ctx is not None:
            self._ctx.__exit__(exc_type, exc, tb)


def _short_id(roots: Mapping[str, Path], generation: int) -> str:
    flat: dict[str, str] = {}
    for key, root in roots.items():
        for rel, digest in tree_files(Path(root)).items():
            flat[f"{key}/{rel}"] = digest
    return content_hash({"tree": tree_hash_from_files(flat), "generation": generation})[:16]


def _safe_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)
    if not safe:
        raise ValidationError(f"非法记录 id: {value!r}")
    return safe
