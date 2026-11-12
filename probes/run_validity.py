"""Batch 2a validity runner: oracle / control / family cells, one task per state."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "probes/report/validity"
BUDGET_STOP = 2.2
SINGLE_RUN_NOTE = 0.30

CELLS = (
    ("oracle", "oracle", (("probe-order-1", 2), ("probe-order-2", 2))),
    ("control", "v0-pro-max", (("probe-ctl-a", 2), ("probe-ctl-b", 2))),
    ("family", "v0-pro-max", (("probe-order-1", 4), ("probe-order-2", 4))),
)


def _command(task_id: str, genome: str, state: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "molt",
        "search",
        "--scenario",
        "probe",
        "--runtime",
        "pi",
        "--genome",
        f"shells/probe/genomes/{genome}.json",
        "--state",
        str(state),
        "--task",
        task_id,
    ]


def _run_one(cell: str, genome: str, task_id: str, rep: int) -> dict:
    state = Path("/tmp/molt-probe/validity") / cell / f"{task_id}-r{rep}"
    if state.exists():
        shutil.rmtree(state)
    cmd = _command(task_id, genome, state)
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=900, check=False
    )
    record = {
        "cell": cell,
        "task_id": task_id,
        "rep": rep,
        "state": str(state),
        "command": cmd,
        "exit": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip()[-1000:],
    }
    try:
        from molt.core.store import StateStore

        store = StateStore(state)
        runs = store.list_runs()
        results = store.list_results()
        if runs:
            run = runs[-1]
            record.update(
                {
                    "run_id": run.run_id,
                    "outcome": run.outcome,
                    "cost_usd": run.usage.cost_usd,
                    "wall_ms": run.usage.wall_ms,
                    "turns": run.usage.turns,
                    "pi_version": run.ext.get("runtime", {}).get("pi_version"),
                    "probe": run.ext.get("probe"),
                    "solved": results[-1].scores["search"].solved if results else 0,
                    "cost_source": run.ext.get("runtime", {}).get("cost_source"),
                }
            )
    except Exception as exc:  # noqa: BLE001 - 记录坏 state 而不是中断整批
        record["state_error"] = f"{type(exc).__name__}: {exc}"
    return record


def _task_counts(records: list[dict], cell: str) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for record in records:
        if record.get("cell") != cell:
            continue
        task_id = record["task_id"]
        item = counts.setdefault(task_id, {"passes": 0, "valid": 0, "runs": 0})
        item["runs"] += 1
        if record.get("solved") == 1:
            item["passes"] += 1
        if record.get("outcome") in ("passed", "failed"):
            item["valid"] += 1
    return counts


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    cost_total = 0.0
    budget_stopped = False
    cost_notes: list[str] = []
    for cell, genome, tasks in CELLS:
        for task_id, reps in tasks:
            for rep in range(1, reps + 1):
                record = _run_one(cell, genome, task_id, rep)
                records.append(record)
                cost = float(record.get("cost_usd", 0.0) or 0.0)
                cost_total += cost
                if cost > SINGLE_RUN_NOTE:
                    cost_notes.append(f"{cell}/{task_id}-r{rep}: ${cost:.6f}")
                (OUT / "runs.json").write_text(
                    json.dumps({"runs": records, "cost_total": round(cost_total, 8)},
                               ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                print(
                    f"{cell}/{task_id}-r{rep} exit={record.get('exit')} "
                    f"outcome={record.get('outcome')} solved={record.get('solved')} "
                    f"cost=${cost:.6f} total=${cost_total:.6f}",
                    flush=True,
                )
                if cost_total >= BUDGET_STOP:
                    budget_stopped = True
                    break
            if budget_stopped:
                break
        if budget_stopped:
            break

    leak = subprocess.run(
        [sys.executable, "probes/accept.py", "--stage", "leak"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    leak_ok = leak.returncode == 0
    family = _task_counts(records, "family")
    control = _task_counts(records, "control")
    oracle = _task_counts(records, "oracle")
    family_solved = sum(1 for item in family.values() if item["passes"] > 0)
    control_solved = sum(1 for item in control.values() if item["passes"] > 0)
    oracle_solved = sum(1 for item in oracle.values() if item["passes"] > 0)
    control_ok = bool(control) and all(
        item["runs"] == 2 and item["passes"] == 2 for item in control.values()
    )
    oracle_ok = bool(oracle) and all(
        item["runs"] == 2 and item["passes"] == 2 for item in oracle.values()
    )
    family_no_valid = any(item["valid"] == 0 for item in family.values())
    expected_runs = sum(reps for _, _, tasks in CELLS for _, reps in tasks)
    missing_runs = expected_runs - len(records)

    if missing_runs:
        verdict = "partial"
    elif not leak_ok:
        verdict = "probe_invalid"
    elif not control_ok or not oracle_ok:
        verdict = "probe_invalid"
    elif family_no_valid:
        verdict = "probe_invalid"
    elif family_solved == 0:
        verdict = "green"
    elif family_solved == 1:
        verdict = "partial_headroom"
    else:
        verdict = "too_easy"

    payload = {
        "verdict": verdict,
        "family_solved": family_solved,
        "control_solved": control_solved,
        "oracle_solved": oracle_solved,
        "cost_total": round(cost_total, 8),
        "leak_ok": leak_ok,
        "budget_stopped": budget_stopped,
        "missing_runs": missing_runs,
        "expected_runs": expected_runs,
        "family_tasks": family,
        "control_tasks": control,
        "oracle_tasks": oracle,
        "control_ok": control_ok,
        "oracle_ok": oracle_ok,
        "family_no_valid": family_no_valid,
        "cost_note": cost_notes,
        "runs": records,
    }
    (OUT / "verdict.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"verdict={verdict} cost=${cost_total:.6f} missing={missing_runs}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
