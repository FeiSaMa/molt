"""Small storage helpers shared by the upper registry, evaluator, and demo.

All upper-layer state must live outside the repository tree. Writes are atomic
(temp file in the same directory + ``os.replace``) so an interrupted demo never
leaves a half-written registry or evaluation record.
"""

from __future__ import annotations

import errno
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any


class StatePathError(ValueError):
    """Raised when a state path would write inside the repository tree."""


def ensure_outside_repo(state_dir: Path | str, repo_root: Path | str) -> Path:
    resolved_state = Path(state_dir).expanduser().resolve()
    resolved_repo = Path(repo_root).expanduser().resolve()
    if resolved_state == resolved_repo or resolved_repo in resolved_state.parents:
        raise StatePathError(
            f"upper state must be outside the repository tree: {resolved_state}"
        )
    return resolved_state


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EBADF}:
                raise
    finally:
        os.close(fd)


def atomic_write_json(
    path: Path,
    payload: Any,
    *,
    mode: int = 0o600,
    fault_hook: Callable[[str], None] | None = None,
) -> None:
    """Write one JSON file atomically.

    Order: temp write → flush → fsync temp → ``os.replace`` → optional fault
    hook → fsync parent directory. The fault hook is used by crash tests to stop
    exactly after the rename but before the directory fsync.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        if fault_hook is not None:
            fault_hook("before_replace")
        os.replace(temp_path, path)
        if fault_hook is not None:
            fault_hook("after_replace_before_dir_fsync")
        _fsync_directory(path.parent)
    finally:
        if temp_path.exists():
            temp_path.unlink()
