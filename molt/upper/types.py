"""Upper-layer records: assets, evaluations, decisions, and B2 projections.

The upper layer owns its own scenario/version and evidence records. It does not
write, replace, or merge core gate decisions; lower-layer references are kept in
dedicated ``lower_refs`` fields whose keys must start with ``lower_``.

This module is pure stdlib so it can be imported by API/UI code without pulling
in the replay runtime. Future capabilities are *typed placeholders only*: they
are always ``unimplemented`` and no execution path exists in B1.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

API_SCHEMA_VERSION = "1.0.0"

ASSET_KINDS = ("policy_memory", "skill", "mcp_profile")
ASSET_ORIGINS = ("seed_import", "synthetic", "real_run")
ASSET_EVAL_STATES = ("not_evaluated", "replayed", "evaluated")
ASSET_STATUSES = (
    "draft",
    "evaluated",
    "approved",
    "rejected",
    "active",
    "rolled_back",
)
UPPER_DECISIONS = ("keep", "reject", "rollback")
IMPLEMENTATION_STATUSES = ("stub", "implemented")
EVALUATION_STATUSES = ("pending", "passed", "failed")
APPROVAL_LEVELS = ("standard", "strict")
FUTURE_CAPABILITY_NAMES = ("context_budget", "compaction", "vector_rag")

AssetKind = Literal["policy_memory", "skill", "mcp_profile"]
AssetStatus = Literal["draft", "evaluated", "approved", "rejected", "active", "rolled_back"]
UpperDecisionValue = Literal["keep", "reject", "rollback"]
ImplementationStatus = Literal["stub", "implemented"]
ApprovalLevel = Literal["standard", "strict"]
FutureCapabilityName = Literal["context_budget", "compaction", "vector_rag"]
FutureCapabilityStatus = Literal["unimplemented"]

LOWER_VERDICT_KEYS = frozenset({"verdict", "lower_verdict", "gate_verdict", "core_verdict"})


def canonical_json(payload: Any) -> str:
    """Canonical JSON used for all upper-layer hashes.

    Exact rule: ``json.dumps(payload, sort_keys=True, separators=(",", ":"),
    ensure_ascii=False, allow_nan=False)`` encoded as UTF-8. ``allow_nan=False``
    rejects NaN/Inf; empty values must be explicit ``null`` (keys are never
    omitted by this helper, but callers must pass them).
    """
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_sha256(content: Any) -> str:
    return sha256_text(canonical_json(content))


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso8601(value: Any) -> datetime:
    """Parse an ISO8601 timestamp; raise ``ValueError`` when it is not valid."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty ISO8601 string")
    text = value.strip()
    if text[-1] in {"Z", "z"}:
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def is_valid_iso8601(value: Any) -> bool:
    try:
        parse_iso8601(value)
    except ValueError:
        return False
    return True


def validate_lower_refs(refs: dict[str, Any]) -> None:
    """Keep lower-layer references structurally separate from upper verdicts.

    Every key must be a ``lower_*`` reference field. No nested verdict/decision
    payloads are allowed, so ``lower_refs`` cannot smuggle a lower verdict into
    an upper decision.
    """

    if not isinstance(refs, dict):
        raise ValueError("lower_refs must be a dict")
    for key, value in refs.items():
        if not isinstance(key, str) or not key.startswith("lower_"):
            raise ValueError(f"lower_refs key must start with 'lower_': {key!r}")
        if key in {"lower_verdict", "lower_decision"}:
            raise ValueError(f"lower verdict/decision is not allowed in lower_refs: {key!r}")
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            continue
        if isinstance(value, list) and all(
            isinstance(item, (str, int, float, bool)) for item in value
        ):
            continue
        raise ValueError(f"lower_refs value must be scalar or scalar list: {key!r}")


def _reject_lower_verdict_keys(payload: Any, *, path: str = "metrics") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in LOWER_VERDICT_KEYS:
                raise ValueError(f"lower verdict key is not allowed in {path}: {key!r}")
            _reject_lower_verdict_keys(value, path=f"{path}.{key}")
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            _reject_lower_verdict_keys(value, path=f"{path}[{index}]")


