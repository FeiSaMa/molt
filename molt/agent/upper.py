"""B2 upper-layer HTTP service.

This module is the only bridge between the B1 ``molt.upper`` records and the
local HTTP API. It is deliberately a *read-only projection* plus three fixed
operations:

* ``GET  /api/upper/graph``      -- project the current graph (no raw content);
* ``GET  /api/upper/assets``     -- project redacted asset metadata;
* ``GET  /api/upper/evaluations/{id}`` -- project one persisted evaluation;
* ``POST /api/upper/replay``     -- run the fixed C1b replay scenario;
* ``POST /api/upper/branches/{id}/switch`` -- activate a branch (CAS);
* ``POST /api/upper/rollback``   -- move a branch head to a historical node.

The service never accepts a scenario, command, filesystem path, or lower-layer
verdict. Lower ids stay in dedicated ``lower_*`` reference fields. All responses
are sanitized: no absolute paths, secrets, raw asset text, or execution
handles are returned.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from molt.upper.demo import run_demo
from molt.upper.evaluator import ScenarioError, load_scenario
from molt.upper.fixture import build_synthetic_fixture
from molt.upper.graph import (
    ApprovalError,
    ConflictError,
    GraphError,
    GraphIntegrityError,
    GraphValidationError,
    UpperGraphStore,
)
from molt.upper.registry import UpperRegistry, UpperRegistryError
from molt.upper.seed import import_seeds
from molt.upper.storage import atomic_write_json, ensure_outside_repo
from molt.upper.types import API_SCHEMA_VERSION, canonical_json, sha256_text, utc_now

UPPER_SCENARIO_REF = "c1b-memory-v1"
UPPER_API_SCHEMA_VERSION = API_SCHEMA_VERSION
API_ACTOR = "upper-api"
BOOTSTRAP_FILE = "api-bootstrap.json"
REPLAY_DIR = "replay"
REPLAY_SUMMARY_FILE = "replay-summary.json"

_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_ABS_PATH_RE = re.compile(
    r"(?<![\w:])/(?:home|Users|root|tmp|var|etc|opt|srv|run|mnt|media|proc|sys)"
    r"/[^\s\"',\]\)}]+"
)
_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s\"',\]\)}]+")
_SECRET_RE = re.compile(
    r"(?i)(?:"
    r"\bsk-[A-Za-z0-9_-]{8,}"
    r"|\bghp_[A-Za-z0-9]{20,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\bxox[baprs]-[A-Za-z0-9-]{8,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bBearer\s+[A-Za-z0-9._-]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r")"
)
_DROP_KEYS = frozenset(
    {
        "absolute_path",
        "access_token",
        "api_key",
        "argv",
        "authorization",
        "command",
        "credential",
        "credentials",
        "password",
        "raw",
        "raw_text",
        "secret",
        "source_path",
        "token",
    }
)
_ALLOWED_STATUSES = frozenset({400, 401, 403, 404, 409, 413, 422, 503})


class UpperApiError(Exception):
    """A safe, serializable upper API error (no paths or internal details)."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if status not in _ALLOWED_STATUSES:  # pragma: no cover - programmer error
            raise ValueError(f"unsupported upper API status: {status}")
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = _public(self.details)
        return {"error": error}


def _clean_text(value: str) -> str:
    value = _ABS_PATH_RE.sub("<path>", value)
    value = _WINDOWS_PATH_RE.sub("<path>", value)
    return _SECRET_RE.sub("<redacted>", value)


def _public(value: Any) -> Any:
    """Recursively strip absolute paths, raw/source paths, and secret-shaped strings."""

    if isinstance(value, dict):
        return {
            key: _public(item)
            for key, item in value.items()
            if str(key) not in _DROP_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_public(item) for item in value]
    if isinstance(value, str):
        return _clean_text(value)
    return value


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpperApiError(
            503,
            "upper_unavailable",
            "上层状态不可读",
            {"reason": "state_unreadable"},
        ) from exc
    if not isinstance(payload, dict):
        raise UpperApiError(
            503,
            "upper_unavailable",
            "上层状态格式非法",
            {"reason": "state_invalid"},
        )
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_json(path, payload)


