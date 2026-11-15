"""Translate an allowlisted MCP server into an ``UpperAsset`` (kind=mcp_profile).

The profile content only carries the non-sensitive facts an approval decision
needs: server id, tool names, timeout, output budget, and two hashes that bind
the asset to the actual artifacts:

* ``spec_hash`` — the allowlist entry (command/module/cwd/tools/timeout/budget);
* ``artifact_hash`` — the spec plus ``<module>.py`` plus every corpus file,
  in sorted order.

Command lines, env and filesystem paths never enter the asset, so the public
projection cannot leak host layout. ``verify_artifact_hash`` recomputes both
before execution and fails closed.

``origin``: ``molt.upper.types.ASSET_ORIGINS`` has no ``"manual"`` value
(only ``seed_import`` / ``synthetic`` / ``real_run``), so an operator-authored
profile is stored as ``synthetic`` with ``eval_state="not_evaluated"``; the
honest label lives in the sidecar returned by :func:`profile_sidecar`
(``declared_origin="manual"``, ``evidence_kind="manual"``). ``real_run`` is
only accepted with explicit ``evidence_refs``.

Nothing here writes to the registry; ``assert_profile_executable`` is a
read-only guard for the integration slice that later wires activation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from molt.mcp.allowlist import McpAllowlist, McpServerSpec, default_allowlist_path
from molt.mcp.jsonrpc import McpError, McpIntegrityError
from molt.upper.types import UpperAsset, canonical_json

PROFILE_KIND = "mcp_profile"
PROFILE_PERMISSION_SCOPE = "read_only:project_kb"
PROFILE_APPROVAL_LEVEL = "strict"
PROFILE_VERSION = "1.0.0"
PROFILE_ORIGIN = "synthetic"
ARTIFACT_HASH_PREFIX = "sha256:"
ARTIFACT_SCHEME = "mcp-artifact-v1"


class McpProfileError(McpError):
    """The asset is not a well-formed / executable MCP profile."""


def spec_material(spec: McpServerSpec) -> dict[str, Any]:
    """The allowlist facts a profile hash commits to (no env, no host paths)."""

    return {
        "server_id": spec.server_id,
        "command": list(spec.command),
        "module": spec.module,
        "cwd": spec.cwd,
        "tools": list(spec.tools),
        "timeout_s": spec.timeout_s,
        "max_output_bytes": spec.max_output_bytes,
    }


def compute_spec_hash(spec: McpServerSpec) -> str:
    """Hash of the allowlist entry alone."""

    return ARTIFACT_HASH_PREFIX + hashlib.sha256(
        canonical_json(spec_material(spec)).encode("utf-8")
    ).hexdigest()


def compute_artifact_hash(repo_root: str | Path, spec: McpServerSpec) -> str:
    """Hash of the spec + server module source + every corpus file (sorted)."""

    root = Path(repo_root).resolve()
    digest = hashlib.sha256()
    digest.update(ARTIFACT_SCHEME.encode("utf-8") + b"\0")
    digest.update(canonical_json(spec_material(spec)).encode("utf-8"))

    server = root.joinpath(*spec.module.split(".")).with_suffix(".py")
    if not server.is_file():
        raise McpIntegrityError("artifact_missing", spec.module)
    digest.update(b"\0server\0")
    digest.update(server.relative_to(root).as_posix().encode("utf-8"))
    digest.update(b"\0")
    digest.update(server.read_bytes())

    if spec.corpus:
        corpus = root / spec.corpus
        if not corpus.is_dir():
            raise McpIntegrityError("artifact_missing", spec.corpus)
        for path in sorted(item for item in corpus.rglob("*") if item.is_file()):
            digest.update(b"\0corpus\0")
            digest.update(path.relative_to(corpus).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
    return ARTIFACT_HASH_PREFIX + digest.hexdigest()


def build_profile_asset(
    spec: McpServerSpec,
    *,
    created_by: str = "operator",
    origin: str = PROFILE_ORIGIN,
    repo_root: str | Path | None = None,
    evidence_refs: tuple[str, ...] = (),
) -> UpperAsset:
    """Build a draft ``mcp_profile`` asset for one allowlisted server.

    ``repo_root`` is required for a complete asset: it is what makes
    ``artifact_hash`` / ``spec_hash`` available. Without it the asset is
    structurally valid but ``verify_artifact_hash`` will refuse it.
    """

    if origin == "manual":
        raise McpProfileError(
            "origin_invalid",
            "ASSET_ORIGINS has no 'manual'; use 'synthetic' + profile_sidecar()",
        )
    if origin == "real_run" and not evidence_refs:
        raise McpProfileError("real_run_requires_evidence", spec.server_id)

    content: dict[str, Any] = {
        "server_id": spec.server_id,
        "tools": list(spec.tools),
        "timeout_s": spec.timeout_s,
        "max_output_bytes": spec.max_output_bytes,
    }
    if repo_root is not None:
        root = Path(repo_root).resolve()
        content["artifact_hash"] = compute_artifact_hash(root, spec)
        content["spec_hash"] = compute_spec_hash(spec)
    _assert_no_host_paths(content)

    return UpperAsset(
        asset_id=f"mcp-profile-{spec.server_id}",
        kind=PROFILE_KIND,
        name=f"MCP profile: {spec.server_id}",
        version=PROFILE_VERSION,
        source="molt-mcp-allowlist",
        family="mcp",
        created_by=created_by,
        evidence_refs=tuple(evidence_refs),
        status="draft",
        content=content,
        implementation_status="implemented",
        permission_scope=PROFILE_PERMISSION_SCOPE,
        approval_level=PROFILE_APPROVAL_LEVEL,
        origin=origin,
    )


def profile_sidecar(
    spec: McpServerSpec,
    *,
    created_by: str = "operator",
    visibility: str = "internal",
) -> dict[str, Any]:
    """Non-asset metadata to be written next to the profile (authorship, visibility)."""

    return {
        "server_id": spec.server_id,
        "authorship": "operator",
        "created_by": created_by,
        "visibility": visibility,
        "declared_origin": "manual",
        "evidence_kind": "manual",
        "asset_origin_enum": PROFILE_ORIGIN,
    }


def verify_artifact_hash(
    asset: UpperAsset,
    repo_root: str | Path,
    *,
    spec: McpServerSpec | None = None,
) -> None:
    """Recompute both hashes and fail closed on any drift."""

    if not isinstance(asset, UpperAsset) or asset.kind != PROFILE_KIND:
        raise McpProfileError("profile_kind_invalid", str(getattr(asset, "kind", "?")))
    content = asset.content if isinstance(asset.content, dict) else {}
    artifact_hash = content.get("artifact_hash")
    spec_hash = content.get("spec_hash")
    if not isinstance(artifact_hash, str) or not isinstance(spec_hash, str):
        raise McpIntegrityError("artifact_hash_missing", asset.asset_id)

    if spec is None:
        server_id = content.get("server_id")
        if not isinstance(server_id, str) or not server_id:
            raise McpProfileError("profile_server_id_missing", asset.asset_id)
        allowlist = McpAllowlist.load(default_allowlist_path(repo_root), repo_root=repo_root)
        spec = allowlist.get(server_id)

    if compute_spec_hash(spec) != spec_hash:
        raise McpIntegrityError("spec_changed", asset.asset_id)
    if compute_artifact_hash(repo_root, spec) != artifact_hash:
        raise McpIntegrityError("artifact_changed", asset.asset_id)


def assert_profile_executable(asset: UpperAsset) -> None:
    """Raise unless ``asset`` is an active, strict profile with no pending change."""

    if not isinstance(asset, UpperAsset):
        raise McpProfileError("profile_invalid", type(asset).__name__)
    if asset.kind != PROFILE_KIND:
        raise McpProfileError("profile_kind_invalid", str(asset.kind))
    if asset.status != "active":
        raise McpProfileError("profile_not_active", str(asset.status))
    if asset.approval_level != PROFILE_APPROVAL_LEVEL:
        raise McpProfileError("profile_not_strict", str(asset.approval_level))
    if asset.pending_permission:
        raise McpProfileError("permission_change_pending", str(asset.asset_id))
    if asset.implementation_status != "implemented":
        raise McpProfileError("profile_not_implemented", str(asset.implementation_status))


def _assert_no_host_paths(content: dict[str, Any]) -> None:
    def walk(value: Any) -> None:
        if isinstance(value, str):
            if value.startswith(("/", "~")) or "://" in value or value.startswith("file:"):
                raise McpProfileError("profile_content_has_host_path", value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(content)