@dataclass(frozen=True)
class FutureCapability:
    """Versioned placeholder; B1 never executes these capabilities."""

    name: FutureCapabilityName
    status: FutureCapabilityStatus = "unimplemented"
    interface_version: str = "future-0"
    note: str = ""

    def __post_init__(self) -> None:
        if self.name not in FUTURE_CAPABILITY_NAMES:
            raise ValueError(f"unknown future capability: {self.name!r}")
        if self.status != "unimplemented":
            raise ValueError("B1 only permits unimplemented future capabilities")
        if not self.interface_version:
            raise ValueError("future capability interface_version is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "interface_version": self.interface_version,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FutureCapability:
        return cls(
            name=payload["name"],
            status=payload.get("status", "unimplemented"),
            interface_version=payload.get("interface_version", "future-0"),
            note=payload.get("note", ""),
        )


def default_future_capabilities() -> tuple[FutureCapability, ...]:
    """All future capabilities are typed and unimplemented in B1.

    Trigger conditions for later experiments (documented, not executed):
    real long-context tasks, ``max_context > 200k``, window failures, or a
    dedicated SoL-Pi reproduction. C1b currently carries a handful of short
    rules, so there is no compaction benefit or window-pressure data. RAG stays
    on deterministic exact/prefix routing; no embedding is introduced.
    """

    return (
        FutureCapability(
            name="context_budget",
            interface_version="future-1",
            note=(
                "unimplemented: only consider with real long-context tasks, "
                "max_context > 200k, window failures, or a dedicated SoL-Pi reproduction"
            ),
        ),
        FutureCapability(
            name="compaction",
            interface_version="future-1",
            note=(
                "unimplemented: C1b has short rules only; no window-pressure or "
                "compaction-benefit data exists"
            ),
        ),
        FutureCapability(
            name="vector_rag",
            interface_version="future-1",
            note=(
                "unimplemented: retrieval remains deterministic exact/prefix matching; "
                "no embedding model is used"
            ),
        ),
    )


@dataclass
class UpperAsset:
    asset_id: str
    kind: AssetKind
    name: str
    version: str
    source: str
    family: str
    created_by: str
    evidence_refs: tuple[str, ...] = ()
    status: AssetStatus = "draft"
    upper_scenario_id: str | None = None
    upper_scenario_version: str | None = None
    source_path_hash: str | None = None
    content_hash: str | None = None
    extracted_summary: str | None = None
    classification: str | None = None
    importer_version: str | None = None
    provenance: str | None = None
    verified_lineage: bool = False
    date_unknown: bool = True
    source_category: str | None = None
    seed_branch: str | None = None
    seed_identity_hash: str | None = None
    # Asset-record fingerprint. ``context_hash`` is reserved for graph context
    # nodes/manifests (``graph.compute_context_hash``).
    record_hash: str | None = None
    origin: str = "real_run"
    eval_state: str = "not_evaluated"
    content: dict[str, Any] = field(default_factory=dict)
    hash: str = ""
    implementation_status: ImplementationStatus = "stub"
    permission_scope: str | None = None
    approval_level: ApprovalLevel | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    permission_approved_by: str | None = None
    permission_approved_at: str | None = None
    pending_permission: dict[str, Any] | None = None
    status_reason: str | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("asset_id", "name", "version", "source", "family", "created_by"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"UpperAsset.{field_name} is required")
        if self.kind not in ASSET_KINDS:
            raise ValueError(f"unknown asset kind: {self.kind!r}")
        if self.status not in ASSET_STATUSES:
            raise ValueError(f"unknown asset status: {self.status!r}")
        if self.implementation_status not in IMPLEMENTATION_STATUSES:
            raise ValueError(f"unknown implementation_status: {self.implementation_status!r}")
        if self.approval_level is not None and self.approval_level not in APPROVAL_LEVELS:
            raise ValueError(f"unknown approval_level: {self.approval_level!r}")
        if self.origin not in ASSET_ORIGINS:
            raise ValueError(f"unknown asset origin: {self.origin!r}")
        if self.eval_state not in ASSET_EVAL_STATES:
            raise ValueError(f"unknown asset eval_state: {self.eval_state!r}")
        if self.origin in {"seed_import", "synthetic"} and self.eval_state != "not_evaluated":
            raise ValueError(f"{self.origin} assets must be not_evaluated")
        if not isinstance(self.content, dict):
            raise ValueError("UpperAsset.content must be a dict")
        if not isinstance(self.evidence_refs, tuple):
            self.evidence_refs = tuple(self.evidence_refs)
        if self.kind == "mcp_profile":
            if not self.permission_scope:
                raise ValueError("mcp_profile requires permission_scope")
            if self.approval_level != "strict":
                raise ValueError("mcp_profile requires approval_level='strict'")
        if not self.hash:
            self.hash = self.compute_content_hash()
        if self.content_hash is None:
            self.content_hash = self.hash

    def compute_content_hash(self) -> str:
        return content_sha256(self.content)

    def verify_hash(self) -> bool:
        return self.hash == self.compute_content_hash()

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "kind": self.kind,
            "name": self.name,
            "version": self.version,
            "source": self.source,
            "family": self.family,
            "created_by": self.created_by,
            "evidence_refs": list(self.evidence_refs),
            "status": self.status,
            "upper_scenario_id": self.upper_scenario_id,
            "upper_scenario_version": self.upper_scenario_version,
            "source_path_hash": self.source_path_hash,
            "content_hash": self.content_hash,
            "extracted_summary": self.extracted_summary,
            "classification": self.classification,
            "importer_version": self.importer_version,
            "provenance": self.provenance,
            "verified_lineage": self.verified_lineage,
            "date_unknown": self.date_unknown,
            "source_category": self.source_category,
            "seed_branch": self.seed_branch,
            "seed_identity_hash": self.seed_identity_hash,
            "record_hash": self.record_hash,
            "origin": self.origin,
            "eval_state": self.eval_state,
            "content": self.content,
            "hash": self.hash,
            "implementation_status": self.implementation_status,
            "permission_scope": self.permission_scope,
            "approval_level": self.approval_level,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "permission_approved_by": self.permission_approved_by,
            "permission_approved_at": self.permission_approved_at,
            "pending_permission": self.pending_permission,
            "status_reason": self.status_reason,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> UpperAsset:
        return cls(
            asset_id=payload["asset_id"],
            kind=payload["kind"],
            name=payload["name"],
            version=payload["version"],
            source=payload["source"],
            family=payload["family"],
            created_by=payload["created_by"],
            evidence_refs=tuple(payload.get("evidence_refs", ())),
            status=payload.get("status", "draft"),
            upper_scenario_id=payload.get("upper_scenario_id"),
            upper_scenario_version=payload.get("upper_scenario_version"),
            source_path_hash=payload.get("source_path_hash"),
            content_hash=payload.get("content_hash"),
            extracted_summary=payload.get("extracted_summary"),
            classification=payload.get("classification"),
            importer_version=payload.get("importer_version"),
            provenance=payload.get("provenance"),
            verified_lineage=bool(payload.get("verified_lineage", False)),
            date_unknown=bool(payload.get("date_unknown", True)),
            source_category=payload.get("source_category"),
            seed_branch=payload.get("seed_branch"),
            seed_identity_hash=payload.get("seed_identity_hash"),
            record_hash=payload.get("record_hash", payload.get("context_hash")),
            origin=payload.get("origin", "real_run"),
            eval_state=payload.get("eval_state", "not_evaluated"),
            content=dict(payload.get("content", {})),
            hash=payload.get("hash", ""),
            implementation_status=payload.get("implementation_status", "stub"),
            permission_scope=payload.get("permission_scope"),
            approval_level=payload.get("approval_level"),
            approved_by=payload.get("approved_by"),
            approved_at=payload.get("approved_at"),
            permission_approved_by=payload.get("permission_approved_by"),
            permission_approved_at=payload.get("permission_approved_at"),
            pending_permission=payload.get("pending_permission"),
            status_reason=payload.get("status_reason"),
            updated_at=payload.get("updated_at"),
        )

    def to_public_dict(self) -> dict[str, Any]:
        """Read-only B2 projection. No write handles or execution callables."""
        return {
            "asset_id": self.asset_id,
            "kind": self.kind,
            "name": self.name,
            "version": self.version,
            "source": self.source,
            "family": self.family,
            "created_by": self.created_by,
            "evidence_refs": list(self.evidence_refs),
            "status": self.status,
            "upper_scenario_id": self.upper_scenario_id,
            "upper_scenario_version": self.upper_scenario_version,
            "source_path_hash": self.source_path_hash,
            "content_hash": self.content_hash,
            "extracted_summary": self.extracted_summary,
            "classification": self.classification,
            "importer_version": self.importer_version,
            "provenance": self.provenance,
            "verified_lineage": self.verified_lineage,
            "date_unknown": self.date_unknown,
            "source_category": self.source_category,
            "seed_branch": self.seed_branch,
            "seed_identity_hash": self.seed_identity_hash,
            "record_hash": self.record_hash,
            "origin": self.origin,
            "eval_state": self.eval_state,
            "content": self.content,
            "hash": self.hash,
            "hash_short": self.hash[:12],
            "implementation_status": self.implementation_status,
            "permission_scope": self.permission_scope,
            "approval_level": self.approval_level,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "pending_permission": self.pending_permission,
            "status_reason": self.status_reason,
            "updated_at": self.updated_at,
        }


@dataclass
class UpperEvaluation:
    evaluation_id: str
    upper_scenario_id: str
    upper_scenario_version: str
    asset_id: str
    asset_version: str
    family: str
    task_set: str
    mode: str
    provenance: dict[str, Any]
    tasks: list[dict[str, Any]]
    metrics: dict[str, Any]
    telemetry: dict[str, Any]
    reference_counts: dict[str, Any]
    baseline: dict[str, Any]
    lower_refs: dict[str, Any]
    context_node_id: str | None = None
    branch_id: str | None = None
    context_hash: str | None = None
    context_status: str = "unbound"
    status: str = "pending"
    decision_id: str | None = None
    approval: dict[str, Any] | None = None
    future_capabilities: tuple[FutureCapability, ...] = field(
        default_factory=default_future_capabilities
    )
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.evaluation_id or not self.upper_scenario_id:
            raise ValueError("evaluation_id and upper_scenario_id are required")
        if not self.upper_scenario_version:
            raise ValueError("upper_scenario_version is required")
        if self.mode not in {"replay", "live"}:
            raise ValueError(f"unknown evaluation mode: {self.mode!r}")
        if self.context_status not in {"unbound", "stable", "context_changed"}:
            raise ValueError(f"unknown context_status: {self.context_status!r}")
        if self.status not in EVALUATION_STATUSES:
            raise ValueError(f"unknown evaluation status: {self.status!r}")
        if self.approval is not None:
            self._validate_approval(self.approval)
        if not isinstance(self.future_capabilities, tuple):
            self.future_capabilities = tuple(self.future_capabilities)
        for capability in self.future_capabilities:
            if not isinstance(capability, FutureCapability):
                raise ValueError("future_capabilities must contain FutureCapability records")
            if capability.status != "unimplemented":
                raise ValueError("B1 evaluations must keep future capabilities unimplemented")
        validate_lower_refs(self.lower_refs)
        _reject_lower_verdict_keys(self.metrics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "upper_scenario_id": self.upper_scenario_id,
            "upper_scenario_version": self.upper_scenario_version,
            "asset_id": self.asset_id,
            "asset_version": self.asset_version,
            "family": self.family,
            "task_set": self.task_set,
            "mode": self.mode,
            "provenance": self.provenance,
            "tasks": self.tasks,
            "metrics": self.metrics,
            "telemetry": self.telemetry,
            "reference_counts": self.reference_counts,
            "baseline": self.baseline,
            "lower_refs": self.lower_refs,
            "context_node_id": self.context_node_id,
            "branch_id": self.branch_id,
            "context_hash": self.context_hash,
            "context_status": self.context_status,
            "status": self.status,
            "decision_id": self.decision_id,
            "approval": self.approval,
            "future_capabilities": [capability.to_dict() for capability in self.future_capabilities],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> UpperEvaluation:
        return cls(
            evaluation_id=payload["evaluation_id"],
            upper_scenario_id=payload["upper_scenario_id"],
            upper_scenario_version=payload["upper_scenario_version"],
            asset_id=payload["asset_id"],
            asset_version=payload["asset_version"],
            family=payload["family"],
            task_set=payload["task_set"],
            mode=payload["mode"],
            provenance=dict(payload.get("provenance", {})),
            tasks=list(payload.get("tasks", [])),
            metrics=dict(payload.get("metrics", {})),
            telemetry=dict(payload.get("telemetry", {})),
            reference_counts=dict(payload.get("reference_counts", {})),
            baseline=dict(payload.get("baseline", {})),
            lower_refs=dict(payload.get("lower_refs", {})),
            context_node_id=payload.get("context_node_id"),
            branch_id=payload.get("branch_id"),
            context_hash=payload.get("context_hash"),
            context_status=payload.get("context_status", "unbound"),
            status=payload.get("status", "pending"),
            decision_id=payload.get("decision_id"),
            approval=dict(payload["approval"]) if payload.get("approval") else None,
            future_capabilities=tuple(
                FutureCapability.from_dict(item)
                for item in payload.get("future_capabilities", [])
            )
            or default_future_capabilities(),
            created_at=payload.get("created_at", utc_now()),
        )

    @staticmethod
    def _validate_approval(approval: dict[str, Any]) -> None:
        if not isinstance(approval, dict):
            raise ValueError("approval must be a dict")
        if not str(approval.get("approved_by") or "").strip():
            raise ValueError("approval.approved_by is required")
        if not is_valid_iso8601(approval.get("approved_at")):
            raise ValueError("approval.approved_at must be an ISO8601 timestamp")

    def bind_approval(
        self,
        *,
        approved_by: str,
        approved_at: str,
        approval_level: str | None = None,
    ) -> UpperEvaluation:
        """Record the approval that a graph commit must match exactly."""
        approval = {
            "approved_by": approved_by,
            "approved_at": approved_at,
            "approval_level": approval_level,
        }
        self._validate_approval(approval)
        self.approval = approval
        return self

    def bind_context(
        self,
        *,
        node_id: str,
        branch_id: str,
        context_hash: str,
        status: str = "stable",
    ) -> UpperEvaluation:
        """Attach a graph context binding to an already-created evaluation."""
        if status not in {"stable", "context_changed"}:
            raise ValueError(f"unknown context_status: {status!r}")
        self.context_node_id = node_id
        self.branch_id = branch_id
        self.context_hash = context_hash
        self.context_status = status
        return self

    def to_public_dict(self) -> dict[str, Any]:
        return self.to_dict()


@dataclass
class UpperDecision:
    decision_id: str
    upper_scenario_id: str
    upper_scenario_version: str
    asset_id: str
    asset_version: str
    upper_decision: UpperDecisionValue
    reason: str
    metrics: dict[str, Any]
    lower_refs: dict[str, Any]
    evaluation_ids: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.decision_id or not self.reason:
            raise ValueError("decision_id and reason are required")
        if self.upper_decision not in UPPER_DECISIONS:
            raise ValueError(f"unknown upper_decision: {self.upper_decision!r}")
        if not self.upper_scenario_version:
            raise ValueError("upper_scenario_version is required")
        if not isinstance(self.evaluation_ids, tuple):
            self.evaluation_ids = tuple(self.evaluation_ids)
        validate_lower_refs(self.lower_refs)
        _reject_lower_verdict_keys(self.metrics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "upper_scenario_id": self.upper_scenario_id,
            "upper_scenario_version": self.upper_scenario_version,
            "asset_id": self.asset_id,
            "asset_version": self.asset_version,
            "upper_decision": self.upper_decision,
            "reason": self.reason,
            "metrics": self.metrics,
            "lower_refs": self.lower_refs,
            "evaluation_ids": list(self.evaluation_ids),
            "provenance": self.provenance,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> UpperDecision:
        return cls(
            decision_id=payload["decision_id"],
            upper_scenario_id=payload["upper_scenario_id"],
            upper_scenario_version=payload["upper_scenario_version"],
            asset_id=payload["asset_id"],
            asset_version=payload["asset_version"],
            upper_decision=payload["upper_decision"],
            reason=payload["reason"],
            metrics=dict(payload.get("metrics", {})),
            lower_refs=dict(payload.get("lower_refs", {})),
            evaluation_ids=tuple(payload.get("evaluation_ids", ())),
            provenance=dict(payload.get("provenance", {})),
            created_at=payload.get("created_at", utc_now()),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return self.to_dict()


class RetrievalHitDict(TypedDict):
    asset_id: str
    asset_version: str
    status: str
    source: str
    family: str
    score: float
    match_type: str
    matched_key: str
    reason: str


class RetrievalResultDict(TypedDict):
    family: str
    query: str | None
    task_id: str | None
    mode: str
    statuses: list[str]
    hits: list[RetrievalHitDict]
    empty: bool


class UpperAssetPublicDict(TypedDict, total=False):
    asset_id: str
    kind: AssetKind
    name: str
    version: str
    source: str
    family: str
    created_by: str
    evidence_refs: list[str]
    status: AssetStatus
    upper_scenario_id: str | None
    upper_scenario_version: str | None
    source_path_hash: str | None
    content_hash: str | None
    extracted_summary: str | None
    classification: str | None
    importer_version: str | None
    provenance: str | None
    verified_lineage: bool
    date_unknown: bool
    source_category: str | None
    seed_branch: str | None
    seed_identity_hash: str | None
    record_hash: str | None
    origin: str
    eval_state: str
    content: dict[str, Any]
    hash: str
    hash_short: str
    implementation_status: ImplementationStatus
    permission_scope: str | None
    approval_level: ApprovalLevel | None
    approved_by: str | None
    approved_at: str | None
    pending_permission: dict[str, Any] | None
    status_reason: str | None
    updated_at: str | None


class UpperEvaluationPublicDict(TypedDict, total=False):
    evaluation_id: str
    upper_scenario_id: str
    upper_scenario_version: str
    asset_id: str
    asset_version: str
    family: str
    task_set: str
    mode: str
    metrics: dict[str, Any]
    telemetry: dict[str, Any]
    reference_counts: dict[str, Any]
    lower_refs: dict[str, Any]
    context_node_id: str | None
    branch_id: str | None
    context_hash: str | None
    context_status: str
    status: str
    decision_id: str | None
    approval: dict[str, Any] | None
    future_capabilities: list[dict[str, Any]]
    created_at: str


class UpperDecisionPublicDict(TypedDict, total=False):
    decision_id: str
    upper_scenario_id: str
    upper_scenario_version: str
    asset_id: str
    asset_version: str
    upper_decision: UpperDecisionValue
    reason: str
    metrics: dict[str, Any]
    lower_refs: dict[str, Any]
    evaluation_ids: list[str]
    provenance: dict[str, Any]
    created_at: str


class UpperApiEnvelope(TypedDict):
    schema_version: str
    kind: str
    payload: dict[str, Any]


def api_envelope(kind: str, payload: dict[str, Any]) -> UpperApiEnvelope:
    """B2 response envelope; B1 only defines the shape, not an HTTP server."""
    return {"schema_version": API_SCHEMA_VERSION, "kind": kind, "payload": payload}
