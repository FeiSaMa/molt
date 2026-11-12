"""Batch 2c: memory transfer vs baseline, gate via molt.core.policy.decide."""

# ruff: noqa: E402

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from molt.core.hash import sha256_file, sha256_text
from molt.core.policy import SideEval, TaskEval, decide
from molt.core.store import StateStore
from molt.core.types import BudgetCaps, Genome, Policy, Task

OUT = ROOT / "probes/report/2c"
STATE_ROOT = Path("/tmp/molt-probe/2c")
BUDGET_STOP = 2.2
BUDGET_LIMIT = 2.5
SINGLE_RUN_NOTE = 0.30
TRANSFER_TASKS = ("probe-order-x5", "probe-order-x6", "probe-order-x7", "probe-order-x8")
CONTROL_TASKS = ("probe-ctl-a", "probe-ctl-b")


def _genome(name: str) -> Genome:
    return Genome.from_dict(
        json.loads((ROOT / "shells/probe/genomes" / f"{name}.json").read_text(encoding="utf-8"))
    )


def _task(task_id: str) -> Task:
    return Task.from_dict(
        json.loads((ROOT / "bench/probe" / task_id / "task.json").read_text(encoding="utf-8"))
    )


def _specs() -> list[tuple[str, str, str, int]]:
    specs: list[tuple[str, str, str, int]] = []
    for group, genome in (
        ("baseline", "v0-pro-max"),
        ("memory", "gen2"),
    ):
        for task_id in TRANSFER_TASKS:
            for rep in (1, 2):
                specs.append((group, genome, task_id, rep))
    for task_id in CONTROL_TASKS:
        for rep in (1, 2):
            specs.append(("control", "gen2", task_id, rep))
    return specs


def _run_one(group: str, genome: str, task_id: str, rep: int) -> dict:
    state = STATE_ROOT / group / f"{task_id}-r{rep}"
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
        f"shells/probe/genomes/{genome}.json",
        "--state",
        str(state),
        "--task",
        task_id,
    ]
    completed = subprocess.run(
        cmd,
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    record: dict = {
        "group": group,
        "genome": genome,
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
                    "solved": results[-1].scores["search"].solved if results else 0,
                    "verifier_hash": run.verifier_hash,
                }
            )
    except Exception as exc:  # noqa: BLE001
        record["state_error"] = f"{type(exc).__name__}: {exc}"
    return record


def _task_counts(records: list[dict], group: str, tasks: tuple[str, ...]) -> dict[str, dict]:
    summary: dict[str, dict] = {}
    for task_id in tasks:
        items = [
            record
            for record in records
            if record.get("group") == group and record.get("task_id") == task_id
        ]
        summary[task_id] = {
            "runs": len(items),
            "passes": sum(1 for item in items if item.get("solved") == 1),
            "valid": sum(1 for item in items if item.get("outcome") in ("passed", "failed")),
            "outcomes": [item.get("outcome") for item in items],
            "cost_usd": round(sum(float(item.get("cost_usd", 0) or 0) for item in items), 8),
            "run_ids": [item.get("run_id") for item in items],
        }
    return summary


def _function_source(text: str, name: str) -> str | None:
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node)
    return None


def _function_check(records: list[dict]) -> dict:
    checks: list[dict] = []
    for record in records:
        task_id = str(record["task_id"])
        task = _task(task_id)
        function = (task.ext.get("probe") or {}).get("function")
        stub = ROOT / "bench/probe" / task_id / "workspace/solution.py"
        matches = sorted((Path(record["state"]) / "work").glob("*/workspace/solution.py"))
        if not matches:
            checks.append({"group": record["group"], "task_id": task_id, "rep": record["rep"],
                           "present": False, "changed": False, "added": []})
            continue
        workspace = matches[-1]
        current = workspace.read_text(encoding="utf-8")
        original = stub.read_text(encoding="utf-8")
        target = _function_source(current, str(function)) if function else None
        baseline = _function_source(original, str(function)) if function else None
        added = sorted(
            {node.name for node in ast.parse(current).body if isinstance(node, ast.FunctionDef)}
            - {node.name for node in ast.parse(original).body if isinstance(node, ast.FunctionDef)}
        )
        dest = OUT / "solutions" / str(record["group"]) / f"{task_id}-r{record['rep']}.py.txt"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace, dest)
        checks.append({
            "group": record["group"], "task_id": task_id, "rep": record["rep"],
            "function": function, "present": target is not None,
            "changed": target is not None and baseline is not None and target != baseline,
            "added": added,
        })
    violations = [item for item in checks if not item["present"] or not item["changed"] or item["added"]]
    return {"all_ok": not violations, "violations": violations, "checks": checks}