def _short(value: Any) -> str:
    text = str(value or "")
    return text[:12]


def _project_active(pointer: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pointer:
        return None
    node_id = pointer.get("node_id") or pointer.get("head_node_id")
    return {
        "branch_id": pointer.get("branch_id"),
        "context_node_id": node_id,
        "context_hash": pointer.get("context_hash"),
    }


def _project_graph(payload: dict[str, Any]) -> dict[str, Any]:
    """Project a B1 graph public dict onto the stable upper API v1 shape."""

    active = payload.get("active") or {}
    branches: list[dict[str, Any]] = []
    for branch in payload.get("branches", []):
        tip = branch.get("tip_node_id") or branch.get("head_node_id")
        branches.append(
            {
                "branch_id": branch.get("branch_id"),
                "label": branch.get("label") or branch.get("name"),
                "permission_scope": branch.get("permission_scope"),
                "tip_node_id": tip,
                "tip_hash": tip,
            }
        )
    nodes: list[dict[str, Any]] = []
    for node in payload.get("nodes", []):
        node_id = node.get("node_id")
        nodes.append(
            {
                "node_id": node_id,
                "parents": list(node.get("parents", [])),
                "kind": node.get("kind"),
                "origin": node.get("origin"),
                "eval_state": node.get("eval_state"),
                "context_hash": node.get("context_hash"),
                "hash_short": node.get("hash_short") or _short(node_id),
                "created_at": node.get("created_at"),
            }
        )
    return {
        "revision": int(payload.get("revision", 0)),
        "active": (
            {
                "branch_id": active.get("branch_id"),
                "context_node_id": active.get("node_id") or active.get("head_node_id"),
                "context_hash": active.get("context_hash"),
            }
            if active
            else None
        ),
        "branches": sorted(branches, key=lambda item: str(item.get("branch_id"))),
        "nodes": nodes,
    }


class UpperService:
    """Server-side facade over a repository-external upper state directory."""

    def __init__(
        self,
        state_dir: Path | str,
        *,
        repo_root: Path | str,
        seed_base: Path | str | None = None,
        scenario_ref: str = UPPER_SCENARIO_REF,
        grace_s: float = 30.0,
        replay_runner: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.state_dir = ensure_outside_repo(Path(state_dir), self.repo_root)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_dir, 0o700)
        self.upper_dir = self.state_dir / "upper"
        self.upper_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.upper_dir, 0o700)
        self.seed_base = Path(seed_base).expanduser() if seed_base is not None else None
        self.scenario_ref = scenario_ref
        self.grace_s = grace_s
        self._replay_runner = replay_runner or run_demo
        self._init_lock = threading.Lock()
        self._replay_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._initialized = False

    # ---- bootstrap ------------------------------------------------------
    @property
    def bootstrap_path(self) -> Path:
        return self.upper_dir / BOOTSTRAP_FILE

    def ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            if self.bootstrap_path.is_file():
                self._initialized = True
                return
            try:
                self._bootstrap_locked()
            except (OSError, ValueError, UpperRegistryError, GraphError) as exc:
                raise UpperApiError(
                    503,
                    "upper_unavailable",
                    "上层状态初始化失败",
                    {"reason": "bootstrap_failed", "detail": type(exc).__name__},
                ) from exc
            self._initialized = True

    def _bootstrap_locked(self) -> None:
        registry = UpperRegistry(self.state_dir, repo_root=self.repo_root)
        seed_assets = self._seed_assets()
        graph = UpperGraphStore(
            self.state_dir, repo_root=self.repo_root, registry=registry
        )
        if not graph.nodes():
            build_synthetic_fixture(
                state_dir=self.state_dir,
                repo_root=self.repo_root,
                seed_assets=seed_assets,
                registry=registry,
            )
        scenario_assets = self._register_scenario_assets(registry)
        scenario = self._scenario()
        marker = {
            "schema_version": UPPER_API_SCHEMA_VERSION,
            "created_at": utc_now(),
            "scenario_id": scenario.scenario_id if scenario else None,
            "scenario_version": scenario.version if scenario else None,
            "seed_assets": len(seed_assets),
            "scenario_assets": scenario_assets,
            "graph_bootstrap": "upper-synthetic-1",
            "merge_supported": False,
            "delete_supported": False,
        }
        _write_json(self.bootstrap_path, marker)

    def _seed_assets(self) -> list[Any]:
        try:
            return list(import_seeds(self.seed_base).assets)
        except Exception:  # noqa: BLE001 - seed import is optional and fail-closed
            return []

    def _scenario(self) -> Any:
        try:
            return load_scenario(self.scenario_ref, self.repo_root)
        except (ScenarioError, OSError):
            return None

    def _register_scenario_assets(self, registry: UpperRegistry) -> int:
        scenario = self._scenario()
        if scenario is None:
            return 0
        count = 0
        for asset_id, spec in scenario.assets.items():
            # Evaluated memory assets are only projected after a local REPLAY
            # has produced its own records; do not pre-claim replay lineage in
            # the synthetic bootstrap registry.
            if spec.get("evaluate"):
                continue
            try:
                registry.register(scenario.build_asset(asset_id))
            except UpperRegistryError:
                continue
            count += 1
        change = scenario.governance.get("permission_change") or {}
        if change.get("asset_id") and change.get("requested_scope"):
            try:
                registry.request_permission_change(
                    str(change["asset_id"]),
                    scope=str(change["requested_scope"]),
                    requested_by=str(
                        scenario.governance.get("replay_operator", "upper-replay-operator")
                    ),
                    reason=str(change.get("reason", "permission change requires approval")),
                )
            except UpperRegistryError:
                pass
        return count

    def _registry(self) -> UpperRegistry:
        return UpperRegistry(self.state_dir, repo_root=self.repo_root)

    def _graph(self) -> UpperGraphStore:
        return UpperGraphStore(
            self.state_dir, repo_root=self.repo_root, registry=self._registry()
        )

    # ---- read projections ----------------------------------------------
    def graph_public(self) -> dict[str, Any]:
        self.ensure_initialized()
        graph = self._graph()
        pointer = graph.active_pointer()
        return _project_graph(
            {
                "revision": pointer.get("revision", 0) if pointer else 0,
                "active": pointer,
                "branches": [branch.to_public_dict() for branch in graph.branches()],
                "nodes": [node.to_public_dict() for node in graph.nodes()],
            }
        )

    def assets_public(self) -> dict[str, Any]:
        self.ensure_initialized()
        records: dict[str, dict[str, Any]] = {
            asset.asset_id: asset.to_public_dict()
            for asset in self._registry().list_assets()
        }
        replay_registry = self._replay_registry()
        if replay_registry is not None:
            for asset in replay_registry.list_assets():
                records[asset.asset_id] = asset.to_public_dict()

        assets: list[dict[str, Any]] = []
        for record in records.values():
            summary = (
                record.get("extracted_summary")
                or (record.get("content") or {}).get("extracted_summary")
                or record.get("name")
            )
            assets.append(
                {
                    "asset_id": record.get("asset_id"),
                    "kind": record.get("kind"),
                    "name": record.get("name"),
                    "version": record.get("version"),
                    "source_category": record.get("source_category"),
                    "classification": record.get("classification"),
                    "summary": summary,
                    "seed_identity_hash": record.get("seed_identity_hash"),
                    "record_hash": record.get("record_hash"),
                    "hash_short": _short(record.get("hash")),
                    "status": record.get("status"),
                    "origin": record.get("origin"),
                    "eval_state": record.get("eval_state"),
                    "permission_scope": record.get("permission_scope"),
                    "approval_level": record.get("approval_level"),
                    "pending_permission": record.get("pending_permission"),
                    "implementation_status": record.get("implementation_status"),
                    "verified_lineage": bool(record.get("verified_lineage", False)),
                }
            )
        assets.sort(key=lambda item: (str(item.get("kind")), str(item.get("asset_id"))))
        counts: dict[str, int] = {}
        for asset in assets:
            kind = str(asset.get("kind"))
            counts[kind] = counts.get(kind, 0) + 1
        scenario = self._scenario()
        return {
            "scenario": (
                {
                    "upper_scenario_id": scenario.scenario_id,
                    "upper_scenario_version": scenario.version,
                    "family": scenario.family_id,
                }
                if scenario
                else None
            ),
            "counts": counts,
            "assets": _public(assets),
        }

    def evaluation_public(self, evaluation_id: str) -> dict[str, Any]:
        self.ensure_initialized()
        if not _ID_RE.fullmatch(evaluation_id or ""):
            raise UpperApiError(404, "not_found", "evaluation 不存在")
        record = self._load_record("evaluations", evaluation_id)
        if record is None:
            raise UpperApiError(404, "not_found", "evaluation 不存在")
        payload = dict(record)
        decision_id = str(payload.get("decision_id") or "")
        if decision_id and _ID_RE.fullmatch(decision_id):
            decision = self._load_record("decisions", decision_id)
            if decision is not None:
                payload["decision"] = decision
        payload["mode_label"] = "REPLAY" if payload.get("mode") == "replay" else "LIVE"
        if payload.get("mode") == "replay":
            payload["cost_usd"] = 0.0
            payload["cost_source"] = "none"
        return _public(payload)

    def _record_dirs(self, kind: str) -> tuple[Path, ...]:
        replay_state = self.upper_dir / REPLAY_DIR
        return (
            self.upper_dir / kind,
            replay_state / "upper" / kind,
            replay_state / kind,
        )

    def _load_record(self, kind: str, record_id: str) -> dict[str, Any] | None:
        for directory in self._record_dirs(kind):
            path = directory / f"{record_id}.json"
            if path.is_file():
                return _read_json_file(path)
        return None

    def _replay_registry(self) -> UpperRegistry | None:
        replay_state = self.upper_dir / REPLAY_DIR
        if not (replay_state / "registry.json").is_file():
            return None
        try:
            return UpperRegistry(replay_state, repo_root=self.repo_root)
        except (UpperRegistryError, OSError):
            return None

    # ---- replay ---------------------------------------------------------
    def run_replay(self) -> dict[str, Any]:
        self.ensure_initialized()
        if not self._replay_lock.acquire(blocking=False):
            raise UpperApiError(
                409,
                "upper_busy",
                "已有 REPLAY 在运行",
                {"reason": "upper_busy"},
            )
        handle = None
        try:
            handle = open(self.upper_dir / "replay.lock", "a+", encoding="utf-8")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise UpperApiError(
                    409,
                    "upper_busy",
                    "已有 REPLAY 在运行",
                    {"reason": "upper_busy"},
                ) from None
            replay_state = self.upper_dir / REPLAY_DIR
            try:
                summary = self._replay_runner(
                    scenario_ref=self.scenario_ref,
                    state_dir=replay_state,
                    repo_root=self.repo_root,
                    reset=True,
                    grace_s=self.grace_s,
                )
            except SystemExit as exc:
                raise UpperApiError(
                    503,
                    "upper_eval_unavailable",
                    "REPLAY evaluator 不可用",
                    {"reason": "replay_failed", "detail": str(exc)[:200]},
                ) from exc
            except (OSError, ValueError, GraphError) as exc:
                raise UpperApiError(
                    503,
                    "upper_eval_unavailable",
                    "REPLAY evaluator 执行失败",
                    {"reason": "replay_failed", "detail": str(exc)[:200]},
                ) from exc
            except Exception as exc:  # noqa: BLE001 - never leak internals
                raise UpperApiError(
                    503,
                    "upper_eval_unavailable",
                    "REPLAY evaluator 执行失败",
                    {"reason": "replay_failed", "detail": type(exc).__name__},
                ) from exc

            if not isinstance(summary, dict) or summary.get("mode") != "replay":
                raise UpperApiError(
                    503,
                    "upper_eval_unavailable",
                    "REPLAY 结果格式非法",
                    {"reason": "replay_result_invalid"},
                )
            try:
                self._copy_replay_records(replay_state)
                payload = self._replay_payload(summary)
                _write_json(self.upper_dir / REPLAY_SUMMARY_FILE, payload)
            except OSError as exc:
                raise UpperApiError(
                    503,
                    "upper_unavailable",
                    "REPLAY 证据落盘失败",
                    {"reason": "state_write_failed"},
                ) from exc
            return payload
        finally:
            if handle is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()
            self._replay_lock.release()

    def _copy_replay_records(self, replay_state: Path) -> None:
        for kind in ("evaluations", "decisions"):
            source = replay_state / "upper" / kind
            target = self.upper_dir / kind
            if not source.is_dir():
                continue
            target.mkdir(parents=True, exist_ok=True)
            for path in sorted(source.glob("*.json")):
                if not path.is_file() or not _ID_RE.fullmatch(path.stem):
                    continue
                target_path = target / path.name
                data = path.read_bytes()
                if target_path.is_file() and target_path.read_bytes() == data:
                    continue
                temporary = target / f".{path.name}.tmp"
                try:
                    temporary.write_bytes(data)
                    os.replace(temporary, target_path)
                finally:
                    if temporary.exists():
                        temporary.unlink(missing_ok=True)

    def _replay_payload(self, summary: dict[str, Any]) -> dict[str, Any]:
        decisions = list(summary.get("decisions") or [])
        evaluation_ids = sorted(
            {
                str(item.get("evaluation_id"))
                for phase in ("transfer", "regression")
                for item in summary.get("phases", {}).get(phase, [])
                if item.get("evaluation_id")
            }
        )
        replay_id = "replay-" + sha256_text(
            canonical_json(
                {
                    "scenario_version": summary.get("upper_scenario_version"),
                    "evaluation_ids": evaluation_ids,
                    "decisions": [
                        [item.get("asset_id"), item.get("upper_decision")]
                        for item in decisions
                    ],
                }
            )
        )[:16]
        graph = summary.get("graph") or {}
        return _public(
            {
                "schema_version": UPPER_API_SCHEMA_VERSION,
                "replay_id": replay_id,
                "mode": "replay",
                "mode_label": "REPLAY",
                "calls_model": False,
                "upper_scenario_id": summary.get("upper_scenario_id"),
                "upper_scenario_version": summary.get("upper_scenario_version"),
                "scenario_ref": summary.get("scenario_ref"),
                "state_scope": "external",
                "generated_at": utc_now(),
                "phases": {
                    phase: list(summary.get("phases", {}).get(phase, []))
                    for phase in ("transfer", "regression")
                },
                "decisions": decisions,
                "baseline_evaluations": summary.get("baseline_evaluations") or {},
                "graph": _project_graph(graph),
                "graph_rollback": summary.get("graph_rollback"),
                "asset_status": {
                    "active": list(
                        (summary.get("final_state") or {}).get("active") or []
                    ),
                    "rolled_back": list(
                        (summary.get("final_state") or {}).get("rolled_back") or []
                    ),
                },
                "pending_permissions": list(
                    (summary.get("final_state") or {}).get("pending_permissions") or []
                ),
                "notes": list(summary.get("notes") or []),
            }
        )

    # ---- graph mutations ------------------------------------------------
    def switch_branch(
        self,
        branch_id: str,
        *,
        expected_revision: int,
        expected_tip: str,
    ) -> dict[str, Any]:
        self.ensure_initialized()
        if not _ID_RE.fullmatch(branch_id or ""):
            raise UpperApiError(404, "not_found", "branch 不存在")
        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise UpperApiError(400, "bad_request", "expected_revision 必须是非负整数")
        if not isinstance(expected_tip, str) or not expected_tip.strip():
            raise UpperApiError(400, "bad_request", "expected_tip 必须是非空字符串")
        with self._write_lock:
            graph = self._graph()
            pointer = graph.active_pointer()
            if pointer is None:
                raise UpperApiError(503, "upper_unavailable", "图谱没有 active pointer")
            self._check_revision(pointer, expected_revision)
            try:
                branch = graph.branch(branch_id)
            except GraphValidationError:
                raise UpperApiError(404, "not_found", "branch 不存在") from None
            if branch.tip_node_id != expected_tip:
                raise UpperApiError(
                    409,
                    "stale_tip",
                    "分支 tip 已变化",
                    {
                        "reason": "stale_tip",
                        "current_tip": branch.tip_node_id,
                    },
                )
            if pointer.get("branch_id") == branch_id:
                try:
                    graph.validate_context(branch.tip_node_id, branch_id)
                except ApprovalError as exc:
                    raise UpperApiError(
                        422,
                        "approval_required",
                        str(exc),
                        {"reason": "approval_required", "branch_id": branch_id},
                    ) from None
                except GraphValidationError as exc:
                    raise UpperApiError(
                        422,
                        "graph_validation",
                        str(exc),
                        {"reason": "graph_validation", "branch_id": branch_id},
                    ) from None
                return {
                    "changed": False,
                    "revision": pointer["revision"],
                    "active": _project_active(pointer),
                }
            try:
                graph.switch(
                    branch_id,
                    branch.tip_node_id,
                    expected_tip=expected_tip,
                    expected_active_branch=pointer.get("branch_id"),
                    author=API_ACTOR,
                )
            except ConflictError as exc:
                raise self._graph_conflict(exc, pointer) from None
            except ApprovalError as exc:
                raise UpperApiError(
                    422,
                    "approval_required",
                    str(exc),
                    {"reason": "approval_required", "branch_id": branch_id},
                ) from None
            except (GraphValidationError, GraphIntegrityError) as exc:
                raise UpperApiError(
                    422,
                    "graph_validation",
                    str(exc),
                    {"reason": "graph_validation", "branch_id": branch_id},
                ) from None
            except GraphError as exc:
                raise UpperApiError(
                    503,
                    "upper_unavailable",
                    "图谱切换失败",
                    {"reason": "graph_error", "detail": type(exc).__name__},
                ) from None
            new_pointer = graph.active_pointer()
            return {
                "changed": True,
                "revision": new_pointer["revision"],
                "active": _project_active(new_pointer),
                "branch": {
                    "branch_id": branch_id,
                    "label": branch.label,
                    "permission_scope": branch.permission_scope,
                    "tip_node_id": branch.tip_node_id,
                    "tip_hash": branch.tip_node_id,
                },
            }

    def rollback(
        self,
        *,
        branch_id: str,
        target_node_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        self.ensure_initialized()
        if not _ID_RE.fullmatch(branch_id or ""):
            raise UpperApiError(404, "not_found", "branch 不存在")
        if not _ID_RE.fullmatch(target_node_id or ""):
            raise UpperApiError(404, "not_found", "目标节点不存在")
        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise UpperApiError(400, "bad_request", "expected_revision 必须是非负整数")
        with self._write_lock:
            graph = self._graph()
            pointer = graph.active_pointer()
            if pointer is None:
                raise UpperApiError(503, "upper_unavailable", "图谱没有 active pointer")
            self._check_revision(pointer, expected_revision)
            try:
                branch = graph.branch(branch_id)
                target = graph.node(target_node_id)
            except GraphValidationError:
                raise UpperApiError(404, "not_found", "branch 或目标节点不存在") from None
            if target.status != "committed":
                raise UpperApiError(
                    422,
                    "graph_validation",
                    "目标节点不是已提交节点",
                    {"reason": "target_not_committed"},
                )
            if not self._is_ancestor(graph, target.node_id, branch.tip_node_id):
                raise UpperApiError(
                    422,
                    "graph_validation",
                    "目标节点不在当前分支历史上",
                    {"reason": "target_not_ancestor"},
                )
            if target.node_id == branch.tip_node_id:
                raise UpperApiError(
                    422,
                    "graph_validation",
                    "目标节点已经是 branch tip",
                    {"reason": "already_at_target"},
                )
            try:
                graph.rollback(
                    branch_id,
                    target.node_id,
                    expected_tip=branch.tip_node_id,
                    expected_active_branch=pointer.get("branch_id"),
                    author=API_ACTOR,
                    reason=f"api_rollback:{target.node_id[:12]}",
                )
            except ConflictError as exc:
                raise self._graph_conflict(exc, pointer) from None
            except ApprovalError as exc:
                raise UpperApiError(
                    422,
                    "approval_required",
                    str(exc),
                    {"reason": "approval_required", "branch_id": branch_id},
                ) from None
            except (GraphValidationError, GraphIntegrityError) as exc:
                raise UpperApiError(
                    422,
                    "graph_validation",
                    str(exc),
                    {"reason": "graph_validation", "branch_id": branch_id},
                ) from None
            except GraphError as exc:
                raise UpperApiError(
                    503,
                    "upper_unavailable",
                    "图谱回退失败",
                    {"reason": "graph_error", "detail": type(exc).__name__},
                ) from None
            new_pointer = graph.active_pointer()
            return {
                "changed": True,
                "branch_id": branch_id,
                "target_node_id": target.node_id,
                "revision": new_pointer["revision"],
                "active": _project_active(new_pointer),
                "history_retained": True,
            }

    @staticmethod
    def _check_revision(pointer: dict[str, Any], expected_revision: int) -> None:
        current = int(pointer.get("revision", 0))
        if current != expected_revision:
            raise UpperApiError(
                409,
                "stale_revision",
                "revision 已变化",
                {
                    "reason": "stale_revision",
                    "current_revision": current,
                    "expected_revision": expected_revision,
                },
            )

    @staticmethod
    def _graph_conflict(exc: ConflictError, pointer: dict[str, Any]) -> UpperApiError:
        return UpperApiError(
            409,
            "conflict",
            str(exc),
            {
                "reason": "cas_conflict",
                "current_revision": int(pointer.get("revision", 0)),
            },
        )

    @staticmethod
    def _is_ancestor(graph: UpperGraphStore, candidate: str, tip: str) -> bool:
        nodes = {node.node_id: node for node in graph.nodes()}
        stack = [tip]
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            if current == candidate:
                return True
            if current in seen:
                continue
            seen.add(current)
            node = nodes.get(current)
            if node is not None:
                stack.extend(node.parents)
        return False


def parse_strict_body(
    payload: dict[str, Any],
    *,
    required: tuple[str, ...],
) -> dict[str, Any]:
    """Validate a POST body without accepting unknown or missing keys."""

    unknown = sorted(set(payload) - set(required))
    if unknown:
        raise UpperApiError(
            400,
            "bad_request",
            "请求体包含未知字段",
            {"unknown_fields": unknown},
        )
    missing = [key for key in required if key not in payload]
    if missing:
        raise UpperApiError(
            400,
            "bad_request",
            "请求体缺少字段",
            {"missing_fields": missing},
        )
    return {key: payload[key] for key in required}


__all__ = [
    "API_ACTOR",
    "UPPER_API_SCHEMA_VERSION",
    "UPPER_SCENARIO_REF",
    "UpperApiError",
    "UpperService",
    "parse_strict_body",
]
