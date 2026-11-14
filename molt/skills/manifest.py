"""Skill manifest parsing and package hashing (stdlib only).

A skill package is a directory under the repository ``skills/`` root that holds
exactly two files: ``manifest.json`` (metadata, permissions, provenance) and
``SKILL.md`` (the reviewed policy body). Neither file is executable and the
loader refuses anything else.

Hash rule (``package_hash``)::

    sha256(canonical_json(manifest.to_dict()).encode("utf-8") + b"\n" + body)

with canonical JSON = ``json.dumps(sort_keys=True, separators=(",", ":"),
ensure_ascii=False, allow_nan=False)``. The manifest never stores its own hash:
the hash travels in loader/binding records, so a manifest edit always changes it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"
MANIFEST_FILENAME = "manifest.json"
BODY_FILENAME = "SKILL.md"
HASH_PREFIX = "sha256:"

SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
# Tool permissions are explicit tokens: local tool names (``experiments_get``)
# or namespaced MCP tools (``mcp:project_kb:search_docs``). No wildcards.
TOOL_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_:-]{0,127}$")
SOURCE_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
FILESYSTEM_SCOPES = ("none", "read_only", "workspace")

MAX_KEYWORDS = 32
MAX_SOURCE_REFS = 16

MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "skill_id",
        "version",
        "family",
        "title",
        "description",
        "triggers",
        "permissions",
        "source_refs",
    }
)
# A manifest must not carry its own hash; the hash is a record-level fact.
RESERVED_HASH_FIELDS = ("package_hash", "content_hash", "skill_hash", "manifest_hash")


class SkillManifestError(ValueError):
    """Raised when a skill manifest is malformed, unknown-keyed, or unreadable."""


def canonical_json(payload: Any) -> str:
    """Canonical JSON shared by every skill-layer hash."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@dataclass(frozen=True)
class TriggerSpec:
    keywords: tuple[str, ...]
    task_families: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "keywords": list(self.keywords),
            "task_families": list(self.task_families),
        }


@dataclass(frozen=True)
class PermissionSpec:
    tools: tuple[str, ...]
    network: bool
    filesystem: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tools": list(self.tools),
            "network": self.network,
            "filesystem": self.filesystem,
        }


@dataclass(frozen=True)
class SourceRef:
    source_id: str
    revision: str
    locator: str
    available_content: str
    source_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_id": self.source_id,
            "revision": self.revision,
            "locator": self.locator,
            "available_content": self.available_content,
        }
        if self.source_hash is not None:
            payload["source_hash"] = self.source_hash
        return payload


@dataclass(frozen=True)
class SkillManifest:
    schema_version: str
    skill_id: str
    version: str
    family: str
    title: str
    description: str
    triggers: TriggerSpec
    permissions: PermissionSpec
    source_refs: tuple[SourceRef, ...]

    def to_dict(self) -> dict[str, Any]:
        """Manifest as canonical-JSON input. Never contains a self hash."""
        return {
            "schema_version": self.schema_version,
            "skill_id": self.skill_id,
            "version": self.version,
            "family": self.family,
            "title": self.title,
            "description": self.description,
            "triggers": self.triggers.to_dict(),
            "permissions": self.permissions.to_dict(),
            "source_refs": [ref.to_dict() for ref in self.source_refs],
        }


def _as_mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SkillManifestError(f"{where} must be a JSON object")
    for key in value:
        if not isinstance(key, str):
            raise SkillManifestError(f"{where} keys must be strings")
    return value


def _check_fields(
    mapping: dict[str, Any],
    *,
    allowed: frozenset[str],
    required: tuple[str, ...],
    where: str,
) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise SkillManifestError(f"{where} has unknown fields: {', '.join(unknown)}")
    missing = [name for name in required if name not in mapping]
    if missing:
        raise SkillManifestError(f"{where} is missing required fields: {', '.join(missing)}")


def _str_field(
    mapping: dict[str, Any],
    key: str,
    where: str,
    *,
    pattern: re.Pattern[str] | None = None,
) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise SkillManifestError(f"{where}.{key} must be a string")
    if not value.strip():
        raise SkillManifestError(f"{where}.{key} must be a non-empty string")
    if pattern is not None and not pattern.match(value):
        raise SkillManifestError(f"{where}.{key} has an invalid value: {value!r}")
    return value


