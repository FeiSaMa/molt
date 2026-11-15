"""Repository-external upper asset registry with lifecycle and approval guards.

The registry stores ``registry.json`` under an operator-provided state directory
that must be outside the repository tree. Every mutation is persisted atomically.
Stubs are never executed: ``execute_stub`` only reports the non-execution.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .storage import atomic_write_json, ensure_outside_repo
from .types import UpperAsset, utc_now

REGISTRY_SCHEMA_VERSION = "1.0.0"


class UpperRegistryError(RuntimeError):
    """Raised for invalid lifecycle transitions or registry misuse."""



def _merged_mutation(method):
    """Run a registry mutation under the cross-process write lock, after reload."""

    def wrapper(self, *args, **kwargs):
        with self._write_locked():
            self.refresh()
            return method(self, *args, **kwargs)

    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper

class UpperRegistry:
    def __init__(self, state_dir: Path | str, *, repo_root: Path | str | None = None) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        if repo_root is not None:
            ensure_outside_repo(self.state_dir, repo_root)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "registry.json"
        self._assets: dict[str, UpperAsset] = {}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != REGISTRY_SCHEMA_VERSION:
            raise UpperRegistryError(
                f"unsupported registry schema: {payload.get('schema_version')!r}"
            )
        for item in payload.get("assets", []):
            asset = UpperAsset.from_dict(item)
            if not asset.verify_hash():
                raise UpperRegistryError(
                    f"asset hash mismatch for {asset.asset_id}: content changed on disk"
                )
            if asset.asset_id in self._assets:
                raise UpperRegistryError(f"duplicate asset_id: {asset.asset_id}")
            self._assets[asset.asset_id] = asset

    def _save(self) -> None:
        payload = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "updated_at": utc_now(),
            "assets": [
                asset.to_dict()
                for asset in sorted(self._assets.values(), key=lambda item: item.asset_id)
            ],
        }
        atomic_write_json(self.path, payload)

    @contextmanager
    def _write_locked(self):
        """Serialize registry mutations across threads and processes.

        ``registry.json`` is a whole-file snapshot, so a stale in-memory copy
        must never be written back over assets another writer just added.
        """

        self.state_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.state_dir / ".registry.lock"
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def refresh(self) -> None:
        """Re-read the on-disk snapshot before a read-modify-write cycle."""

        self._assets = {}
        if self.path.exists():
            self._load()

    def _require(self, asset_id: str) -> UpperAsset:
        try:
            return self._assets[asset_id]
        except KeyError:
            raise UpperRegistryError(f"unknown asset_id: {asset_id!r}") from None

    @staticmethod
    def _require_actor(actor: str | None, field_name: str) -> str:
        if actor is None or not actor.strip():
            raise UpperRegistryError(f"{field_name} must be a non-empty identity")
        return actor.strip()

    @_merged_mutation
    def register(self, asset: UpperAsset, *, replace: bool = False) -> UpperAsset:
        if asset.asset_id in self._assets:
            existing = self._assets[asset.asset_id]
            if not replace:
                raise UpperRegistryError(f"asset already registered: {asset.asset_id}")
            if existing.status != "draft":
                raise UpperRegistryError(
                    f"can only replace a draft asset, got {existing.status}"
                )
        if asset.status != "draft":
            raise UpperRegistryError("new assets must be registered as draft")
        asset.updated_at = utc_now()
        self._assets[asset.asset_id] = asset
        self._save()
        return asset

    @_merged_mutation
    def annotate(self, asset_id: str, patch: Mapping[str, Any]) -> UpperAsset:
        """Merge non-executable metadata into an asset's content.

        Used by automatic evolution to attach ``source_session``/``evidence_id``
        after the lifecycle wrote the package. Package/artifact hashes are not
        changed, and an identity conflict is still detected by the caller.
        """

        asset = self._require(asset_id)
        if not isinstance(patch, Mapping):
            raise UpperRegistryError("annotation patch must be a mapping")
        asset.content.update({str(key): value for key, value in patch.items()})
        asset.hash = asset.compute_content_hash()
        asset.content_hash = asset.hash
        asset.updated_at = utc_now()
        self._save()
        return asset

    def get(self, asset_id: str) -> UpperAsset:
        return self._require(asset_id)

    def list_assets(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        family: str | None = None,
    ) -> list[UpperAsset]:
        assets = [
            asset
            for asset in self._assets.values()
            if (kind is None or asset.kind == kind)
            and (status is None or asset.status == status)
            and (family is None or asset.family == family)
        ]
        return sorted(assets, key=lambda item: item.asset_id)

    @_merged_mutation
    def mark_evaluated(self, asset_id: str) -> UpperAsset:
        asset = self._require(asset_id)
        if asset.status != "draft":
            raise UpperRegistryError(
                f"asset {asset_id} must be draft to become evaluated, got {asset.status}"
            )
        asset.status = "evaluated"
        asset.updated_at = utc_now()
        self._save()
        return asset

    @_merged_mutation
    def approve(
        self,
        asset_id: str,
        *,
        approved_by: str,
        approval_level: str | None = None,
    ) -> UpperAsset:
        asset = self._require(asset_id)
        if asset.status != "evaluated":
            raise UpperRegistryError(
                f"asset {asset_id} must be evaluated to approve, got {asset.status}"
            )
        approver = self._require_actor(approved_by, "approved_by")
        if approval_level is not None and approval_level not in {"standard", "strict"}:
            raise UpperRegistryError(f"unknown approval_level: {approval_level!r}")
        if asset.kind == "mcp_profile":
            if asset.approval_level != "strict" or approval_level not in {None, "strict"}:
                raise UpperRegistryError("mcp_profile approval requires approval_level='strict'")
        elif approval_level is not None:
            asset.approval_level = approval_level
        asset.status = "approved"
        asset.approved_by = approver
        asset.approved_at = utc_now()
        asset.status_reason = None
        asset.updated_at = utc_now()
        self._save()
        return asset

    @_merged_mutation
    def reject(self, asset_id: str, *, rejected_by: str, reason: str) -> UpperAsset:
        asset = self._require(asset_id)
        if asset.status != "evaluated":
            raise UpperRegistryError(
                f"asset {asset_id} must be evaluated to reject, got {asset.status}"
            )
        actor = self._require_actor(rejected_by, "rejected_by")
        if not reason.strip():
            raise UpperRegistryError("reject reason is required")
        asset.status = "rejected"
        asset.approved_by = None
        asset.approved_at = None
        asset.pending_permission = None
        asset.status_reason = f"rejected by {actor}: {reason.strip()}"
        asset.updated_at = utc_now()
        self._save()
        return asset

    @_merged_mutation
    def activate(self, asset_id: str) -> UpperAsset:
        asset = self._require(asset_id)
        if asset.status != "approved":
            raise UpperRegistryError(
                f"asset {asset_id} must be approved to activate, got {asset.status}"
            )
        if not asset.approved_by:
            raise UpperRegistryError("cannot activate without approved_by")
        if asset.kind == "mcp_profile" and asset.approval_level != "strict":
            raise UpperRegistryError("cannot activate mcp_profile without strict approval")
        if asset.pending_permission is not None:
            raise UpperRegistryError("cannot activate while a permission change is pending")
        asset.status = "active"
        asset.status_reason = None
        asset.updated_at = utc_now()
        self._save()
        return asset

    @_merged_mutation
    def rollback(self, asset_id: str, *, reason: str, rolled_back_by: str) -> UpperAsset:
        asset = self._require(asset_id)
        if asset.status != "active":
            raise UpperRegistryError(
                f"asset {asset_id} must be active to roll back, got {asset.status}"
            )
        actor = self._require_actor(rolled_back_by, "rolled_back_by")
        if not reason.strip():
            raise UpperRegistryError("rollback reason is required")
        asset.status = "rolled_back"
        asset.pending_permission = None
        asset.status_reason = f"rolled back by {actor}: {reason.strip()}"
        asset.updated_at = utc_now()
        self._save()
        return asset

    @_merged_mutation
    def restore(self, asset_id: str, *, reason: str, restored_by: str) -> UpperAsset:
        asset = self._require(asset_id)
        if asset.status != "rolled_back":
            raise UpperRegistryError(
                f"asset {asset_id} must be rolled_back to restore, got {asset.status}"
            )
        actor = self._require_actor(restored_by, "restored_by")
        if not reason.strip():
            raise UpperRegistryError("restore reason is required")
        if not asset.approved_by:
            raise UpperRegistryError("cannot restore without approved_by")
        asset.status = "active"
        asset.pending_permission = None
        asset.status_reason = f"restored by {actor}: {reason.strip()}"
        asset.updated_at = utc_now()
        self._save()
        return asset

    @_merged_mutation
    def request_permission_change(
        self,
        asset_id: str,
        *,
        scope: str,
        requested_by: str,
        reason: str,
    ) -> UpperAsset:
        asset = self._require(asset_id)
        if asset.kind != "mcp_profile":
            raise UpperRegistryError("permission changes only apply to mcp_profile assets")
        if asset.status in {"rejected", "rolled_back"}:
            raise UpperRegistryError(f"cannot change permissions for {asset.status} asset")
        requester = self._require_actor(requested_by, "requested_by")
        if not scope.strip():
            raise UpperRegistryError("permission scope must be non-empty")
        if scope == asset.permission_scope:
            raise UpperRegistryError("permission scope is unchanged")
        if not reason.strip():
            raise UpperRegistryError("permission change reason is required")
        asset.pending_permission = {
            "requested_scope": scope.strip(),
            "requested_by": requester,
            "requested_at": utc_now(),
            "reason": reason.strip(),
            "approval_level": "strict",
            "approved": False,
        }
        asset.updated_at = utc_now()
        self._save()
        return asset

    @_merged_mutation
    def approve_permission_change(self, asset_id: str, *, approved_by: str) -> UpperAsset:
        asset = self._require(asset_id)
        pending = asset.pending_permission
        if not pending:
            raise UpperRegistryError("no pending permission change")
        approver = self._require_actor(approved_by, "approved_by")
        if approver == pending.get("requested_by"):
            raise UpperRegistryError("permission change requires an independent approver")
        if asset.approval_level != "strict" or pending.get("approval_level") != "strict":
            raise UpperRegistryError("permission change requires strict approval")
        asset.permission_scope = str(pending["requested_scope"])
        asset.permission_approved_by = approver
        asset.permission_approved_at = utc_now()
        asset.pending_permission = None
        asset.updated_at = utc_now()
        self._save()
        return asset

    def execute_stub(self, asset_id: str) -> dict[str, Any]:
        """Report that a B1 asset is a stub; never import or invoke the asset."""
        asset = self._require(asset_id)
        if asset.implementation_status != "stub":
            raise UpperRegistryError("B1 does not execute implemented assets")
        return {
            "asset_id": asset.asset_id,
            "kind": asset.kind,
            "executed": False,
            "external_calls": [],
            "reason": "stub_not_executed",
            "implementation_status": asset.implementation_status,
        }

    def public_state(self) -> dict[str, Any]:
        assets = self.list_assets()
        return {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "state_scope": "external",
            "assets": [asset.to_public_dict() for asset in assets],
            "active": [asset.asset_id for asset in assets if asset.status == "active"],
            "rolled_back": [
                asset.asset_id for asset in assets if asset.status == "rolled_back"
            ],
            "pending_permissions": [
                {"asset_id": asset.asset_id, **asset.pending_permission}
                for asset in assets
                if asset.pending_permission is not None
            ],
        }
