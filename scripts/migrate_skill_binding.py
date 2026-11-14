#!/usr/bin/env python3
"""One-shot migration / crash reconciliation for skill bindings.

The legacy online shape has a binding entry such as::

    {"skill_id": "repo-evidence", "version": "1.0.0",
     "asset_id": "skill-repo-evidence-1.0.0", "package_hash": "sha256:..."}

with no ``skill-registry``, no published ``skills/packages`` and no
``skills/commits``. The new runtime only trusts registry-backed packages, so the
binding must be migrated once by an operator.

This tool never runs at service startup. It delegates the write to
``molt.skills.lifecycle.migrate_existing_binding``; dry-run constructs the
registry on a temporary path when the state has no registry yet, so a preview
does not create any state. ``--apply`` backs up ``current.json`` first and runs
``reconcile_commits`` before and after the migration.

Exit codes: 0 success/noop, 2 validation failure, 3 internal/recovery needed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from molt.skills import lifecycle  # noqa: E402
from molt.skills.binding import BindingError, BindingStore  # noqa: E402
from molt.skills.evolution import SkillEvolutionError  # noqa: E402
from molt.skills.loader import (  # noqa: E402
    SkillPackageError,
    load_package,
    validate_package,
)
from molt.upper.registry import UpperRegistry, UpperRegistryError  # noqa: E402
from molt.upper.storage import (  # noqa: E402
    StatePathError,
    atomic_write_json,
    ensure_outside_repo,
)

DEFAULT_SKILL_ID = "repo-evidence"
DEFAULT_TARGET_ASSET_ID = "skill-repo-evidence-1-0-0"
DEFAULT_ACTOR = "operator"
DEFAULT_REASON = "migrate existing operator binding"
BACKUP_SUFFIX = "bak"
INTENTS_SUBDIR = ("skills", "intents")


class MigrationError(RuntimeError):
    def __init__(self, code: str, status: int = 2) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def _backup_path(current: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return current.with_name(f"{current.name}.{BACKUP_SUFFIX}-{stamp}")


def _backup_current(current: Path) -> Path:
    backup = _backup_path(current)
    shutil.copy2(current, backup)
    os.chmod(backup, 0o600)
    return backup


def _read_entry(store: BindingStore, skill_id: str) -> dict[str, Any] | None:
    try:
        snapshot = store.load()
    except BindingError:
        raise MigrationError("migration_binding_unreadable", 2) from None
    skills = snapshot.get("skills")
    if not isinstance(skills, dict):
        return None
    entry = skills.get(skill_id)
    return entry if isinstance(entry, dict) else None


def _registry_for(
    state_dir: Path, repo_root: Path, apply: bool
) -> tuple[UpperRegistry, tempfile.TemporaryDirectory[str] | None]:
    registry_root = state_dir / "skill-registry"
    if apply or (registry_root / "registry.json").is_file():
        return UpperRegistry(registry_root, repo_root=repo_root), None
    # Dry-run on a fresh state must not create <skill-state>/skill-registry.
    temp = tempfile.TemporaryDirectory(prefix="molt-migrate-registry-")
    return UpperRegistry(Path(temp.name), repo_root=repo_root), temp


def _intents_dir(state_dir: Path) -> Path:
    return state_dir.joinpath(*INTENTS_SUBDIR)


def _read_intents(state_dir: Path) -> list[dict[str, Any]]:
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
    return intents


def _intent_adoptable(intent: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    expected = intent.get("expected_revision")
    to = intent.get("to")
    if not isinstance(expected, int) or not isinstance(to, dict):
        return False
    if int(snapshot.get("revision", -1)) != expected + 1:
        return False
    entries = snapshot.get("skills")
    if not isinstance(entries, dict):
        return False
    for entry in entries.values():
        if not isinstance(entry, dict):
            continue
        if (
            entry.get("asset_id") == to.get("asset_id")
            and entry.get("version") == to.get("version")
            and entry.get("package_hash") == to.get("hash")
        ):
            return True
    return False


def _cleanup_stale_intents(state_dir: Path, store: BindingStore) -> list[str]:
    """Mark prepared non-adoptable intents stale; never touch current/commits."""

    snapshot = store.load()
    stale: list[str] = []
    for intent in _read_intents(state_dir):
        if intent.get("status") != "prepared":
            continue
        if _intent_adoptable(intent, snapshot):
            continue
        updated = dict(intent)
        updated["status"] = "stale"
        updated["stale_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        updated["reason_detail"] = "cleanup_stale_intents"
        atomic_write_json(_intents_dir(state_dir) / f"{intent['intent_id']}.json", updated)
        stale.append(str(intent["intent_id"]))
    return stale


def _normalize_adopted(result: Any) -> list[dict[str, Any]]:
    adopted: list[dict[str, Any]] = []
    if not isinstance(result, list):
        return adopted
    for item in result:
        if not isinstance(item, dict) or item.get("action") != "adopt":
            continue
        intent_id = item.get("intent_id")
        if not isinstance(intent_id, str) or not intent_id:
            continue
        adopted.append({"intent_id": intent_id, "revision": item.get("revision")})
    return adopted


def _reconcile(
    state_dir: Path, store: BindingStore, registry: UpperRegistry
) -> list[dict[str, Any]]:
    reconcile = getattr(lifecycle, "reconcile_commits", None)
    if not callable(reconcile):
        raise MigrationError("migration_unavailable", 3)
    return _normalize_adopted(reconcile(state_dir, store, registry))


def _stale_ids(state_dir: Path) -> list[str]:
    return [
        str(intent["intent_id"])
        for intent in _read_intents(state_dir)
        if intent.get("status") == "stale"
    ]


def _recovery(
    args: argparse.Namespace, repo_root: Path, backup: Path | None
) -> dict[str, Any]:
    script = Path(__file__).resolve()
    command = (
        f"python3 -B {script} --skill-state {args.skill_state} "
        f"--repo-root {repo_root} --reconcile-only --json"
    )
    return {"command": command, "backup": str(backup) if backup is not None else None}


def _payload(
    status: str,
    *,
    from_entry: Any,
    to_entry: Any,
    revision: Any,
    commit_record: Any,
    registry_status: Any,
    dry_run: bool,
    backup: Path | None = None,
    reconciled: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "from": from_entry,
        "to": to_entry,
        "revision": revision,
        "commit_record": commit_record,
        "registry_status": registry_status,
        "dry_run": dry_run,
    }
    if backup is not None:
        payload["backup"] = str(backup)
    if reconciled:
        payload["reconciled"] = reconciled
    return payload


def _print(payload: dict[str, Any], pretty: bool) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2 if pretty else None,
            sort_keys=False,
        )
    )


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="一次性迁移旧 skill binding（默认 dry-run，不做任何写入）"
    )
    parser.add_argument("--skill-state", type=Path, required=True, help="<skill-state>（仓库外）")
    parser.add_argument("--skills-root", type=Path, default=None, help="默认 <repo>/skills")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--skill-id", default=DEFAULT_SKILL_ID)
    parser.add_argument("--target-asset-id", default=DEFAULT_TARGET_ASSET_ID)
    parser.add_argument("--actor", default=DEFAULT_ACTOR)
    parser.add_argument("--reason", default=DEFAULT_REASON)
    parser.add_argument("--apply", action="store_true", help="实际写入；缺省 dry-run")
    parser.add_argument("--json", action="store_true", help="缩进 JSON 输出")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--reconcile-only", action="store_true", help="只对账 prepared intent，不迁移"
    )
    mode.add_argument(
        "--cleanup-stale-intents",
        action="store_true",
        help="只把不可采用 prepared intent 标 stale（不删文件/不改 current）",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root.expanduser().resolve()
    skills_root = (
        args.skills_root.expanduser()
        if args.skills_root is not None
        else repo_root / "skills"
    )
    dry_run = not args.apply

    def emit(payload: dict[str, Any]) -> int:
        _print(payload, args.json)
        return 0

    def error(code: str, status: int, **extra: Any) -> int:
        payload: dict[str, Any] = {"status": "error", "code": code, "dry_run": dry_run}
        payload.update(extra)
        _print(payload, args.json)
        return status

    try:
        state_dir = ensure_outside_repo(args.skill_state.expanduser(), repo_root)
    except StatePathError:
        return error("migration_state_inside_repo", 2)

    try:
        store = BindingStore(state_dir, repo_root=repo_root)
    except BindingError:
        return error("migration_state_invalid", 2)

    if args.cleanup_stale_intents:
        try:
            stale = _cleanup_stale_intents(state_dir, store)
            revision = store.load()["revision"]
        except (BindingError, OSError, ValueError):
            return error("migration_cleanup_failed", 2)
        return emit(
            {
                "status": "cleaned",
                "adopted": [],
                "stale": stale,
                "revision": revision,
                "dry_run": False,
            }
        )

    if args.reconcile_only:
        registry: UpperRegistry | None = None
        temp: tempfile.TemporaryDirectory[str] | None = None
        try:
            registry, temp = _registry_for(state_dir, repo_root, False)
            adopted = _reconcile(state_dir, store, registry)
            revision = store.load()["revision"]
            stale = _stale_ids(state_dir)
        except MigrationError as exc:
            return error(exc.code, exc.status)
        except (BindingError, UpperRegistryError, OSError, ValueError):
            return error("migration_reconcile_failed", 2)
        finally:
            if temp is not None:
                temp.cleanup()
        return emit(
            {
                "status": "reconciled",
                "adopted": adopted,
                "stale": stale,
                "revision": revision,
                "dry_run": False,
            }
        )

    try:
        entry = _read_entry(store, args.skill_id)
    except MigrationError as exc:
        return error(exc.code, exc.status)
    if entry is None:
        return emit(
            _payload(
                "noop",
                from_entry=None,
                to_entry=None,
                revision=store.load()["revision"],
                commit_record=None,
                registry_status=None,
                dry_run=dry_run,
            )
        )

    try:
        package = load_package(skills_root, args.skill_id)
        validate_package(package)
    except SkillPackageError:
        return error("migration_package_unavailable", 2)
    if package.content_hash != entry.get("package_hash"):
        return error("migration_package_mismatch", 2)

    migrate = getattr(lifecycle, "migrate_existing_binding", None)
    if migrate is None:
        return error("migration_unavailable", 3)

    backup: Path | None = None
    if args.apply:
        try:
            backup = _backup_current(store.path)
        except OSError:
            return error("migration_backup_failed", 3)

    registry = None
    temp = None
    reconciled: list[dict[str, Any]] = []
    try:
        registry, temp = _registry_for(state_dir, repo_root, args.apply)
        if args.apply:
            try:
                reconciled = _reconcile(state_dir, store, registry)
            except (MigrationError, BindingError, UpperRegistryError, OSError, ValueError):
                return error(
                    "migration_reconcile_failed",
                    3,
                    recovery=_recovery(args, repo_root, backup),
                )
            entry = _read_entry(store, args.skill_id)
            if entry is None:
                return emit(
                    _payload(
                        "noop",
                        from_entry=None,
                        to_entry=None,
                        revision=store.load()["revision"],
                        commit_record=None,
                        registry_status=None,
                        dry_run=dry_run,
                        backup=backup,
                        reconciled=reconciled,
                    )
                )
            if package.content_hash != entry.get("package_hash"):
                return error(
                    "migration_package_mismatch",
                    2,
                    backup=str(backup) if backup else None,
                )
        result = migrate(
            registry,
            store,
            package,
            existing_entry=entry,
            target_asset_id=args.target_asset_id,
            actor=args.actor,
            reason=args.reason,
            dry_run=dry_run,
        )
    except (BindingError, SkillEvolutionError, SkillPackageError, UpperRegistryError):
        return error(
            "migration_failed",
            3,
            backup=str(backup) if backup else None,
            recovery=_recovery(args, repo_root, backup),
        )
    except Exception:  # noqa: BLE001 - CLI boundary
        return error(
            "migration_internal_error",
            3,
            backup=str(backup) if backup else None,
            recovery=_recovery(args, repo_root, backup),
        )
    finally:
        if temp is not None:
            temp.cleanup()

    status = str(result.get("status", "planned"))
    if status not in ("noop", "planned", "applied"):
        return error("migration_result_invalid", 3)

    commit_record = result.get("commit_record")
    if isinstance(commit_record, str) and commit_record.startswith("commit_record_pending"):
        payload = _payload(
            status,
            from_entry=result.get("from"),
            to_entry=result.get("to"),
            revision=result.get("revision"),
            commit_record=commit_record,
            registry_status=result.get("registry_status"),
            dry_run=dry_run,
            backup=backup,
            reconciled=reconciled,
        )
        payload["recovery"] = _recovery(args, repo_root, backup)
        _print(payload, args.json)
        return 3

    if args.apply:
        try:
            more = _reconcile(state_dir, store, registry)
        except (MigrationError, BindingError, UpperRegistryError, OSError, ValueError):
            return error(
                "migration_reconcile_failed",
                3,
                backup=str(backup) if backup else None,
                recovery=_recovery(args, repo_root, backup),
            )
        reconciled.extend(more)
    return emit(
        _payload(
            status,
            from_entry=result.get("from"),
            to_entry=result.get("to"),
            revision=result.get("revision"),
            commit_record=commit_record,
            registry_status=result.get("registry_status"),
            dry_run=dry_run,
            backup=backup,
            reconciled=reconciled,
        )
    )


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - last-resort CLI boundary
        print(json.dumps({"status": "error", "code": "migration_internal_error"}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