def _str_list(value: Any, where: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SkillManifestError(f"{where} must be a JSON array")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise SkillManifestError(f"{where}[{index}] must be a non-empty string")
        items.append(item)
    if not allow_empty and not items:
        raise SkillManifestError(f"{where} must not be empty")
    if len(set(items)) != len(items):
        raise SkillManifestError(f"{where} must not contain duplicates")
    return tuple(items)


def _parse_triggers(value: Any, where: str) -> TriggerSpec:
    mapping = _as_mapping(value, where)
    _check_fields(
        mapping,
        allowed=frozenset({"keywords", "task_families"}),
        required=("keywords", "task_families"),
        where=where,
    )
    keywords = _str_list(mapping["keywords"], f"{where}.keywords", allow_empty=False)
    if len(keywords) > MAX_KEYWORDS:
        raise SkillManifestError(f"{where}.keywords accepts at most {MAX_KEYWORDS} entries")
    families = _str_list(mapping["task_families"], f"{where}.task_families")
    if not families:
        raise SkillManifestError(f"{where}.task_families must not be empty")
    return TriggerSpec(keywords=keywords, task_families=families)


def _parse_permissions(value: Any, where: str) -> PermissionSpec:
    mapping = _as_mapping(value, where)
    _check_fields(
        mapping,
        allowed=frozenset({"tools", "network", "filesystem"}),
        required=("tools", "network", "filesystem"),
        where=where,
    )
    tools = _str_list(mapping["tools"], f"{where}.tools", allow_empty=False)
    for index, tool in enumerate(tools):
        if not TOOL_TOKEN_RE.match(tool):
            raise SkillManifestError(f"{where}.tools[{index}] has an invalid value: {tool!r}")
    network = mapping["network"]
    if not isinstance(network, bool):
        raise SkillManifestError(f"{where}.network must be a boolean")
    filesystem = _str_field(mapping, "filesystem", where)
    if filesystem not in FILESYSTEM_SCOPES:
        raise SkillManifestError(
            f"{where}.filesystem must be one of {list(FILESYSTEM_SCOPES)}, got {filesystem!r}"
        )
    return PermissionSpec(tools=tools, network=network, filesystem=filesystem)


def _parse_source_refs(value: Any, where: str) -> tuple[SourceRef, ...]:
    if not isinstance(value, list) or not value:
        raise SkillManifestError(f"{where} must be a non-empty JSON array")
    if len(value) > MAX_SOURCE_REFS:
        raise SkillManifestError(f"{where} accepts at most {MAX_SOURCE_REFS} entries")
    refs: list[SourceRef] = []
    for index, item in enumerate(value):
        item_where = f"{where}[{index}]"
        mapping = _as_mapping(item, item_where)
        _check_fields(
            mapping,
            allowed=frozenset(
                {"source_id", "revision", "locator", "available_content", "source_hash"}
            ),
            required=("source_id", "revision", "locator", "available_content"),
            where=item_where,
        )
        source_hash: str | None = None
        if "source_hash" in mapping:
            source_hash = _str_field(mapping, "source_hash", item_where)
            if not SOURCE_HASH_RE.match(source_hash):
                raise SkillManifestError(
                    f"{item_where}.source_hash must look like 'sha256:<64 hex chars>'"
                )
        refs.append(
            SourceRef(
                source_id=_str_field(mapping, "source_id", item_where),
                revision=_str_field(mapping, "revision", item_where),
                locator=_str_field(mapping, "locator", item_where),
                available_content=_str_field(mapping, "available_content", item_where),
                source_hash=source_hash,
            )
        )
    return tuple(refs)


def manifest_from_dict(payload: Any, *, origin: str = "manifest") -> SkillManifest:
    """Validate a decoded manifest document and return the typed view."""
    mapping = _as_mapping(payload, origin)
    reserved = [name for name in RESERVED_HASH_FIELDS if name in mapping]
    if reserved:
        raise SkillManifestError(
            f"{origin} must not store its own hash (found {', '.join(reserved)})"
        )
    _check_fields(
        mapping,
        allowed=MANIFEST_FIELDS,
        required=tuple(sorted(MANIFEST_FIELDS)),
        where=origin,
    )
    schema_version = _str_field(mapping, "schema_version", origin)
    if schema_version != SCHEMA_VERSION:
        raise SkillManifestError(
            f"{origin}.schema_version must be {SCHEMA_VERSION}, got {schema_version!r}"
        )
    skill_id = _str_field(mapping, "skill_id", origin, pattern=SKILL_ID_RE)
    version = _str_field(mapping, "version", origin, pattern=VERSION_RE)
    return SkillManifest(
        schema_version=schema_version,
        skill_id=skill_id,
        version=version,
        family=_str_field(mapping, "family", origin),
        title=_str_field(mapping, "title", origin),
        description=_str_field(mapping, "description", origin),
        triggers=_parse_triggers(mapping["triggers"], f"{origin}.triggers"),
        permissions=_parse_permissions(mapping["permissions"], f"{origin}.permissions"),
        source_refs=_parse_source_refs(mapping["source_refs"], f"{origin}.source_refs"),
    )


def parse_manifest(path: Path | str) -> SkillManifest:
    """Read and validate one ``manifest.json``; unknown fields are rejected."""
    manifest_path = Path(path)
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillManifestError(f"cannot read manifest {manifest_path}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SkillManifestError(f"manifest {manifest_path} is not valid JSON: {exc}") from exc
    return manifest_from_dict(payload, origin=str(manifest_path))


def package_hash(manifest: SkillManifest, body_bytes: bytes) -> str:
    """``sha256(canonical_json(manifest) + b"\\n" + body)`` with the ``sha256:`` prefix."""
    if not isinstance(body_bytes, (bytes, bytearray)):
        raise TypeError("body_bytes must be bytes")
    manifest_bytes = canonical_json(manifest.to_dict()).encode("utf-8")
    digest = hashlib.sha256(manifest_bytes + b"\n" + bytes(body_bytes)).hexdigest()
    return HASH_PREFIX + digest
