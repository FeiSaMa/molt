"""Bounded file snapshots for isolated edit and read directories."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

MAX_FILES = 64
MAX_FILE_BYTES = 256 * 1024
MAX_TREE_BYTES = 2 * 1024 * 1024
_COMPONENT = re.compile(r"[A-Za-z0-9._-]{1,100}")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def validate_paths(paths: list[str]) -> None:
    if len(paths) > MAX_FILES:
        raise ValueError("too many managed files")
    names = set()
    for path in paths:
        if not isinstance(path, str) or len(path) > 240:
            raise ValueError("invalid managed path")
        parts = path.split("/")
        if any(
            not _COMPONENT.fullmatch(part) or part in {".", ".."}
            or part.lower() in {".git", ".env"} or part.lower().startswith(".env.")
            for part in parts
        ):
            raise ValueError(f"invalid managed path: {path}")
        if path in names:
            raise ValueError(f"duplicate managed path: {path}")
        names.add(path)
    for name in names:
        parts = name.split("/")
        if any("/".join(parts[:i]) in names for i in range(1, len(parts))):
            raise ValueError(f"managed file conflicts with a directory: {name}")


def validate_sizes(sizes: list[int]) -> None:
    if any(not 0 <= size <= MAX_FILE_BYTES for size in sizes) or sum(sizes) > MAX_TREE_BYTES:
        raise ValueError("managed file or tree size exceeds limit")


@contextmanager
def open_directory(path: Path):
    """Open each directory without following symbolic links."""
    path = path.absolute()
    fd = os.open(path.anchor, _DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            if part == "..":
                raise ValueError(f"parent traversal in directory: {path}")
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _read_file(directory: int, path: str) -> bytes:
    parts = path.split("/")
    fd = os.dup(directory)
    try:
        for part in parts[:-1]:
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        with os.fdopen(os.open(parts[-1], flags, dir_fd=fd), "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ValueError(f"managed input must be a regular file with one link: {path}")
            validate_sizes([before.st_size])
            data = handle.read(MAX_FILE_BYTES + 1)
            after = os.fstat(handle.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                len(data), after.st_mtime_ns, after.st_ctime_ns
            ):
                raise ValueError(f"managed input changed during capture: {path}")
            return data
    finally:
        os.close(fd)


def capture(root: Path, manifest: Path | list[str], *, allow_missing: bool = False) -> dict[str, bytes]:
    """Read only the explicitly listed files in a quiescent edit directory."""
    try:
        if isinstance(manifest, Path):
            with open_directory(manifest.parent) as directory:
                paths = json.loads(_read_file(directory, manifest.name))
        else:
            paths = manifest
        if not isinstance(paths, list):
            raise ValueError("managed manifest must be a JSON array of paths")
        validate_paths(paths)
        with open_directory(root) as directory:
            contents = {}
            total = 0
            for path in sorted(paths):
                try:
                    data = _read_file(directory, path)
                except FileNotFoundError:
                    if allow_missing:
                        continue
                    raise
                total += len(data)
                if total > MAX_TREE_BYTES:
                    raise ValueError("managed tree size exceeds limit")
                contents[path] = data
    except (OSError, UnicodeError, RecursionError) as exc:
        raise ValueError(f"cannot capture managed input: {exc}") from exc
    return contents


def publish_tree(parent: Path, contents: dict[str, bytes], *, writable: bool = False) -> Path:
    """Publish a fresh project by renaming its complete tree into place."""
    parent = parent.absolute()
    with open_directory(parent) as fd:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("export parent must be a private directory with mode 0700")
    slot = Path(tempfile.mkdtemp(prefix="molt-tree-", dir=parent))
    stage = slot / "preparing"
    stage.mkdir(mode=0o700)
    directories = {stage}
    for name, data in contents.items():
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        directories.update(directory for directory in path.parents if directory.is_relative_to(stage))
        path.write_bytes(data)
        path.chmod(0o600 if writable else 0o400)
    for directory in directories:
        directory.chmod(0o700 if writable else 0o500)
    published = slot / "project"
    stage.rename(published)
    return published
