"""Skill runtime (slice A): manifests, packages, selection, binding, evidence.

Slice A is read-only with respect to turns and tools: nothing here is wired into
``molt/agent/turn.py`` or ``molt/agent/tools.py`` yet. The surface is deliberately
small and stdlib-only so it can be reviewed before integration.
"""

from __future__ import annotations

from .binding import (
    BINDING_SCHEMA_VERSION,
    SECTIONS,
    BindingConflict,
    BindingError,
    BindingStore,
    snapshot_sha256,
)
from .evaluate import (
    EVIDENCE_KIND,
    CaptureAdapter,
    SkillEvidenceError,
    context_loaded_evidence,
    run_context_check,
)
from .loader import (
    SkillIntegrityError,
    SkillPackage,
    SkillPackageError,
    SkillPathError,
    load_package,
    render_system_block,
    system_block_sha256,
    validate_package,
)
from .manifest import (
    BODY_FILENAME,
    MANIFEST_FILENAME,
    SCHEMA_VERSION,
    SkillManifest,
    SkillManifestError,
    package_hash,
    parse_manifest,
)
from .selector import SELECTION_MODE, SkillSelection, select

__all__ = [
    "BINDING_SCHEMA_VERSION",
    "BODY_FILENAME",
    "EVIDENCE_KIND",
    "MANIFEST_FILENAME",
    "SCHEMA_VERSION",
    "SELECTION_MODE",
    "SECTIONS",
    "BindingConflict",
    "BindingError",
    "BindingStore",
    "CaptureAdapter",
    "SkillEvidenceError",
    "SkillIntegrityError",
    "SkillManifest",
    "SkillManifestError",
    "SkillPackage",
    "SkillPackageError",
    "SkillPathError",
    "SkillSelection",
    "context_loaded_evidence",
    "load_package",
    "package_hash",
    "parse_manifest",
    "render_system_block",
    "run_context_check",
    "select",
    "snapshot_sha256",
    "system_block_sha256",
    "validate_package",
]
