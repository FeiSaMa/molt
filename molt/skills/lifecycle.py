"""Skill asset lifecycle on top of ``UpperRegistry`` + ``BindingStore``.

Atomicity model (decided by the main session, not by this module):

* The **single linearization point** is ``BindingStore``'s ``current.json``
  pointer. ``registry`` active only means "this version may be used"; it is not
  the execution pointer, and this module never claims that ``registry.json`` and
  the binding snapshot are updated atomically across files.
* Activation order is fixed: validate the source package → publish it atomically
  under ``<state>/skills/packages/<asset_id>/<skill_id>/`` → re-validate the
  published hash → ``registry.activate`` (old versions are left untouched; an
  already ``active`` version may be re-pointed to, e.g. after a rollback or a
  stale CAS retry) → **one** CAS ``store.commit`` of a full snapshot
  (skills + mcp) → sidecar bookkeeping → commit record.
* Rollback is pointer-first: the target must already be published and its hash
  must match → **one** CAS commit back to the target → sidecar + commit record.
  Registry status is not rewritten, so a previous version stays usable.
* Crash/publish semantics: a crash or write failure before ``commit`` leaves the
  old pointer fully usable; a crash after ``commit`` leaves the new pointer fully
  usable with cleanup/record pending. ``current`` must never be half-written.
  ``try/except`` compensation only covers explicit exceptions, not SIGKILL.
* Prepared intents: activate/rollback/migrate write
  ``<state>/skills/intents/<action>-<uuid>.json`` before the CAS. The intent is
  kept for audit (never deleted); ``reconcile_commits`` adopts an intent whose
  CAS landed but whose ``commits/<revision>.json`` is missing, and marks an
  intent whose CAS never happened as ``stale`` so it can never enter the
  adoption chain or become a rollback target.

Evidence binding (D5.9): evaluation records carry ``asset_id`` plus candidate /
baseline package hashes; :func:`mark_evaluated_from_evidence` and
:func:`approve_asset` refuse evidence that belongs to another asset or hash.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import socket
import threading
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..upper.registry import UpperRegistryError
from ..upper.storage import atomic_write_json
from ..upper.types import ASSET_ORIGINS, UpperAsset, parse_iso8601, utc_now
from .evolution import SkillEvolutionError, candidate_asset_id, evidence_allows_approval
from .loader import SkillPackage, SkillPackageError, load_package, validate_package
from .manifest import (
    BODY_FILENAME,
    MANIFEST_FILENAME,
    canonical_json,
    package_hash,
    parse_manifest,
)

LIFECYCLE_SCHEMA_VERSION = "1.0.0"
SIDECAR_SCHEMA_VERSION = "1.0.0"
EVIDENCE_FINALS = ("keep", "reject", "rollback")
REUSABLE_STATUSES = ("approved", "active")
PACKAGES_SUBDIR = ("skills", "packages")
COMMITS_SUBDIR = ("skills", "commits")
INTENTS_SUBDIR = ("skills", "intents")
INTENT_SCHEMA_VERSION = "1"
LIFECYCLE_LOCK_NAME = ".lifecycle.lock"
INTENT_STALE_AFTER_S = 300
EVIDENCE_ASSET_MISMATCH = "evidence_asset_mismatch"
EVIDENCE_NOT_KEEP = "evidence_not_keep"
EVIDENCE_INVALID_PHASE_RECORD = "evidence_invalid_phase_record"
EVIDENCE_INVALID_PROVENANCE = "evidence_invalid_provenance"
ROLLBACK_TARGET_MISSING = "rollback_target_missing"
BASELINE_KIND = "operator_baseline"

_AUTHORSHIP = {
    "manual": "operator",
    "provider": "model",
    "synthetic": "generated",
    "operator_baseline": "operator",
}
_DECLARED_ORIGINS = tuple(_AUTHORSHIP)
_ENTRY_KEYS = ("skill_id", "version", "asset_id", "package_hash")


def _asset_id(pkg: SkillPackage) -> str:
    return candidate_asset_id(pkg.skill_id, pkg.version)


def _sidecar_path(sidecar_dir: Path | str, asset_id: str) -> Path:
    return Path(sidecar_dir).expanduser() / f"{asset_id}.json"


def _load_sidecar(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillEvolutionError(f"cannot read sidecar {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SkillEvolutionError(f"sidecar {path} is not a JSON object")
    return payload


def _write_sidecar(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_json(path, payload)


def _require_actor(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SkillEvolutionError(f"{name} is required")
    return value.strip()


def _state_dir_for(store: Any) -> Path:
    state_dir = getattr(store, "state_dir", None)
    if state_dir is None:
        raise SkillEvolutionError("store_state_dir_unknown: cannot place packages/commits")
    return Path(state_dir)


# --------------------------------------------------------------------------- #
# lifecycle lock (one lock for reconcile + intent + CAS + record)
# --------------------------------------------------------------------------- #
_LOCK_LOCAL = threading.local()


def _lifecycle_lock_path(state_dir: Path | str) -> Path:
    return Path(state_dir).joinpath("skills", LIFECYCLE_LOCK_NAME)


@contextmanager
def _lifecycle_lock(state_dir: Path | str):
    """Exclusive lifecycle lock; re-entrant within one thread.

    The lock file is ``<state>/skills/.lifecycle.lock`` — distinct from
    ``BindingStore``'s own ``skills/.lock``, so nesting lifecycle → binding is
    legal. Recursion is tracked per thread: the inner acquisitions only bump a
    depth counter, and only the outermost release performs ``LOCK_UN`` + close.
    """
    depth = int(getattr(_LOCK_LOCAL, "depth", 0))
    if depth:
        _LOCK_LOCAL.depth = depth + 1
        try:
            yield
        finally:
            _LOCK_LOCAL.depth = depth
        return

    path = _lifecycle_lock_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except Exception:
        handle.close()
        raise
    _LOCK_LOCAL.depth = 1
    _LOCK_LOCAL.handle = handle
    try:
        yield
    finally:
        _LOCK_LOCAL.depth = 0
        _LOCK_LOCAL.handle = None
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()



# --------------------------------------------------------------------------- #
# package publication (D6.6)
# --------------------------------------------------------------------------- #
def _packages_root(state_dir: Path | str) -> Path:
    return Path(state_dir).joinpath(*PACKAGES_SUBDIR)


def _package_target(state_dir: Path | str, asset_id: str, skill_id: str) -> Path:
    return _packages_root(state_dir) / asset_id / skill_id


def _commits_dir(state_dir: Path | str) -> Path:
    return Path(state_dir).joinpath(*COMMITS_SUBDIR)


def _hash_package_dir(package_dir: Path) -> str:
    manifest = parse_manifest(package_dir / MANIFEST_FILENAME)
    body_bytes = (package_dir / BODY_FILENAME).read_bytes()
    return package_hash(manifest, body_bytes)


def publish_package(state_dir: Path | str, pkg: SkillPackage, asset_id: str) -> Path:
    """Atomically publish ``pkg`` to ``<state>/skills/packages/<asset_id>/<skill_id>``.

    Same hash already present → reused; different hash in the same slot →
    ``package_conflict``. The package directory is staged as a sibling
    ``.<skill_id>.tmp-<uuid>`` and swapped in with ``os.replace``, so a reader
    never sees a partial package.
    """
    if not isinstance(pkg, SkillPackage):
        raise SkillEvolutionError("pkg must be a SkillPackage")
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise SkillEvolutionError("asset_id is required")
    target = _package_target(state_dir, asset_id, pkg.skill_id)
    if target.exists():
        try:
            existing = _hash_package_dir(target)
        except (OSError, SkillPackageError, ValueError) as exc:
            raise SkillEvolutionError(f"package_conflict:{target}: {exc}") from exc
        if existing == pkg.content_hash:
            return target
        raise SkillEvolutionError("package_conflict")

    root = _packages_root(state_dir)
    tmp = target.parent / f".{target.name}.tmp-{uuid.uuid4().hex}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        tmp.mkdir(parents=True, exist_ok=False)
        (tmp / MANIFEST_FILENAME).write_text(
            canonical_json(pkg.manifest.to_dict()) + "\n", encoding="utf-8"
        )
        (tmp / BODY_FILENAME).write_bytes(pkg.body.encode("utf-8"))
        staged = _hash_package_dir(tmp)
        if staged != pkg.content_hash:
            raise SkillEvolutionError("package_publish_hash_mismatch")
        os.replace(tmp, target)
    except SkillEvolutionError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        if target.exists():
            try:
                if _hash_package_dir(target) == pkg.content_hash:
                    return target
            except (OSError, SkillPackageError, ValueError):
                pass
            raise SkillEvolutionError("package_conflict") from exc
        raise SkillEvolutionError(f"package_publish_failed:{exc}") from exc
    return target


# --------------------------------------------------------------------------- #
# registration / evidence / approval
# --------------------------------------------------------------------------- #
def register_asset(
    registry: Any,
    pkg: SkillPackage,
    *,
    created_by: str = "operator",
    origin: str = "synthetic",
    sidecar_dir: Path | str,
    declared_origin: str,
) -> UpperAsset:
    """Register one skill package as a ``draft`` asset plus a metadata sidecar.

    ``origin`` must be one of ``ASSET_ORIGINS`` (upper-side vocabulary). How the
    body was actually authored (``manual`` / ``provider`` / ``synthetic``) is
    recorded in the sidecar's ``declared_origin``, so operator authorship is not
    laundered into ``real_run``.
    """
    if not isinstance(pkg, SkillPackage):
        raise SkillEvolutionError("pkg must be a SkillPackage")
    if origin not in ASSET_ORIGINS:
        raise SkillEvolutionError(
            f"origin must be one of {ASSET_ORIGINS}, got {origin!r}"
        )
    if declared_origin not in _DECLARED_ORIGINS:
        raise SkillEvolutionError(
            f"declared_origin must be one of {_DECLARED_ORIGINS}, got {declared_origin!r}"
        )
    creator = _require_actor(created_by, "created_by")

    asset_id = _asset_id(pkg)
    content = {
        "package_hash": pkg.content_hash,
        "skill_id": pkg.skill_id,
        "triggers": pkg.manifest.triggers.to_dict(),
        "permissions": pkg.manifest.permissions.to_dict(),
        "source_refs": [ref.to_dict() for ref in pkg.manifest.source_refs],
        # Source location only; activation publishes to <state>/skills/packages.
        "package_dir": str(pkg.package_dir),
    }
    asset = UpperAsset(
        asset_id=asset_id,
        kind="skill",
        name=pkg.skill_id,
        version=pkg.version,
        source=f"skills/{pkg.skill_id}/{pkg.version}",
        family=pkg.manifest.family,
        created_by=creator,
        origin=origin,
        eval_state="not_evaluated",
        status="draft",
        content=content,
    )
    registered = registry.register(asset)

    sidecar = {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "asset_id": asset_id,
        "package_hash": pkg.content_hash,
        "authorship": _AUTHORSHIP[declared_origin],
        "declared_origin": declared_origin,
        "visibility": "internal",
        "sensitivity": "public",
        "source_refs": [ref.to_dict() for ref in pkg.manifest.source_refs],
        "review": {
            "status": "pending",
            "reviewed_by": None,
            "reviewed_at": None,
            "note": None,
        },
        "created_at": utc_now(),
        "created_by": creator,
    }
    _write_sidecar(_sidecar_path(sidecar_dir, asset_id), sidecar)
    return registered


BASELINE_NOTE = "manual operator baseline; not model-evaluated; no keep evidence"
MIGRATION_NOTE = (
    "migrated existing operator binding; not model-evaluated; no keep evidence"
)


def _register_operator_baseline(
    registry: Any,
    pkg: SkillPackage,
    *,
    asset_id: str,
    actor: str,
    note: str,
    sidecar_dir: Path | str,
) -> UpperAsset:
    """Register an operator baseline as a real active skill asset (no keep claim).

    ``UpperRegistry.register`` only accepts ``draft`` and there is no
    operator-baseline transition, so the asset is persisted through the
    registry's own writer after being marked ``active``; no other asset is
    touched. The sidecar records ``review.status="not_reviewed"``.
    """
    content = {
        "package_hash": pkg.content_hash,
        "skill_id": pkg.skill_id,
        "triggers": pkg.manifest.triggers.to_dict(),
        "permissions": pkg.manifest.permissions.to_dict(),
        "source_refs": [ref.to_dict() for ref in pkg.manifest.source_refs],
        "package_dir": str(pkg.package_dir),
        "baseline_kind": BASELINE_KIND,
    }
    asset = UpperAsset(
        asset_id=asset_id,
        kind="skill",
        name=pkg.skill_id,
        version=pkg.version,
        source=f"skills/{pkg.skill_id}/{pkg.version}",
        family=pkg.manifest.family,
        created_by=actor,
        origin="synthetic",
        eval_state="not_evaluated",
        status="draft",
        implementation_status="implemented",
        content=content,
        approved_by="operator-config",
        approved_at=utc_now(),
        status_reason=note,
    )
    registered = registry.register(asset)
    registered.status = "active"
    registered.updated_at = utc_now()
    registry._save()  # see docstring: no public draft→active transition exists

    sidecar = {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "asset_id": asset_id,
        "package_hash": pkg.content_hash,
        "authorship": _AUTHORSHIP[BASELINE_KIND],
        "declared_origin": BASELINE_KIND,
        "visibility": "internal",
        "sensitivity": "public",
        "source_refs": [ref.to_dict() for ref in pkg.manifest.source_refs],
        "review": {
            "status": "not_reviewed",
            "reviewed_by": None,
            "reviewed_at": None,
            "note": note,
        },
        "approved_by": "operator-config",
        "approval_note": note,
        "created_at": utc_now(),
        "created_by": actor,
    }
    _write_sidecar(_sidecar_path(sidecar_dir, asset_id), sidecar)
    return registered


def bootstrap_baseline(
    registry: Any,
    store: Any,
    pkg: SkillPackage,
    *,
    actor: str,
    expected_revision: int = 0,
) -> dict[str, Any]:
    """Register the pre-existing operator baseline as a real, active skill asset.

    This is not an evaluation or an approval: no keep evidence and no human
    review are claimed (the sidecar records ``review.status="not_reviewed"`` and
    the approval note says so). It exists so a later rollback has a registry
    target instead of an untracked pointer. Runs under the lifecycle lock.
    """
    state_dir = _state_dir_for(store)
    with _lifecycle_lock(state_dir):
        return _bootstrap_baseline_locked(
            registry, store, pkg, state_dir=state_dir, actor=actor,
            expected_revision=expected_revision,
        )


def _bootstrap_baseline_locked(
    registry: Any,
    store: Any,
    pkg: SkillPackage,
    *,
    state_dir: Path,
    actor: str,
    expected_revision: int,
) -> dict[str, Any]:
    operator = _require_actor(actor, "actor")
    if not isinstance(pkg, SkillPackage):
        raise SkillEvolutionError("pkg must be a SkillPackage")
    asset_id = _asset_id(pkg)
    registered = _register_operator_baseline(
        registry,
        pkg,
        asset_id=asset_id,
        actor=operator,
        note=BASELINE_NOTE,
        sidecar_dir=state_dir.joinpath("skills", "sidecars"),
    )

    published = publish_package(state_dir, pkg, asset_id)
    entry = _entry_for(registered)
    committed = store.commit(
        "skills", entry["skill_id"], entry, expected_revision=expected_revision
    )
    revision = _snapshot_revision(store, committed)

    record = {
        "revision": revision,
        "action": "activate",
        "actor": operator,
        "reason": BASELINE_KIND,
        "evidence_id": None,
        "from": None,
        "to": _entry_summary(entry),
        "at": utc_now(),
    }
    return {
        "asset": registered,
        "entry": entry,
        "revision": revision,
        "package_dir": str(published),
        "commit_record": _write_commit_record(state_dir, record),
    }


def _get_asset_or_none(registry: Any, asset_id: str) -> UpperAsset | None:
    try:
        return registry.get(asset_id)
    except UpperRegistryError:
        return None


def migrate_existing_binding(
    registry: Any,
    store: Any,
    package: SkillPackage,
    *,
    existing_entry: Mapping[str, Any],
    target_asset_id: str,
    actor: str,
    reason: str,
    dry_run: bool = True,
    fault_hook: Any | None = None,
) -> dict[str, Any]:
    state_dir = _state_dir_for(store)
    with _lifecycle_lock(state_dir):
        return _migrate_existing_binding_locked(
            registry,
            store,
            package,
            state_dir=state_dir,
            existing_entry=existing_entry,
            target_asset_id=target_asset_id,
            actor=actor,
            reason=reason,
            dry_run=dry_run,
            fault_hook=fault_hook,
        )


def _migrate_existing_binding_locked(
    registry: Any,
    store: Any,
    package: SkillPackage,
    *,
    state_dir: Path,
    existing_entry: Mapping[str, Any],
    target_asset_id: str,
    actor: str,
    reason: str,
    dry_run: bool,
    fault_hook: Any | None,
) -> dict[str, Any]:
    """One-shot migration of a legacy binding entry to the current asset shape.

    Never called automatically and never triggered by service start. The old
    entry (e.g. ``asset_id="skill-repo-evidence-1.0.0"``) is validated against the
    package from ``skills/``; the target asset is either reused (already
    registered and usable) or registered with the operator-baseline semantics
    (no keep evidence, no human review claim). Only the binding pointer moves, in
    one CAS commit that keeps the whole snapshot including ``mcp``.

    ``dry_run=True`` performs read-only checks and returns a plan. ``dry_run=False``
    publishes the package, ensures the registry asset, writes a prepared intent,
    commits the binding and writes a ``action="migrate"`` commit record. A crash
    between the commit and the record is recovered by ``reconcile_commits``.
    Repeating the call after a successful migration returns ``status="noop"``.
    ``fault_hook`` is test-only (``"after_intent"``/``"after_commit"``/``"after_record"``).
    """
    operator = _require_actor(actor, "actor")
    if not isinstance(reason, str) or not reason.strip():
        raise SkillEvolutionError("reason is required")
    if not isinstance(package, SkillPackage):
        raise SkillEvolutionError("package must be a SkillPackage")
    if not isinstance(target_asset_id, str) or not target_asset_id.strip():
        raise SkillEvolutionError("target_asset_id is required")
    if not isinstance(existing_entry, Mapping):
        raise SkillEvolutionError("migration_package_mismatch: existing_entry must be a mapping")

    # 1. Package integrity + agreement with the entry being migrated.
    try:
        validate_package(package)
    except SkillPackageError as exc:
        raise SkillEvolutionError(f"migration_package_mismatch: {exc}") from exc
    for key, expected in (
        ("skill_id", package.skill_id),
        ("version", package.version),
        ("package_hash", package.content_hash),
    ):
        if str(existing_entry.get(key)) != expected:
            raise SkillEvolutionError("migration_package_mismatch")

    skill_id = package.skill_id
    target_hash = package.content_hash
    target_asset_id = target_asset_id.strip()
    sidecar_dir = state_dir.joinpath("skills", "sidecars")
    packages_path = _package_target(state_dir, target_asset_id, skill_id)
    to_summary = {
        "skill_id": skill_id,
        "asset_id": target_asset_id,
        "version": package.version,
        "hash": target_hash,
    }

    binding = store.load()
    revision = int(binding["revision"])
    current = (binding.get("skills") or {}).get(skill_id)
    if current is None:
        raise SkillEvolutionError("migration_binding_missing")
    from_entry = {
        "skill_id": str(current["skill_id"]),
        "version": str(current["version"]),
        "asset_id": str(current["asset_id"]),
        "package_hash": str(current["package_hash"]),
    }

    # 2. Idempotent path: the pointer already names the target.
    if current.get("asset_id") == target_asset_id:
        if current.get("package_hash") != target_hash:
            raise SkillEvolutionError("migration_package_mismatch")
        return {
            "status": "noop",
            "from": from_entry,
            "to": to_summary,
            "revision": revision,
            "packages_path": str(packages_path),
            "dry_run": dry_run,
        }

    # The described entry must be the one actually bound, or the record lies.
    for key in ("skill_id", "version", "package_hash"):
        if str(existing_entry.get(key)) != str(current.get(key)):
            raise SkillEvolutionError("migration_binding_mismatch")
    if existing_entry.get("asset_id") and existing_entry["asset_id"] != current.get("asset_id"):
        raise SkillEvolutionError("migration_binding_mismatch")

    # 3. Registry side: reuse a usable target, otherwise register a baseline.
    registered = _get_asset_or_none(registry, target_asset_id)
    if registered is not None:
        if (
            registered.name != skill_id
            or registered.version != package.version
            or str(registered.content.get("package_hash", "")) != target_hash
            or registered.status not in REUSABLE_STATUSES
        ):
            raise SkillEvolutionError("migration_registry_conflict")
        need_register = False
    else:
        need_register = True

    # 4. Publish plan (read-only here; the published hash must match the binding).
    if packages_path.is_dir():
        try:
            existing_hash = _hash_package_dir(packages_path)
        except (OSError, SkillPackageError, ValueError) as exc:
            raise SkillEvolutionError(f"migration_package_mismatch: {exc}") from exc
        if existing_hash != target_hash:
            raise SkillEvolutionError("package_conflict")
        publish_action = "reuse"
    else:
        publish_action = "publish"

    if dry_run:
        return {
            "status": "planned",
            "from": from_entry,
            "to": to_summary,
            "revision": revision,
            "packages_path": str(packages_path),
            "plan": {
                "register_asset": need_register,
                "publish": publish_action,
                "next_revision": revision + 1,
                "sidecar_dir": str(sidecar_dir),
            },
            "dry_run": True,
        }

    # 5. Apply: publish → registry (if needed) → one CAS commit → commit record.
    #    Every validation/publish failure above happens before the commit; a
    #    failed commit leaves the old pointer and any newly active asset unused.
    published = publish_package(state_dir, package, target_asset_id)
    if _hash_package_dir(published) != target_hash:
        raise SkillEvolutionError("migration_package_mismatch: published hash differs")
    if need_register:
        registered = _register_operator_baseline(
            registry,
            package,
            asset_id=target_asset_id,
            actor=operator,
            note=MIGRATION_NOTE,
            sidecar_dir=sidecar_dir,
        )
    new_entry = {
        "skill_id": skill_id,
        "version": package.version,
        "asset_id": target_asset_id,
        "package_hash": target_hash,
    }
    intent = _prepare_intent(
        state_dir,
        action="migrate",
        skill_id=skill_id,
        expected_revision=revision,
        from_entry=from_entry,
        to_entry=new_entry,
        actor=operator,
        reason=reason.strip(),
        evidence_id=None,
    )
    _call_fault_hook(fault_hook, "after_intent")

    committed = store.commit(
        "skills", skill_id, new_entry, expected_revision=intent["expected_revision"]
    )
    new_revision = _snapshot_revision(store, committed)
    _call_fault_hook(fault_hook, "after_commit")

    record = {
        "revision": new_revision,
        "action": "migrate",
        "actor": operator,
        "reason": reason.strip(),
        "evidence_id": None,
        "from": from_entry,
        "to": to_summary,
        "intent_id": intent["intent_id"],
        "at": utc_now(),
    }
    commit_record = _write_commit_record(state_dir, record)
    if commit_record == "written":
        try:
            _mark_intent(state_dir, intent, "committed")
        except Exception:  # record exists; reconcile will mark it later
            pass
    _call_fault_hook(fault_hook, "after_record")
    return {
        "status": "applied",
        "from": from_entry,
        "to": to_summary,
        "revision": new_revision,
        "commit_record": commit_record,
        "packages_path": str(published),
        "registry_status": registry.get(target_asset_id).status,
        "intent_id": intent["intent_id"],
        "dry_run": False,
    }


def _load_evidence(evidence: Any) -> dict[str, Any]:
    if isinstance(evidence, (str, Path)):
        path = Path(evidence).expanduser()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillEvolutionError(f"cannot read evidence {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise SkillEvolutionError(f"evidence {path} is not a JSON object")
        return payload
    if not isinstance(evidence, dict):
        raise SkillEvolutionError("evidence must be a dict or a JSON path")
    return evidence


def _assert_evidence_binding(
    registry: Any,
    asset_id: str,
    evidence: dict[str, Any],
    *,
    package_hash: str | None = None,
) -> UpperAsset:
    """D5.9: evidence must name this asset and this exact package hash."""
    asset = registry.get(asset_id)
    expected_hash = str(asset.content.get("package_hash", ""))
    if evidence.get("asset_id") != asset_id:
        raise SkillEvolutionError(EVIDENCE_ASSET_MISMATCH)
    if not expected_hash or evidence.get("candidate_package_hash") != expected_hash:
        raise SkillEvolutionError(EVIDENCE_ASSET_MISMATCH)
    if package_hash is not None and package_hash != expected_hash:
        raise SkillEvolutionError(EVIDENCE_ASSET_MISMATCH)
    skill_ids = {asset.name, asset.content.get("skill_id")}
    if evidence.get("skill_id") not in skill_ids:
        raise SkillEvolutionError(EVIDENCE_ASSET_MISMATCH)
    return asset


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_phase_record(phase: str, record: Any) -> None:
    """Fail-closed structure check for one phase record (D5.9 evidence).

    A record that merely *has* ``transfer``/``regression`` keys (even ``null``)
    must not mark an asset evaluated: the metrics are what the verdict was
    computed from, so their shape is validated before the state transition.
    """
    if not isinstance(record, Mapping):
        raise SkillEvolutionError(EVIDENCE_INVALID_PHASE_RECORD)
    declared_phase = record.get("phase")
    if declared_phase is not None and declared_phase != phase:
        raise SkillEvolutionError(EVIDENCE_INVALID_PHASE_RECORD)
    metrics = record.get("metrics")
    if not isinstance(metrics, Mapping):
        raise SkillEvolutionError(EVIDENCE_INVALID_PHASE_RECORD)
    for side in ("candidate", "baseline"):
        side_metrics = metrics.get(side)
        if not isinstance(side_metrics, Mapping):
            raise SkillEvolutionError(EVIDENCE_INVALID_PHASE_RECORD)
        if not _is_number(side_metrics.get("pass_rate")):
            raise SkillEvolutionError(EVIDENCE_INVALID_PHASE_RECORD)
        for counter in ("attempts", "passed"):
            if counter in side_metrics and (
                not isinstance(side_metrics[counter], int)
                or isinstance(side_metrics[counter], bool)
            ):
                raise SkillEvolutionError(EVIDENCE_INVALID_PHASE_RECORD)
    if not isinstance(metrics.get("tasks_regressed"), list):
        raise SkillEvolutionError(EVIDENCE_INVALID_PHASE_RECORD)


def _validate_evidence_provenance(payload: Mapping[str, Any]) -> str:
    """Fail-closed mode/provenance check shared by evaluate and approve.

    ``mode`` (top level) and ``provenance.kind`` must agree when both are
    present; ``live`` requires an actual model call, ``manual`` forbids one.
    """
    provenance = payload.get("provenance")
    if not isinstance(provenance, Mapping):
        raise SkillEvolutionError("evidence.provenance must be an object")
    kind = provenance.get("kind")
    mode = payload.get("mode", kind)
    calls_model = payload.get("calls_model", provenance.get("calls_model"))
    if mode not in ("live", "manual"):
        raise SkillEvolutionError(EVIDENCE_INVALID_PROVENANCE)
    if mode == "live" and calls_model is not True:
        raise SkillEvolutionError(EVIDENCE_INVALID_PROVENANCE)
    if mode == "manual" and calls_model is not False:
        raise SkillEvolutionError(EVIDENCE_INVALID_PROVENANCE)
    if kind is not None and kind != mode:
        raise SkillEvolutionError(EVIDENCE_INVALID_PROVENANCE)
    return str(mode)


def mark_evaluated_from_evidence(
    registry: Any,
    asset_id: str,
    evidence: Any,
    *,
    package_hash: str | None = None,
) -> UpperAsset:
    """Move a draft asset to ``evaluated`` after a two-phase evaluation.

    Order matters: (1) asset/hash binding, (2) verdict + phase-record structure
    (fail closed), (3) provenance honesty, (4) ``mark_evaluated``. A record
    produced for another asset/hash, or the wrong shape, is rejected and nothing
    is marked.

    ``UpperAsset.eval_state`` is deliberately not rewritten: ``molt/upper/types.py``
    forbids ``eval_state != "not_evaluated"`` for ``synthetic``/``seed_import``
    origins, and this module does not touch ``molt/upper/**``.
    """
    payload = _load_evidence(evidence)
    # 1. D5.9: evidence must belong to this asset and this package hash.
    _assert_evidence_binding(registry, asset_id, payload, package_hash=package_hash)
    # 2. Verdict and phase structure; null/empty records are not evidence.
    final = payload.get("final")
    if final not in EVIDENCE_FINALS:
        raise SkillEvolutionError(
            f"evidence final must be one of {EVIDENCE_FINALS}, got {final!r}"
        )
    for phase in ("transfer", "regression"):
        _validate_phase_record(phase, payload.get(phase))
    # 3. Provenance honesty: mode/provenance must agree and live needs a call.
    _validate_evidence_provenance(payload)
    # 4. Only now is the state transition allowed.
    return registry.mark_evaluated(asset_id)


def approve_asset(
    registry: Any,
    asset_id: str,
    *,
    approved_by: str,
    note: str,
    evidence: Any,
) -> UpperAsset:
    """Explicit operator approval, gated on a ``keep`` evidence record (D5.8).

    ``reject``/``rollback`` evidence must never be promoted; a missing or
    non-keep record raises ``evidence_not_keep``. ``note`` is validated and left
    to the caller for audit (``UpperRegistry.approve`` clears ``status_reason``).
    """
    approver = _require_actor(approved_by, "approved_by")
    if not isinstance(note, str):
        raise SkillEvolutionError("note must be a string")
    if evidence is None:
        raise SkillEvolutionError(EVIDENCE_NOT_KEEP)
    payload = _load_evidence(evidence)
    # Same validation ladder as mark_evaluated_from_evidence, and all of it runs
    # before the registry transition: a rejected approval must leave the asset
    # ``evaluated`` (never partially approved).
    # 1. D5.9 binding: the record must name this asset and package hash.
    _assert_evidence_binding(registry, asset_id, payload)
    # 2. Phase records must carry real metrics, not just keys.
    _validate_phase_record("transfer", payload.get("transfer"))
    _validate_phase_record("regression", payload.get("regression"))
    # 3. Provenance honesty (live ↔ calls_model, manual ↔ no model call).
    _validate_evidence_provenance(payload)
    # 4. Only a keep verdict may be promoted.
    if not evidence_allows_approval(payload):
        raise SkillEvolutionError(EVIDENCE_NOT_KEEP)
    return registry.approve(asset_id, approved_by=approver)


# --------------------------------------------------------------------------- #
# executability / entries
# --------------------------------------------------------------------------- #
def _package_dir_for(asset: UpperAsset, package_root: Path | str | None) -> Path:
    if package_root is not None:
        root = Path(package_root).expanduser()
        return root / asset.name
    recorded = asset.content.get("package_dir") if asset.content else None
    if not isinstance(recorded, str) or not recorded:
        raise SkillEvolutionError(
            f"asset {asset.asset_id} has no package_dir; pass package_root"
        )
    return Path(recorded)


def _assert_executable(
    asset: UpperAsset, expected_hash: str, package_root: Path | str | None
) -> SkillPackage:
    """Load + re-hash the on-disk source package; hash must match the asset."""
    package_dir = _package_dir_for(asset, package_root)
    try:
        pkg = load_package(package_dir.parent, asset.name, asset.version)
        validate_package(pkg)
    except SkillPackageError as exc:
        raise SkillEvolutionError(
            f"package_unusable:{asset.asset_id}: {exc}"
        ) from exc
    if pkg.content_hash != expected_hash:
        raise SkillEvolutionError(
            f"package_hash_mismatch:{asset.asset_id}: "
            f"{pkg.content_hash} != {expected_hash}"
        )
    return pkg


def _entry_for(asset: UpperAsset) -> dict[str, str]:
    return {
        "skill_id": asset.name,
        "version": asset.version,
        "asset_id": asset.asset_id,
        "package_hash": str(asset.content.get("package_hash", "")),
    }


def _entry_summary(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    return {
        "skill_id": entry.get("skill_id"),
        "asset_id": entry.get("asset_id"),
        "version": entry.get("version"),
        "hash": entry.get("package_hash"),
    }


def _snapshot_revision(store: Any, committed: Any) -> int:
    if isinstance(committed, dict) and isinstance(committed.get("revision"), int):
        return committed["revision"]
    return int(store.load()["revision"])


# --------------------------------------------------------------------------- #
# sidecar / commit bookkeeping
# --------------------------------------------------------------------------- #
def _mark_superseded(
    sidecar_dir: Path | str,
    previous: dict[str, Any],
    *,
    revision: int,
    superseded_by: str,
) -> None:
    path = _sidecar_path(sidecar_dir, previous["asset_id"])
    payload = _load_sidecar(path)
    payload.setdefault("schema_version", SIDECAR_SCHEMA_VERSION)
    payload.setdefault("asset_id", previous["asset_id"])
    payload.setdefault("package_hash", previous.get("package_hash"))
    payload["superseded_at"] = utc_now()
    payload["superseded_by"] = superseded_by
    payload["superseded_revision"] = revision
    _write_sidecar(path, payload)


def _mark_rolled_back(
    sidecar_dir: Path | str,
    current: dict[str, Any],
    *,
    reason: str,
    rolled_back_by: str,
) -> None:
    path = _sidecar_path(sidecar_dir, current["asset_id"])
    payload = _load_sidecar(path)
    payload.setdefault("schema_version", SIDECAR_SCHEMA_VERSION)
    payload.setdefault("asset_id", current["asset_id"])
    payload.setdefault("package_hash", current.get("package_hash"))
    payload["rolled_back_at"] = utc_now()
    payload["rolled_back_by"] = rolled_back_by
    payload["rolled_back_reason"] = reason
    _write_sidecar(path, payload)


def _write_commit_record(state_dir: Path | str, record: dict[str, Any]) -> str:
    path = _commits_dir(state_dir) / f"{record['revision']}.json"
    try:
        atomic_write_json(path, record)
    except Exception as exc:  # pointer already switched; report, never revert
        return f"commit_record_pending:{exc}"
    return "written"


# --------------------------------------------------------------------------- #
# prepared intents (crash consistency around the CAS commit)
# --------------------------------------------------------------------------- #
def _intents_dir(state_dir: Path | str) -> Path:
    return Path(state_dir).joinpath(*INTENTS_SUBDIR)


def _intent_path(state_dir: Path | str, intent_id: str) -> Path:
    return _intents_dir(state_dir) / f"{intent_id}.json"


def _new_intent_id(action: str) -> str:
    return f"{action}-{uuid.uuid4().hex}"


def _load_intents(state_dir: Path | str) -> list[dict[str, Any]]:
    directory = _intents_dir(state_dir)
    if not directory.is_dir():
        return []
    intents: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("intent_id"):
            intents.append(payload)
    intents.sort(key=lambda item: str(item.get("at", "")))
    return intents


def _prepare_intent(
    state_dir: Path | str,
    *,
    action: str,
    skill_id: str,
    expected_revision: int,
    from_entry: dict[str, Any] | None,
    to_entry: dict[str, Any],
    actor: str,
    reason: str | None,
    evidence_id: str | None,
) -> dict[str, Any]:
    """Write a prepared intent *before* the CAS commit.

    The intent is the crash-recovery record: if the process dies after the CAS
    but before the commit record, ``reconcile_commits`` can adopt it. A prepared
    intent whose CAS never happened is ignored (and marked ``stale``); it never
    enters the adoption chain. ``skill_id`` pins the section key, ``owner_pid`` /
    ``owner_host`` let reconciliation tell a live writer from a crashed one.
    """
    prepared_at = utc_now()
    intent = {
        "schema_version": INTENT_SCHEMA_VERSION,
        "intent_id": _new_intent_id(action),
        "action": action,
        "skill_id": skill_id,
        "expected_revision": int(expected_revision),
        "from": _entry_summary(from_entry),
        "to": _entry_summary(to_entry),
        "actor": actor,
        "reason": reason,
        "evidence_id": evidence_id,
        "owner_pid": os.getpid(),
        "owner_host": socket.gethostname(),
        "prepared_at": prepared_at,
        "status": "prepared",
        "at": prepared_at,
    }
    atomic_write_json(_intent_path(state_dir, intent["intent_id"]), intent)
    return intent


def _mark_intent(
    state_dir: Path | str,
    intent: dict[str, Any],
    status: str,
    *,
    reason: str | None = None,
) -> None:
    """Rewrite only ``status``/timestamps/reason; the rest of the intent is immutable."""
    updated = dict(intent)
    updated["status"] = status
    if status == "committed":
        updated["committed_at"] = utc_now()
    if reason is not None:
        updated["reason_detail"] = reason
    atomic_write_json(_intent_path(state_dir, intent["intent_id"]), updated)


def _call_fault_hook(fault_hook: Any, point: str) -> None:
    if fault_hook is not None:
        fault_hook(point)


def _binding_matches_to(entries: dict[str, Any], intent: dict[str, Any]) -> bool:
    """Exact key match: only ``skills[intent.skill_id]`` may satisfy ``intent.to``."""
    to = intent.get("to") or {}
    entry = entries.get(intent.get("skill_id"))
    if not isinstance(entry, dict):
        return False
    return (
        entry.get("asset_id") == to.get("asset_id")
        and entry.get("version") == to.get("version")
        and entry.get("package_hash") == to.get("hash")
    )


def _intent_writer_state(intent: dict[str, Any]) -> str:
    """``self`` | ``alive`` | ``dead`` | ``unknown`` for a prepared intent's writer."""
    pid = intent.get("owner_pid")
    host = intent.get("owner_host")
    if not isinstance(pid, int) or isinstance(pid, bool) or host != socket.gethostname():
        return "unknown"
    if pid == os.getpid():
        return "self"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "dead"
    except PermissionError:
        return "alive"
    except OSError:  # pragma: no cover - defensive
        return "alive"
    return "alive"


def _intent_age_seconds(intent: dict[str, Any]) -> float | None:
    stamp = intent.get("prepared_at") or intent.get("at")
    if not isinstance(stamp, str):
        return None
    try:
        prepared = parse_iso8601(stamp)
    except ValueError:
        return None
    if prepared.tzinfo is None:
        prepared = prepared.replace(tzinfo=UTC)
    return (datetime.now(UTC) - prepared).total_seconds()


def _record_from_intent(intent: dict[str, Any], revision: int) -> dict[str, Any]:
    return {
        "revision": revision,
        "action": intent.get("action"),
        "actor": intent.get("actor"),
        "reason": intent.get("reason"),
        "evidence_id": intent.get("evidence_id"),
        "from": intent.get("from"),
        "to": intent.get("to"),
        "intent_id": intent.get("intent_id"),
        "at": intent.get("at"),
    }


def reconcile_commits(
    state_dir: Path | str,
    store: Any,
    registry: Any,
    *,
    assume_locked: bool = False,
) -> list[dict[str, Any]]:
    """Adopt prepared intents whose CAS commit happened but whose record did not.

    Runs under the lifecycle lock unless the caller already holds it
    (``assume_locked=True``, used by activate/rollback). Read-only with respect to
    ``current`` and never deletes intents:

    * ``current.revision == intent.expected_revision + 1`` and
      ``snapshot["skills"][intent.skill_id]`` matches ``intent.to`` → the commit
      was adopted. A missing ``commits/<revision>.json`` is re-written from the
      intent; the intent is marked ``committed`` and ``{"action": "adopt"}`` is
      returned.
    * ``current.revision <= intent.expected_revision`` → the CAS has not happened.
      A live writer (``owner_pid`` alive on the same host, other than this
      process, while we hold the lock) is reported as
      ``{"skipped": "active_writer"}`` and left ``prepared``; a remote/unknown
      owner is left alone until ``prepared_at`` exceeds
      ``INTENT_STALE_AFTER_S``. Dead or self-owned (this process holds the lock)
      intents are marked ``stale``.
    * Anything else (revision moved on, or the bound entry differs) → ``stale``
      with a reason.

    Idempotent: committed/stale intents are skipped, so repeated calls are safe.
    """
    if assume_locked:
        return _reconcile_commits_locked(state_dir, store, registry)
    with _lifecycle_lock(state_dir):
        return _reconcile_commits_locked(state_dir, store, registry)


def _reconcile_commits_locked(
    state_dir: Path | str, store: Any, registry: Any
) -> list[dict[str, Any]]:
    del registry  # accepted for symmetry with callers; reconciliation never writes assets
    state = Path(state_dir)
    binding = store.load()
    revision = int(binding["revision"])
    entries = binding.get("skills") or {}
    results: list[dict[str, Any]] = []
    for intent in _load_intents(state):
        if intent.get("status") != "prepared":
            continue
        expected = intent.get("expected_revision")
        to = intent.get("to") or {}
        if not isinstance(expected, int) or not isinstance(to, dict):
            _mark_intent(state, intent, "stale", reason="intent_invalid")
            continue
        if revision == expected + 1 and _binding_matches_to(entries, intent):
            record_path = _commits_dir(state) / f"{revision}.json"
            if record_path.is_file():
                try:
                    existing = json.loads(record_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    existing = {}
                if isinstance(existing, dict) and existing.get("to") not in (None, to):
                    _mark_intent(state, intent, "stale", reason="record_conflict")
                    continue
            else:
                if _write_commit_record(state, _record_from_intent(intent, revision)) != "written":
                    continue  # retry on the next reconciliation
            _mark_intent(state, intent, "committed")
            results.append(
                {"intent_id": intent["intent_id"], "action": "adopt", "revision": revision}
            )
        elif revision <= expected:
            writer = _intent_writer_state(intent)
            if writer == "alive":
                results.append(
                    {
                        "intent_id": intent["intent_id"],
                        "action": "skip",
                        "skipped": "active_writer",
                    }
                )
                continue
            if writer == "unknown":
                age = _intent_age_seconds(intent)
                if age is None or age > INTENT_STALE_AFTER_S:
                    _mark_intent(state, intent, "stale", reason="prepared_timeout")
                else:
                    results.append(
                        {
                            "intent_id": intent["intent_id"],
                            "action": "skip",
                            "skipped": "active_writer",
                        }
                    )
                continue
            # ``self``: this process holds the lifecycle lock, so no writer is
            # active; ``dead``: the owner is gone.
            _mark_intent(state, intent, "stale", reason="cas_not_applied")
        elif revision == expected + 1:
            _mark_intent(state, intent, "stale", reason="to_mismatch")
        else:
            _mark_intent(state, intent, "stale", reason="revision_mismatch")
    return results


def _commit_records(state_dir: Path | str) -> list[dict[str, Any]]:
    """Adopted-transition chain; the only source of truth for rollback targets."""
    directory = _commits_dir(state_dir)
    if not directory.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("revision"), int):
            records.append(payload)
    records.sort(key=lambda item: item["revision"])
    return records


def _bootstrap_entry(registry: Any, skill_id: str) -> dict[str, str] | None:
    for asset in registry.list_assets(kind="skill", status="active"):
        if asset.name == skill_id and asset.content.get("baseline_kind") == BASELINE_KIND:
            return _entry_for(asset)
    return None


def _resolve_rollback_target(
    state_dir: Path | str, registry: Any, store: Any, current_asset_id: str, skill_id: str
) -> dict[str, str] | None:
    """Return the adopted predecessor of ``current_asset_id``.

    Reconciliation runs first, so an activation that committed without its record
    becomes a normal chain entry before the lookup. Rule: the ``from`` of the most
    recent ``action="activate"`` commit record that adopted ``current_asset_id``.
    Binding ``history/`` files are never consulted — an orphan history entry that
    was never committed to ``current`` is not a target. If the chain is empty but
    the current binding matches a prepared intent that already committed (record
    unreadable), that intent's ``from`` is used. An intent whose CAS never happened
    (current revision still <= expected) is never a target. With no commit records
    at all (legacy state), only the bootstrap-registered operator baseline is
    accepted.
    """
    reconcile_commits(state_dir, store, registry, assume_locked=True)
    records = _commit_records(state_dir)
    for record in reversed(records):
        if record.get("action") != "activate":
            continue
        target = record.get("to") or {}
        if target.get("asset_id") != current_asset_id:
            continue
        source = record.get("from")
        if not isinstance(source, dict) or not source.get("asset_id"):
            return None
        if source.get("asset_id") == current_asset_id:  # pragma: no cover
            return None
        return {
            "skill_id": skill_id,
            "version": str(source.get("version")),
            "asset_id": str(source.get("asset_id")),
            "package_hash": str(source.get("hash")),
        }
    if records:
        return None

    # Chain missing (record write failed): only an already-committed intent may
    # supply the predecessor; stale intents cannot.
    revision = int(store.load()["revision"])
    for intent in _load_intents(state_dir):
        to = intent.get("to") or {}
        expected = intent.get("expected_revision")
        if not isinstance(expected, int):
            continue
        if intent.get("skill_id") != skill_id:
            continue
        if to.get("asset_id") != current_asset_id or revision != expected + 1:
            continue
        source = intent.get("from")
        if not isinstance(source, dict) or not source.get("asset_id"):
            continue
        return {
            "skill_id": skill_id,
            "version": str(source.get("version")),
            "asset_id": str(source.get("asset_id")),
            "package_hash": str(source.get("hash")),
        }
    return _bootstrap_entry(registry, skill_id)


# --------------------------------------------------------------------------- #
# activation / rollback
# --------------------------------------------------------------------------- #
def activate_asset(
    registry: Any,
    store: Any,
    asset_id: str,
    *,
    expected_revision: int,
    actor: str,
    evidence_id: str,
    reason: str | None = None,
    sidecar_dir: Path | str | None = None,
    package_root: Path | str | None = None,
    fault_hook: Any | None = None,
) -> dict[str, Any]:
    """Publish, validate, then point the binding at ``asset_id``.

    Runs under the lifecycle lock (reconcile → publish → intent → CAS → record).

    A publish or validation failure raises before ``commit``: the old pointer is
    untouched and the candidate never becomes effective. ``actor`` and
    ``evidence_id`` are required because every activation is recorded under
    ``<state>/skills/commits/<revision>.json``.

    Crash consistency (prepared intent): a prepared intent is written before the
    CAS and kept for audit. If the process dies after the CAS but before the
    commit record, ``reconcile_commits`` adopts the intent and re-writes the
    record. ``fault_hook`` is test-only and is called at ``"after_intent"``,
    ``"after_commit"`` and ``"after_record"``.
    """
    state_dir = _state_dir_for(store)
    with _lifecycle_lock(state_dir):
        return _activate_asset_locked(
            registry,
            store,
            asset_id,
            state_dir=state_dir,
            expected_revision=expected_revision,
            actor=actor,
            evidence_id=evidence_id,
            reason=reason,
            sidecar_dir=sidecar_dir,
            package_root=package_root,
            fault_hook=fault_hook,
        )


def _activate_asset_locked(
    registry: Any,
    store: Any,
    asset_id: str,
    *,
    state_dir: Path,
    expected_revision: int,
    actor: str,
    evidence_id: str,
    reason: str | None,
    sidecar_dir: Path | str | None,
    package_root: Path | str | None,
    fault_hook: Any | None,
) -> dict[str, Any]:
    operator = _require_actor(actor, "actor")
    evidence_ref = _require_actor(evidence_id, "evidence_id")

    asset = registry.get(asset_id)
    if asset.kind != "skill":
        raise SkillEvolutionError(f"asset {asset_id} is not a skill")
    # ``active`` is accepted alongside ``approved``: it still means "approved and
    # allowed to use". Without this, one stale CAS retry (or a re-forward after a
    # rollback) would permanently lock a version out, because ``UpperRegistry``
    # has no active→approved transition.
    if asset.status not in REUSABLE_STATUSES:
        raise SkillEvolutionError(
            f"asset {asset_id} must be approved to activate, got {asset.status}"
        )
    entry = _entry_for(asset)
    if not entry["package_hash"]:
        raise SkillEvolutionError(f"asset {asset_id} has no package_hash")

    reconcile_commits(state_dir, store, registry, assume_locked=True)
    source = _assert_executable(asset, entry["package_hash"], package_root)
    published = publish_package(state_dir, source, asset_id)
    if _hash_package_dir(published) != entry["package_hash"]:
        raise SkillEvolutionError(
            f"package_hash_mismatch:{asset_id}: published package differs"
        )

    skill_id = entry["skill_id"]
    previous = store.get_section("skills", skill_id)

    # "Allowed to use": does not stop or rewrite any previous version. An
    # already-active asset needs no second transition.
    if asset.status == "approved":
        registry.activate(asset_id)
    elif asset.status != "active":  # pragma: no cover - guarded above
        raise SkillEvolutionError(
            f"asset {asset_id} must be approved to activate, got {asset.status}"
        )

    # Prepared intent: written before the CAS so a crash between the commit and
    # the record can be reconciled instead of losing the adoption chain.
    intent = _prepare_intent(
        state_dir,
        action="activate",
        skill_id=skill_id,
        expected_revision=expected_revision,
        from_entry=previous,
        to_entry=entry,
        actor=operator,
        reason=reason,
        evidence_id=evidence_ref,
    )
    _call_fault_hook(fault_hook, "after_intent")

    # The single linearization point: one CAS commit carries both sections.
    committed = store.commit(
        "skills", skill_id, entry, expected_revision=intent["expected_revision"]
    )
    revision = _snapshot_revision(store, committed)
    _call_fault_hook(fault_hook, "after_commit")

    cleanup = "not_required"
    superseded = None
    if previous is not None and previous.get("asset_id") != asset_id:
        superseded = previous.get("asset_id")
        if sidecar_dir is None:
            cleanup = "cleanup_pending:sidecar_dir_unknown"
        else:
            try:
                _mark_superseded(
                    sidecar_dir, previous, revision=revision, superseded_by=asset_id
                )
            except Exception as exc:  # pointer stays; report, never roll back
                cleanup = f"cleanup_pending:{exc}"

    record = {
        "revision": revision,
        "action": "activate",
        "actor": operator,
        "reason": reason,
        "evidence_id": evidence_ref,
        "from": _entry_summary(previous),
        "to": _entry_summary(entry),
        "intent_id": intent["intent_id"],
        "at": utc_now(),
    }
    commit_record = _write_commit_record(state_dir, record)
    if commit_record == "written":
        try:
            _mark_intent(state_dir, intent, "committed")
        except Exception:  # record exists; reconcile will mark it later
            pass
    _call_fault_hook(fault_hook, "after_record")
    return {
        "entry": entry,
        "previous": previous,
        "revision": revision,
        "superseded": superseded,
        "package_dir": str(published),
        "cleanup": cleanup,
        "intent_id": intent["intent_id"],
        "commit_record": commit_record,
    }


def rollback_asset(
    registry: Any,
    store: Any,
    asset_id: str,
    *,
    reason: str,
    rolled_back_by: str,
    expected_revision: int,
    previous_entry: dict[str, Any] | None = None,
    sidecar_dir: Path | str | None = None,
    evidence_id: str | None = None,
    fault_hook: Any | None = None,
) -> dict[str, Any]:
    """Move the pointer back to the adopted predecessor (pointer-first).

    The target is derived after ``reconcile_commits`` from the commit chain: the
    ``from`` of the most recent ``action="activate"`` record that adopted
    ``asset_id``. Binding ``history/`` files are never used. An activation that
    committed but whose record was lost is adopted from its prepared intent
    first, so its ``from`` is a valid target; an intent whose CAS never happened
    is stale and can never be a target. With no commit records at all (legacy
    state), only the bootstrap operator baseline is accepted.

    The target must already be published under
    ``<state>/skills/packages/<asset_id>/<skill_id>`` with a matching hash, and —
    when registered — still ``approved``/``active``. Missing target →
    ``rollback_target_missing``; missing package → ``rollback_target_unavailable``.
    A failed CAS keeps the current pointer intact. Runs under the lifecycle lock.
    """
    state_dir = _state_dir_for(store)
    with _lifecycle_lock(state_dir):
        return _rollback_asset_locked(
            registry,
            store,
            asset_id,
            state_dir=state_dir,
            reason=reason,
            rolled_back_by=rolled_back_by,
            expected_revision=expected_revision,
            previous_entry=previous_entry,
            sidecar_dir=sidecar_dir,
            evidence_id=evidence_id,
            fault_hook=fault_hook,
        )


def _rollback_asset_locked(
    registry: Any,
    store: Any,
    asset_id: str,
    *,
    state_dir: Path,
    reason: str,
    rolled_back_by: str,
    expected_revision: int,
    previous_entry: dict[str, Any] | None,
    sidecar_dir: Path | str | None,
    evidence_id: str | None,
    fault_hook: Any | None,
) -> dict[str, Any]:
    actor = _require_actor(rolled_back_by, "rolled_back_by")
    if not isinstance(reason, str) or not reason.strip():
        raise SkillEvolutionError("reason is required")

    skill_id: str | None = None
    # Adopt a commit that happened without its record before deriving a target.
    reconcile_commits(state_dir, store, registry, assume_locked=True)
    binding = store.load()
    current = None
    for key, entry in (binding.get("skills") or {}).items():
        if isinstance(entry, dict) and entry.get("asset_id") == asset_id:
            current = entry
            skill_id = key
            break
    if current is None:
        raise SkillEvolutionError(f"binding_mismatch: no skills binding for {asset_id!r}")
    assert skill_id is not None
    if current.get("skill_id") != skill_id:  # pragma: no cover - store invariant
        raise SkillEvolutionError("binding_mismatch: entry key disagrees with skill_id")

    target = _resolve_rollback_target(state_dir, registry, store, asset_id, skill_id)
    if target is None:
        raise SkillEvolutionError(ROLLBACK_TARGET_MISSING)
    if previous_entry is not None:
        if not isinstance(previous_entry, dict) or set(previous_entry) != set(_ENTRY_KEYS):
            raise SkillEvolutionError(
                "previous_entry must contain exactly skill_id/version/asset_id/package_hash"
            )
        asserted = {name: str(previous_entry[name]) for name in _ENTRY_KEYS}
        if asserted != target:
            raise SkillEvolutionError("rollback_target_mismatch: entry and commit chain disagree")

    try:
        target_asset: UpperAsset | None = registry.get(target["asset_id"])
    except UpperRegistryError:
        raise SkillEvolutionError(ROLLBACK_TARGET_MISSING) from None
    if target_asset.status not in REUSABLE_STATUSES:
        raise SkillEvolutionError(
            f"rollback_target_not_reusable:{target['asset_id']}: {target_asset.status}"
        )
    if (
        target_asset.name != target["skill_id"]
        or target_asset.version != target["version"]
    ):
        raise SkillEvolutionError("rollback_target_mismatch: entry and asset disagree")

    published = _package_target(state_dir, target["asset_id"], target["skill_id"])
    if not published.is_dir():
        raise SkillEvolutionError("rollback_target_unavailable")
    try:
        published_hash = _hash_package_dir(published)
    except (OSError, SkillPackageError, ValueError) as exc:
        raise SkillEvolutionError("rollback_target_unavailable") from exc
    if published_hash != target["package_hash"]:
        raise SkillEvolutionError(
            f"package_hash_mismatch:{target['asset_id']}: "
            f"{published_hash} != {target['package_hash']}"
        )

    intent = _prepare_intent(
        state_dir,
        action="rollback",
        skill_id=skill_id,
        expected_revision=expected_revision,
        from_entry=current,
        to_entry=target,
        actor=actor,
        reason=reason,
        evidence_id=evidence_id,
    )
    _call_fault_hook(fault_hook, "after_intent")

    committed = store.commit(
        "skills", skill_id, target, expected_revision=intent["expected_revision"]
    )
    revision = _snapshot_revision(store, committed)
    _call_fault_hook(fault_hook, "after_commit")

    cleanup = "not_required"
    if sidecar_dir is None:
        cleanup = "cleanup_pending:sidecar_dir_unknown"
    else:
        try:
            _mark_rolled_back(
                sidecar_dir, current, reason=reason, rolled_back_by=actor
            )
        except Exception as exc:  # pointer already switched; report only
            cleanup = f"cleanup_pending:{exc}"

    record = {
        "revision": revision,
        "action": "rollback",
        "actor": actor,
        "reason": reason,
        "evidence_id": evidence_id,
        "from": _entry_summary(current),
        "to": _entry_summary(target),
        "intent_id": intent["intent_id"],
        "at": utc_now(),
    }
    commit_record = _write_commit_record(state_dir, record)
    if commit_record == "written":
        try:
            _mark_intent(state_dir, intent, "committed")
        except Exception:  # record exists; reconcile will mark it later
            pass
    _call_fault_hook(fault_hook, "after_record")
    return {
        "entry": target,
        "previous": current,
        "revision": revision,
        "rolled_back": asset_id,
        "package_dir": str(published),
        "cleanup": cleanup,
        "intent_id": intent["intent_id"],
        "commit_record": commit_record,
    }


__all__ = [
    "BASELINE_KIND",
    "COMMITS_SUBDIR",
    "EVIDENCE_FINALS",
    "EVIDENCE_ASSET_MISMATCH",
    "EVIDENCE_NOT_KEEP",
    "INTENTS_SUBDIR",
    "INTENT_SCHEMA_VERSION",
    "LIFECYCLE_SCHEMA_VERSION",
    "MIGRATION_NOTE",
    "PACKAGES_SUBDIR",
    "REUSABLE_STATUSES",
    "ROLLBACK_TARGET_MISSING",
    "SIDECAR_SCHEMA_VERSION",
    "activate_asset",
    "approve_asset",
    "bootstrap_baseline",
    "mark_evaluated_from_evidence",
    "migrate_existing_binding",
    "publish_package",
    "reconcile_commits",
    "register_asset",
    "rollback_asset",
]
