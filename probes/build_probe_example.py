"""Generate ``web/data/probe/example.json`` from the 2c probe evidence.

The example is *not* a live run.  Every value is either read from a repository
evidence file or recomputed by running the recorded solution snapshot against
the task's own ``check_cases.py``:

- run 1 (baseline v0) and lineage counters:
  ``probes/report/2c/rows.json`` + ``probes/report/2c/followup.json``
- run 2 (memory v3 / gen4) and lineage counters:
  ``probes/report/2c/memory-v3/rows.json`` + ``verdict.json``
- gen2 / gen3 lineage counters: ``probes/report/2c/verdict.json`` (``memory_v2``)
- code before/after: ``probes/report/2c/solutions/**`` and
  ``probes/report/2c/memory-v3/solutions/**`` (the run's solution snapshot)
- case inputs/expected: the ``cases`` tuple in
  ``bench/probe/<task>/tests/check_cases.py``
- case actual: the recorded solution snapshot executed against that checker
- memory lines / sources / reflection: ``shells/probe/memory/gen4.md`` and
  ``probes/report/2c/memory-v3/reflection.json``

The script is deterministic: sorted inputs, no timestamps from the clock, no
random values.  ``revision`` is a sha256 of the payload without the revision
field, so re-running on the same evidence produces byte-identical output.

Usage::

    python probes/build_probe_example.py
    python probes/build_probe_example.py --out web/data/probe/example.json
"""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

TASKS = (
    "probe-order-x5",
    "probe-order-x6",
    "probe-order-x7",
    "probe-order-x8",
)
HEADROOM_TASKS = TASKS[:3]

DEFAULT_OUT = "web/data/probe/example.json"
NOTE = "example generated from 2c evidence; not a live run"

# The example carries v3 (gen4) run data, so its headline verdict is the v3 one.
# The 2c main experiment (v1 memory) was ``partial_transfer``; that口径 is kept
# in ``verdict_note`` so the footer can show both without contradiction.
VERDICT = "v3_stable_all"
VERDICT_NOTE = "v3 16/16；x8 基线 2/2，不计入 headroom；主 2c（v1）为 partial_transfer"

# The reflection is a fixed, reviewed artifact (gen4.md).  The per-line mapping
# is curated for that artifact; the generator fails loudly if the line count
# changes so a new mapping cannot silently drift.
LINE_CASES: tuple[tuple[str, ...], ...] = (
    (
        "probe-order-2/case_01",
        "probe-order-2/case_03",
        "probe-order-x3/case_01",
        "probe-order-x3/case_02",
    ),
    (
        "probe-order-2/case_01",
        "probe-order-x3/case_01",
        "probe-order-x3/case_02",
    ),
    (
        "probe-order-2/case_01",
        "probe-order-x3/case_01",
        "probe-order-x3/case_02",
        "probe-order-x3/case_03",
    ),
    (
        "probe-order-2/case_02",
        "probe-order-2/case_03",
        "probe-order-x3/case_02",
    ),
    (
        "probe-order-x3/case_01",
        "probe-order-x3/case_02",
        "probe-order-x3/case_03",
    ),
)


def _load(relative: str) -> Any:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _jsonable(value: Any) -> Any:
    """Convert AST literals (tuples) into JSON-compatible values."""
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _text(relative: str) -> str:
    path = (ROOT / relative).resolve()
    if not path.is_file():
        raise SystemExit(f"missing evidence file: {relative}")
    if not path.is_relative_to(ROOT):
        raise SystemExit(f"evidence escapes repository: {relative}")
    return path.read_text(encoding="utf-8")


def _workspace_text(task_id: str) -> str:
    return _text(f"bench/probe/{task_id}/workspace/solution.py")


def _row_snapshot(row: dict[str, Any]) -> str:
    """2c rows do not carry solution_snapshot for the main groups; derive it."""
    recorded = row.get("solution_snapshot")
    if isinstance(recorded, str) and recorded:
        return recorded
    group = row.get("group")
    if group not in ("baseline", "memory"):
        raise SystemExit(f"cannot locate solution snapshot for {row.get('task_id')}")
    return (
        f"probes/report/2c/solutions/{group}/"
        f"{row['task_id']}-r{row['rep']}.py.txt"
    )


