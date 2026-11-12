"""Batch 2b matrix runner: oracle 4 + 4 cells × 4 tasks × 2 reps = 36 runs.
Input: scenario ``probe``; v0-{pro,flash}-{max,high} and ``oracle`` genomes; order-2/x3 + ctl-a/b.
Output: ``probes/report/matrix/{runs,verdict,next-phase}.json`` plus a decision-table sha256 check.
Model: real pi search runs (36).
Cost: ~$1.00 evidence, $1.80 stop / $2.00 limit.
"""

# ruff: noqa: E402

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from molt.core.store import StateStore

OUT = ROOT / "probes/report/matrix"
MATRIX_ROOT = Path("/tmp/molt-probe/matrix-v3")
BUDGET_STOP = 1.8
BUDGET_LIMIT = 2.0
SINGLE_RUN_NOTE = 0.30
CELLS = ("pro-max", "pro-high", "flash-max", "flash-high")
FAMILY_TASKS = ("probe-order-2", "probe-order-x3")
GENOMES = {
    "pro-max": "v0-pro-max",
    "pro-high": "v0-pro-high",
    "flash-max": "v0-flash-max",
    "flash-high": "v0-flash-high",
    "oracle": "oracle",
}


def _specs() -> list[tuple[str, str, int]]:
    specs: list[tuple[str, str, int]] = []
    for task_id in FAMILY_TASKS:
        for rep in (1, 2):
            specs.append(("oracle", task_id, rep))
    for cell in CELLS:
        for task_id in ("probe-ctl-a", "probe-ctl-b"):
            for rep in (1, 2):
                specs.append((cell, task_id, rep))
    for cell in CELLS:
        for task_id in FAMILY_TASKS:
            for rep in (1, 2):
                specs.append((cell, task_id, rep))
    return specs