def _side(genome: Genome, tasks: dict[str, dict]) -> SideEval:
    task_evals = tuple(
        TaskEval(task_id, item["passes"] > 0, item["cost_usd"])
        for task_id, item in tasks.items()
    )
    total_runs = sum(item["runs"] for item in tasks.values())
    invalid = sum(item["runs"] - item["valid"] for item in tasks.values())
    return SideEval(
        genome_id=genome.genome_id,
        scenario_version=genome.scenario_version,
        tasks=task_evals,
        limits=genome.budget,
        invalid_ratio=(invalid / total_runs) if total_runs else 1.0,
        overhead_spend_usd=0.0,
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    cost_total = 0.0
    budget_stopped = False
    cost_notes: list[str] = []
    for group, genome, task_id, rep in _specs():
        record = _run_one(group, genome, task_id, rep)
        records.append(record)
        cost = float(record.get("cost_usd", 0.0) or 0.0)
        cost_total += cost
        if cost > SINGLE_RUN_NOTE:
            cost_notes.append(f"{group}/{task_id}-r{rep}: ${cost:.6f}")
        (OUT / "rows.json").write_text(
            json.dumps({"runs": records, "cost_total": round(cost_total, 8)},
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"{group}/{task_id}-r{rep} exit={record.get('exit')} "
            f"outcome={record.get('outcome')} solved={record.get('solved')} "
            f"cost=${cost:.6f} total=${cost_total:.6f}",
            flush=True,
        )
        if cost_total >= BUDGET_STOP:
            budget_stopped = True
            break

    baseline = _task_counts(records, "baseline", TRANSFER_TASKS)
    memory = _task_counts(records, "memory", TRANSFER_TASKS)
    controls = _task_counts(records, "control", CONTROL_TASKS)
    memory_solved = sum(1 for item in memory.values() if item["passes"] > 0)
    baseline_solved = sum(1 for item in baseline.values() if item["passes"] > 0)
    control_solved = sum(1 for item in controls.values() if item["passes"] > 0)
    expected_runs = 20
    missing_runs = expected_runs - len(records)

    baseline_genome = _genome("v0-pro-max")
    memory_genome = _genome("gen2")
    baseline_side = _side(baseline_genome, baseline)
    memory_side = _side(memory_genome, memory)
    policy = Policy(
        k=0,
        invalid_ratio_max=0.25,
        budget=BudgetCaps(execution_budget_usd=BUDGET_LIMIT, search_overhead_budget_usd=0.1),
    )
    verifier_hash = sha256_file(ROOT / "shells/probe/verifier.py")
    decision = decide(
        policy,
        baseline_side,
        memory_side,
        decision_id="memory-2c-gate",
        scenario_version="probe-4",
        shell_version="0.2.0",
        verifier_hash=verifier_hash,
        created_at="2026-11-12T00:00:00Z",
    )
    (OUT / "gate.json").write_text(
        json.dumps(decision.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if missing_runs:
        verdict = "partial"
    elif control_solved < 2:
        verdict = "memory_harmful"
    elif memory_solved == 4 and baseline_solved <= 2:
        verdict = "memory_works"
    elif memory_solved == 3:
        verdict = "partial_transfer"
    else:
        verdict = "memory_not_effective"

    gen2_text = (ROOT / "shells/probe/memory/gen2.md").read_text(encoding="utf-8")
    proposal = json.loads((OUT / "proposal.json").read_text(encoding="utf-8"))
    function_check = _function_check(records)
    (OUT / "function-check.json").write_text(
        json.dumps(function_check, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    payload = {
        "verdict": verdict,
        "memory_solved": memory_solved,
        "baseline_solved": baseline_solved,
        "control_solved": control_solved,
        "memory_tasks": memory,
        "baseline_tasks": baseline,
        "control_tasks": controls,
        "cost_total": round(cost_total, 8),
        "budget_limit": BUDGET_LIMIT,
        "budget_stopped": budget_stopped,
        "missing_runs": missing_runs,
        "expected_runs": expected_runs,
        "cost_note": cost_notes,
        "gate_verdict": decision.verdict,
        "gate_reason": decision.reason,
        "gate_paired_tasks": [item["task_id"] for item in decision.paired],
        "proposal_id": proposal["proposal_id"],
        "gen2_genome_id": memory_genome.genome_id,
        "memory_sha256": sha256_text(gen2_text),
        "function_check_all_ok": function_check["all_ok"],
        "function_check_violations": function_check["violations"],
    }
    (OUT / "verdict.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"verdict={verdict} memory={memory_solved}/4 baseline={baseline_solved}/4 "
        f"control={control_solved}/2 gate={decision.verdict}/{decision.reason} "
        f"cost=${cost_total:.6f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