def _function_name(task_id: str) -> str:
    task = _load(f"bench/probe/{task_id}/task.json")
    function = (task.get("ext") or {}).get("probe", {}).get("function")
    if not isinstance(function, str) or not function:
        raise SystemExit(f"task.json missing ext.probe.function: {task_id}")
    return function


def _signature(source: str, function: str) -> str:
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == function:
            names = [argument.arg for argument in node.args.args]
            returns = ast.unparse(node.returns) if node.returns else "None"
            return f"({', '.join(names)}) -> {returns}"
    return "missing"


def _source_cases(task_id: str) -> list[list[Any]]:
    """Read the ``cases`` tuple from the task's check_cases.py."""
    source = _text(f"bench/probe/{task_id}/tests/check_cases.py")
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "cases":
                return [_jsonable(item) for item in ast.literal_eval(node.value)]
    raise SystemExit(f"cases tuple not found: {task_id}")


def _run_checker(task_id: str, solution: str) -> list[dict[str, Any]]:
    """Run the task checker against ``solution`` in a throwaway directory."""
    with tempfile.TemporaryDirectory(prefix="molt-probe-example-") as temporary:
        workdir = Path(temporary)
        shutil.copyfile(
            ROOT / "bench/probe" / task_id / "tests/check_cases.py",
            workdir / "check_cases.py",
        )
        (workdir / "solution.py").write_text(solution, encoding="utf-8")
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = subprocess.run(
            [sys.executable, "check_cases.py"],
            cwd=workdir,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            raise SystemExit(
                f"checker failed for {task_id}: exit={completed.returncode} "
                f"stderr={completed.stderr[-400:]}"
            )
        payload = json.loads(completed.stdout)
        if payload.get("error") is not None:
            raise SystemExit(f"checker error for {task_id}: {payload['error']}")
        return payload["cases"]


def _cases(task_id: str, solution: str) -> list[dict[str, Any]]:
    declared = _source_cases(task_id)
    reported = _run_checker(task_id, solution)
    if len(declared) != len(reported):
        raise SystemExit(f"case count mismatch for {task_id}")
    cases: list[dict[str, Any]] = []
    for (case_id, case_input, expected), row in zip(declared, reported, strict=True):
        if row["case_id"] != case_id:
            raise SystemExit(f"case id mismatch for {task_id}: {row['case_id']}")
        if _jsonable(row["expected"]) != _jsonable(expected):
            raise SystemExit(f"expected mismatch for {task_id}/{case_id}")
        actual = _jsonable(row["actual"])
        passed = bool(row["passed"])
        if passed != (actual == _jsonable(expected)):
            raise SystemExit(f"passed mismatch for {task_id}/{case_id}")
        cases.append(
            {
                "case_id": case_id,
                "input": _jsonable(case_input),
                "expected": _jsonable(expected),
                "actual": actual,
                "passed": passed,
            }
        )
    return cases


def _diff_lines(before: str, after: str) -> list[dict[str, str]]:
    """Line-level difflib stream: same / del / add (split view rebuilds it)."""
    left = before.splitlines()
    right = after.splitlines()
    matcher = difflib.SequenceMatcher(a=left, b=right, autojunk=False)
    lines: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            lines.extend({"type": "same", "text": text} for text in left[i1:i2])
        elif tag == "delete":
            lines.extend({"type": "del", "text": text} for text in left[i1:i2])
        elif tag == "insert":
            lines.extend({"type": "add", "text": text} for text in right[j1:j2])
        elif tag == "replace":
            lines.extend({"type": "del", "text": text} for text in left[i1:i2])
            lines.extend({"type": "add", "text": text} for text in right[j1:j2])
    return lines


def _baseline_rows() -> list[dict[str, Any]]:
    """All v0 baseline runs on x5–x8: 2c main + replicate2."""
    rows = [
        row
        for row in _load("probes/report/2c/rows.json")["runs"]
        if row["group"] == "baseline"
    ]
    rows.extend(
        row
        for row in _load("probes/report/2c/followup.json")["x5_x6_replication"]["runs"]
        if row["group"] == "baseline"
    )
    return rows


def _v3_rows() -> list[dict[str, Any]]:
    return _load("probes/report/2c/memory-v3/rows.json")["runs"]


def _select_baseline(task_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer the first failing run that actually edited the file."""
    candidates = sorted(
        (row for row in rows if row["task_id"] == task_id),
        key=lambda row: row["rep"],
    )
    if not candidates:
        raise SystemExit(f"no baseline run for {task_id}")
    before = _workspace_text(task_id)
    before_lines = before.splitlines()
    for row in candidates:
        changed = _text(_row_snapshot(row)).splitlines() != before_lines
        if row["solved"] == 0 and changed:
            return row
    for row in candidates:
        if row["solved"] == 0:
            return row
    return candidates[0]


def _select_v3(task_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer a passing gen4 run that kept the original signature."""
    candidates = sorted(
        (
            row
            for row in rows
            if row["task_id"] == task_id and row["memory"] == "gen4"
        ),
        key=lambda row: row["rep"],
    )
    if not candidates:
        raise SystemExit(f"no gen4 run for {task_id}")
    for row in candidates:
        if row["solved"] == 1 and not row["signature_changed"]:
            return row
    for row in candidates:
        if row["solved"] == 1:
            return row
    return candidates[0]


def _run_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "solved": sum(row["solved"] for row in rows),
        "total": len(rows),
        "cost_usd": round(sum(row["cost_usd"] for row in rows), 8),
        "wall_ms": sum(row["wall_ms"] for row in rows),
    }


def _task_counts(
    rows: list[dict[str, Any]],
    task_id: str,
) -> tuple[int, int]:
    selected = [row for row in rows if row["task_id"] == task_id]
    return sum(row["solved"] for row in selected), len(selected)


def _run1_tasks(baseline_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for task_id in TASKS:
        row = _select_baseline(task_id, baseline_rows)
        function = _function_name(task_id)
        before = _workspace_text(task_id)
        after = _text(_row_snapshot(row))
        cases = _cases(task_id, after)
        passed = all(case["passed"] for case in cases)
        if passed != bool(row["solved"]):
            raise SystemExit(f"baseline outcome mismatch: {task_id}")
        passes, runs = _task_counts(baseline_rows, task_id)
        tasks.append(
            {
                "id": task_id,
                "run_id": row["run_id"],
                "function": function,
                "signature": _signature(after, function),
                "passed": passed,
                "passes": passes,
                "runs": runs,
                "cases": cases,
                "code_before": before,
                "code_after": after,
                "diff_lines": _diff_lines(before, after),
            }
        )
    return tasks


def _run2_tasks(
    baseline_rows: list[dict[str, Any]],
    v3_rows: list[dict[str, Any]],
    v3_verdict: dict[str, Any],
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for task_id in TASKS:
        baseline = _select_baseline(task_id, baseline_rows)
        row = _select_v3(task_id, v3_rows)
        function = _function_name(task_id)
        before = _text(_row_snapshot(baseline))
        after = _text(_row_snapshot(row))
        cases = _cases(task_id, after)
        passed = all(case["passed"] for case in cases)
        if passed != bool(row["solved"]):
            raise SystemExit(f"v3 outcome mismatch: {task_id}")
        counts = v3_verdict["v3"][task_id]
        task: dict[str, Any] = {
            "id": task_id,
            "run_id": row["run_id"],
            "function": function,
            "signature": _signature(after, function),
            "passed": passed,
            "passes": counts["passes"],
            "runs": counts["runs"],
            "cases": cases,
            "code_before": before,
            "code_after": after,
            "diff_lines": _diff_lines(before, after),
        }
        if task_id == "probe-order-x5":
            task["note"] = "v3 的 4/4 含 2 次 gen3 续跑；本卡展示 gen4 r1"
        tasks.append(task)
    return tasks


def _lineage(
    baseline_rows: list[dict[str, Any]],
    verdict_2c: dict[str, Any],
    v3_verdict: dict[str, Any],
) -> list[dict[str, Any]]:
    v0_passes, v0_runs = _task_counts_for(baseline_rows, HEADROOM_TASKS)
    v1 = verdict_2c["memory_v2"]["v1"]
    v2 = v3_verdict["v2"]
    gen2_passes = sum(v1[key]["passes"] for key in ("x5", "x6", "x7"))
    gen2_runs = sum(v1[key]["runs"] for key in ("x5", "x6", "x7"))
    gen3_passes = sum(v2[task]["passes"] for task in HEADROOM_TASKS)
    gen3_runs = sum(v2[task]["runs"] for task in HEADROOM_TASKS)
    gen4_passes = sum(v3_verdict["v3"][task]["passes"] for task in HEADROOM_TASKS)
    gen4_runs = sum(v3_verdict["v3"][task]["runs"] for task in HEADROOM_TASKS)

    expected = {
        "v0": (0, 10),
        "gen2": (8, 12),
        "gen3": (12, 12),
        "gen4": (12, 12),
    }
    actual = {
        "v0": (v0_passes, v0_runs),
        "gen2": (gen2_passes, gen2_runs),
        "gen3": (gen3_passes, gen3_runs),
        "gen4": (gen4_passes, gen4_runs),
    }
    if actual != expected:
        raise SystemExit(f"lineage counts drifted: {actual} != {expected}")

    return [
        {
            "gen": "v0",
            "label": "baseline",
            "passes": v0_passes,
            "runs": v0_runs,
            "scope": list(HEADROOM_TASKS),
            "note": "2c baseline + replicate2；x8 2/2 不计入 headroom",
        },
        {
            "gen": "gen2",
            "label": "回放",
            "passes": gen2_passes,
            "runs": gen2_runs,
            "scope": list(HEADROOM_TASKS),
            "note": "失败用例原文回放（gen2.md）；x8 0/4 负迁移",
        },
        {
            "gen": "gen3",
            "label": "反思",
            "passes": gen3_passes,
            "runs": gen3_runs,
            "scope": list(HEADROOM_TASKS),
            "note": "一次反思产出规则（gen3.md）；x8 0/2",
        },
        {
            "gen": "gen4",
            "label": "约束反思",
            "passes": gen4_passes,
            "runs": gen4_runs,
            "scope": list(HEADROOM_TASKS),
            "note": "约束反思（gen4.md）；x5 含 2 次 gen3 续跑；x8 4/4",
        },
    ]


def _task_counts_for(
    rows: list[dict[str, Any]],
    task_ids: tuple[str, ...],
) -> tuple[int, int]:
    selected = [row for row in rows if row["task_id"] in task_ids]
    return sum(row["solved"] for row in selected), len(selected)


def _memory_block() -> dict[str, Any]:
    reflection = _load("probes/report/2c/memory-v3/reflection.json")
    lines = [
        line.strip()
        for line in _text("shells/probe/memory/gen4.md").splitlines()
        if line.strip()
    ]
    if len(lines) != len(LINE_CASES):
        raise SystemExit(
            f"memory line count changed: {len(lines)} != {len(LINE_CASES)}; "
            "review LINE_CASES before regenerating"
        )
    sources = list(reflection["source_files"])
    source_cases: list[str] = []
    for source in sources:
        task_id = Path(source).stem.removeprefix("pro-max-")
        text = _text(source)
        for match in re.finditer(r"case_id:\s*([A-Za-z0-9_-]+)", text):
            source_cases.append(f"{task_id}/{match.group(1)}")
    known = set(source_cases)
    for line_number, ids in enumerate(LINE_CASES, start=1):
        missing = [case_id for case_id in ids if case_id not in known]
        if missing:
            raise SystemExit(f"memory line {line_number} references unknown cases: {missing}")
    return {
        "lines": lines,
        "line_cases": [list(ids) for ids in LINE_CASES],
        "source_cases": source_cases,
        "sources": sources,
        "attempts": len(reflection["attempts"]),
        "reflection_model": reflection["model"],
        "cost_usd": reflection["cost_usd"],
    }


def _negative_transfer(
    baseline_rows: list[dict[str, Any]],
    v3_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    task_id = "probe-order-x8"
    function = _function_name(task_id)
    baseline = sorted(
        (row for row in baseline_rows if row["task_id"] == task_id),
        key=lambda row: row["rep"],
    )
    drifted = sorted(
        (
            row
            for row in _load("probes/report/2c/rows.json")["runs"]
            if row["group"] == "memory" and row["task_id"] == task_id
        ),
        key=lambda row: row["rep"],
    )
    restored = sorted(
        (
            row
            for row in v3_rows
            if row["task_id"] == task_id and row["memory"] == "gen4"
        ),
        key=lambda row: row["rep"],
    )
    if not baseline or not drifted or not restored:
        raise SystemExit("negative-transfer evidence missing")

    baseline_signature = _signature(_text(_row_snapshot(baseline[0])), function)
    drifted_signature = _signature(_text(_row_snapshot(drifted[0])), function)
    restored_signature = _signature(_text(_row_snapshot(restored[0])), function)
    if restored_signature == baseline_signature:
        status = "restored"
    elif drifted_signature == baseline_signature:
        status = "none"
    else:
        status = "active"
    return {
        "task_id": task_id,
        "baseline_signature": baseline_signature,
        "drifted_signature": drifted_signature,
        "restored_signature": restored_signature,
        "status": status,
        "baseline_passes": sum(row["solved"] for row in baseline),
        "baseline_runs": len(baseline),
        "drifted_passes": sum(row["solved"] for row in drifted),
        "drifted_runs": len(drifted),
        "restored_passes": sum(row["solved"] for row in restored),
        "restored_runs": len(restored),
        "restored_signature_runs": sum(
            1 for row in restored if row["signature"] == baseline_signature
        ),
        "note": (
            "x8 baseline 2/2；gen2 0/2（签名漂移到 "
            f"{drifted_signature}）；gen4 4/4，"
            f"{sum(1 for row in restored if row['signature'] == baseline_signature)}"
            f"/{len(restored)} 保持原签名"
        ),
    }


def build() -> dict[str, Any]:
    baseline_rows = _baseline_rows()
    v3_rows = _v3_rows()
    verdict_2c = _load("probes/report/2c/verdict.json")
    v3_verdict = _load("probes/report/2c/memory-v3/verdict.json")

    run1_tasks = _run1_tasks(baseline_rows)
    run2_tasks = _run2_tasks(baseline_rows, v3_rows, v3_verdict)
    run1 = _run_block(baseline_rows)
    run1["solved"] = sum(1 for task in run1_tasks if task["passed"])
    run1["total"] = len(run1_tasks)
    run1["headroom_solved"] = sum(
        1 for task in run1_tasks if task["id"] in HEADROOM_TASKS and task["passed"]
    )
    run1["tasks"] = run1_tasks
    run2 = _run_block(v3_rows)
    run2["solved"] = sum(1 for task in run2_tasks if task["passed"])
    run2["total"] = len(run2_tasks)
    run2["headroom_solved"] = sum(
        1 for task in run2_tasks if task["id"] in HEADROOM_TASKS and task["passed"]
    )
    run2["tasks"] = run2_tasks
    # run2's cost/wall are the whole v3 experiment (18 runs + reflection),
    # not just the four displayed solution snapshots.
    run2["cost_usd"] = round(v3_verdict["cost_total"], 8)
    run2["wall_ms"] = sum(row["wall_ms"] for row in v3_rows)

    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "live": False,
        "phase": "done",
        "started_at": "2026-11-11T00:00:00Z",
        "updated_at": "2026-11-12T00:00:00Z",
        "lineage": _lineage(baseline_rows, verdict_2c, v3_verdict),
        "run1": run1,
        "memory": _memory_block(),
        "run2": run2,
        "negative_transfer": _negative_transfer(baseline_rows, v3_rows),
        "verdict": VERDICT,
        "verdict_note": VERDICT_NOTE,
        "cost_usd": round(run1["cost_usd"] + run2["cost_usd"], 8),
        "wall_ms": run1["wall_ms"] + run2["wall_ms"],
        "note": NOTE,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    payload["revision"] = hashlib.sha256(canonical).hexdigest()
    return payload


def write(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    path.write_text(text + "\n", encoding="utf-8")
    try:
        shown = path.resolve().relative_to(ROOT)
    except ValueError:
        shown = path
    print(
        f"wrote {shown} bytes={len(text) + 1} "
        f"revision={payload['revision'][:12]}…"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(ROOT / DEFAULT_OUT),
        help=f"output path (default: {DEFAULT_OUT} relative to the repo root)",
    )
    args = parser.parse_args()
    payload = build()
    write(payload, Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
