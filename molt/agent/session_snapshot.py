"""Immutable per-session snapshots with a single CAS commit pointer.

A snapshot is the unit of conversation, strategy, binding and rollback. Every
committed state is written as a content-addressed object first; the small
``active.json`` pointer is replaced last, so a crash or a failed CAS never
exposes a half-written snapshot.

The store is deliberately session-scoped: it never writes the global skill or
MCP registries and it never removes shared assets. Session rollback selects an
ancestor of *this* session only, and always appends a new revision.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_SESSION = re.compile(r"^sess-[a-f0-9]{16}$")
_OBJECT = re.compile(r"^[a-f0-9]{64}$")
_MESSAGE_KEYS = {
    "message_id",
    "role",
    "content",
    "created_at",
    "turn_id",
    "experiment_refs",
    "usage",
}


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class SnapshotConflict(RuntimeError):
    """The expected revision (or rollback direction) no longer matches."""


class SnapshotInvalid(RuntimeError):
    """The session id, object hash, pointer or snapshot shape is invalid."""


class SessionSnapshotStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)

    # -- paths / locking --------------------------------------------------
    def _dir(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not _SESSION.fullmatch(session_id):
            raise SnapshotInvalid("invalid_session_id")
        path = self.root / session_id
        if path.is_symlink():
            raise SnapshotInvalid("symlink_session")
        path.mkdir(mode=0o700, exist_ok=True)
        return path

    @contextmanager
    def _locked(self, session_id: str):
        directory = self._dir(session_id)
        lock_path = directory / "lock"
        with lock_path.open("a+b") as handle:
            os.chmod(lock_path, 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield directory
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _atomic(self, path: Path, value: Any) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(canonical(value))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _deleted(self, directory: Path) -> bool:
        return (directory / "deleted.json").is_file()

    def _object(self, directory: Path, value: Any) -> str:
        identifier = digest(value)
        path = directory / (identifier + ".json")
        if path.exists():
            if path.read_bytes() != canonical(value):
                raise SnapshotInvalid("object_integrity_mismatch")
        else:
            self._atomic(path, value)
        return identifier

    def _read_object(self, directory: Path, identifier: str) -> dict[str, Any]:
        if not isinstance(identifier, str) or not _OBJECT.fullmatch(identifier):
            raise SnapshotInvalid("invalid_snapshot_id")
        path = directory / (identifier + ".json")
        if path.is_symlink():
            raise SnapshotInvalid("symlink_object")
        try:
            value = json.loads(path.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise SnapshotInvalid("object_unreadable") from None
        if not isinstance(value, dict) or digest(value) != identifier:
            raise SnapshotInvalid("object_integrity_mismatch")
        return value

    def _load(self, directory: Path) -> dict[str, Any]:
        if self._deleted(directory):
            raise SnapshotInvalid("session_deleted")
        try:
            pointer = json.loads((directory / "active.json").read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise SnapshotInvalid("pointer_unreadable") from None
        if (
            not isinstance(pointer, dict)
            or set(pointer) != {"revision", "snapshot_id", "record_id"}
            or type(pointer.get("revision")) is not int
            or pointer["revision"] < 0
            or not isinstance(pointer.get("snapshot_id"), str)
            or not isinstance(pointer.get("record_id"), str)
        ):
            raise SnapshotInvalid("pointer_invalid")
        record = self._read_object(directory, pointer["record_id"])
        snapshot = self._read_object(directory, pointer["snapshot_id"])
        if (
            record.get("snapshot_id") != pointer["snapshot_id"]
            or type(record.get("revision")) is not int
            or record["revision"] != pointer["revision"]
            or snapshot.get("session_id") != directory.name
        ):
            raise SnapshotInvalid("pointer_record_mismatch")
        required = {"revision", "previous_record", "snapshot_id", "action", "evidence"}
        if (
            set(record) - (required | {"message_count"})
            or not required <= set(record)
            or not isinstance(record.get("action"), str)
            or not isinstance(record.get("evidence"), dict)
            or (
                "message_count" in record
                and type(record.get("message_count")) is not int
            )
            or (record["revision"] == 0) != (record.get("previous_record") is None)
        ):
            raise SnapshotInvalid("record_invalid")
        return {"pointer": pointer, "snapshot": snapshot, "record": record}

    def _publish(
        self,
        directory: Path,
        snapshot: dict[str, Any],
        *,
        previous: dict[str, Any] | None,
        action: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot_id = self._object(directory, snapshot)
        record = {
            "revision": previous["revision"] + 1 if previous else 0,
            "previous_record": previous["record_id"] if previous else None,
            "snapshot_id": snapshot_id,
            "action": action,
            "message_count": len(snapshot.get("messages", [])),
            "evidence": evidence,
        }
        record_id = self._object(directory, record)
        pointer = {
            "revision": record["revision"],
            "snapshot_id": snapshot_id,
            "record_id": record_id,
        }
        # The sole commit point: every immutable object above is already fsynced.
        self._atomic(directory / "active.json", pointer)
        return {"pointer": pointer, "snapshot": snapshot, "record": record}

    # -- public API --------------------------------------------------------
    def create(
        self,
        session_id: str,
        *,
        provider: str,
        created_at: str,
        system_prompt: str,
        binding: dict[str, Any],
    ) -> dict[str, Any]:
        with self._locked(session_id) as directory:
            if self._deleted(directory):
                raise SnapshotInvalid("session_deleted")
            if (directory / "active.json").exists():
                return self._load(directory)
            snapshot = {
                "schema_version": "1.0.0",
                "session_id": session_id,
                "provider": provider,
                "created_at": created_at,
                "system_prompt": system_prompt,
                "binding": copy.deepcopy(binding),
                "strategy": {"skill": "", "memory": [], "mcp_tools": None, "mcp_recipes": []},
                "assets": [],
                "context": {
                    "probe": {"experiment_ids": []},
                    "lab": {"run_ids": []},
                    "upper": None,
                    "binding_revision": int(binding.get("revision", 0)),
                },
                "files": {},
                "messages": [],
                "last_evolution": None,
            }
            return self._publish(
                directory, snapshot, previous=None, action="initialize", evidence={}
            )

    def current(self, session_id: str) -> dict[str, Any]:
        with self._locked(session_id) as directory:
            return self._load(directory)

    def _history_locked(self, directory: Path) -> list[dict[str, Any]]:
        current = self._load(directory)
        records = [current["record"]]
        seen: set[str] = set()
        while records[-1]["previous_record"]:
            identifier = records[-1]["previous_record"]
            if not isinstance(identifier, str) or identifier in seen:
                raise SnapshotInvalid("history_cycle")
            seen.add(identifier)
            records.append(self._read_object(directory, identifier))
        return records  # only committed records; orphan objects are ignored

    def history(self, session_id: str) -> list[dict[str, Any]]:
        with self._locked(session_id) as directory:
            return self._history_locked(directory)

    def read_snapshot(self, session_id: str, snapshot_id: str) -> dict[str, Any]:
        """Read a committed snapshot only if it is in this session's chain."""

        with self._locked(session_id) as directory:
            records = self._history_locked(directory)
            if not any(record.get("snapshot_id") == snapshot_id for record in records):
                raise SnapshotInvalid("target_not_in_session_history")
            return self._read_object(directory, snapshot_id)

    def read_by_revision(self, session_id: str, revision: int) -> dict[str, Any]:
        if type(revision) is not int or revision < 0:
            raise SnapshotInvalid("invalid_revision")
        with self._locked(session_id) as directory:
            for record in self._history_locked(directory):
                if record.get("revision") == revision:
                    return self._read_object(directory, str(record["snapshot_id"]))
        raise SnapshotInvalid("revision_not_found")

    def commit(
        self,
        session_id: str,
        snapshot: dict[str, Any],
        *,
        expected_revision: int,
        action: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        with self._locked(session_id) as directory:
            if self._deleted(directory):
                raise SnapshotInvalid("session_deleted")
            current = self._load(directory)
            if current["pointer"]["revision"] != expected_revision:
                raise SnapshotConflict("stale_revision")
            self._validate_snapshot(session_id, snapshot)
            return self._publish(
                directory,
                snapshot,
                previous=current["pointer"],
                action=action,
                evidence=evidence,
            )

    def _validate_snapshot(self, session_id: str, snapshot: Any) -> None:
        if not isinstance(snapshot, dict):
            raise SnapshotInvalid("invalid_snapshot_shape")
        if snapshot.get("session_id") != session_id:
            raise SnapshotInvalid("cross_session_snapshot")
        binding = snapshot.get("binding")
        if (
            not isinstance(binding, dict)
            or type(binding.get("revision")) is not int
            or binding["revision"] < 0
            or not isinstance(binding.get("skills"), dict)
            or not isinstance(binding.get("mcp"), dict)
        ):
            raise SnapshotInvalid("invalid_binding_snapshot")
        if (
            not isinstance(snapshot.get("messages"), list)
            or not isinstance(snapshot.get("assets", []), list)
            or not isinstance(snapshot.get("strategy"), dict)
            or not isinstance(snapshot.get("context", {}), dict)
            or not isinstance(snapshot.get("files"), dict)
            or not isinstance(snapshot.get("system_prompt"), str)
            or not isinstance(snapshot.get("provider"), str)
            or not isinstance(snapshot.get("created_at"), str)
        ):
            raise SnapshotInvalid("invalid_snapshot_shape")
        for message in snapshot["messages"]:
            if (
                not isinstance(message, dict)
                or set(message) - _MESSAGE_KEYS
                or not all(
                    isinstance(message.get(key), str)
                    for key in ("message_id", "role", "content", "created_at", "turn_id")
                )
                or message["role"] not in {"user", "assistant"}
                or (
                    message.get("experiment_refs") is not None
                    and not isinstance(message["experiment_refs"], list)
                )
                or (
                    message.get("usage") is not None
                    and not isinstance(message["usage"], dict)
                )
            ):
                raise SnapshotInvalid("invalid_message")
        for asset in snapshot.get("assets", []):
            if (
                not isinstance(asset, dict)
                or not isinstance(asset.get("asset_id"), str)
                or not isinstance(asset.get("kind"), str)
            ):
                raise SnapshotInvalid("invalid_asset_ref")

    def rollback(
        self,
        session_id: str,
        target_id: str,
        *,
        expected_revision: int,
        target_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._locked(session_id) as directory:
            if self._deleted(directory):
                raise SnapshotInvalid("session_deleted")
            current = self._load(directory)
            if current["pointer"]["revision"] != expected_revision:
                raise SnapshotConflict("stale_revision")
            record = current["record"]
            found = False
            selected_revision = None
            if target_revision is not None:
                if type(target_revision) is not int or target_revision < 0:
                    raise SnapshotInvalid("invalid_target_revision")
                records = self._history_locked(directory)
                target_record = next(
                    (item for item in records if item.get("revision") == target_revision),
                    None,
                )
                if target_record is None:
                    raise SnapshotInvalid("target_revision_not_found")
                if target_record.get("snapshot_id") != target_id:
                    raise SnapshotInvalid("target_revision_mismatch")
                found = True
                selected_revision = target_revision
            else:
                seen: set[str] = set()
                while True:
                    if record["snapshot_id"] == target_id:
                        found = True
                        selected_revision = record["revision"]
                        break
                    if not record["previous_record"]:
                        break
                    identifier = record["previous_record"]
                    if identifier in seen:
                        raise SnapshotInvalid("history_cycle")
                    seen.add(identifier)
                    record = self._read_object(directory, identifier)
            if not found:
                raise SnapshotInvalid("target_not_in_session_history")
            if current["pointer"]["snapshot_id"] == target_id:
                raise SnapshotConflict("already_at_target")
            target = self._read_object(directory, target_id)
            self._validate_snapshot(session_id, target)
            return self._publish(
                directory,
                target,
                previous=current["pointer"],
                action="rollback",
                evidence={
                    "from_snapshot": current["pointer"]["snapshot_id"],
                    "from_revision": current["pointer"]["revision"],
                    "target_snapshot": target_id,
                    "target_revision": selected_revision,
                    "history_retained": True,
                },
            )

    def session_ids(self) -> list[str]:
        result: list[str] = []
        for path in self.root.iterdir() if self.root.is_dir() else []:
            if not _SESSION.fullmatch(path.name):
                continue
            if not (path / "active.json").is_file():
                continue
            if (path / "deleted.json").exists():
                continue
            result.append(path.name)
        return sorted(result)

    def delete(self, session_id: str) -> None:
        with self._locked(session_id) as directory:
            if (directory / "deleted.json").exists():
                return
            self._atomic(
                directory / "deleted.json",
                {
                    "session_id": session_id,
                    "deleted_at": __import__("datetime")
                    .datetime.now(__import__("datetime").timezone.utc)
                    .isoformat(),
                },
            )
