"""Append-only upper-layer state graph.

The graph is a small, branch-oriented context DAG, not a general VCS:

- nodes are immutable and only appended to ``upper/nodes.jsonl``;
- branches hold a pointer to a committed node;
- ``upper/current.json`` is the single crash-atomic branch/active pointer;
- ``switch`` and ``rollback`` are CAS operations guarded by ``fcntl.flock``;
- there is **no merge, no CRDT, and no delete API**.

Pointer/tip changes use an intent/commit event protocol around one atomic
current-file replace; startup recovery reconciles an intent without commit.

Every switch/rollback validates the target node's context hash and the current
registry approval state (D6): each referenced asset must be ``approved`` or
``active`` with ``approved_by``/``approved_at``, and MCP profiles additionally
require ``approval_level="strict"``.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .registry import UpperRegistry
from .storage import atomic_write_json, ensure_outside_repo
from .types import (
    ASSET_EVAL_STATES,
    ASSET_KINDS,
    ASSET_ORIGINS,
    canonical_json,
    is_valid_iso8601,
    sha256_text,
    utc_now,
)

CURRENT_SCHEMA_VERSION = "1.0.0"
NODE_KINDS = ("root", "context", "evaluation", "decision", "rollback")
NODE_STATUSES = ("candidate", "committed")
APPROVAL_LEVELS = ("standard", "strict")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class GraphError(RuntimeError):
    """Base class for graph failures."""


class GraphValidationError(GraphError):
    """The target node or manifest is structurally invalid."""


class GraphIntegrityError(GraphError):
    """Persisted node bytes do not match their recorded node id."""


class ConflictError(GraphError):
    """CAS expected head/active branch does not match the current pointer."""


class ApprovalError(GraphError):
    """The target context has assets that are not approved for activation."""


class ContextChangedError(GraphError):
    """The active context changed while an evaluation was running."""


class GraphRecoveryError(GraphError):
    """Startup intent recovery could not append a commit/abort event."""


class CommitEventPending(GraphError):
    """Current state was committed, but the commit event append failed.

    The state is fully the new revision; reopening the graph performs idempotent
    recovery and appends the missing commit event.
    """


def _normalize_refs(refs: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for ref in refs:
        item = {
            "asset_id": str(ref["asset_id"]),
            "version": str(ref["version"]),
            "hash": str(ref["hash"]),
        }
        normalized.append(item)
    return sorted(normalized, key=lambda item: (item["asset_id"], item["version"], item["hash"]))


def _normalize_mcp(refs: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for ref in refs:
        item = {
            "profile_id": str(ref["profile_id"]),
            "version": str(ref["version"]),
            "hash": str(ref["hash"]),
            "permission_scope": str(ref.get("permission_scope", "")),
            "approval_level": str(ref.get("approval_level", "")),
        }
        normalized.append(item)
    return sorted(normalized, key=lambda item: (item["profile_id"], item["version"], item["hash"]))


def compute_context_hash(manifest: dict[str, Any]) -> str:
    payload = {key: value for key, value in manifest.items() if key != "context_hash"}
    return sha256_text(canonical_json(payload))


def build_context_manifest(
    *,
    parents: list[str] | tuple[str, ...],
    policy_memory_refs: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    skill_refs: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    mcp_profiles: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    project_requirements: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
    scenario_refs: list[str] | tuple[str, ...] = (),
    scenario_version: str = "",
    eval_refs: list[str] | tuple[str, ...] = (),
    task_refs: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "parents": list(parents),
        "policy_memory_refs": _normalize_refs(list(policy_memory_refs)),
        "skill_refs": _normalize_refs(list(skill_refs)),
        "mcp_profiles": _normalize_mcp(list(mcp_profiles)),
        "project_requirements": dict(project_requirements or {"ref": None, "hash": None}),
        "budget": dict(budget or {"limits": {}}),
        "scenario_refs": sorted(str(item) for item in scenario_refs),
        "scenario_version": str(scenario_version),
        "eval_refs": sorted(str(item) for item in eval_refs),
        "task_refs": sorted(str(item) for item in task_refs),
    }
    manifest["context_hash"] = compute_context_hash(manifest)
    return manifest


def manifest_with(manifest: dict[str, Any], **changes: Any) -> dict[str, Any]:
    payload = {key: value for key, value in manifest.items() if key != "context_hash"}
    payload.update(changes)
    payload["context_hash"] = compute_context_hash(payload)
    return payload


def asset_refs_from_manifest(manifest: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    refs: dict[tuple[str, str, str], dict[str, Any]] = {}
    for field in ("policy_memory_refs", "skill_refs"):
        for ref in manifest.get(field, []):
            key = (str(ref["asset_id"]), str(ref["version"]), str(ref["hash"]))
            refs[key] = {
                "asset_id": key[0],
                "version": key[1],
                "hash": key[2],
            }
    for profile in manifest.get("mcp_profiles", []):
        key = (str(profile["profile_id"]), str(profile["version"]), str(profile["hash"]))
        refs[key] = {
            "asset_id": key[0],
            "version": key[1],
            "hash": key[2],
        }
    return tuple(
        sorted(refs.values(), key=lambda item: (item["asset_id"], item["version"], item["hash"]))
    )


def _node_payload(node: UpperNode, *, include_id: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "parents": list(node.parents),
        "kind": node.kind,
        "status": node.status,
        "context_hash": node.context_hash,
        "manifest": node.manifest,
        "asset_refs": list(node.asset_refs),
        "task_refs": list(node.task_refs),
        "evaluation_id": node.evaluation_id,
        "evidence_refs": list(node.evidence_refs),
        "message": node.message,
        "origin": node.origin,
        "eval_state": node.eval_state,
        "source_category": node.source_category,
        "created_at": node.created_at,
        "author": node.author,
    }
    if include_id:
        payload["node_id"] = node.node_id
    return payload


def _node_id_payload(node: UpperNode) -> dict[str, Any]:
    """Semantic node identity: metadata timestamps/authors are excluded."""
    payload = _node_payload(node, include_id=False)
    payload.pop("created_at", None)
    payload.pop("author", None)
    return payload


@dataclass(frozen=True)
class UpperNode:
    node_id: str
    parents: tuple[str, ...]
    kind: str
    status: str
    context_hash: str
    manifest: dict[str, Any]
    asset_refs: tuple[dict[str, Any], ...]
    task_refs: tuple[str, ...]
    evaluation_id: str | None
    evidence_refs: tuple[str, ...]
    message: str
    origin: str
    eval_state: str
    source_category: str
    created_at: str
    author: str

    def __post_init__(self) -> None:
        if self.kind not in NODE_KINDS:
            raise GraphValidationError(f"unknown node kind: {self.kind!r}")
        if self.status not in NODE_STATUSES:
            raise GraphValidationError(f"unknown node status: {self.status!r}")
        if self.kind == "root" and self.parents:
            raise GraphValidationError("root node must not have parents")
        if self.kind != "root" and not self.parents:
            raise GraphValidationError("non-root node requires a parent")
        if self.kind == "root" and self.status != "committed":
            raise GraphValidationError("root node must be committed")
        if not self.author.strip():
            raise GraphValidationError("node author is required")
        if self.origin not in ASSET_ORIGINS:
            raise GraphValidationError(f"unknown node origin: {self.origin!r}")
        if self.eval_state not in ASSET_EVAL_STATES:
            raise GraphValidationError(f"unknown node eval_state: {self.eval_state!r}")
        if self.origin in {"seed_import", "synthetic"} and self.eval_state != "not_evaluated":
            raise GraphValidationError(f"{self.origin} nodes must be not_evaluated")
        required = {
            "parents",
            "policy_memory_refs",
            "skill_refs",
            "mcp_profiles",
            "project_requirements",
            "budget",
            "scenario_refs",
            "scenario_version",
            "eval_refs",
            "task_refs",
            "context_hash",
        }
        missing = sorted(required - set(self.manifest))
        if missing:
            raise GraphValidationError(f"manifest missing fields: {missing}")
        if list(self.parents) != list(self.manifest.get("parents", [])):
            raise GraphValidationError("manifest parents must match node parents")
        if list(self.task_refs) != list(self.manifest.get("task_refs", [])):
            raise GraphValidationError("node task_refs must match manifest task_refs")
        if self.context_hash != self.manifest.get("context_hash"):
            raise GraphValidationError("node context_hash must match manifest context_hash")
        expected_hash = compute_context_hash(self.manifest)
        if self.context_hash != expected_hash:
            raise GraphValidationError("context_hash does not match the manifest content")
        expected_refs = asset_refs_from_manifest(self.manifest)
        if tuple(self.asset_refs) != expected_refs:
            raise GraphValidationError("node asset_refs do not match the manifest refs")
        if not HEX64.match(self.node_id):
            raise GraphValidationError("node_id must be a sha256 hex digest")
        expected_id = sha256_text(canonical_json(_node_id_payload(self)))
        if self.node_id != expected_id:
            raise GraphIntegrityError("node_id does not match the canonical node payload")

    @classmethod
    def build(
        cls,
        *,
        parents: list[str] | tuple[str, ...],
        kind: str,
        manifest: dict[str, Any],
        status: str = "committed",
        evaluation_id: str | None = None,
        evidence_refs: list[str] | tuple[str, ...] = (),
        message: str = "",
        origin: str = "synthetic",
        eval_state: str = "not_evaluated",
        source_category: str = "synthetic_fixture",
        author: str = "system",
        created_at: str | None = None,
    ) -> UpperNode:
        manifest = dict(manifest)
        if list(manifest.get("parents", [])) != list(parents):
            manifest = manifest_with(manifest, parents=list(parents))
        if "context_hash" not in manifest:
            manifest["context_hash"] = compute_context_hash(manifest)
        context_hash = str(manifest["context_hash"])
        refs = asset_refs_from_manifest(manifest)
        task_refs = tuple(manifest.get("task_refs", []))
        created_at = created_at or utc_now()
        payload = {
            "parents": list(parents),
            "kind": kind,
            "status": status,
            "context_hash": context_hash,
            "manifest": manifest,
            "asset_refs": list(refs),
            "task_refs": list(task_refs),
            "evaluation_id": evaluation_id,
            "evidence_refs": list(evidence_refs),
            "message": message,
            "origin": origin,
            "eval_state": eval_state,
            "source_category": source_category,
        }
        node_id = sha256_text(canonical_json(payload))
        return cls(
            node_id=node_id,
            parents=tuple(parents),
            kind=kind,
            status=status,
            context_hash=context_hash,
            manifest=manifest,
            asset_refs=refs,
            task_refs=task_refs,
            evaluation_id=evaluation_id,
            evidence_refs=tuple(evidence_refs),
            message=message,
            origin=origin,
            eval_state=eval_state,
            source_category=source_category,
            created_at=created_at,
            author=author,
        )

    def to_dict(self) -> dict[str, Any]:
        return _node_payload(self, include_id=True)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> UpperNode:
        return cls(
            node_id=payload["node_id"],
            parents=tuple(payload.get("parents", ())),
            kind=payload["kind"],
            status=payload["status"],
            context_hash=payload["context_hash"],
            manifest=dict(payload["manifest"]),
            asset_refs=tuple(dict(ref) for ref in payload.get("asset_refs", ())),
            task_refs=tuple(payload.get("task_refs", ())),
            evaluation_id=payload.get("evaluation_id"),
            evidence_refs=tuple(payload.get("evidence_refs", ())),
            message=payload.get("message", ""),
            origin=payload.get("origin", "synthetic"),
            eval_state=payload.get("eval_state", "not_evaluated"),
            source_category=payload.get("source_category", "synthetic_fixture"),
            created_at=payload["created_at"],
            author=payload["author"],
        )

    @property
    def provenance_class(self) -> str:
        if self.origin == "seed_import":
            return "seed_import"
        if self.origin == "synthetic":
            return "synthetic_reconstructed"
        if self.origin == "real_run" and self.eval_state == "replayed":
            return "real_replay"
        if self.origin == "real_run" and self.eval_state == "evaluated":
            return "real_evaluation"
        return "real_run"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "hash_short": self.node_id[:12],
            "parents": list(self.parents),
            "kind": self.kind,
            "status": self.status,
            "context_hash": self.context_hash,
            "asset_refs": [dict(ref) for ref in self.asset_refs],
            "task_refs": list(self.task_refs),
            "evaluation_id": self.evaluation_id,
            "message": self.message,
            "origin": self.origin,
            "eval_state": self.eval_state,
            "provenance_class": self.provenance_class,
            "source_category": self.source_category,
            "created_at": self.created_at,
            "author": self.author,
            "manifest": self.manifest,
        }


@dataclass
class UpperBranch:
    branch_id: str
    label: str
    permission_scope: str | None
    approval_level: str | None
    tip_node_id: str
    base_node_id: str
    created_at: str
    updated_at: str

    @property
    def name(self) -> str:
        return self.label

    def __post_init__(self) -> None:
        if not self.branch_id or not self.label or not self.tip_node_id or not self.base_node_id:
            raise GraphValidationError("branch id/label/tip/base are required")
        if self.approval_level is not None and self.approval_level not in APPROVAL_LEVELS:
            raise GraphValidationError(f"unknown branch approval_level: {self.approval_level!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch_id": self.branch_id,
            "label": self.label,
            "name": self.label,
            "permission_scope": self.permission_scope,
            "approval_level": self.approval_level,
            "tip_node_id": self.tip_node_id,
            "head_node_id": self.tip_node_id,
            "base_node_id": self.base_node_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> UpperBranch:
        return cls(
            branch_id=payload["branch_id"],
            label=payload.get("label", payload.get("name", "")),
            permission_scope=payload.get("permission_scope"),
            approval_level=payload.get("approval_level"),
            tip_node_id=payload.get("tip_node_id", payload.get("head_node_id")),
            base_node_id=payload["base_node_id"],
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
        )

    def to_public_dict(self) -> dict[str, Any]:
        return self.to_dict()


@dataclass(frozen=True)
class ContextBinding:
    graph: UpperGraphStore
    branch_id: str
    node_id: str
    context_hash: str

    @classmethod
    def capture(cls, graph: UpperGraphStore, *, branch_id: str | None = None) -> ContextBinding:
        active = graph.active_pointer()
        if not active:
            raise ContextChangedError("no active graph pointer to bind")
        if branch_id is not None and active["branch_id"] != branch_id:
            raise ContextChangedError("requested branch is not the active branch")
        return cls(
            graph=graph,
            branch_id=active["branch_id"],
            node_id=active.get("node_id", active["head_node_id"]),
            context_hash=active["context_hash"],
        )

    def assert_unchanged(self) -> None:
        active = self.graph.active_pointer()
        if not active:
            raise ContextChangedError("active graph pointer disappeared")
        node_id = active.get("node_id", active.get("head_node_id"))
        if (
            active["branch_id"] != self.branch_id
            or node_id != self.node_id
            or active["context_hash"] != self.context_hash
        ):
            raise ContextChangedError(
                f"active context changed: {self.branch_id}/{self.node_id} -> "
                f"{active['branch_id']}/{node_id}"
            )
        try:
            self.graph.validate_context(self.node_id, self.branch_id)
        except GraphError as exc:
            raise ContextChangedError(f"registry/context validation changed: {exc}") from exc



class UpperGraphStore:
    """Crash-atomic graph store: one ``upper/current.json`` is the truth.

    Pointer updates use an intent/commit protocol:

    1. append ``intent`` event (event_id, target_revision, payload_hash, ...);
    2. write the single current file with ``revision+1`` via temp + fsync +
       ``os.replace`` + directory fsync;
    3. append ``commit`` event referencing the intent.

    On open, an intent without commit is reconciled: if current.revision and the
    payload hash match the intent, a recovery commit is appended; otherwise an
    abort is appended and the current pointer stays old.
    """

    def __init__(
        self,
        state_dir: Path | str,
        *,
        repo_root: Path | str,
        registry: UpperRegistry | Any | None = None,
        fault_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.state_dir = ensure_outside_repo(Path(state_dir), self.repo_root)
        self.graph_dir = self.state_dir / "upper"
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        self.current_path = self.graph_dir / "current.json"
        self.nodes_path = self.graph_dir / "nodes.jsonl"
        self.events_path = self.graph_dir / "events.jsonl"
        self.lock_path = self.graph_dir / "lock"
        self.lock_path.touch(exist_ok=True)
        self.registry = registry
        self.fault_hook = fault_hook
        self._nodes: list[UpperNode] = []
        self._current: dict[str, Any] = {}
        self._branches: dict[str, UpperBranch] = {}
        self._write_locked = False
        with self._locked(bootstrap=True):
            pass

    # ---- bootstrapping / loading -------------------------------------
    def _bootstrap_locked(self) -> None:
        if self.current_path.exists():
            return
        legacy_branches = self.state_dir / "branches.json"
        legacy_active = self.state_dir / "active.json"
        if not self.nodes_path.exists():
            legacy_nodes = self.state_dir / "nodes.jsonl"
            if legacy_nodes.exists():
                shutil.copyfile(legacy_nodes, self.nodes_path)
        if not self.events_path.exists():
            legacy_events = self.state_dir / "events.jsonl"
            if legacy_events.exists():
                shutil.copyfile(legacy_events, self.events_path)
        if legacy_branches.exists() or legacy_active.exists():
            branches: list[dict[str, Any]] = []
            if legacy_branches.exists():
                payload = json.loads(legacy_branches.read_text(encoding="utf-8"))
                branches = list(payload.get("branches", []))
            active: dict[str, Any] = {}
            if legacy_active.exists():
                payload = json.loads(legacy_active.read_text(encoding="utf-8"))
                active = dict(payload.get("active") or {})
            current = {
                "schema_version": CURRENT_SCHEMA_VERSION,
                "revision": int(active.get("revision", 0)),
                "active_branch_id": active.get("branch_id"),
                "context_node_id": active.get("node_id", active.get("head_node_id")),
                "context_hash": active.get("context_hash"),
                "branches": sorted(branches, key=lambda item: item["branch_id"]),
                "updated_at": utc_now(),
                "last_event_seq": 0,
            }
            atomic_write_json(self.current_path, current)
            self._append_event(
                {
                    "type": "migrate_legacy",
                    "op": "migrate",
                    "branch_id": current["active_branch_id"],
                    "old_node_id": None,
                    "new_node_id": current["context_node_id"],
                    "old_active_branch": None,
                    "new_active_branch": current["active_branch_id"],
                    "revision": current["revision"],
                    "author": "migration",
                }
            )
            return
        initial = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "revision": 0,
            "active_branch_id": None,
            "context_node_id": None,
            "context_hash": None,
            "branches": [],
            "updated_at": utc_now(),
            "last_event_seq": 0,
        }
        atomic_write_json(self.current_path, initial)

    def _load_current(self) -> dict[str, Any]:
        if not self.current_path.exists():
            raise GraphIntegrityError("graph current.json is missing")
        payload = json.loads(self.current_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != CURRENT_SCHEMA_VERSION:
            raise GraphIntegrityError("unsupported current.json schema")
        revision = int(payload.get("revision", -1))
        if revision < 0:
            raise GraphIntegrityError("current.revision must be >= 0")
        current = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "revision": revision,
            "active_branch_id": payload.get("active_branch_id"),
            "context_node_id": payload.get("context_node_id"),
            "context_hash": payload.get("context_hash"),
            "branches": list(payload.get("branches", [])),
            "updated_at": payload.get("updated_at", utc_now()),
            "last_event_seq": int(payload.get("last_event_seq", 0)),
        }
        if current["active_branch_id"] is None:
            if current["context_node_id"] is not None or current["context_hash"] is not None:
                raise GraphIntegrityError("inactive current must not carry a context")
        elif not current["context_node_id"] or not current["context_hash"]:
            raise GraphIntegrityError("active current requires context node/hash")
        return current

    def _branches_from_current(self) -> dict[str, UpperBranch]:
        branches: dict[str, UpperBranch] = {}
        for item in self._current.get("branches", []):
            branch = UpperBranch.from_dict(item)
            if branch.branch_id in branches:
                raise GraphIntegrityError(f"duplicate branch_id: {branch.branch_id}")
            branches[branch.branch_id] = branch
        return branches

    @staticmethod
    def _branch_dicts(branches: list[UpperBranch]) -> list[dict[str, Any]]:
        return [
            {
                "branch_id": branch.branch_id,
                "label": branch.label,
                "permission_scope": branch.permission_scope,
                "approval_level": branch.approval_level,
                "tip_node_id": branch.tip_node_id,
                "base_node_id": branch.base_node_id,
                "created_at": branch.created_at,
                "updated_at": branch.updated_at,
            }
            for branch in sorted(branches, key=lambda item: item.branch_id)
        ]

    @staticmethod
    def _current_payload(current: dict[str, Any]) -> dict[str, Any]:
        return {
            "revision": current["revision"],
            "active_branch_id": current["active_branch_id"],
            "context_node_id": current["context_node_id"],
            "context_hash": current["context_hash"],
            "branches": current["branches"],
        }

    def _current_payload_hash(self) -> str:
        return sha256_text(canonical_json(self._current_payload(self._current)))

    def _reload(self) -> None:
        self._nodes = self._load_nodes()
        self._current = self._load_current()
        self._branches = self._branches_from_current()
        self._validate_current()

    def _validate_current(self) -> None:
        nodes = {node.node_id: node for node in self._nodes}
        for branch in self._branches.values():
            tip = nodes.get(branch.tip_node_id)
            base = nodes.get(branch.base_node_id)
            if tip is None:
                raise GraphIntegrityError(f"branch tip node is missing: {branch.branch_id}")
            if base is None:
                raise GraphIntegrityError(f"branch base node is missing: {branch.branch_id}")
            if tip.status != "committed" or base.status != "committed":
                raise GraphIntegrityError("branch tips/base must be committed nodes")
        active_branch_id = self._current["active_branch_id"]
        if active_branch_id is None:
            return
        branch = self._branches.get(active_branch_id)
        if branch is None:
            raise GraphIntegrityError("active branch is missing")
        node_id = self._current["context_node_id"]
        if branch.tip_node_id != node_id:
            raise GraphIntegrityError("active context is not the branch tip")
        node = nodes.get(node_id)
        if node is None:
            raise GraphIntegrityError("active context node is missing")
        if node.context_hash != self._current["context_hash"]:
            raise GraphIntegrityError("active context_hash does not match the node")

    def _load_nodes(self) -> list[UpperNode]:
        if not self.nodes_path.exists():
            return []
        nodes: list[UpperNode] = []
        seen: set[str] = set()
        for line_number, line in enumerate(
            self.nodes_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                node = UpperNode.from_dict(json.loads(line))
            except Exception as exc:
                raise GraphIntegrityError(
                    f"invalid node line {line_number}: {type(exc).__name__}: {exc}"
                ) from exc
            if node.node_id in seen:
                raise GraphIntegrityError(f"duplicate node_id: {node.node_id}")
            seen.add(node.node_id)
            nodes.append(node)
        return nodes

    def _load_events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            self.events_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise GraphIntegrityError(f"invalid event line {line_number}") from exc
            if not isinstance(payload, dict):
                raise GraphIntegrityError(f"event line {line_number} is not an object")
            events.append(payload)
        return events

    # ---- persistence --------------------------------------------------
    def _append_line(self, path: Path, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

    def _append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = dict(event)
        payload.setdefault("created_at", utc_now())
        if "event_id" not in payload:
            payload["event_id"] = "evt-" + sha256_text(canonical_json(payload))[:16]
        self._append_line(self.events_path, payload)
        return payload

    def _write_current(self, current: dict[str, Any]) -> None:
        atomic_write_json(self.current_path, current, fault_hook=self.fault_hook)

    def _fault(self, point: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(point)

    @contextmanager
    def _locked(self, *, bootstrap: bool = False) -> Iterator[None]:
        with open(self.lock_path, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            self._write_locked = True
            try:
                if bootstrap:
                    self._bootstrap_locked()
                self._reload()
                self._recover_locked()
                yield
            finally:
                self._write_locked = False
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _read_guard(self) -> Iterator[None]:
        """Shared-lock reads so a UI never observes a half-applied commit.

        Writers hold ``LOCK_EX`` across the whole intent/current/commit
        transaction, so readers block until the state is consistent. Inside a
        write transaction the in-memory state is already consistent, so no
        second (deadlocking) lock is taken.
        """
        if self._write_locked:
            yield
            return
        with open(self.lock_path, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _reload_for_read(self) -> None:
        try:
            self._reload()
        except GraphIntegrityError:
            # A concurrent writer may have been observed between the nodes and
            # current reads; retry once under the shared lock before failing.
            self._reload()

    def _recover_locked(self) -> None:
        events = self._load_events()
        committed = {
            event.get("intent_event_id")
            for event in events
            if event.get("type") == "commit"
        }
        aborted = {
            event.get("intent_event_id")
            for event in events
            if event.get("type") == "abort"
        }
        for intent in events:
            if intent.get("type") != "intent":
                continue
            intent_id = intent.get("event_id")
            if not intent_id or intent_id in committed or intent_id in aborted:
                continue
            target_revision = intent.get("target_revision")
            payload_hash = intent.get("payload_hash")
            matches = (
                self._current["revision"] == target_revision
                and self._current_payload_hash() == payload_hash
            )
            if matches:
                event = {
                    "type": "commit",
                    "op": intent.get("op"),
                    "branch_id": intent.get("branch_id"),
                    "old_node_id": intent.get("old_node_id"),
                    "new_node_id": intent.get("new_node_id"),
                    "old_active_branch": intent.get("old_active_branch"),
                    "new_active_branch": intent.get("new_active_branch"),
                    "revision": target_revision,
                    "intent_event_id": intent_id,
                    "event_seq": self._current["last_event_seq"],
                    "author": "recovery",
                    "recovered": True,
                }
            else:
                event = {
                    "type": "abort",
                    "op": intent.get("op"),
                    "branch_id": intent.get("branch_id"),
                    "old_node_id": intent.get("old_node_id"),
                    "new_node_id": intent.get("new_node_id"),
                    "old_active_branch": intent.get("old_active_branch"),
                    "new_active_branch": intent.get("new_active_branch"),
                    "revision": self._current["revision"],
                    "intent_event_id": intent_id,
                    "author": "recovery",
                    "reason": "current revision/payload does not match intent",
                }
            try:
                self._append_event(event)
            except Exception as exc:
                raise GraphRecoveryError(
                    f"failed to recover intent {intent_id}: {exc}"
                ) from exc
            if event["type"] == "commit":
                committed.add(intent_id)
            else:
                aborted.add(intent_id)

    # ---- lookups ------------------------------------------------------
    def _refresh(self) -> None:
        if self._write_locked:
            return
        with self._read_guard():
            self._reload_for_read()

    def nodes(self) -> list[UpperNode]:
        with self._read_guard():
            self._reload_for_read()
            return list(self._nodes)

    def branches(self) -> list[UpperBranch]:
        self._refresh()
        return sorted(self._branches.values(), key=lambda item: item.branch_id)

    def branch(self, branch_id: str) -> UpperBranch:
        self._refresh()
        try:
            return self._branches[branch_id]
        except KeyError:
            raise GraphValidationError(f"unknown branch: {branch_id!r}") from None

    def node(self, node_id: str) -> UpperNode:
        if not self._write_locked:
            with self._read_guard():
                self._reload_for_read()
        for node in self._nodes:
            if node.node_id == node_id:
                return node
        raise GraphValidationError(f"unknown node: {node_id!r}")

    def events(self) -> list[dict[str, Any]]:
        return self._load_events()

    def _active_pointer_from_current(self) -> dict[str, Any] | None:
        if self._current["active_branch_id"] is None:
            return None
        node_id = self._current["context_node_id"]
        node = next(item for item in self._nodes if item.node_id == node_id)
        return {
            "branch_id": self._current["active_branch_id"],
            "head_node_id": node_id,
            "node_id": node_id,
            "context_hash": self._current["context_hash"],
            "revision": self._current["revision"],
            "updated_at": self._current["updated_at"],
            "last_event_seq": self._current["last_event_seq"],
            "context_snapshot": node.manifest,
        }

    def active_pointer(self) -> dict[str, Any] | None:
        self._refresh()
        return self._active_pointer_from_current()

    def active_node(self) -> UpperNode | None:
        self._refresh()
        if self._current["active_branch_id"] is None:
            return None
        node_id = self._current["context_node_id"]
        return next(item for item in self._nodes if item.node_id == node_id)

    def active_asset_refs(self) -> list[dict[str, Any]]:
        node = self.active_node()
        if node is None:
            return []
        return [dict(ref) for ref in node.asset_refs]

    def root_node(self) -> UpperNode:
        for node in self.nodes():
            if node.kind == "root":
                return node
        raise GraphValidationError("graph has no root node")

    def validate_context(self, node_id: str, branch_id: str) -> UpperNode:
        """Validate a committed context against current registry approvals (D6)."""
        self._refresh()
        branch = self._branches.get(branch_id)
        if branch is None:
            raise GraphValidationError(f"unknown branch: {branch_id!r}")
        node = self.node(node_id)
        if node.status != "committed":
            raise GraphValidationError("context node is not committed")
        self._validate_target(node, branch)
        return node

    def to_public_dict(self) -> dict[str, Any]:
        self._refresh()
        active = self._active_pointer_from_current()
        return {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "state_scope": "external",
            "revision": self._current["revision"],
            "last_event_seq": self._current["last_event_seq"],
            "active": active,
            "branches": [
                branch.to_public_dict()
                for branch in sorted(self._branches.values(), key=lambda item: item.branch_id)
            ],
            "nodes": [node.to_public_dict() for node in self._nodes],
            "merge_supported": False,
            "delete_supported": False,
        }

    # ---- mutation -----------------------------------------------------
    def create_root(
        self,
        *,
        author: str = "system",
        message: str = "root",
        scenario_refs: list[str] | tuple[str, ...] = (),
        scenario_version: str = "",
        project_requirements: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
    ) -> UpperNode:
        with self._locked():
            for node in self._nodes:
                if node.kind == "root":
                    return node
            manifest = build_context_manifest(
                parents=[],
                scenario_refs=scenario_refs,
                scenario_version=scenario_version,
                project_requirements=project_requirements,
                budget=budget,
            )
            node = UpperNode.build(
                parents=[],
                kind="root",
                status="committed",
                manifest=manifest,
                message=message,
                author=author,
            )
            self._append_node_locked(node)
            self._append_event(
                {
                    "type": "create_root",
                    "op": "root",
                    "branch_id": None,
                    "old_node_id": None,
                    "new_node_id": node.node_id,
                    "old_active_branch": None,
                    "new_active_branch": None,
                    "revision": self._current["revision"],
                    "author": author,
                }
            )
            return node

    def _append_node_locked(self, node: UpperNode) -> UpperNode:
        for existing in self._nodes:
            if existing.node_id == node.node_id:
                return existing
        self._append_line(self.nodes_path, node.to_dict())
        self._nodes.append(node)
        return node

    def append_node(
        self,
        parent_ids: list[str] | tuple[str, ...],
        *,
        kind: str,
        manifest: dict[str, Any] | None = None,
        evaluation_id: str | None = None,
        evidence_refs: list[str] | tuple[str, ...] = (),
        message: str = "",
        origin: str = "synthetic",
        eval_state: str = "not_evaluated",
        source_category: str = "synthetic_fixture",
        author: str = "system",
        status: str = "committed",
    ) -> UpperNode:
        with self._locked():
            if kind not in NODE_KINDS or kind == "root":
                raise GraphValidationError("append_node kind must be context/evaluation/decision/rollback")
            if not parent_ids:
                raise GraphValidationError("append_node requires parent ids")
            for parent_id in parent_ids:
                self.node(parent_id)
            if manifest is None:
                manifest = self.node(parent_ids[0]).manifest
            manifest = manifest_with(manifest, parents=list(parent_ids))
            node = UpperNode.build(
                parents=parent_ids,
                kind=kind,
                status=status,
                manifest=manifest,
                evaluation_id=evaluation_id,
                evidence_refs=evidence_refs,
                message=message,
                origin=origin,
                eval_state=eval_state,
                source_category=source_category,
                author=author,
            )
            node = self._append_node_locked(node)
            self._append_event(
                {
                    "type": "append",
                    "op": kind,
                    "branch_id": None,
                    "old_node_id": parent_ids[0],
                    "new_node_id": node.node_id,
                    "old_active_branch": self._current["active_branch_id"],
                    "new_active_branch": self._current["active_branch_id"],
                    "revision": self._current["revision"],
                    "author": author,
                    "kind": kind,
                }
            )
            return node

    def create_candidate(
        self,
        parent: str,
        manifest: dict[str, Any],
        evaluation_ref: str,
        *,
        author: str = "system",
        message: str = "",
        evidence_refs: list[str] | tuple[str, ...] = (),
        origin: str = "synthetic",
        eval_state: str = "not_evaluated",
        source_category: str = "synthetic_fixture",
    ) -> UpperNode:
        with self._locked():
            parent_node = self.node(parent)
            if parent_node.status != "committed":
                raise GraphValidationError("candidate parent must be committed")
            eval_refs = sorted(set(manifest.get("eval_refs", [])) | {evaluation_ref})
            manifest = manifest_with(manifest, parents=[parent], eval_refs=eval_refs)
            node = UpperNode.build(
                parents=[parent],
                kind="context",
                status="candidate",
                manifest=manifest,
                evaluation_id=evaluation_ref,
                evidence_refs=evidence_refs,
                message=message or f"candidate {evaluation_ref}",
                origin=origin,
                eval_state=eval_state,
                source_category=source_category,
                author=author,
            )
            node = self._append_node_locked(node)
            self._append_event(
                {
                    "type": "candidate",
                    "op": "candidate",
                    "branch_id": None,
                    "old_node_id": parent,
                    "new_node_id": node.node_id,
                    "old_active_branch": self._current["active_branch_id"],
                    "new_active_branch": self._current["active_branch_id"],
                    "revision": self._current["revision"],
                    "author": author,
                    "evaluation_id": evaluation_ref,
                }
            )
            return node

    def commit_candidate(
        self,
        node_id: str,
        expected_tip: str,
        approval: dict[str, Any],
        *,
        evaluation_id: str,
        branch_id: str | None = None,
        author: str | None = None,
    ) -> UpperNode:
        with self._locked():
            branch = self._branch_by_tip(expected_tip, branch_id=branch_id)
            candidate = self.node(node_id)
            if candidate.kind != "context" or candidate.status != "candidate":
                raise GraphValidationError("commit target is not a candidate context node")
            if not isinstance(approval, dict):
                raise ApprovalError("commit approval must be a dict")
            self._validate_evaluation_binding(candidate, evaluation_id, approval)
            approved_by = str(approval.get("approved_by")).strip()
            approved_at = str(approval.get("approved_at")).strip()
            self._validate_target(candidate, branch)
            manifest = manifest_with(candidate.manifest, parents=[candidate.node_id])
            commit = UpperNode.build(
                parents=[candidate.node_id],
                kind="decision",
                status="committed",
                manifest=manifest,
                evaluation_id=candidate.evaluation_id,
                evidence_refs=tuple(candidate.evidence_refs)
                + (f"approval:{approved_by}:{approved_at}",),
                message=str(approval.get("message", "commit candidate")),
                origin=candidate.origin,
                eval_state=candidate.eval_state,
                source_category=candidate.source_category,
                author=author or approved_by,
            )
            commit = self._append_node_locked(commit)
            new_branch = replace(branch, tip_node_id=commit.node_id, updated_at=utc_now())
            branches = [
                new_branch if item.branch_id == branch.branch_id else item
                for item in self._branches.values()
            ]
            self._commit_transition(
                op="commit",
                branch_id=branch.branch_id,
                old_node_id=expected_tip,
                new_node_id=commit.node_id,
                old_active_branch=self._current["active_branch_id"],
                new_active_branch=branch.branch_id,
                branches=branches,
                context_node_id=commit.node_id,
                context_hash=commit.context_hash,
                author=author or approved_by,
                reason=str(approval.get("message", "")),
            )
            return commit

    def record_evaluation_ref(
        self,
        node_id: str,
        evaluation_id: str,
        *,
        origin: str = "real_run",
        eval_state: str = "replayed",
        source_category: str = "probes/report/2c",
        evidence_refs: list[str] | tuple[str, ...] = (),
        author: str = "system",
    ) -> dict[str, Any]:
        """Append an evaluation reference without mutating the context node."""
        if not evaluation_id.strip():
            raise GraphValidationError("evaluation_id is required")
        if origin not in ASSET_ORIGINS or eval_state not in ASSET_EVAL_STATES:
            raise GraphValidationError("invalid evaluation origin/eval_state")
        with self._locked():
            node = self.node(node_id)
            if node.status != "committed":
                raise GraphValidationError("evaluation context must be committed")
            event = {
                "type": "evaluation_ref",
                "op": "evaluate",
                "branch_id": self._current["active_branch_id"],
                "node_id": node.node_id,
                "context_node_id": node.node_id,
                "context_hash": node.context_hash,
                "evaluation_id": evaluation_id,
                "origin": origin,
                "eval_state": eval_state,
                "source_category": source_category,
                "evidence_refs": list(evidence_refs),
                "revision": self._current["revision"],
                "author": author,
            }
            return self._append_event(event)

    def reject_candidate(
        self,
        node_id: str,
        *,
        expected_tip: str,
        rejected_by: str,
        reason: str,
        branch_id: str | None = None,
    ) -> UpperNode:
        with self._locked():
            branch = self._branch_by_tip(expected_tip, branch_id=branch_id)
            candidate = self.node(node_id)
            if candidate.kind != "context" or candidate.status != "candidate":
                raise GraphValidationError("reject target is not a candidate context node")
            if not rejected_by.strip() or not reason.strip():
                raise GraphValidationError("reject requires rejected_by and reason")
            self._append_event(
                {
                    "type": "reject_candidate",
                    "op": "reject",
                    "branch_id": branch.branch_id,
                    "old_node_id": expected_tip,
                    "new_node_id": candidate.node_id,
                    "old_active_branch": self._current["active_branch_id"],
                    "new_active_branch": self._current["active_branch_id"],
                    "revision": self._current["revision"],
                    "author": rejected_by,
                    "reason": reason,
                }
            )
            return candidate

    def fork(
        self,
        label: str,
        from_node_id: str,
        *,
        author: str = "system",
        permission_scope: str | None = None,
        approval_level: str | None = None,
        branch_id: str | None = None,
    ) -> UpperBranch:
        with self._locked():
            node = self.node(from_node_id)
            if node.status != "committed":
                raise GraphValidationError("fork base must be committed")
            if any(branch.label == label for branch in self._branches.values()):
                raise GraphValidationError(f"branch label already exists: {label!r}")
            now = utc_now()
            if branch_id is None:
                branch_id = "br-" + sha256_text(f"{label}:{from_node_id}:{now}")[:16]
            if branch_id in self._branches:
                raise GraphValidationError(f"branch_id already exists: {branch_id!r}")
            branch = UpperBranch(
                branch_id=branch_id,
                label=label,
                permission_scope=permission_scope,
                approval_level=approval_level,
                tip_node_id=from_node_id,
                base_node_id=from_node_id,
                created_at=now,
                updated_at=now,
            )
            branches = list(self._branches.values()) + [branch]
            self._commit_transition(
                op="fork",
                branch_id=branch_id,
                old_node_id=from_node_id,
                new_node_id=from_node_id,
                old_active_branch=self._current["active_branch_id"],
                new_active_branch=self._current["active_branch_id"],
                branches=branches,
                context_node_id=self._current["context_node_id"],
                context_hash=self._current["context_hash"],
                author=author,
            )
            return self._branches[branch_id]

    def switch(
        self,
        branch_id: str,
        target_node_id: str,
        expected_head: str | None = None,
        *,
        expected_tip: str | None = None,
        expected_active_branch: str | None = None,
        author: str = "system",
    ) -> UpperNode:
        with self._locked():
            branch = self.branch(branch_id)
            target = self.node(target_node_id)
            self._check_expected(branch, expected_head=expected_head, expected_tip=expected_tip)
            if expected_active_branch is not None:
                if self._current["active_branch_id"] != expected_active_branch:
                    raise ConflictError("active branch does not match expected_active_branch")
            self._check_target(target, branch)
            new_branch = replace(branch, tip_node_id=target.node_id, updated_at=utc_now())
            branches = [
                new_branch if item.branch_id == branch.branch_id else item
                for item in self._branches.values()
            ]
            self._commit_transition(
                op="switch",
                branch_id=branch_id,
                old_node_id=branch.tip_node_id,
                new_node_id=target.node_id,
                old_active_branch=self._current["active_branch_id"],
                new_active_branch=branch_id,
                branches=branches,
                context_node_id=target.node_id,
                context_hash=target.context_hash,
                author=author,
            )
            return target

    def rollback(
        self,
        branch_id: str,
        target_node_id: str,
        expected_head: str | None = None,
        *,
        expected_tip: str | None = None,
        expected_active_branch: str | None = None,
        author: str = "system",
        reason: str = "",
    ) -> UpperNode:
        with self._locked():
            branch = self.branch(branch_id)
            target = self.node(target_node_id)
            self._check_expected(branch, expected_head=expected_head, expected_tip=expected_tip)
            if expected_active_branch is not None:
                if self._current["active_branch_id"] != expected_active_branch:
                    raise ConflictError("active branch does not match expected_active_branch")
            if not reason.strip():
                raise GraphValidationError("rollback reason is required")
            self._check_target(target, branch)
            new_branch = replace(branch, tip_node_id=target.node_id, updated_at=utc_now())
            branches = [
                new_branch if item.branch_id == branch.branch_id else item
                for item in self._branches.values()
            ]
            self._commit_transition(
                op="rollback",
                branch_id=branch_id,
                old_node_id=branch.tip_node_id,
                new_node_id=target.node_id,
                old_active_branch=self._current["active_branch_id"],
                new_active_branch=branch_id,
                branches=branches,
                context_node_id=target.node_id,
                context_hash=target.context_hash,
                author=author,
                reason=reason,
            )
            return target

    # ---- evaluation binding (D2) -------------------------------------
    def _evaluation_dirs(self) -> tuple[Path, ...]:
        # Canonical location is ``<state>/upper/evaluations``; the legacy
        # ``<state>/evaluations`` layout of earlier demo states is still read.
        return (self.graph_dir / "evaluations", self.state_dir / "evaluations")

    def _decision_dirs(self) -> tuple[Path, ...]:
        return (self.graph_dir / "decisions", self.state_dir / "decisions")

    def _load_evaluation_record(self, evaluation_id: str) -> dict[str, Any]:
        """Load the persisted evaluation record a commit must be bound to."""
        if not isinstance(evaluation_id, str) or not RECORD_ID.match(evaluation_id):
            raise ApprovalError(f"invalid evaluation_id: {evaluation_id!r}")
        for directory in self._evaluation_dirs():
            path = directory / f"{evaluation_id}.json"
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ApprovalError(
                    f"evaluation record is not parsable: {evaluation_id}"
                ) from exc
            if not isinstance(payload, dict):
                raise ApprovalError(f"evaluation record is not an object: {evaluation_id}")
            if str(payload.get("evaluation_id", evaluation_id)) != evaluation_id:
                raise ApprovalError(f"evaluation record id mismatch: {evaluation_id}")
            return payload
        raise ApprovalError(f"evaluation record not found: {evaluation_id}")

    def _decision_verdict(self, record: dict[str, Any], evaluation_id: str) -> str | None:
        """Resolve the evaluation's upper decision (embedded or by decision_id)."""
        verdict = record.get("upper_decision")
        if verdict is not None:
            return str(verdict)
        decision_id = record.get("decision_id")
        if decision_id:
            paths = [directory / f"{decision_id}.json" for directory in self._decision_dirs()]
        else:
            paths = []
            for directory in self._decision_dirs():
                if directory.is_dir():
                    paths.extend(sorted(directory.glob("*.json")))
        for path in paths:
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            if decision_id and str(payload.get("decision_id", decision_id)) != str(decision_id):
                continue
            evaluation_ids = {str(item) for item in payload.get("evaluation_ids", ())}
            if evaluation_id not in evaluation_ids and str(
                payload.get("evaluation_id")
            ) != evaluation_id:
                continue
            return str(payload.get("upper_decision"))
        return None

    def _ancestor_ids(self, node: UpperNode) -> set[str]:
        nodes = {item.node_id: item for item in self._nodes}
        seen: set[str] = set()
        stack = list(node.parents)
        while stack:
            node_id = stack.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            parent = nodes.get(node_id)
            if parent is not None:
                stack.extend(parent.parents)
        return seen

    def _validate_evaluation_binding(
        self,
        candidate: UpperNode,
        evaluation_id: str,
        approval: dict[str, Any],
    ) -> None:
        """Fail closed unless a passed, keep, explicitly approved evaluation is bound."""
        if candidate.evaluation_id != evaluation_id:
            raise ApprovalError(
                "evaluation_id does not match the candidate evaluation reference"
            )
        approved_by = str(approval.get("approved_by") or "").strip()
        approved_at = str(approval.get("approved_at") or "").strip()
        if not approved_by or not approved_at:
            raise ApprovalError("commit requires approved_by and approved_at")
        if not is_valid_iso8601(approved_at):
            raise ApprovalError("approved_at must be an ISO8601 timestamp")
        record = self._load_evaluation_record(evaluation_id)
        status = str(record.get("status", ""))
        if status != "passed":
            raise ApprovalError(
                f"evaluation {evaluation_id} is not passed: {status!r}"
            )
        verdict = self._decision_verdict(record, evaluation_id)
        if verdict != "keep":
            raise ApprovalError(
                f"evaluation {evaluation_id} is not a keep decision: {verdict!r}"
            )
        recorded = record.get("approval")
        if not isinstance(recorded, dict):
            raise ApprovalError(f"evaluation {evaluation_id} has no recorded approval")
        if (
            str(recorded.get("approved_by") or "").strip() != approved_by
            or str(recorded.get("approved_at") or "").strip() != approved_at
        ):
            raise ApprovalError("approval does not match the evaluation record")
        asset_id = record.get("asset_id")
        if asset_id is not None:
            candidate_assets = {str(ref["asset_id"]) for ref in candidate.asset_refs}
            if str(asset_id) not in candidate_assets:
                raise ApprovalError(
                    f"evaluation asset {asset_id} is not part of the candidate context"
                )
        recorded_node = record.get("context_node_id")
        recorded_hash = record.get("context_hash")
        if recorded_node:
            if str(recorded_node) not in self._ancestor_ids(candidate):
                raise ApprovalError(
                    "evaluation context node is not part of the candidate history"
                )
            bound = next(
                (item for item in self._nodes if item.node_id == str(recorded_node)), None
            )
            if bound is None:
                raise ApprovalError("evaluation context node is missing from the graph")
            if recorded_hash and str(recorded_hash) != bound.context_hash:
                raise ApprovalError(
                    "evaluation context_hash does not match its context node"
                )
        elif recorded_hash and str(recorded_hash) != candidate.context_hash:
            raise ApprovalError("evaluation context_hash does not match the candidate")

    # ---- validation and transaction ----------------------------------
    def _branch_by_tip(self, tip: str, *, branch_id: str | None) -> UpperBranch:
        if branch_id is not None:
            branch = self.branch(branch_id)
            if branch.tip_node_id != tip:
                raise ConflictError("expected_tip does not match branch tip")
            return branch
        matches = [branch for branch in self._branches.values() if branch.tip_node_id == tip]
        if not matches:
            raise GraphValidationError(f"no branch points at tip {tip!r}")
        if len(matches) > 1:
            raise GraphValidationError("tip is shared by multiple branches; pass branch_id")
        return matches[0]

    def _check_expected(
        self,
        branch: UpperBranch,
        *,
        expected_head: str | None,
        expected_tip: str | None,
    ) -> None:
        if expected_head is not None and expected_tip is not None and expected_head != expected_tip:
            raise ConflictError("expected_head and expected_tip disagree")
        expected = expected_tip if expected_tip is not None else expected_head
        if expected is not None and branch.tip_node_id != expected:
            raise ConflictError(
                f"CAS head mismatch: branch={branch.tip_node_id} expected={expected}"
            )

    def _check_target(self, target: UpperNode, branch: UpperBranch) -> None:
        if target.status != "committed":
            raise ApprovalError("target node is not committed")
        self._validate_target(target, branch)

    def _validate_target(self, target: UpperNode, branch: UpperBranch) -> None:
        profiles = target.manifest.get("mcp_profiles", [])
        if profiles and branch.approval_level != "strict":
            raise ApprovalError("branch with MCP profiles requires approval_level='strict'")
        if not target.asset_refs and not profiles:
            return
        if self.registry is None:
            raise ApprovalError("registry is required to activate a context with assets")
        for ref in target.asset_refs:
            try:
                asset = self.registry.get(ref["asset_id"])
            except Exception as exc:
                raise GraphValidationError(
                    f"asset not found in registry: {ref['asset_id']}"
                ) from exc
            if asset.version != ref["version"] or asset.hash != ref["hash"]:
                raise GraphValidationError(
                    f"asset version/hash mismatch: {ref['asset_id']}"
                )
            if asset.status not in {"approved", "active"}:
                raise ApprovalError(
                    f"asset {ref['asset_id']} is {asset.status}, not approved/active"
                )
            if not asset.approved_by or not asset.approved_at:
                raise ApprovalError(
                    f"asset {ref['asset_id']} has no approved_by/approved_at"
                )
            if asset.kind not in ASSET_KINDS:
                raise GraphValidationError(f"unknown asset kind: {asset.kind!r}")
        for profile in profiles:
            try:
                asset = self.registry.get(profile["profile_id"])
            except Exception as exc:
                raise GraphValidationError(
                    f"MCP profile not found in registry: {profile['profile_id']}"
                ) from exc
            if asset.kind != "mcp_profile":
                raise GraphValidationError(
                    f"referenced profile is not an mcp_profile: {profile['profile_id']}"
                )
            if asset.approval_level != "strict" or profile.get("approval_level") != "strict":
                raise ApprovalError(
                    f"MCP profile {profile['profile_id']} requires approval_level='strict'"
                )
            if asset.permission_scope != profile.get("permission_scope"):
                raise GraphValidationError(
                    f"MCP permission_scope mismatch: {profile['profile_id']}"
                )

    def _commit_transition(
        self,
        *,
        op: str,
        branch_id: str,
        old_node_id: str,
        new_node_id: str,
        old_active_branch: str | None,
        new_active_branch: str | None,
        branches: list[UpperBranch],
        context_node_id: str | None,
        context_hash: str | None,
        author: str,
        reason: str = "",
    ) -> dict[str, Any]:
        current = self._current
        target_revision = current["revision"] + 1
        now = utc_now()
        new_current = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "revision": target_revision,
            "active_branch_id": new_active_branch,
            "context_node_id": context_node_id,
            "context_hash": context_hash,
            "branches": self._branch_dicts(branches),
            "updated_at": now,
            "last_event_seq": current["last_event_seq"] + 2,
        }
        payload_hash = sha256_text(canonical_json(self._current_payload(new_current)))
        intent = {
            "type": "intent",
            "op": op,
            "branch_id": branch_id,
            "old_node_id": old_node_id,
            "new_node_id": new_node_id,
            "old_active_branch": old_active_branch,
            "new_active_branch": new_active_branch,
            "target_revision": target_revision,
            "revision": target_revision,
            "payload_hash": payload_hash,
            "event_seq": current["last_event_seq"] + 1,
            "author": author,
            "created_at": now,
        }
        if reason:
            intent["reason"] = reason
        intent = self._append_event(intent)
        self._fault("before_current_write")
        self._write_current(new_current)
        self._current = new_current
        self._branches = {branch.branch_id: branch for branch in branches}
        try:
            self._fault("after_current_before_commit")
        except Exception as exc:
            raise CommitEventPending(
                f"current revision {target_revision} is committed; commit event pending"
            ) from exc
        commit = {
            "type": "commit",
            "op": op,
            "branch_id": branch_id,
            "old_node_id": old_node_id,
            "new_node_id": new_node_id,
            "old_active_branch": old_active_branch,
            "new_active_branch": new_active_branch,
            "revision": target_revision,
            "intent_event_id": intent["event_id"],
            "event_seq": current["last_event_seq"] + 2,
            "author": author,
            "created_at": utc_now(),
        }
        if reason:
            commit["reason"] = reason
        try:
            self._append_event(commit)
        except Exception as exc:
            raise CommitEventPending(
                f"current revision {target_revision} is committed; commit event pending"
            ) from exc
        return new_current
