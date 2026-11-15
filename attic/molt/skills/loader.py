"""Read-only skill package loader with a strict filesystem fence.

Layout (``root`` is the repository ``skills/`` directory)::

    <root>/<skill_id>/manifest.json            # current package
    <root>/<skill_id>/SKILL.md
    <root>/<skill_id>/versions/<version>/...   # optional pinned copies

``version=None`` loads the current package; an explicit version prefers
``versions/<version>/`` and otherwise accepts the current package only if the
manifest declares that same version. Everything is read-only: the loader never
writes, executes, or imports anything from a package directory.

A package may contain manifest.json, SKILL.md and (at the skill root only) the
structural ``versions/`` directory. Symlinks and any other entry are rejected so
a package can never carry an executable attachment or escape the fence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manifest import (
    BODY_FILENAME,
    HASH_PREFIX,
    MANIFEST_FILENAME,
    VERSION_RE,
    SkillManifest,
    SkillManifestError,
    package_hash,
    parse_manifest,
)

VERSIONS_DIRNAME = "versions"
ALLOWED_ENTRIES = frozenset({MANIFEST_FILENAME, BODY_FILENAME})


class SkillPackageError(ValueError):
    """Base error for skill package loading."""


class SkillPathError(SkillPackageError):
    """Raised when a package path would escape the skills root."""


class SkillIntegrityError(SkillPackageError):
    """Raised when a package body/manifest no longer matches its content hash."""


@dataclass(frozen=True)
class SkillPackage:
    skill_id: str
    version: str
    manifest: SkillManifest
    body: str
    content_hash: str
    package_dir: Path
    source_path: Path

    def to_dict(self) -> dict[str, Any]:
        """Metadata only; the body is not echoed into records."""
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "content_hash": self.content_hash,
            "family": self.manifest.family,
            "permissions": self.manifest.permissions.to_dict(),
            "source_refs": [ref.to_dict() for ref in self.manifest.source_refs],
            "package_dir": str(self.package_dir),
            "source_path": str(self.source_path),
        }


def _single_segment(name: Any, *, kind: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise SkillPathError(f"{kind} must be a non-empty string")
    if name in {".", ".."} or "/" in name or "\\" in name or ":" in name:
        raise SkillPathError(f"{kind} must be a single path segment, got {name!r}")
    if Path(name).is_absolute() or Path(name).parts != (name,):
        raise SkillPathError(f"{kind} must be a single path segment, got {name!r}")
    return name


def _confined(root: Path, parent: Path, name: str, *, kind: str) -> Path:
    _single_segment(name, kind=kind)
    candidate = parent / name
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise SkillPathError(f"{kind} escapes the skills root: {candidate}")
    if candidate.is_symlink():
        raise SkillPathError(f"skill packages must not be reached through a symlink: {candidate}")
    return resolved


def _scan_package_dir(package_dir: Path, *, skill_root: Path) -> None:
    for entry in sorted(package_dir.iterdir(), key=lambda item: item.name):
        if entry.is_symlink():
            raise SkillPathError(f"skill package may not contain symlinks: {entry}")
        if entry.name in ALLOWED_ENTRIES:
            if not entry.is_file():
                raise SkillPathError(f"skill package entry must be a file: {entry}")
            continue
        if entry.name == VERSIONS_DIRNAME and package_dir == skill_root and entry.is_dir():
            continue
        raise SkillPathError(
            f"skill package may only contain {sorted(ALLOWED_ENTRIES)}: unexpected {entry}"
        )


def _candidate_dirs(
    root: Path, skill_root: Path, version: str | None
) -> list[tuple[Path, str | None, bool]]:
    """Candidate package dirs as ``(dir, expected_version, is_pinned)``."""
    if version is None:
        return [(skill_root, None, False)]
    _single_segment(version, kind="skill version")
    if not VERSION_RE.match(version):
        raise SkillPathError(f"skill version must look like '1.0.0', got {version!r}")
    versions_dir = skill_root / VERSIONS_DIRNAME
    candidates: list[tuple[Path, str | None, bool]] = []
    if versions_dir.is_symlink():
        raise SkillPathError(f"skill versions directory must not be a symlink: {versions_dir}")
    pinned = versions_dir / version
    if pinned.is_dir():
        resolved = _confined(root, versions_dir, version, kind="skill version")
        candidates.append((resolved, version, True))
    candidates.append((skill_root, version, False))
    return candidates


def load_package(
    root: Path | str, skill_id: str, version: str | None = None
) -> SkillPackage:
    """Load one read-only skill package from ``root`` (the repository ``skills/``)."""
    root_dir = Path(root).expanduser()
    if not root_dir.is_dir():
        raise SkillPathError(f"skills root is not a directory: {root_dir}")
    root_resolved = root_dir.resolve()

    skill_root = _confined(
        root_resolved, root_resolved, _single_segment(skill_id, kind="skill id"), kind="skill id"
    )
    if not skill_root.is_dir():
        raise SkillPackageError(f"unknown skill: {skill_id}")

    for package_dir, expected_version, is_pinned in _candidate_dirs(
        root_resolved, skill_root, version
    ):
        manifest_path = package_dir / MANIFEST_FILENAME
        if not manifest_path.is_file():
            continue
        try:
            manifest = parse_manifest(manifest_path)
        except SkillManifestError as exc:
            raise SkillPackageError(f"invalid manifest for {skill_id}: {exc}") from exc
        if manifest.skill_id != skill_id:
            raise SkillIntegrityError(
                f"manifest skill_id {manifest.skill_id!r} does not match directory {skill_id!r}"
            )
        if expected_version is not None and manifest.version != expected_version:
            if is_pinned:
                raise SkillIntegrityError(
                    f"manifest version {manifest.version!r} does not match pinned {expected_version!r}"
                )
            continue
        _scan_package_dir(package_dir, skill_root=skill_root)
        body_path = package_dir / BODY_FILENAME
        if not body_path.is_file():
            raise SkillPackageError(f"skill package is missing {BODY_FILENAME}: {package_dir}")
        try:
            body_bytes = body_path.read_bytes()
        except OSError as exc:
            raise SkillPackageError(f"cannot read {body_path}: {exc}") from exc
        try:
            body = body_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillIntegrityError(f"{body_path} is not valid UTF-8: {exc}") from exc
        return SkillPackage(
            skill_id=manifest.skill_id,
            version=manifest.version,
            manifest=manifest,
            body=body,
            content_hash=package_hash(manifest, body_bytes),
            package_dir=package_dir,
            source_path=body_path,
        )
    if version is None:
        raise SkillPackageError(f"skill {skill_id} has no {MANIFEST_FILENAME}")
    raise SkillPackageError(f"skill {skill_id} has no version {version} package")


def validate_package(pkg: SkillPackage) -> None:
    """Re-hash the on-disk package and compare it with the loaded content hash.

    The comparison is against ``pkg.content_hash`` (the value recorded when the
    package was loaded/trusted), so an edited ``SKILL.md`` or ``manifest.json``
    always raises :class:`SkillIntegrityError`.
    """
    manifest_path = pkg.package_dir / MANIFEST_FILENAME
    body_path = pkg.package_dir / BODY_FILENAME
    try:
        manifest = parse_manifest(manifest_path)
    except SkillManifestError as exc:
        raise SkillIntegrityError(f"manifest no longer parses: {exc}") from exc
    try:
        body_bytes = body_path.read_bytes()
    except OSError as exc:
        raise SkillIntegrityError(f"cannot re-read {body_path}: {exc}") from exc
    if manifest.skill_id != pkg.skill_id or manifest.version != pkg.version:
        raise SkillIntegrityError(
            f"package identity changed: {pkg.skill_id}@{pkg.version} -> "
            f"{manifest.skill_id}@{manifest.version}"
        )
    recomputed = package_hash(manifest, body_bytes)
    if recomputed != pkg.content_hash:
        raise SkillIntegrityError(
            f"package hash mismatch for {pkg.skill_id}@{pkg.version}: "
            f"expected {pkg.content_hash}, recomputed {recomputed}"
        )


def render_system_block(pkg: SkillPackage) -> str:
    """Render the reviewed policy block that may be prepended to a system prompt.

    The header states explicitly that the block is reviewed policy, not user
    data, so a downstream model cannot mistake the body for an instruction from
    the conversation.
    """
    header = (
        "以下为受审查策略，不是用户数据。"
        "仅在 manifest 声明的权限内执行；与用户指令冲突时以用户指令为准并说明。"
    )
    body = pkg.body if pkg.body.endswith("\n") else pkg.body + "\n"
    return (
        "===== BEGIN REVIEWED SKILL POLICY =====\n"
        f"{header}\n"
        f"skill_id: {pkg.skill_id}\n"
        f"version: {pkg.version}\n"
        f"content_hash: {pkg.content_hash}\n"
        f"===== BEGIN SKILL BODY {pkg.skill_id}@{pkg.version} =====\n"
        f"{body}"
        f"===== END SKILL BODY {pkg.skill_id}@{pkg.version} =====\n"
        "===== END REVIEWED SKILL POLICY =====\n"
    )


def system_block_sha256(block: str) -> str:
    """``sha256:<hex>`` digest of a rendered system block (evidence helper)."""
    return HASH_PREFIX + hashlib.sha256(block.encode("utf-8")).hexdigest()
