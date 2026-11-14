"""Evaluation port for the upper layer.

`molt.upper.evaluator` is only a graph orchestrator/wiring layer. Evaluation
protocols are owned by this port:

- prefer the third macro's ``molt.upper_eval.api.evaluate`` (read-only import);
- else invoke the fixed CLI ``python -m molt.upper_eval.evaluate`` with a
  request/out file in an independent temp directory and a killable process group;
- if neither is available return ``status="blocked"`` with
  ``error.code="upper_eval_unavailable"``; never fabricate metrics.

The B1 local adapter is explicitly labelled ``adapter="local_replay"`` and only
re-scores recorded solutions; it must not be presented as the third macro's
evaluator.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .types import canonical_json, sha256_text, utc_now

EVAL_SCHEMA_VERSION = "1.0.0"
STATUSES = ("passed", "failed", "blocked", "not_evaluated")
REQUIRED_METRIC_KEYS = (
    "transfer",
    "regression",
    "retrieval",
    "cost_usd",
    "latency_ms",
    "permission",
)
ABSOLUTE_PATH = re.compile(r"/(?:home|Users)/[^\s\"']+")


class EvalPortError(ValueError):
    """Invalid evaluation request/result."""


def _sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return ABSOLUTE_PATH.sub("<path>", value)
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()}
    return value


@dataclass
class EvaluationRequest:
    asset_ref: dict[str, Any]
    scenario_version: str
    task_refs: list[dict[str, Any]]
    dimensions: list[str] = field(default_factory=lambda: ["transfer", "regression"])
    options: dict[str, Any] = field(default_factory=lambda: {"replay": True, "mcp_static_only": True})
    schema_version: str = EVAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EVAL_SCHEMA_VERSION:
            raise EvalPortError("unsupported request schema_version")
        if not self.scenario_version:
            raise EvalPortError("scenario_version is required")
        for key in ("asset_id", "version", "seed_identity_hash", "record_hash"):
            if key not in self.asset_ref:
                raise EvalPortError(f"asset_ref missing {key}")
        if not isinstance(self.task_refs, list):
            raise EvalPortError("task_refs must be a list")
        for task in self.task_refs:
            for key in (
                "task_id",
                "family",
                "baseline_solution_ref",
                "candidate_solution_ref",
                "source_run_id",
                "provenance",
            ):
                if key not in task:
                    raise EvalPortError(f"task_ref missing {key}")
            for key in ("baseline_solution_ref", "candidate_solution_ref"):
                ref = str(task[key])
                if Path(ref).is_absolute() or ".." in Path(ref).parts:
                    raise EvalPortError(f"{key} must be a repository-relative path")
        if not isinstance(self.dimensions, list) or not self.dimensions:
            raise EvalPortError("dimensions must be a non-empty list")
        if not isinstance(self.options, dict):
            raise EvalPortError("options must be a dict")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "asset_ref": self.asset_ref,
            "scenario_version": self.scenario_version,
            "task_refs": self.task_refs,
            "dimensions": self.dimensions,
            "options": self.options,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EvaluationRequest:
        return cls(
            asset_ref=dict(payload["asset_ref"]),
            scenario_version=payload["scenario_version"],
            task_refs=[dict(item) for item in payload.get("task_refs", [])],
            dimensions=list(payload.get("dimensions", [])),
            options=dict(payload.get("options", {})),
            schema_version=payload.get("schema_version", EVAL_SCHEMA_VERSION),
        )

    def request_hash(self) -> str:
        return sha256_text(canonical_json(self.to_dict()))


@dataclass
class EvaluationResult:
    evaluation_id: str
    content_hash: str
    status: str
    metrics: dict[str, Any]
    evidence_refs: list[str]
    provenance: dict[str, Any]
    error: dict[str, Any] | None = None
    schema_version: str = EVAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EVAL_SCHEMA_VERSION:
            raise EvalPortError("unsupported result schema_version")
        if self.status not in STATUSES:
            raise EvalPortError(f"unknown result status: {self.status!r}")
        if not self.evaluation_id or not self.content_hash:
            raise EvalPortError("evaluation_id/content_hash are required")
        missing = [key for key in REQUIRED_METRIC_KEYS if key not in self.metrics]
        if missing:
            raise EvalPortError(f"metrics missing keys: {missing}")
        if not isinstance(self.metrics["retrieval"], dict):
            raise EvalPortError("metrics.retrieval must be a dict")
        for key in ("hits", "misses", "sources"):
            if key not in self.metrics["retrieval"]:
                raise EvalPortError(f"metrics.retrieval missing {key}")
        if not isinstance(self.provenance, dict) or "adapter" not in self.provenance:
            raise EvalPortError("result provenance must include adapter")
        if self.status == "blocked" and not self.error:
            raise EvalPortError("blocked result requires error")
        if self.error is not None and "code" not in self.error:
            raise EvalPortError("error must include code")

    def to_dict(self) -> dict[str, Any]:
        return _sanitize(
            {
                "schema_version": self.schema_version,
                "evaluation_id": self.evaluation_id,
                "content_hash": self.content_hash,
                "status": self.status,
                "metrics": self.metrics,
                "evidence_refs": self.evidence_refs,
                "provenance": self.provenance,
                "error": self.error,
            }
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EvaluationResult:
        return cls(
            evaluation_id=payload["evaluation_id"],
            content_hash=payload["content_hash"],
            status=payload["status"],
            metrics=dict(payload["metrics"]),
            evidence_refs=list(payload.get("evidence_refs", [])),
            provenance=dict(payload.get("provenance", {})),
            error=payload.get("error"),
            schema_version=payload.get("schema_version", EVAL_SCHEMA_VERSION),
        )


def _blocked(request: EvaluationRequest, code: str, message: str) -> EvaluationResult:
    return EvaluationResult(
        evaluation_id=f"eval-req-{request.request_hash()[:16]}",
        content_hash=sha256_text(canonical_json({"request": request.to_dict(), "status": code})),
        status="blocked",
        metrics={
            "transfer": {"tasks": 0, "candidate": {}, "baseline": {}},
            "regression": {"tasks": 0, "candidate": {}, "baseline": {}},
            "retrieval": {"hits": 0, "misses": len(request.task_refs), "sources": []},
            "cost_usd": 0.0,
            "latency_ms": {"total": 0.0, "mean": 0.0, "max": 0.0},
            "permission": {"executed": False, "mcp_calls": 0},
        },
        evidence_refs=[],
        provenance={"adapter": "blocked", "mode": "none", "calls_model": False},
        error={"code": code, "message": _sanitize(message)},
    )


def _rate(passed: int, attempts: int) -> dict[str, Any]:
    return {
        "attempts": attempts,
        "passed": passed,
        "pass_rate": round(passed / attempts, 6) if attempts else 0.0,
    }


def local_replay_adapter(request: EvaluationRequest) -> EvaluationResult:
    """B1 REPLAY adapter: re-score recorded solutions with ProbeVerifier."""
    from .evaluator import ReplayRunner, SnapshotRef

    repo_root = Path(__file__).resolve().parents[2]
    work = Path(tempfile.mkdtemp(prefix="molt-upper-local-"))
    runner = ReplayRunner(repo_root, work)
    candidate_rows: list[dict[str, Any]] = []
    baseline_rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    errors: list[str] = []
    try:
        for index, task in enumerate(request.task_refs):
            task_id = str(task["task_id"])
            for bucket, key in (
                (candidate_rows, "candidate_solution_ref"),
                (baseline_rows, "baseline_solution_ref"),
            ):
                ref = SnapshotRef(
                    task_id=task_id,
                    rep=index,
                    run_id=str(task.get("source_run_id", "local")),
                    solution_path=str(task[key]),
                    recorded_solved=None,
                    recorded_outcome=None,
                    group="local_replay",
                    source_path=str(task[key]),
                    memory=None,
                    genome=None,
                )
                outcome = runner.replay(ref, label=f"local-{key}-{index}")
                latencies.append(float(outcome["latency_ms"]))
                if outcome["error"]:
                    errors.append(str(outcome["error"]))
                bucket.append(
                    {
                        "task_id": task_id,
                        "family": task.get("family", ""),
                        "passed": bool(outcome["passed"]),
                        "source_run_id": task.get("source_run_id"),
                    }
                )
    except Exception as exc:
        return _blocked(request, "local_replay_error", f"{type(exc).__name__}: {exc}")

    candidates = {row["task_id"]: row for row in candidate_rows}
    baselines = {row["task_id"]: row for row in baseline_rows}
    candidate_passed = sum(1 for row in candidate_rows if row["passed"])
    baseline_passed = sum(1 for row in baseline_rows if row["passed"])
    transfer = {
        "tasks": len(candidate_rows),
        "candidate": _rate(candidate_passed, len(candidate_rows)),
        "baseline": _rate(baseline_passed, len(baseline_rows)),
        "tasks_improved": [
            task_id
            for task_id in candidates
            if candidates[task_id]["passed"] and not baselines.get(task_id, {}).get("passed", False)
        ],
        "tasks_regressed": [
            task_id
            for task_id in candidates
            if not candidates[task_id]["passed"] and baselines.get(task_id, {}).get("passed", False)
        ],
    }
    regression_tasks = [
        row for row in candidate_rows if "regression" in str(row.get("family", "")).lower()
    ]
    regression = {
        "tasks": len(regression_tasks),
        "candidate": _rate(
            sum(1 for row in regression_tasks if row["passed"]), len(regression_tasks)
        ),
        "baseline": _rate(
            sum(
                1
                for row in regression_tasks
                if baselines.get(row["task_id"], {}).get("passed", False)
            ),
            len(regression_tasks),
        ),
    }
    status = "not_evaluated" if not candidate_rows else (
        "passed" if candidate_passed == len(candidate_rows) else "failed"
    )
    total_latency = round(sum(latencies), 3)
    result = EvaluationResult(
        evaluation_id=f"eval-local-{request.request_hash()[:16]}",
        content_hash=sha256_text(canonical_json({"request": request.to_dict(), "status": status})),
        status=status,
        metrics={
            "transfer": transfer,
            "regression": regression,
            "retrieval": {"hits": 0, "misses": len(request.task_refs), "sources": []},
            "cost_usd": 0.0,
            "latency_ms": {
                "total": total_latency,
                "mean": round(total_latency / len(latencies), 3) if latencies else 0.0,
                "max": max(latencies) if latencies else 0.0,
            },
            "permission": {
                "executed": False,
                "mcp_calls": 0,
                "mcp_static_only": bool(request.options.get("mcp_static_only", True)),
            },
        },
        evidence_refs=sorted(
            {
                str(task[key])
                for task in request.task_refs
                for key in ("candidate_solution_ref", "baseline_solution_ref")
            }
        ),
        provenance={
            "adapter": "local_replay",
            "mode": "replay",
            "calls_model": False,
            "created_at": utc_now(),
            "errors": sorted(set(errors)),
        },
    )
    return result


def _external_available() -> bool:
    try:
        importlib.import_module("molt.upper_eval.api")
        return True
    except (ImportError, ModuleNotFoundError):
        return False


def _run_module_cli(request: EvaluationRequest, *, timeout_s: float) -> EvaluationResult:
    repo_root = Path(__file__).resolve().parents[2]
    work = Path(tempfile.mkdtemp(prefix="molt-upper-eval-"))
    request_path = work / "request.json"
    out_path = work / "result.json"
    request_path.write_text(
        json.dumps(request.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    command = [
        sys.executable,
        "-m",
        "molt.upper_eval.evaluate",
        "--request",
        str(request_path),
        "--out",
        str(out_path),
    ]
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    process = subprocess.Popen(
        command,
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.communicate()
        return _blocked(request, "upper_eval_timeout", "upper_eval CLI timed out")
    finally:
        if process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
    if process.returncode != 0 or not out_path.is_file():
        detail = (stderr or stdout or "").strip()[-500:]
        return _blocked(
            request,
            "upper_eval_unavailable",
            f"upper_eval CLI unavailable: {detail or 'no output'}",
        )
    try:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        return EvaluationResult.from_dict(payload)
    except Exception as exc:
        return _blocked(
            request,
            "upper_eval_bad_result",
            f"invalid upper_eval result: {type(exc).__name__}: {exc}",
        )


def evaluate(
    request: EvaluationRequest,
    *,
    adapter: str = "auto",
    timeout_s: float = 30.0,
) -> EvaluationResult:
    """Dispatch to the third macro evaluator, else block; local adapter is explicit."""
    if adapter not in {"auto", "external", "local_replay"}:
        raise EvalPortError(f"unknown adapter: {adapter!r}")
    if adapter == "local_replay":
        return local_replay_adapter(request)
    if _external_available():
        try:
            module = importlib.import_module("molt.upper_eval.api")
            payload = module.evaluate(request.to_dict())
            if isinstance(payload, EvaluationResult):
                return payload
            return EvaluationResult.from_dict(payload)
        except Exception as exc:
            return _blocked(
                request,
                "upper_eval_error",
                f"upper_eval.api failed: {type(exc).__name__}: {exc}",
            )
    result = _run_module_cli(request, timeout_s=timeout_s)
    if result.status == "blocked" and result.error and result.error.get("code") == "upper_eval_unavailable":
        return _blocked(
            request,
            "upper_eval_unavailable",
            "third macro upper_eval is not installed; no metrics fabricated",
        )
    return result