def _run_one(cell: str, task_id: str, rep: int) -> dict:
    state = MATRIX_ROOT / cell / f"{task_id}-r{rep}"
    if state.exists():
        shutil.rmtree(state)
    cmd = [
        sys.executable,
        "-m",
        "molt",
        "search",
        "--scenario",
        "probe",
        "--runtime",
        "pi",
        "--genome",
        f"shells/probe/genomes/{GENOMES[cell]}.json",
        "--state",
        str(state),
        "--task",
        task_id,
    ]
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=900, check=False
    )
    record: dict = {
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
    except Exception as exc:  # noqa: BLE001
        record["state_error"] = f"{type(exc).__name__}: {exc}"
    return record


def _task_summary(records: list[dict], cell: str, tasks: tuple[str, ...]) -> dict[str, dict]:
    summary: dict[str, dict] = {}
    for task_id in tasks:
        items = [
            record
            for record in records
            if record.get("cell") == cell and record.get("task_id") == task_id
        ]
        passes = sum(1 for item in items if item.get("solved") == 1)
        valid = sum(1 for item in items if item.get("outcome") in ("passed", "failed"))
        summary[task_id] = {
            "runs": len(items),
            "passes": passes,
            "valid": valid,
            "outcomes": [item.get("outcome") for item in items],
            "run_ids": [item.get("run_id") for item in items],
        }
    return summary


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    specs = _specs()
    records: list[dict] = []
    cost_total = 0.0
    budget_stopped = False
    cost_notes: list[str] = []
    for cell, task_id, rep in specs:
        record = _run_one(cell, task_id, rep)
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

    oracle_tasks = FAMILY_TASKS
    control_tasks = ("probe-ctl-a", "probe-ctl-b")
    family_tasks = FAMILY_TASKS
    oracle = {
        task_id: _task_summary(records, "oracle", oracle_tasks)[task_id]
        for task_id in oracle_tasks
    }
    controls = {
        cell: _task_summary(records, cell, control_tasks) for cell in CELLS
    }
    families = {
        cell: _task_summary(records, cell, family_tasks) for cell in CELLS
    }
    oracle_solved = sum(1 for item in oracle.values() if item["passes"] > 0)
    oracle_run_passed = sum(item["passes"] for item in oracle.values())
    oracle_run_total = sum(item["runs"] for item in oracle.values())
    oracle_failed_runs = [
        {
            "run_id": record.get("run_id"),
            "task_id": record.get("task_id"),
            "rep": record.get("rep"),
            "outcome": record.get("outcome"),
            "solved": record.get("solved"),
            "cost_usd": record.get("cost_usd"),
        }
        for record in records
        if record.get("cell") == "oracle" and record.get("solved") != 1
    ]
    oracle_note = (
        f"task-level {oracle_solved}/2, run-level {oracle_run_passed}/{oracle_run_total}"
    )
    K = {
        cell: sum(1 for item in controls[cell].values() if item["passes"] > 0)
        for cell in CELLS
    }
    F = {
        cell: sum(1 for item in families[cell].values() if item["passes"] > 0)
        for cell in CELLS
    }
    expected_runs = 4 + len(CELLS) * 4 * 2
    missing_runs = expected_runs - len(records)

    table_path = ROOT / "probes/decision-table.json"
    table_sha = hashlib.sha256(table_path.read_bytes()).hexdigest()
    meta = json.loads((OUT / "decision-meta.json").read_text(encoding="utf-8"))
    table_ok = meta.get("table_sha256") == table_sha

    if missing_runs:
        verdict, reason = "partial", f"missing_runs: {missing_runs} (budget_stopped={budget_stopped})"
    elif not table_ok:
        verdict, reason = "probe_invalid", "decision-table sha256 mismatch"
    elif oracle_solved < 2:
        verdict, reason = "probe_invalid", f"oracle_solved={oracle_solved} < 2"
    elif any(K[cell] < 2 for cell in CELLS):
        verdict, reason = "probe_invalid", f"control K<2: {K}"
    else:
        from probes.compare import lookup

        f_vector = {
            "pro_max": F["pro-max"],
            "pro_high": F["pro-high"],
            "flash_max": F["flash-max"],
            "flash_high": F["flash-high"],
        }
        row = lookup(f_vector, mode="full")
        verdict, reason = str(row["verdict"]), str(row["reason"])

    payload = {
        "verdict": verdict,
        "reason": reason,
        "F": F,
        "K": K,
        "family_tasks": families,
        "control_tasks": controls,
        "oracle_tasks": oracle,
        "oracle_solved": oracle_solved,
        "oracle_run_passed": oracle_run_passed,
        "oracle_run_total": oracle_run_total,
        "oracle_run_level": f"{oracle_run_passed}/{oracle_run_total}",
        "oracle_failed_runs": oracle_failed_runs,
        "oracle_note": oracle_note,
        "cost_total": round(cost_total, 8),
        "table_sha256": table_sha,
        "table_ok": table_ok,
        "budget_stopped": budget_stopped,
        "budget_limit": BUDGET_LIMIT,
        "missing_runs": missing_runs,
        "expected_runs": expected_runs,
        "cost_note": cost_notes,
        "memory_trigger_recorded_only": verdict == "memory_trigger",
        "runs": records,
    }
    (OUT / "verdict.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    next_phase_path = OUT / "next-phase.json"
    if verdict == "memory_trigger":
        trigger = next((cell for cell in CELLS if F[cell] == 0), None)
        next_phase = {
            "phase": "2c",
            "trigger_cell": trigger,
            "family_tasks": list(FAMILY_TASKS),
            "transfer_tasks": [
                "probe-order-x5",
                "probe-order-x6",
                "probe-order-x7",
                "probe-order-x8",
            ],
            "budget_remaining_usd": round(BUDGET_LIMIT - cost_total, 6),
            "note": "recorded after matrix; 2c not implemented",
        }
        next_phase_path.write_text(
            json.dumps(next_phase, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    elif next_phase_path.exists():
        next_phase_path.unlink()
    print(f"verdict={verdict} reason={reason} cost=${cost_total:.6f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
