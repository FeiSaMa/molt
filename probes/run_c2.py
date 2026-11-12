"""C2 舍入族有效性门：16 runs（族 2×4 / 控制 2×2 / oracle 2×2）。"""

# ruff: noqa: E402

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from molt.core.store import StateStore

OUT = ROOT / "probes/report/c2"
STATE_ROOT = Path("/tmp/molt-probe/c2")
BUDGET_LIMIT = 1.5
BUDGET_STOP = 1.3
PER_RUN_TIMEOUT = 500
MAX_WORKERS = 3

CELLS = (
    ("family", "c2-pro-max", (("c2-percent", 4), ("c2-ratio", 4))),
    ("control", "c2-pro-max", (("c2-ctl-a", 2), ("c2-ctl-b", 2))),
    ("oracle", "c2-oracle", (("c2-percent", 2), ("c2-ratio", 2))),
)


def _specs() -> list[tuple[str, str, str, int]]:
    return [
        (cell, genome, task_id, rep)
        for cell, genome, tasks in CELLS
        for task_id, reps in tasks
        for rep in range(1, reps + 1)
    ]


def _run_one(cell: str, genome: str, task_id: str, rep: int) -> dict:
    state = STATE_ROOT / cell / f"{task_id}-r{rep}"
    if state.exists():
        shutil.rmtree(state)
    command = [
        sys.executable,
        "-m",
        "molt",
        "search",
        "--scenario",
        "probe-c2",
        "--runtime",
        "pi",
        "--genome",
        f"shells/probe-c2/genomes/{genome}.json",
        "--state",
        str(state),
        "--task",
        task_id,
    ]
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    record: dict = {
        "cell": cell,
        "genome": genome,
        "task_id": task_id,
        "rep": rep,
        "state": str(state),
        "command": command,
    }
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=PER_RUN_TIMEOUT,
            check=False,
        )
        record.update(
            {
                "exit": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip()[-1000:],
            }
        )
    except subprocess.TimeoutExpired as exc:
        record.update(
            {
                "exit": None,
                "timeout": True,
                "stdout": (exc.stdout or b"").decode(errors="replace").strip()
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "").strip(),
                "stderr": (exc.stderr or b"").decode(errors="replace")[-1000:]
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or "")[-1000:],
            }
        )
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
                }
            )
    except Exception as exc:  # noqa: BLE001 - 记录坏 state，不中断整批
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


def _write(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    specs = _specs()
    expected_runs = len(specs)
    records: list[dict] = []
    lock = Lock()
    cost_total = 0.0
    budget_stopped = False

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(_run_one, *spec) for spec in specs]
        for future in as_completed(futures):
            record = future.result()
            with lock:
                records.append(record)
                cost_total += float(record.get("cost_usd", 0.0) or 0.0)
                if cost_total >= BUDGET_STOP:
                    budget_stopped = True
                    for pending in futures:
                        pending.cancel()
                _write(
                    OUT / "rows.json",
                    {
                        "runs": sorted(
                            records,
                            key=lambda item: (
                                item["cell"],
                                item["task_id"],
                                item["rep"],
                            ),
                        ),
                        "cost_total": round(cost_total, 8),
                    },
                )
                print(
                    f"{record['cell']}/{record['task_id']}-r{record['rep']} "
                    f"exit={record.get('exit')} outcome={record.get('outcome')} "
                    f"solved={record.get('solved')} cost=${record.get('cost_usd', 0):.6f} "
                    f"total=${cost_total:.6f}",
                    flush=True,
                )
            if cost_total >= BUDGET_LIMIT:
                for pending in futures:
                    pending.cancel()
                break

    records.sort(key=lambda item: (item["cell"], item["task_id"], item["rep"]))
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
    missing_runs = expected_runs - len(records)
    invalid_runs = sum(
        1 for item in records if item.get("outcome") not in ("passed", "failed")
    )

    if missing_runs:
        verdict = "partial"
    elif invalid_runs:
        verdict = "c2_invalid"
    elif family_solved == 0 and control_ok and oracle_ok:
        verdict = "c2_valid"
    else:
        verdict = "c2_invalid"

    payload = {
        "verdict": verdict,
        "family_solved": family_solved,
        "control_solved": control_solved,
        "oracle_solved": oracle_solved,
        "control_ok": control_ok,
        "oracle_ok": oracle_ok,
        "family_tasks": family,
        "control_tasks": control,
        "oracle_tasks": oracle,
        "expected_runs": expected_runs,
        "missing_runs": missing_runs,
        "invalid_runs": invalid_runs,
        "budget_limit": BUDGET_LIMIT,
        "budget_stop": BUDGET_STOP,
        "budget_stopped": budget_stopped,
        "cost_total": round(cost_total, 8),
        "runs": records,
    }
    _write(OUT / "verdict.json", payload)
    print(
        f"verdict={verdict} family_solved={family_solved} "
        f"control_ok={control_ok} oracle_ok={oracle_ok} "
        f"missing={missing_runs} cost=${cost_total:.6f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
