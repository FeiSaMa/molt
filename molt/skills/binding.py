"""One immutable-snapshot binding pointer shared by skills and MCP profiles.

There is exactly **one** authoritative file, ``<state>/skills/current.json``::

    {
      "schema_version": "1.0.0",
      "revision": 3,
      "snapshot_sha256": "<64 hex>",
      "skills": {"repo-evidence": {skill_id, version, asset_id, package_hash}},
      "mcp":    {"<server>": {server_id, version, asset_id, package_hash}},
      "updated_at": "2026-11-14T02:40:55Z"
    }

Every commit builds a *complete* new snapshot (revision + 1) and swaps the whole
document atomically; there is no field-level merge and never a second pointer
file.

Crash safety and concurrency:

* the whole read → CAS check → history write → current swap window runs under an
  OS ``flock(LOCK_EX)`` on ``<state>/skills/.lock``, so two ``BindingStore``
  instances (or two processes) cannot both pass the CAS on the same revision;
  ``expected_revision`` is compared against a snapshot re-read *inside* the lock;
* history files are content-addressed: ``history/<revision>-<snapshot_sha256[:12]>.json``.
  A crash between the history write and the current swap leaves an *orphan*
  history file with no reference; it is ignored by ``load()`` and never blocks
  the retried commit at the same revision number;
* ``load()`` refuses to trust a pointer whose history record is missing or whose
  content hash does not match (``BindingError("history_missing")``);
* an existing history file is immutable: writing different content to the same
  content-addressed path is an error, never an overwrite.

Writes go through :func:`molt.upper.storage.atomic_write_json` (temp + fsync +
``os.replace`` + directory fsync) and the state directory must live outside the
repository tree (:func:`molt.upper.storage.ensure_outside_repo`).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from molt.upper.storage import StatePathError, atomic_write_json, ensure_outside_repo

BINDING_SCHEMA_VERSION = "1.0.0"
SECTIONS = ("skills", "mcp")
BINDING_DIRNAME = "skills"
CURRENT_FILENAME = "current.json"
HISTORY_DIRNAME = "history"
LOCK_FILENAME = ".lock"
SNAPSHOT_HASH_FIELD = "snapshot_sha256"

SECTION_ID_KEY = {"skills": "skill_id", "mcp": "server_id"}
SECTION_REQUIRED = {
    "skills": ("skill_id", "version", "asset_id", "package_hash"),
    "mcp": ("server_id", "version", "asset_id", "package_hash"),
}
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PACKAGE_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SNAPSHOT_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class BindingError(ValueError):
    """Raised for invalid binding paths, snapshots, or immutable history."""


class BindingConflict(BindingError):
    """Raised when ``expected_revision`` does not match the current revision."""

    def __init__(self, expected_revision: int, actual_revision: int) -> None:
        super().__init__(
            f"binding revision conflict: expected {expected_revision}, "
            f"actual {actual_revision}"
        )
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _default_repo_root() -> Path:
    # molt/skills/binding.py -> molt/skills -> molt -> <repo>
    return Path(__file__).resolve().parents[2]


def snapshot_sha256(payload: dict[str, Any]) -> str:
    """Content hash of a snapshot, excluding the ``snapshot_sha256`` field itself."""
    body = {key: value for key, value in payload.items() if key != SNAPSHOT_HASH_FIELD}
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _empty_binding() -> dict[str, Any]:
    return {
        "schema_version": BINDING_SCHEMA_VERSION,
        "revision": 0,
        SNAPSHOT_HASH_FIELD: None,
        "skills": {},
        "mcp": {},
        "updated_at": None,
    }


def _copy(payload: Any) -> Any:
    """Deep copy through JSON so snapshots never share mutable state."""
    return json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))


def _require_section(section: Any) -> str:
    if section not in SECTIONS:
        raise BindingError(f"unknown binding section {section!r}; expected one of {list(SECTIONS)}")
    return str(section)


def _require_key(key: Any) -> str:
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise BindingError(f"invalid binding key {key!r}")
    return key


def _validate_snapshot(payload: Any, *, origin: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BindingError(f"{origin} must be a JSON object")
    expected = {"schema_version", "revision", SNAPSHOT_HASH_FIELD, "skills", "mcp", "updated_at"}
    unknown = sorted(set(payload) - expected)
    if unknown:
        raise BindingError(f"{origin} has unknown fields: {', '.join(unknown)}")
    missing = sorted(expected - set(payload))
    if missing:
        raise BindingError(f"{origin} is missing fields: {', '.join(missing)}")
    revision = payload["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise BindingError(f"{origin}.revision must be a non-negative integer")
    if not isinstance(payload["schema_version"], str) or not payload["schema_version"]:
        raise BindingError(f"{origin}.schema_version must be a non-empty string")
    updated_at = payload["updated_at"]
    if updated_at is not None and not isinstance(updated_at, str):
        raise BindingError(f"{origin}.updated_at must be a string or null")
    for section in SECTIONS:
        mapping = payload[section]
        if not isinstance(mapping, dict):
            raise BindingError(f"{origin}.{section} must be a JSON object")
        for key, entry in mapping.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                raise BindingError(f"{origin}.{section}[{key!r}] must be a JSON object")
    return payload


def _validate_entry(section: str, entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise BindingError(f"{section} binding entry must be a JSON object")
    unknown = sorted(set(entry) - set(SECTION_REQUIRED[section]))
    if unknown:
        raise BindingError(f"{section} binding entry has unknown fields: {', '.join(unknown)}")
    missing = [name for name in SECTION_REQUIRED[section] if name not in entry]
    if missing:
        raise BindingError(f"{section} binding entry is missing fields: {', '.join(missing)}")
    for name, value in entry.items():
        if not isinstance(value, str) or not value.strip():
            raise BindingError(f"{section} binding entry field {name} must be a non-empty string")
    if not _PACKAGE_HASH_RE.match(entry["package_hash"]):
        raise BindingError(
            f"{section} binding entry package_hash must look like 'sha256:<64 hex chars>'"
        )
    return _copy(entry)


class BindingStore:
    """CAS-guarded single-pointer store for skill/MCP bindings."""

    def __init__(self, state_dir: Path | str, *, repo_root: Path | str | None = None) -> None:
        repo = Path(repo_root) if repo_root is not None else _default_repo_root()
        try:
            resolved = ensure_outside_repo(Path(state_dir).expanduser(), repo)
        except StatePathError as exc:
            raise BindingError(str(exc)) from exc
        self.state_dir = resolved
        self.dir = resolved / BINDING_DIRNAME
        self.path = self.dir / CURRENT_FILENAME
        self.history_dir = self.dir / HISTORY_DIRNAME
        self.lock_path = self.dir / LOCK_FILENAME

    # -- paths -----------------------------------------------------------

    def history_path(self, revision: int, digest: str) -> Path:
        """Content-addressed history path: ``<revision>-<sha256[:12]>.json``."""
        if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
            raise BindingError(f"history revision must be a positive integer, got {revision!r}")
        if not isinstance(digest, str) or not _SNAPSHOT_HASH_RE.match(digest):
            raise BindingError(f"history digest must be 64 lowercase hex chars, got {digest!r}")
        return self.history_dir / f"{revision}-{digest[:12]}.json"

    def history_files(self, revision: int | None = None) -> list[Path]:
        """All history files, optionally filtered by revision (orphans included)."""
        if not self.history_dir.is_dir():
            return []
        if revision is None:
            return sorted(self.history_dir.glob("[0-9]*-*.json"))
        if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
            raise BindingError(f"history revision must be a positive integer, got {revision!r}")
        return sorted(self.history_dir.glob(f"{revision}-*.json"))

    # -- locking ---------------------------------------------------------

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Hold ``flock(LOCK_EX)`` on ``<state>/skills/.lock`` for the whole commit."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    # -- reads -----------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """Return the current snapshot; a missing file means revision 0.

        A pointer whose history record is missing or hash-mismatched is never
        trusted: :class:`BindingError` with ``history_missing`` is raised.
        """
        if not self.path.exists():
            return _empty_binding()
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BindingError(f"cannot read binding {self.path}: {exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BindingError(f"binding {self.path} is not valid JSON: {exc}") from exc
        snapshot = _validate_snapshot(payload, origin=str(self.path))
        self._verify_history(snapshot)
        return snapshot

    def _verify_history(self, snapshot: dict[str, Any]) -> None:
        revision = snapshot["revision"]
        digest = snapshot[SNAPSHOT_HASH_FIELD]
        if revision == 0:
            if digest is not None:
                raise BindingError("history_missing: revision 0 must not carry snapshot_sha256")
            return
        if not isinstance(digest, str) or not _SNAPSHOT_HASH_RE.match(digest):
            raise BindingError("history_missing: current snapshot_sha256 is missing or malformed")
        path = self.history_path(revision, digest)
        if not path.is_file():
            raise BindingError(f"history_missing: {path}")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BindingError(f"history_missing: cannot read {path}: {exc}") from exc
        if not isinstance(record, dict) or snapshot_sha256(record) != digest:
            raise BindingError(f"history_missing: snapshot hash mismatch for {path}")
        if record != snapshot:
            raise BindingError(f"history_missing: snapshot mismatch for {path}")

    def get_section(self, section: str, key: str) -> dict[str, Any] | None:
        section = _require_section(section)
        key = _require_key(key)
        entry = self.load()[section].get(key)
        return _copy(entry) if entry is not None else None

    # -- writes ----------------------------------------------------------

    def commit(
        self,
        section: str,
        key: str,
        entry: dict[str, Any],
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        """Write a whole new snapshot at ``revision + 1`` if the CAS matches."""
        section = _require_section(section)
        key = _require_key(key)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise BindingError("expected_revision must be an integer")
        normalized = _validate_entry(section, entry)
        entry_id = normalized[SECTION_ID_KEY[section]]
        if entry_id != key:
            raise BindingError(
                f"{section} binding key {key!r} must match {SECTION_ID_KEY[section]} {entry_id!r}"
            )
        with self._locked():
            # Re-read *inside* the lock: a value read before locking cannot be
            # trusted as the CAS base.
            current = self.load()
            actual = current["revision"]
            if actual != expected_revision:
                raise BindingConflict(expected_revision, actual)
            snapshot: dict[str, Any] = {
                "schema_version": BINDING_SCHEMA_VERSION,
                "revision": actual + 1,
                "skills": _copy(current["skills"]),
                "mcp": _copy(current["mcp"]),
                "updated_at": _utc_now(),
            }
            snapshot[section][key] = dict(normalized)
            digest = snapshot_sha256(snapshot)
            snapshot[SNAPSHOT_HASH_FIELD] = digest
            # History first: a crash after this line leaves an orphan file that
            # load() ignores, so retrying at the same revision still works.
            self._write_history(snapshot)
            atomic_write_json(self.path, snapshot)
            return _copy(snapshot)

    def _write_history(self, snapshot: dict[str, Any]) -> None:
        path = self.history_path(snapshot["revision"], snapshot[SNAPSHOT_HASH_FIELD])
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise BindingError(f"history entry {path} is unreadable: {exc}") from exc
            if existing != snapshot:
                raise BindingError(f"history entry {path} is immutable and already differs")
            return
        atomic_write_json(path, snapshot)
