"""One-way, opt-in snapshot mirror from v1 files into the Rust kernel."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from molt.kernel_client import MAX_FRAME, KernelClient


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def preview_state(root: Path, socket_path: str) -> dict[str, Any]:
    """Compare a source snapshot to the durable kernel pointer without writing."""
    roots, file_count = state_roots(root)
    roots_bytes = json.dumps(roots, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    manifest = {"roots": roots, "tree_hash": _sha256(roots_bytes)}
    manifest_bytes = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    manifest_hash = _sha256(manifest_bytes)
    with KernelClient(socket_path, timeout=30) as client:
        inspected = client.inspect()
        current = inspected["current"]
        current_roots: dict[str, Any] = {"state": {}}
        if current:
            current_roots = client.get_manifest(current["manifest_hash"])["roots"]
    source_entries = roots["state"]
    current_entries = current_roots.get("state", {})
    differences = []
    for rel in sorted(source_entries.keys() | current_entries.keys()):
        source_entry = source_entries.get(rel)
        current_entry = current_entries.get(rel)
        if source_entry != current_entry:
            differences.append(
                {
                    "path": rel,
                    "source_sha256": source_entry.get("sha256") if source_entry else None,
                    "current_sha256": current_entry.get("sha256") if current_entry else None,
                    "source_size": source_entry.get("size") if source_entry else None,
                    "current_size": current_entry.get("size") if current_entry else None,
                    "change": "added" if current_entry is None else "removed" if source_entry is None else "changed",
                }
            )
    return {
        "state_id": inspected["state_id"],
        "source_manifest_hash": manifest_hash,
        "source_files": file_count,
        "source_bytes": sum(int(entry["size"]) for entry in roots["state"].values()),
        "differences": differences,
        "current_generation": current.get("generation") if current else 0,
        "current_manifest_hash": current.get("manifest_hash") if current else None,
        "changed": not current or current.get("manifest_hash") != manifest_hash,
    }


def state_roots(root: Path) -> tuple[dict[str, Any], int]:
    """Build a stable manifest view of durable v1 state, excluding task workdirs."""
    root = Path(root).resolve(strict=True)
    entries: dict[str, Any] = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        dirs[:] = sorted(d for d in dirs if not (base == root and d == "work"))
        for name in dirs:
            path = base / name
            if path.is_symlink():
                raise ValueError(f"state mirror refuses symlink: {path}")
        for name in sorted(files):
            path = base / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                if stat.S_ISSOCK(info.st_mode):
                    continue
                raise ValueError(f"state mirror refuses non-regular file: {path}")
            data = path.read_bytes()
            after = path.stat()
            if (info.st_size, info.st_mtime_ns, info.st_ino) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ino,
            ):
                raise ValueError(f"state changed while mirroring: {path}")
            rel = path.relative_to(root).as_posix()
            entries[rel] = {
                "type": "file",
                "mode": stat.S_IMODE(info.st_mode) & 0o777,
                "size": len(data),
                "sha256": _sha256(data),
            }
    return {"state": entries}, len(entries)


def mirror_state(
    root: Path, socket_path: str, *, migration_id: str | None = None
) -> dict[str, Any]:
    """Commit a v1 state snapshot into the kernel and verify manifest parity."""
    root = Path(root).resolve(strict=True)
    roots, file_count = state_roots(root)
    if migration_id is not None and not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", migration_id):
        raise ValueError("migration id must be 1-80 ASCII letters, digits, dot, underscore, or hyphen")
    with KernelClient(socket_path, timeout=30) as client:
        for rel, entry in roots["state"].items():
            data = (Path(root) / rel).read_bytes()
            if _sha256(data) != entry["sha256"]:
                raise ValueError(f"state changed before upload: {rel}")
            client.upload_object(data)
        manifest_hash = client.create_manifest(roots)
        stored = client.get_manifest(manifest_hash)
        if stored.get("roots") != roots:
            raise ValueError("kernel manifest differs from v1 state snapshot")
        request_id = (
            f"migration-{migration_id}"
            if migration_id is not None
            else None
        )
        if request_id is not None:
            status = client.request_status(request_id)
            if status["status"] == "committed":
                committed = status["result"]
                if committed["manifest_hash"] != manifest_hash:
                    raise ValueError("migration id already committed a different source snapshot")
                return {
                    "generation": committed["generation"],
                    "manifest_hash": manifest_hash,
                    "files": file_count,
                    "unchanged": True,
                }
        previous = client.current()
        if migration_id is None and previous and previous.get("manifest_hash") == manifest_hash:
            return {
                "generation": previous["generation"],
                "manifest_hash": manifest_hash,
                "files": file_count,
                "unchanged": True,
            }
        previous_generation = int(previous["generation"]) if previous else 0
        request_id = request_id or (
            f"mirror-{previous_generation}-{manifest_hash.removeprefix('sha256:')[:32]}"
        )
        result = client.call(
            "generation.commit",
            {
                "request_id": request_id,
                "manifest_hash": manifest_hash,
                "ts": "1970-01-01T00:00:00Z",
            },
        )
        if result.get("manifest_hash") != manifest_hash:
            raise ValueError("kernel current does not match mirrored v1 state")
        final_roots, final_count = state_roots(root)
        if final_count != file_count or final_roots != roots:
            raise ValueError("source state changed during migration")
        return {
            "generation": result["generation"],
            "manifest_hash": manifest_hash,
            "files": file_count,
            "unchanged": False,
        }


def materialize_current(destination: Path, socket_path: str) -> dict[str, Any]:
    """Project the current manifest into a new directory, verifying every object."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    with KernelClient(socket_path, timeout=30) as client:
        current = client.current()
        if not current:
            raise ValueError("kernel has no committed generation")
        manifest = client.get_manifest(current["manifest_hash"])
        files = 0
        total_bytes = 0
        for root_name, entries in manifest["roots"].items():
            root_part = PurePosixPath(root_name)
            if root_part.is_absolute() or len(root_part.parts) != 1 or root_part.parts[0] in (".", ".."):
                raise ValueError(f"unsafe manifest root: {root_name}")
            for rel, entry in sorted(entries.items()):
                rel_part = PurePosixPath(rel)
                if rel_part.is_absolute() or any(part in ("", ".", "..") for part in rel_part.parts):
                    raise ValueError(f"unsafe manifest path: {rel}")
                size = int(entry["size"])
                if size > MAX_FRAME // 4 - 1024:
                    raise ValueError(f"object too large for bounded IPC read: {rel}")
                data = client.get_object(entry["sha256"])
                if len(data) != size or _sha256(data) != entry["sha256"]:
                    raise ValueError(f"kernel object failed manifest verification: {rel}")
                target = destination / root_name / Path(*rel_part.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(int(entry["mode"]) & 0o777)
                files += 1
                total_bytes += size
    return {
        "generation": current["generation"],
        "manifest_hash": current["manifest_hash"],
        "files": files,
        "bytes": total_bytes,
    }
