"""Deterministic skill-eval tasks: load from ``skills_eval/`` and grade by substring.

No model is ever asked to grade. :func:`grade` is a pure function over the task's
declared ``expect`` block, so the same output always produces the same verdict.

Layout::

    skills_eval/transfer/<task_id>.json    phase="transfer"
    skills_eval/regression/<task_id>.json  phase="regression"
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PHASES = ("transfer", "regression")
TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
EXPECT_KEYS = frozenset({"must_include", "must_not_include", "max_chars"})
DEFAULT_MAX_CHARS = 4000


class EvalTaskError(RuntimeError):
    """Raised for malformed task files or directory/phase mismatches."""


@dataclass(frozen=True)
class EvalTask:
    task_id: str
    phase: str
    prompt: str
    expect: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "phase": self.phase,
            "prompt": self.prompt,
            "expect": {
                "must_include": list(self.expect.get("must_include", [])),
                "must_not_include": list(self.expect.get("must_not_include", [])),
                "max_chars": int(self.expect.get("max_chars", DEFAULT_MAX_CHARS)),
            },
        }


def _str_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise EvalTaskError(f"{where} must be a list of strings")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise EvalTaskError(f"{where} entries must be non-empty strings")
        items.append(item)
    return items


def _normalize_expect(payload: Any, where: str) -> dict[str, Any]:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise EvalTaskError(f"{where}.expect must be an object")
    unknown = sorted(set(payload) - set(EXPECT_KEYS))
    if unknown:
        raise EvalTaskError(f"{where}.expect has unknown fields: {', '.join(unknown)}")
    must_include = _str_list(payload.get("must_include"), f"{where}.expect.must_include")
    must_not_include = _str_list(
        payload.get("must_not_include"), f"{where}.expect.must_not_include"
    )
    max_chars = payload.get("max_chars", DEFAULT_MAX_CHARS)
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
        raise EvalTaskError(f"{where}.expect.max_chars must be a positive integer")
    return {
        "must_include": must_include,
        "must_not_include": must_not_include,
        "max_chars": max_chars,
    }


def task_from_dict(payload: Any, *, phase: str, where: str = "task") -> EvalTask:
    if not isinstance(payload, dict):
        raise EvalTaskError(f"{where} must be a JSON object")
    unknown = sorted(set(payload) - {"task_id", "phase", "prompt", "expect"})
    if unknown:
        raise EvalTaskError(f"{where} has unknown fields: {', '.join(unknown)}")
    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
        raise EvalTaskError(f"{where}.task_id must match {TASK_ID_RE.pattern!r}")
    declared_phase = payload.get("phase")
    if declared_phase != phase:
        raise EvalTaskError(
            f"{where}.phase is {declared_phase!r} but the file lives under {phase!r}"
        )
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise EvalTaskError(f"{where}.prompt must be a non-empty string")
    expect = _normalize_expect(payload.get("expect"), where)
    return EvalTask(task_id=task_id, phase=phase, prompt=prompt, expect=expect)


def load_tasks(root: Path | str) -> list[EvalTask]:
    """Load ``<root>/{transfer,regression}/*.json``, ordered by phase then task id."""
    root_dir = Path(root).expanduser()
    if not root_dir.is_dir():
        raise EvalTaskError(f"skills_eval root is not a directory: {root_dir}")

    tasks: list[EvalTask] = []
    seen: dict[str, str] = {}
    for phase in PHASES:
        phase_dir = root_dir / phase
        if not phase_dir.is_dir():
            raise EvalTaskError(f"missing phase directory: {phase_dir}")
        for path in sorted(phase_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise EvalTaskError(f"cannot read task file {path}: {exc}") from exc
            entries = payload if isinstance(payload, list) else [payload]
            for index, entry in enumerate(entries):
                where = f"{path.name}" if len(entries) == 1 else f"{path.name}[{index}]"
                task = task_from_dict(entry, phase=phase, where=where)
                if task.task_id in seen:
                    raise EvalTaskError(
                        f"duplicate task_id {task.task_id!r} in {seen[task.task_id]} "
                        f"and {path.name}"
                    )
                seen[task.task_id] = path.name
                tasks.append(task)
    return tasks


def select_phase(tasks: Iterable[EvalTask], phase: str) -> list[EvalTask]:
    if phase not in PHASES:
        raise EvalTaskError(f"unknown phase: {phase!r}")
    return [task for task in tasks if task.phase == phase]


def grade(task: EvalTask, output: str) -> dict[str, Any]:
    """Deterministic substring checks; never calls a model."""
    if not isinstance(output, str):
        raise EvalTaskError("output must be a string")
    reasons: list[str] = []
    for needle in task.expect.get("must_include", []):
        if needle not in output:
            reasons.append(f"missing:{needle}")
    for needle in task.expect.get("must_not_include", []):
        if needle in output:
            reasons.append(f"forbidden:{needle}")
    limit = int(task.expect.get("max_chars", DEFAULT_MAX_CHARS))
    if len(output) > limit:
        reasons.append(f"too_long:{len(output)}>{limit}")
    return {"passed": not reasons, "reasons": reasons}


__all__ = [
    "DEFAULT_MAX_CHARS",
    "EvalTask",
    "EvalTaskError",
    "PHASES",
    "grade",
    "load_tasks",
    "select_phase",
    "task_from_dict",
]
