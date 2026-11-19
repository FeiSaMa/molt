"""文件系统原语：原子写、目录 fsync、独占锁。

所有持久化状态都经由这里，避免各模块各写一套 temp→rename。
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from molt.core.write_policy import projection_writes_allowed, v1_read_only


def _reject_read_only_state(path: Path) -> None:
    """Reject writes below a v1 state during a kernel-default release."""
    if not v1_read_only() or projection_writes_allowed():
        return
    resolved = path.resolve(strict=False)
    state_dirs = {"runs", "journal", "generations", "ledger", "events", "archive", "snapshots", "results", "decisions", "proposals", "genomes"}
    for parent in (resolved, *resolved.parents):
        if any((parent / name).exists() for name in state_dirs):
            raise PermissionError("v1 state is read-only during the kernel-default release")


def reject_v1_state_write() -> None:
    """Reject StateStore mutations even when the target tree is still empty."""
    if v1_read_only() and not projection_writes_allowed():
        raise PermissionError("v1 state is read-only during the kernel-default release")


def fsync_dir(path: Path) -> None:
    """对目录调用 fsync，保证 rename 落盘。"""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """同目录 temp 文件 → fsync → rename → fsync 目录。"""
    path = Path(path)
    _reject_read_only_state(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    fsync_dir(path.parent)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def append_line(path: Path, line: str) -> None:
    """追加一行并 fsync；调用方保证该文件只有一个写者。"""
    path = Path(path)
    _reject_read_only_state(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


@contextmanager
def exclusive_lock(lock_path: Path, *, blocking: bool = False) -> Iterator[int]:
    """对 ``lock_path`` 取独占 flock；非阻塞失败抛 ``BlockingIOError``。"""
    lock_path = Path(lock_path)
    _reject_read_only_state(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        fcntl.flock(handle.fileno(), flags)
    except BlockingIOError:
        handle.close()
        raise
    try:
        yield handle.fileno()
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
