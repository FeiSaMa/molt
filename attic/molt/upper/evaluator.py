"""REPLAY evaluator for upper-layer policy-memory assets.

The evaluator re-scores recorded solution snapshots with the existing
``ProbeTaskProvider`` + ``ProbeVerifier``. It never calls a model, never calls a
core gate, and never mutates the recorded evidence. Every replay runs through a
separate worker process so the evaluator can kill the whole process group on
timeout/cancel.

Fact about the existing verifier: bench probe tasks declare ``timeout_s=120``
and ``ProbeVerifier.verify`` uses ``subprocess.run(..., timeout=task.timeout_s)``,
which terminates only the direct checker child. ``ReplayRunner`` adds a worker
process group (``start_new_session=True``) and kills the whole group with
``os.killpg``; its outer budget is ``task.timeout_s + grace_s``.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shells.probe.task_provider import ProbeTaskProvider

from . import eval_port as eval_port_module
from .eval_port import EvaluationRequest, EvaluationResult
from .graph import ContextChangedError
from .retrieval import RetrievalIndex
from .storage import ensure_outside_repo
from .types import UpperAsset, UpperDecision, UpperEvaluation, utc_now

PHASES = ("transfer", "regression")
ERROR_BUCKETS = ("none", "invalid_solution", "checker_failed", "timed_out", "worker_error", "other")


class ScenarioError(ValueError):
    """Raised when an upper scenario or its evidence mapping is invalid."""


@dataclass(frozen=True)
class SnapshotRef:
    task_id: str
    rep: int
    run_id: str
    solution_path: str
    recorded_solved: int | None
    recorded_outcome: str | None
    group: str | None
    source_path: str
    memory: str | None
    genome: str | None


@dataclass(frozen=True)
class UpperScenario:
    path: Path
    repo_root: Path
    scenario_id: str
    version: str
    title: str
    family_id: str
    task_sets: dict[str, tuple[str, ...]]
    baselines: dict[str, dict[str, Any]]
    assets: dict[str, dict[str, Any]]
    lower_domain: dict[str, Any]
    governance: dict[str, Any]

    def build_asset(self, asset_id: str) -> UpperAsset:
        spec = self.assets[asset_id]
        return UpperAsset(
            asset_id=asset_id,
            kind=spec["kind"],
            name=spec["name"],
            version=spec["version"],
            source=spec["source"],
            family=self.family_id,
            created_by=spec["created_by"],
            evidence_refs=tuple(spec.get("evidence_refs", ())),
            status="draft",
            upper_scenario_id=self.scenario_id,
            upper_scenario_version=self.version,
            content=dict(spec.get("content", {})),
            implementation_status=spec.get("implementation_status", "stub"),
            permission_scope=spec.get("permission_scope"),
            approval_level=spec.get("approval_level"),
            origin=spec.get("origin", "real_run"),
            eval_state=spec.get("eval_state", "not_evaluated"),
        )

    def phase_tasks(self, phase: str) -> tuple[str, ...]:
        try:
            return self.task_sets[phase]
        except KeyError:
            raise ScenarioError(f"unknown phase: {phase!r}") from None


def _resolve_under_repo(repo_root: Path, relative: str, *, what: str) -> Path:
    path = (repo_root / relative).resolve()
    if repo_root not in path.parents:
        raise ScenarioError(f"{what} escapes the repository tree: {relative!r}")
    if not path.exists():
        raise ScenarioError(f"{what} does not exist: {relative!r}")
    return path


def load_scenario(scenario_ref: str, repo_root: Path) -> UpperScenario:
    repo_root = Path(repo_root).resolve()
    candidate = Path(scenario_ref).expanduser()
    if candidate.is_file():
        path = candidate.resolve()
    else:
        path = (repo_root / "upper_scenarios" / scenario_ref / "scenario.json").resolve()
    if not path.exists():
        raise ScenarioError(f"upper scenario not found: {scenario_ref!r}")
    upper_root = (repo_root / "upper_scenarios").resolve()
    if upper_root not in path.parents:
        raise ScenarioError("upper scenarios must live under upper_scenarios/, not scenarios/")
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenario_id = str(payload.get("upper_scenario_id", "")).strip()
    version = str(payload.get("upper_scenario_version", "")).strip()
    if not scenario_id or not version.startswith("upper-"):
        raise ScenarioError("upper_scenario_id and upper_scenario_version=upper-* are required")
    family = payload.get("family") or {}
    family_id = str(family.get("family_id", "")).strip()
    task_sets_raw = family.get("task_sets") or {}
    if not family_id:
        raise ScenarioError("family.family_id is required")
    task_sets: dict[str, tuple[str, ...]] = {}
    for phase in PHASES:
        tasks = task_sets_raw.get(phase)
        if not isinstance(tasks, list) or not tasks:
            raise ScenarioError(f"family.task_sets.{phase} must be a non-empty list")
        task_sets[phase] = tuple(str(task) for task in tasks)
    baselines = payload.get("baselines") or {}
    for phase in PHASES:
        spec = baselines.get(phase)
        if not isinstance(spec, dict) or not spec.get("sources"):
            raise ScenarioError(f"baselines.{phase}.sources is required")
    assets: dict[str, dict[str, Any]] = {}
    for spec in payload.get("assets", []):
        asset_id = str(spec.get("asset_id", "")).strip()
        if not asset_id or asset_id in assets:
            raise ScenarioError(f"asset_id is required and unique: {asset_id!r}")
        if spec.get("family", family_id) != family_id:
            raise ScenarioError(f"asset {asset_id} family must be {family_id!r}")
        if spec.get("evaluate"):
            sources = spec.get("sources") or {}
            if not sources:
                raise ScenarioError(f"asset {asset_id} is evaluated but has no sources")
            for phase in sources:
                if phase not in PHASES:
                    raise ScenarioError(f"asset {asset_id} has unknown source phase: {phase!r}")
        assets[asset_id] = spec
    if not assets:
        raise ScenarioError("scenario must define at least one asset")
    return UpperScenario(
        path=path,
        repo_root=repo_root,
        scenario_id=scenario_id,
        version=version,
        title=str(payload.get("title", "")),
        family_id=family_id,
        task_sets=task_sets,
        baselines=baselines,
        assets=assets,
        lower_domain=dict(payload.get("lower_comparison_domain", {})),
        governance=dict(payload.get("governance", {})),
    )


def _iter_rows(block: Any) -> Iterable[dict[str, Any]]:
    if isinstance(block, dict):
        runs = block.get("runs")
        if isinstance(runs, list):
            yield from (row for row in runs if isinstance(row, dict))
            return
        for value in block.values():
            if isinstance(value, list) and value and all(isinstance(row, dict) for row in value):
                if any("task_id" in row for row in value):
                    yield from value
            elif isinstance(value, dict):
                yield from _iter_rows(value)


def _load_sources(repo_root: Path, sources: list[dict[str, Any]]) -> list[SnapshotRef]:
    refs: list[SnapshotRef] = []
    seen: set[tuple[str, int, str]] = set()
    for spec in sources:
        source_path = _resolve_under_repo(repo_root, spec["path"], what="source rows")
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        if spec.get("kind") == "nested":
            pointer = spec.get("pointer")
            if not pointer:
                raise ScenarioError("nested source requires pointer")
            block = payload[pointer]
            rows = _iter_rows(block)
        else:
            rows = payload.get("runs", [])
        task_filter = set(spec.get("task_ids", []))
        rep_filter = set(spec.get("reps", []))
        for row in rows:
            if not isinstance(row, dict):
                continue
            if spec.get("group") is not None and row.get("group") != spec["group"]:
                continue
            if spec.get("memory") is not None and row.get("memory") != spec["memory"]:
                continue
            if spec.get("genome") is not None and row.get("genome") != spec["genome"]:
                continue
            task_id = row.get("task_id")
            if not task_id or (task_filter and task_id not in task_filter):
                continue
            rep = int(row.get("rep", 0))
            if rep_filter and rep not in rep_filter:
                continue
            run_id = row.get("run_id")
            if not run_id:
                raise ScenarioError(f"row without run_id in {spec['path']}")
            snapshot = row.get("solution_snapshot")
            if not snapshot:
                pattern = spec.get("solution_pattern")
                if not pattern:
                    raise ScenarioError(
                        f"row {task_id}/r{rep} has no solution_snapshot and no pattern"
                    )
                snapshot = pattern.format(task_id=task_id, rep=rep, group=row.get("group", ""))
            _resolve_under_repo(repo_root, snapshot, what="solution snapshot")
            key = (task_id, rep, run_id)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                SnapshotRef(
                    task_id=str(task_id),
                    rep=rep,
                    run_id=str(run_id),
                    solution_path=snapshot,
                    recorded_solved=row.get("solved"),
                    recorded_outcome=row.get("outcome"),
                    group=row.get("group"),
                    source_path=spec["path"],
                    memory=row.get("memory"),
                    genome=row.get("genome"),
                )
            )
    if not refs:
        raise ScenarioError("source mapping produced no snapshot references")
    return sorted(refs, key=lambda ref: (ref.task_id, ref.rep, ref.run_id))


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()


class ReplayRunner:
    """Run one recorded solution in a killable worker process group."""

    def __init__(self, repo_root: Path, state_dir: Path, *, grace_s: float = 30.0) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.state_dir = ensure_outside_repo(state_dir, repo_root)
        self.grace_s = max(0.0, float(grace_s))
        self.work_root = self.state_dir / "work"
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.provider = ProbeTaskProvider(self.repo_root)

    def replay(self, ref: SnapshotRef, *, label: str) -> dict[str, Any]:
        task = self.provider.load_task(ref.task_id)
        solution = _resolve_under_repo(
            self.repo_root, ref.solution_path, what="replay solution"
        )
        slug = "".join(
            char if char.isalnum() or char in "-_." else "_" for char in label
        )
        workdir = self.work_root / f"{slug}-{ref.task_id}-r{ref.rep}"
        if workdir.exists():
            shutil.rmtree(workdir)
        workdir.mkdir(parents=True)
        (workdir / "solution.py").write_bytes(solution.read_bytes())

        command = [
            sys.executable,
            "-m",
            "molt.upper.replay_worker",
            "--task-id",
            ref.task_id,
            "--solution",
            str(solution),
            "--workdir",
            str(workdir),
        ]
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = str(self.repo_root) + os.pathsep + env.get("PYTHONPATH", "")
        started = time.perf_counter()
        timed_out = False
        worker_error: str | None = None
        payload: dict[str, Any] = {}
        process = subprocess.Popen(
            command,
            cwd=str(self.repo_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=float(task.timeout_s) + self.grace_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(process)
            stdout, stderr = process.communicate()
        finally:
            if process.poll() is None:
                _kill_process_group(process)
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        if timed_out:
            worker_error = "replay_worker_timeout"
        elif stdout.strip():
            try:
                parsed = json.loads(stdout.strip().splitlines()[-1])
                if isinstance(parsed, dict):
                    payload = parsed
                else:
                    worker_error = "replay_worker_non_object"
            except json.JSONDecodeError:
                worker_error = "replay_worker_bad_json"
        else:
            worker_error = "replay_worker_empty_output"
        if worker_error is None and payload.get("error") == "worker_exception":
            worker_error = "replay_worker_exception"
        if worker_error is None and not payload:
            worker_error = f"replay_worker_exit_{process.returncode}"
        return {
            "passed": bool(payload.get("passed", False)) and worker_error is None,
            "outcome": payload.get("outcome", "worker_error" if worker_error else "failed"),
            "matches": payload.get("matches"),
            "timed_out": timed_out or bool(payload.get("timed_out", False)),
            "error": worker_error or payload.get("error"),
            "cases_passed": payload.get("cases_passed", 0),
            "cases_total": payload.get("cases_total", 0),
            "latency_ms": latency_ms,
            "worker_returncode": process.returncode,
            "worker_stderr": (stderr or "").strip()[-500:],
        }


class UpperEvaluator:
    def __init__(
        self,
        scenario: UpperScenario,
        *,
        state_dir: Path | str,
        grace_s: float = 30.0,
    ) -> None:
        self.scenario = scenario
        self.repo_root = scenario.repo_root
        self.state_dir = ensure_outside_repo(Path(state_dir), self.repo_root)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.runner = ReplayRunner(self.repo_root, self.state_dir, grace_s=grace_s)
        self._baseline_cache: dict[str, UpperEvaluation] = {}

    def _validate_refs(self, refs: list[SnapshotRef], phase: str, *, asset_id: str) -> None:
        allowed = set(self.scenario.phase_tasks(phase))
        unexpected = sorted({ref.task_id for ref in refs} - allowed)
        if unexpected:
            raise ScenarioError(
                f"asset {asset_id} {phase} sources contain tasks outside the family: {unexpected}"
            )

    def baseline_evaluation(self, phase: str) -> UpperEvaluation:
        if phase in self._baseline_cache:
            return self._baseline_cache[phase]
        spec = self.scenario.baselines[phase]
        refs = _load_sources(self.repo_root, list(spec["sources"]))
        self._validate_refs(refs, phase, asset_id=str(spec["baseline_id"]))
        baseline_id = str(spec["baseline_id"])
        version = str(spec.get("version", baseline_id))
        rows = self._run_rows(
            refs,
            asset=None,
            asset_id=baseline_id,
            asset_version=version,
            phase=phase,
            label=f"baseline-{phase}",
        )
        reference_counts = self._reference_counts(rows)
        expected = spec.get("historical")
        if isinstance(expected, dict):
            reference_counts["scenario_expected"] = expected
            reference_counts["scenario_expected_match"] = (
                expected.get("runs") == reference_counts["historical"]["runs"]
                and expected.get("passed") == reference_counts["historical"]["passed"]
            )
        metrics = self._aggregate(rows, rows, phase, asset=None)
        metrics["role"] = "baseline"
        telemetry = self._telemetry(rows, asset=None, phase=phase)
        evaluation = UpperEvaluation(
            evaluation_id=f"eval-{self.scenario.scenario_id}-{phase}-baseline",
            upper_scenario_id=self.scenario.scenario_id,
            upper_scenario_version=self.scenario.version,
            asset_id=baseline_id,
            asset_version=version,
            family=self.scenario.family_id,
            task_set=phase,
            mode="replay",
            provenance=self._provenance(),
            tasks=rows,
            metrics=metrics,
            telemetry=telemetry,
            reference_counts=reference_counts,
            baseline={},
            lower_refs=self._lower_refs(spec, rows),
        )
        self._baseline_cache[phase] = evaluation
        return evaluation

    def evaluate(
        self,
        asset_id: str,
        phase: str,
        *,
        asset: UpperAsset | None = None,
        context_binding: Any | None = None,
    ) -> tuple[UpperEvaluation, UpperDecision | None]:
        if phase not in PHASES:
            raise ScenarioError(f"unknown phase: {phase!r}")
        if context_binding is not None:
            context_binding.assert_unchanged()
        asset_spec = self.scenario.assets[asset_id]
        if not asset_spec.get("evaluate"):
            raise ScenarioError(f"asset {asset_id} is not marked for evaluation")
        sources = asset_spec.get("sources", {}).get(phase)
        if not sources:
            raise ScenarioError(f"asset {asset_id} has no {phase} sources")
        if asset is None:
            asset = self.scenario.build_asset(asset_id)
        refs = _load_sources(self.repo_root, list(sources))
        self._validate_refs(refs, phase, asset_id=asset_id)
        rows = self._run_rows(
            refs,
            asset=asset,
            asset_id=asset.asset_id,
            asset_version=asset.version,
            phase=phase,
            label=f"{asset.asset_id}-{phase}",
        )
        baseline = self.baseline_evaluation(phase)
        metrics = self._aggregate(rows, baseline.tasks, phase, asset=asset)
        telemetry = self._telemetry(rows, asset=asset, phase=phase)
        reference_counts = self._reference_counts(rows)
        context_status = "unbound"
        context_node_id = None
        branch_id = None
        context_hash = None
        if context_binding is not None:
            context_node_id = context_binding.node_id
            branch_id = context_binding.branch_id
            context_hash = context_binding.context_hash
            try:
                context_binding.assert_unchanged()
                context_status = "stable"
            except ContextChangedError:
                context_status = "context_changed"
        evaluation = UpperEvaluation(
            evaluation_id=f"eval-{self.scenario.scenario_id}-{phase}-{asset.asset_id}",
            upper_scenario_id=self.scenario.scenario_id,
            upper_scenario_version=self.scenario.version,
            asset_id=asset.asset_id,
            asset_version=asset.version,
            family=self.scenario.family_id,
            task_set=phase,
            mode="replay",
            provenance=self._provenance(),
            tasks=rows,
            metrics=metrics,
            telemetry=telemetry,
            reference_counts=reference_counts,
            baseline={
                "evaluation_id": baseline.evaluation_id,
                "baseline_id": baseline.asset_id,
                "version": baseline.asset_version,
                "historical": baseline.reference_counts["historical"],
                "recomputed": baseline.reference_counts["recomputed"],
                "reference_counts": baseline.reference_counts,
                "tasks": baseline.tasks,
            },
            lower_refs=self._lower_refs(asset_spec, rows),
            context_node_id=context_node_id,
            branch_id=branch_id,
            context_hash=context_hash,
            context_status=context_status,
        )
        if context_status == "context_changed":
            evaluation.metrics["context_status"] = "context_changed"
            evaluation.telemetry["context_status"] = "context_changed"
            return evaluation, None
        upper_decision, reason = self._decide(phase, metrics)
        decision = UpperDecision(
            decision_id=f"dec-{self.scenario.scenario_id}-{phase}-{asset.asset_id}",
            upper_scenario_id=self.scenario.scenario_id,
            upper_scenario_version=self.scenario.version,
            asset_id=asset.asset_id,
            asset_version=asset.version,
            upper_decision=upper_decision,
            reason=reason,
            metrics={
                "phase": phase,
                "candidate": metrics["candidate"],
                "baseline": metrics["baseline"],
                "transfer_delta": metrics["transfer_delta"],
                "tasks_improved": list(metrics["tasks_improved"]),
                "tasks_regressed": list(metrics["tasks_regressed"]),
                "regression_count": metrics["regression_count"],
                "cost_usd": 0.0,
                "cost_source": "none",
            },
            lower_refs=self._lower_refs(asset_spec, rows),
            evaluation_ids=(evaluation.evaluation_id, baseline.evaluation_id),
            provenance=self._provenance(),
        )
        evaluation.status = "passed" if upper_decision == "keep" else "failed"
        evaluation.decision_id = decision.decision_id
        return evaluation, decision

    def build_evaluation_request(self, asset_id: str, phase: str) -> EvaluationRequest:
        """Build the eval-port request; this method computes no metrics."""
        if phase not in PHASES:
            raise ScenarioError(f"unknown phase: {phase!r}")
        asset_spec = self.scenario.assets[asset_id]
        sources = asset_spec.get("sources", {}).get(phase)
        if not sources:
            raise ScenarioError(f"asset {asset_id} has no {phase} sources")
        asset = self.scenario.build_asset(asset_id)
        refs = _load_sources(self.repo_root, list(sources))
        baseline_refs = _load_sources(
            self.repo_root, list(self.scenario.baselines[phase]["sources"])
        )
        baseline_by_key = {
            (ref.task_id, ref.rep): ref for ref in baseline_refs
        }
        task_refs: list[dict[str, Any]] = []
        for ref in refs:
            baseline = baseline_by_key.get((ref.task_id, ref.rep))
            if baseline is None:
                baseline = min(
                    (
                        candidate
                        for candidate in baseline_refs
                        if candidate.task_id == ref.task_id
                    ),
                    key=lambda candidate: candidate.rep,
                    default=ref,
                )
            task_refs.append(
                {
                    "task_id": ref.task_id,
                    "family": phase,
                    "baseline_solution_ref": baseline.solution_path,
                    "candidate_solution_ref": ref.solution_path,
                    "source_run_id": ref.run_id,
                    "provenance": {
                        "kind": "replay",
                        "source_path": ref.source_path,
                        "solution_snapshot": ref.solution_path,
                    },
                }
            )
        return EvaluationRequest(
            asset_ref={
                "asset_id": asset.asset_id,
                "version": asset.version,
                "seed_identity_hash": asset.seed_identity_hash or "",
                "record_hash": asset.record_hash or asset.hash,
            },
            scenario_version=self.scenario.version,
            task_refs=task_refs,
            dimensions=[phase],
            options={"replay": True, "mcp_static_only": True},
        )

    def evaluate_via_port(
        self,
        asset_id: str,
        phase: str,
        *,
        adapter: str = "auto",
        timeout_s: float = 30.0,
    ) -> EvaluationResult:
        """Delegate metrics to the eval port; graph wiring stays here/B2."""
        request = self.build_evaluation_request(asset_id, phase)
        return eval_port_module.evaluate(
            request, adapter=adapter, timeout_s=timeout_s
        )

    def _run_rows(
        self,
        refs: list[SnapshotRef],
        *,
        asset: UpperAsset | None,
        asset_id: str,
        asset_version: str,
        phase: str,
        label: str,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for ref in refs:
            replayed = self.runner.replay(ref, label=label)
            retrieval = self._retrieval_for(asset, ref.task_id)
            rows.append(
                {
                    "task_id": ref.task_id,
                    "family": self.scenario.family_id,
                    "task_set": phase,
                    "rep": ref.rep,
                    "asset_id": asset_id,
                    "asset_version": asset_version,
                    "source_run_id": ref.run_id,
                    "solution_path": ref.solution_path,
                    "recorded": {
                        "solved": ref.recorded_solved,
                        "outcome": ref.recorded_outcome,
                        "group": ref.group,
                        "memory": ref.memory,
                        "genome": ref.genome,
                        "source_path": ref.source_path,
                    },
                    "replayed": {
                        "passed": replayed["passed"],
                        "outcome": replayed["outcome"],
                        "matches": replayed["matches"],
                        "timed_out": replayed["timed_out"],
                        "error": replayed["error"],
                        "cases_passed": replayed["cases_passed"],
                        "cases_total": replayed["cases_total"],
                    },
                    "passed": replayed["passed"],
                    "latency_ms": replayed["latency_ms"],
                    "cost_usd": 0.0,
                    "cost_source": "none",
                    "retrieval": retrieval,
                    "provenance": {
                        "kind": "replay",
                        "replay_of_run_id": ref.run_id,
                        "solution_snapshot": ref.solution_path,
                        "runtime": "ProbeVerifier",
                        "worker": "molt.upper.replay_worker",
                        "calls_model": False,
                    },
                    "permission": self._permission(asset),
                }
            )
        return rows

    def _retrieval_for(self, asset: UpperAsset | None, task_id: str) -> dict[str, Any]:
        if asset is None:
            return {
                "family": self.scenario.family_id,
                "query": None,
                "task_id": task_id,
                "mode": "deterministic-exact-prefix",
                "statuses": [],
                "hits": [],
                "empty": True,
                "reason": "baseline_no_memory_asset",
            }
        result = RetrievalIndex([asset]).retrieve(
            self.scenario.family_id, task_id=task_id, statuses=None
        )
        payload = result.to_dict()
        payload["reason"] = "asset_under_evaluation"
        return payload

    @staticmethod
    def _permission(asset: UpperAsset | None) -> dict[str, Any]:
        return {
            "asset_kind": asset.kind if asset else "baseline",
            "permission_scope": asset.permission_scope if asset else None,
            "approval_level": asset.approval_level if asset else None,
            "executed": False,
            "mcp_calls": 0,
            "skill_executions": 0,
            "execution_mode": "replay-verifier-only",
        }

    def _aggregate(
        self,
        rows: list[dict[str, Any]],
        baseline_rows: list[dict[str, Any]],
        phase: str,
        *,
        asset: UpperAsset | None,
    ) -> dict[str, Any]:
        tasks = self.scenario.phase_tasks(phase)
        per_task: dict[str, dict[str, Any]] = {}
        improved: list[str] = []
        regressed: list[str] = []
        for task_id in tasks:
            candidate = self._task_rate(rows, task_id)
            baseline = self._task_rate(baseline_rows, task_id)
            delta = round(candidate["pass_rate"] - baseline["pass_rate"], 6)
            if delta > 0:
                improved.append(task_id)
            elif delta < 0:
                regressed.append(task_id)
            per_task[task_id] = {
                "candidate": candidate,
                "baseline": baseline,
                "delta": delta,
            }
        candidate_total = self._rate(rows)
        baseline_total = self._rate(baseline_rows)
        return {
            "phase": phase,
            "family": self.scenario.family_id,
            "task_count": len(tasks),
            "candidate": candidate_total,
            "baseline": baseline_total,
            "transfer_delta": round(
                candidate_total["pass_rate"] - baseline_total["pass_rate"], 6
            ),
            "tasks_improved": improved,
            "tasks_regressed": regressed,
            "regression_count": len(regressed),
            "negative_transfer": bool(regressed),
            "per_task": per_task,
            "retrieval": {
                "hits": sum(1 for row in rows if row["retrieval"]["hits"]),
                "misses": sum(1 for row in rows if not row["retrieval"]["hits"]),
                "sources": sorted(
                    {
                        hit["source"]
                        for row in rows
                        for hit in row["retrieval"]["hits"]
                    }
                ),
            },
            "permission": self._permission(asset),
        }

    @staticmethod
    def _task_rate(rows: list[dict[str, Any]], task_id: str) -> dict[str, Any]:
        selected = [row for row in rows if row["task_id"] == task_id]
        attempts = len(selected)
        passed = sum(1 for row in selected if row["passed"])
        return {
            "attempts": attempts,
            "passed": passed,
            "pass_rate": round(passed / attempts, 6) if attempts else 0.0,
        }

    @staticmethod
    def _rate(rows: list[dict[str, Any]]) -> dict[str, Any]:
        attempts = len(rows)
        passed = sum(1 for row in rows if row["passed"])
        return {
            "attempts": attempts,
            "passed": passed,
            "pass_rate": round(passed / attempts, 6) if attempts else 0.0,
        }

    @staticmethod
    def _reference_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
        historical_runs = len(rows)
        historical_passed = sum(
            1 for row in rows if row["recorded"]["solved"] == 1
        )
        recomputed_passed = sum(1 for row in rows if row["passed"])
        row_mismatches = sum(
            1
            for row in rows
            if (row["recorded"]["solved"] == 1) != row["passed"]
        )
        return {
            "historical": {"runs": historical_runs, "passed": historical_passed},
            "recomputed": {"runs": len(rows), "passed": recomputed_passed},
            "match": historical_passed == recomputed_passed and row_mismatches == 0,
            "row_mismatches": row_mismatches,
        }

    def _telemetry(
        self, rows: list[dict[str, Any]], *, asset: UpperAsset | None, phase: str
    ) -> dict[str, Any]:
        latencies = [float(row["latency_ms"]) for row in rows]
        errors: Counter[str] = Counter()
        for row in rows:
            replay = row["replayed"]
            if replay["timed_out"]:
                errors["timed_out"] += 1
            elif replay["error"] == "invalid_solution":
                errors["invalid_solution"] += 1
            elif replay["error"] == "checker_failed":
                errors["checker_failed"] += 1
            elif replay["error"] and str(replay["error"]).startswith("replay_worker"):
                errors["worker_error"] += 1
            elif replay["error"]:
                errors["other"] += 1
            else:
                errors["none"] += 1
        return {
            "task_set": phase,
            "context_tokens_estimate": self._context_estimate(asset),
            "repeated_prefix_tokens": {
                "value": None,
                "reason": "replay_no_model_context",
            },
            "retrieval_hits": sum(1 for row in rows if row["retrieval"]["hits"]),
            "retrieval_misses": sum(1 for row in rows if not row["retrieval"]["hits"]),
            "errors": {bucket: errors.get(bucket, 0) for bucket in ERROR_BUCKETS},
            "cost_usd": 0.0,
            "cost_source": "none",
            "cost_note": "REPLAY re-scores recorded solutions; no model calls",
            "latency_ms": {
                "total": round(sum(latencies), 3),
                "mean": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
                "max": round(max(latencies), 3) if latencies else 0.0,
                "by_task": {
                    task_id: round(
                        sum(
                            float(row["latency_ms"])
                            for row in rows
                            if row["task_id"] == task_id
                        ),
                        3,
                    )
                    for task_id in self.scenario.phase_tasks(phase)
                },
            },
        }

    def _context_estimate(self, asset: UpperAsset | None) -> dict[str, Any]:
        if asset is None:
            return {"value": None, "reason": "baseline_no_memory_asset"}
        memory_ref = asset.content.get("memory_ref")
        if not isinstance(memory_ref, str) or not memory_ref:
            return {"value": None, "reason": "asset_has_no_memory_text"}
        path = (self.repo_root / memory_ref).resolve()
        if self.repo_root not in path.parents or not path.is_file():
            return {"value": None, "reason": "memory_text_missing", "source": memory_ref}
        text = path.read_text(encoding="utf-8")
        return {
            "value": len(text) // 4,
            "method": "chars_div_4",
            "chars": len(text),
            "lines": len(text.splitlines()),
            "source": memory_ref,
            "note": "rough estimate only; C1b memory is a few short rules",
        }

    def _lower_refs(
        self, spec: dict[str, Any], rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        refs: dict[str, Any] = {
            "lower_scenario_id": self.scenario.lower_domain.get("scenario_id"),
            "lower_scenario_version": self.scenario.lower_domain.get("scenario_version"),
            "lower_run_ids": sorted({row["source_run_id"] for row in rows}),
            "lower_solution_snapshots": sorted({row["solution_path"] for row in rows}),
        }
        genome_id = self._resolve_genome_id(spec)
        if genome_id:
            refs["lower_genome_id"] = genome_id
        return refs

    def _resolve_genome_id(self, spec: dict[str, Any]) -> str | None:
        genome_ref = spec.get("lower_genome_ref")
        if genome_ref:
            path = _resolve_under_repo(self.repo_root, genome_ref, what="lower genome")
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload.get("genome_id")
        genome = spec.get("genome")
        if not genome:
            return None
        if str(genome).startswith("sha256:"):
            return str(genome)
        path = self.repo_root / "shells" / "probe" / "genomes" / f"{genome}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8")).get("genome_id")
        return str(genome)

    def _decide(self, phase: str, metrics: dict[str, Any]) -> tuple[str, str]:
        candidate = metrics["candidate"]
        baseline = metrics["baseline"]
        regressed = metrics["tasks_regressed"]
        if phase == "transfer":
            if candidate["pass_rate"] > baseline["pass_rate"] and not regressed:
                return (
                    "keep",
                    f"transfer_gain:{candidate['pass_rate']:.3f}>"
                    f"{baseline['pass_rate']:.3f};no_task_regression",
                )
            return (
                "reject",
                f"no_transfer_gain:{candidate['pass_rate']:.3f}<="
                f"{baseline['pass_rate']:.3f};regressed={regressed}",
            )
        if regressed or candidate["pass_rate"] < baseline["pass_rate"]:
            return (
                "rollback",
                f"negative_transfer:{candidate['pass_rate']:.3f}<"
                f"{baseline['pass_rate']:.3f};regressed={regressed}",
            )
        return (
            "keep",
            f"no_negative_transfer:{candidate['pass_rate']:.3f}>="
            f"{baseline['pass_rate']:.3f}",
        )

    def _provenance(self) -> dict[str, Any]:
        return {
            "kind": "replay",
            "calls_model": False,
            "driver": "molt.upper.evaluator",
            "verifier": "ProbeVerifier",
            "worker": "molt.upper.replay_worker",
            "upper_scenario_version": self.scenario.version,
            "created_at": utc_now(),
        }


def evaluate_scenario(
    scenario: UpperScenario,
    *,
    state_dir: Path | str,
    grace_s: float = 30.0,
) -> UpperEvaluator:
    return UpperEvaluator(scenario, state_dir=state_dir, grace_s=grace_s)
