"""Skill evaluation runner: run a task set against baseline vs candidate package.

Honesty rules:

* ``mode`` is ``live`` only when the adapter reports ``real_model`` **and** was
  actually called. Scripted/manual adapters are always labelled ``manual`` and
  record ``calls_model=False``.
* Grading is deterministic substring matching (:mod:`molt.skills.eval_tasks`);
  no model grades another model.
* Evidence goes to ``<state>/skills/evaluations/<eval_id>.json`` outside the
  repository; it records the per-task rows, so a pass rate can be recomputed.

The verdict itself is never computed here: :func:`two_phase_evaluate` calls
:func:`molt.skills.evolution.mirror_gate`, which reuses
``UpperEvaluator._decide``.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..upper.storage import StatePathError, atomic_write_json, ensure_outside_repo
from ..upper.types import utc_now
from .eval_tasks import EvalTask, grade, load_tasks, select_phase
from .evaluate import SYSTEM_PREAMBLE, CaptureAdapter
from .evolution import (
    ProviderUnavailable,
    SkillEvolutionError,
    candidate_asset_id,
    mirror_gate,
)
from .loader import SkillPackage, render_system_block, system_block_sha256

EVIDENCE_KIND = "evaluation"
EVIDENCE_SCHEMA_VERSION = "1.0.0"
EVIDENCE_SUBDIR = ("skills", "evaluations")

LIVE = "live"
MANUAL = "manual"

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _repo_root() -> Path:
    return _REPO_ROOT


@dataclass(frozen=True)
class ScriptedReply:
    """Minimal reply shape accepted by :func:`reply_text`."""

    content: str
    tool_calls: tuple[Any, ...] = ()


@dataclass
class ScriptedAdapter:
    """Fixed ``task_id`` → response mapping; never a real model.

    Lookup order for a call is ``"<variant>:<task_id>"`` (variant is
    ``baseline``/``candidate``), then ``<task_id>``, then ``default_response``.
    The runner sets ``current_task_id``/``current_variant`` before each call.
    """

    responses: dict[str, str] = field(default_factory=dict)
    default_response: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    name: str = "scripted"
    model: str = "none"
    real_model: bool = False
    adapter_ready: bool = True
    provider_error: str | None = None
    current_task_id: str | None = None
    current_variant: str | None = None

    def resolve(self) -> str:
        task_id = self.current_task_id or ""
        variant = self.current_variant or ""
        for key in (f"{variant}:{task_id}", task_id):
            if key and key in self.responses:
                return self.responses[key]
        if self.default_response is not None:
            return self.default_response
        raise SkillEvolutionError(f"scripted_response_missing:{task_id or '<unset>'}")

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "none",
    ) -> ScriptedReply:
        self.calls.append(
            {
                "messages": copy.deepcopy(messages),
                "tools": copy.deepcopy(tools or []),
                "tool_choice": tool_choice,
                "task_id": self.current_task_id,
                "variant": self.current_variant,
            }
        )
        return ScriptedReply(content=self.resolve())


def reply_text(reply: Any) -> str:
    if isinstance(reply, str):
        return reply
    if isinstance(reply, dict):
        content = reply.get("content", "")
    else:
        content = getattr(reply, "content", None)
    if not isinstance(content, str):
        raise SkillEvolutionError(
            f"adapter reply has no string content: {type(reply).__name__}"
        )
    return content


def _evidence_dir(state_dir: Path | str) -> Path:
    try:
        root = ensure_outside_repo(Path(state_dir).expanduser(), _repo_root())
    except StatePathError as exc:
        raise SkillEvolutionError(f"evidence state must live outside the repo: {exc}") from exc
    return root.joinpath(*EVIDENCE_SUBDIR)


def _evidence_id(kind: str, baseline: SkillPackage, candidate: SkillPackage) -> str:
    def short(pkg: SkillPackage) -> str:
        return pkg.content_hash.split(":", 1)[-1][:8]

    return (
        f"eval-{candidate.skill_id}-{kind}-"
        f"cand-{candidate.version}-{short(candidate)}-"
        f"base-{baseline.version}-{short(baseline)}"
    )


def _write_evidence(state_dir: Path | str, eval_id: str, payload: dict[str, Any]) -> Path:
    path = _evidence_dir(state_dir) / f"{eval_id}.json"
    atomic_write_json(path, payload)
    return path


class SkillEvaluator:
    """Evaluate one candidate package against a baseline, phase by phase."""

    def __init__(
        self,
        tasks_root: Path | str | None,
        adapter: Any,
        *,
        state_dir: Path | str | None = None,
        driver: str = "molt.skills.eval_runner",
    ) -> None:
        if adapter is None:
            raise SkillEvolutionError("adapter is required")
        self.tasks_root = Path(tasks_root).expanduser() if tasks_root is not None else None
        self.adapter = adapter
        self.state_dir = Path(state_dir).expanduser() if state_dir is not None else None
        self.driver = driver
        self.call_count = 0

    # -- provenance ------------------------------------------------------
    @property
    def calls_model(self) -> bool:
        return bool(getattr(self.adapter, "real_model", False)) and self.call_count > 0

    @property
    def mode(self) -> str:
        return LIVE if self.calls_model else MANUAL

    def _hint(self, task_id: str, variant: str) -> None:
        for name, value in (("current_task_id", task_id), ("current_variant", variant)):
            if hasattr(self.adapter, name):
                setattr(self.adapter, name, value)

    # -- one call --------------------------------------------------------
    def _run_one(
        self, pkg: SkillPackage, task: EvalTask, variant: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        block = render_system_block(pkg)
        messages = [
            {"role": "system", "content": SYSTEM_PREAMBLE},
            {"role": "system", "content": block},
            {"role": "user", "content": task.prompt},
        ]
        self._hint(task.task_id, variant)
        capture = CaptureAdapter(self.adapter)
        reply = capture.complete(messages, [], "none")
        self.call_count += 1
        captured = capture.last_call or {}
        output = reply_text(reply)
        graded = grade(task, output)
        row = {
            "output": output,
            "passed": graded["passed"],
            "reasons": graded["reasons"],
            "skill_id": pkg.skill_id,
            "version": pkg.version,
            "content_hash": pkg.content_hash,
            "system_block_sha256": system_block_sha256(block),
            "captured_message_count": len(captured.get("messages", [])),
            # This runner executes no tools; the empty trace is a recorded fact,
            # not a placeholder to be filled with invented calls.
            "tool_trace": [],
            "tools": [],
        }
        return row, captured

    # -- one phase -------------------------------------------------------
    def evaluate_tasks(
        self,
        baseline_pkg: SkillPackage,
        candidate_pkg: SkillPackage,
        tasks: Iterable[EvalTask],
    ) -> dict[str, Any]:
        task_list = list(tasks)
        if not task_list:
            raise SkillEvolutionError("evaluate_tasks requires at least one task")
        phases = {task.phase for task in task_list}
        if len(phases) != 1:
            raise SkillEvolutionError(f"tasks span multiple phases: {sorted(phases)}")
        phase = phases.pop()
        if baseline_pkg.skill_id != candidate_pkg.skill_id:
            raise SkillEvolutionError(
                "baseline and candidate must describe the same skill_id "
                f"({baseline_pkg.skill_id!r} != {candidate_pkg.skill_id!r})"
            )

        rows: list[dict[str, Any]] = []
        for task in task_list:
            baseline_row, _ = self._run_one(baseline_pkg, task, "baseline")
            candidate_row, _ = self._run_one(candidate_pkg, task, "candidate")
            delta = int(candidate_row["passed"]) - int(baseline_row["passed"])
            rows.append(
                {
                    "task_id": task.task_id,
                    "phase": phase,
                    "prompt": task.prompt,
                    "baseline": baseline_row,
                    "candidate": candidate_row,
                    "delta": delta,
                }
            )
        # ``mode`` is decided after the calls: ``live`` needs a real model *and*
        # an actual invocation; anything else is manual/scripted.
        mode = self.mode
        calls_model = self.calls_model
        for row in rows:
            row["mode"] = mode
            row["calls_model"] = calls_model
        metrics = self._aggregate(phase, rows)
        return {
            "phase": phase,
            "metrics": metrics,
            "rows": rows,
            "mode": mode,
            "calls_model": calls_model,
        }

    def evaluate_pair(
        self, baseline_pkg: SkillPackage, candidate_pkg: SkillPackage, phase: str
    ) -> dict[str, Any]:
        if self.tasks_root is None:
            raise SkillEvolutionError("evaluate_pair needs tasks_root; use evaluate_tasks")
        tasks = select_phase(load_tasks(self.tasks_root), phase)
        if not tasks:
            raise SkillEvolutionError(f"no tasks for phase {phase!r}")
        result = self.evaluate_tasks(baseline_pkg, candidate_pkg, tasks)
        eval_id = _evidence_id(phase, baseline_pkg, candidate_pkg)
        result["eval_id"] = eval_id
        result["evidence_path"] = None
        if self.state_dir is not None:
            record = self._record(
                eval_id=eval_id,
                asset_id=candidate_asset_id(candidate_pkg.skill_id, candidate_pkg.version),
                baseline_pkg=baseline_pkg,
                candidate_pkg=candidate_pkg,
                phase_results={phase: result},
                final=None,
            )
            result["evidence_path"] = str(_write_evidence(self.state_dir, eval_id, record))
        return result

    # -- metrics / evidence ---------------------------------------------
    @staticmethod
    def _aggregate(phase: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(rows)
        candidate_passed = sum(1 for row in rows if row["candidate"]["passed"])
        baseline_passed = sum(1 for row in rows if row["baseline"]["passed"])
        improved = [row["task_id"] for row in rows if row["delta"] > 0]
        regressed = [row["task_id"] for row in rows if row["delta"] < 0]
        return {
            "phase": phase,
            "task_count": total,
            "candidate": {
                "attempts": total,
                "passed": candidate_passed,
                "pass_rate": candidate_passed / total,
            },
            "baseline": {
                "attempts": total,
                "passed": baseline_passed,
                "pass_rate": baseline_passed / total,
            },
            "transfer_delta": (candidate_passed - baseline_passed) / total,
            "tasks_improved": improved,
            "tasks_regressed": regressed,
            "regression_count": len(regressed),
            "per_task": {
                row["task_id"]: {
                    "candidate": row["candidate"]["passed"],
                    "baseline": row["baseline"]["passed"],
                    "delta": row["delta"],
                }
                for row in rows
            },
        }

    def _record(
        self,
        *,
        eval_id: str,
        asset_id: str,
        baseline_pkg: SkillPackage,
        candidate_pkg: SkillPackage,
        phase_results: dict[str, dict[str, Any]],
        final: str | None,
    ) -> dict[str, Any]:
        created_at = utc_now()
        mode = self.mode
        calls_model = self.calls_model
        payload: dict[str, Any] = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "evidence_kind": EVIDENCE_KIND,
            "eval_id": eval_id,
            # D5.9: the record is bound to the asset it may later promote.
            "asset_id": asset_id,
            "skill_id": candidate_pkg.skill_id,
            "candidate_version": candidate_pkg.version,
            "candidate_package_hash": candidate_pkg.content_hash,
            "baseline_package_hash": baseline_pkg.content_hash,
            "baseline": {
                "skill_id": baseline_pkg.skill_id,
                "version": baseline_pkg.version,
                "content_hash": baseline_pkg.content_hash,
            },
            "candidate": {
                "skill_id": candidate_pkg.skill_id,
                "version": candidate_pkg.version,
                "content_hash": candidate_pkg.content_hash,
            },
            # D45: no tool calls happen in this runner; empty traces are facts.
            "tool_trace": [],
            "tools": [],
            "mode": mode,
            "calls_model": calls_model,
            "provenance": {
                "kind": mode,
                "calls_model": calls_model,
                "driver": self.driver,
                "created_at": created_at,
            },
            "created_at": created_at,
        }
        for phase, result in phase_results.items():
            payload[phase] = {
                "metrics": result["metrics"],
                "per_task": result["metrics"]["per_task"],
                "rows": copy.deepcopy(result["rows"]),
            }
        if final is not None:
            payload["final"] = final
        return payload


def two_phase_evaluate(
    baseline_pkg: SkillPackage,
    candidate_pkg: SkillPackage,
    transfer_tasks: Iterable[EvalTask],
    regression_tasks: Iterable[EvalTask],
    state_dir: Path | str,
    *,
    adapter: Any | None = None,
    evaluator: SkillEvaluator | None = None,
    driver: str = "molt.skills.eval_runner",
    asset_id: str | None = None,
) -> dict[str, Any]:
    """Run transfer + regression, mirror the gate, write one evidence record.

    ``adapter`` (or an already-built ``evaluator``) is keyword-only because a
    candidate cannot be scored without one; passing neither raises
    :class:`ProviderUnavailable` rather than inventing outputs.

    ``asset_id`` defaults to :func:`molt.skills.evolution.candidate_asset_id`, the
    same id :func:`molt.skills.lifecycle.register_asset` assigns, so the evidence
    is bound to the asset it is allowed to promote.

    Precedence: ``transfer == "reject"`` → ``final="reject"``; otherwise
    ``regression == "rollback"`` → ``final="rollback"``; otherwise ``keep``.
    """
    transfer_list = list(transfer_tasks)
    regression_list = list(regression_tasks)
    if any(task.phase != "transfer" for task in transfer_list):
        raise SkillEvolutionError("transfer_tasks contains a non-transfer task")
    if any(task.phase != "regression" for task in regression_list):
        raise SkillEvolutionError("regression_tasks contains a non-regression task")

    if evaluator is None:
        if adapter is None:
            raise ProviderUnavailable("adapter_not_configured")
        evaluator = SkillEvaluator(None, adapter, state_dir=state_dir, driver=driver)
    elif state_dir is None:  # pragma: no cover - state_dir is positional
        raise SkillEvolutionError("state_dir is required")

    transfer = evaluator.evaluate_tasks(baseline_pkg, candidate_pkg, transfer_list)
    regression = evaluator.evaluate_tasks(baseline_pkg, candidate_pkg, regression_list)

    transfer_verdict, transfer_reason = mirror_gate("transfer", transfer["metrics"])
    regression_verdict, regression_reason = mirror_gate("regression", regression["metrics"])

    if transfer_verdict == "reject":
        final = "reject"
    elif regression_verdict == "rollback":
        final = "rollback"
    else:
        final = "keep"

    eval_id = _evidence_id("two-phase", baseline_pkg, candidate_pkg)
    bound_asset_id = asset_id or candidate_asset_id(
        candidate_pkg.skill_id, candidate_pkg.version
    )
    record = evaluator._record(
        eval_id=eval_id,
        asset_id=bound_asset_id,
        baseline_pkg=baseline_pkg,
        candidate_pkg=candidate_pkg,
        phase_results={"transfer": transfer, "regression": regression},
        final=final,
    )
    record["transfer"]["verdict"] = transfer_verdict
    record["transfer"]["reason"] = transfer_reason
    record["regression"]["verdict"] = regression_verdict
    record["regression"]["reason"] = regression_reason
    evidence_path = _write_evidence(state_dir, eval_id, record)

    return {
        "transfer": {
            "metrics": transfer["metrics"],
            "verdict": transfer_verdict,
            "reason": transfer_reason,
        },
        "regression": {
            "metrics": regression["metrics"],
            "verdict": regression_verdict,
            "reason": regression_reason,
        },
        "final": final,
        "evidence_path": str(evidence_path),
    }


__all__ = [
    "EVIDENCE_KIND",
    "EVIDENCE_SCHEMA_VERSION",
    "LIVE",
    "MANUAL",
    "ScriptedAdapter",
    "ScriptedReply",
    "SkillEvaluator",
    "reply_text",
    "two_phase_evaluate",
]
