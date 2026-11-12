"""Memory v3: gen4 rule-only memory on x5/x6/x7/x8, plus x5 gen3 r3/r4."""

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

from molt.core.store import StateStore

OUT = ROOT / "probes/report/2c/memory-v3"
GEN4_STATE = Path("/tmp/molt-probe/2c-v3")
GEN3_STATE = Path("/tmp/molt-probe/2c-v2")
BUDGET_STOP = 1.0
BUDGET_LIMIT = 1.2
TASKS = ("probe-order-x5", "probe-order-x6", "probe-order-x7", "probe-order-x8")
FUNCS = {
    "probe-order-x5": "unique",
    "probe-order-x6": "dedupe_merge",
    "probe-order-x7": "dedupe_min",
    "probe-order-x8": "distinct_count",
}
EXPECTED_SIG = {
    "probe-order-x5": "(names) -> list[str]",
    "probe-order-x6": "(a, b) -> list[str]",
    "probe-order-x7": "(names) -> str | None",
    "probe-order-x8": "(names) -> int",
}


def _run_one(memory: str, task_id: str, rep: int) -> dict:
    state = (GEN4_STATE if memory == "gen4" else GEN3_STATE) / f"{task_id}-r{rep}"
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
        f"shells/probe/genomes/{memory}.json",
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
        "memory": memory,
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
                    "solved": results[-1].scores["search"].solved if results else 0,
                    "cost_usd": run.usage.cost_usd,
                    "wall_ms": run.usage.wall_ms,
                    "turns": run.usage.turns,
                }
            )
            workspace = sorted((state / "work").glob("*/workspace/solution.py"))[-1]
            current = workspace.read_text(encoding="utf-8")
            stub = (
                ROOT / "bench/probe" / task_id / "workspace/solution.py"
            ).read_text(encoding="utf-8")
            function = FUNCS[task_id]

            def segment(text: str) -> str | None:
                for node in ast.parse(text).body:
                    if isinstance(node, ast.FunctionDef) and node.name == function:
                        return ast.get_source_segment(text, node)
                return None

            target = segment(current)
            baseline = segment(stub)
            nodes = [
                node
                for node in ast.parse(current).body
                if isinstance(node, ast.FunctionDef) and node.name == function
            ]
            signature = "missing"
            if nodes:
                names = [arg.arg for arg in nodes[0].args.args]
                ret = ast.unparse(nodes[0].returns) if nodes[0].returns else "None"
                signature = f"({', '.join(names)}) -> {ret}"
            dest = OUT / "solutions" / f"{memory}-{task_id}-r{rep}.py.txt"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(workspace, dest)
            record.update(
                {
                    "target_changed": bool(target and baseline and target != baseline),
                    "signature": signature,
                    "signature_changed": signature != EXPECTED_SIG[task_id],
                    "added_functions": sorted(
                        {n.name for n in ast.parse(current).body if isinstance(n, ast.FunctionDef)}
                        - {n.name for n in ast.parse(stub).body if isinstance(n, ast.FunctionDef)}
                    ),
                    "solution_snapshot": str(dest.relative_to(ROOT)),
                }
            )
    except Exception as exc:  # noqa: BLE001
        record["state_error"] = f"{type(exc).__name__}: {exc}"
    return record


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    reflection = json.loads((OUT / "reflection.json").read_text(encoding="utf-8"))
    cost_total = float(reflection.get("cost_usd", 0.0) or 0.0)
    records: list[dict] = []
    budget_stopped = False
    specs = [("gen4", task, rep) for task in TASKS for rep in (1, 2)]
    specs += [("gen3", "probe-order-x5", rep) for rep in (3, 4)]
    for memory, task_id, rep in specs:
        record = _run_one(memory, task_id, rep)
        records.append(record)
        cost_total += float(record.get("cost_usd", 0.0) or 0.0)
        (OUT / "rows.json").write_text(
            json.dumps({"runs": records, "cost_total": round(cost_total, 8)},
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"{memory}/{task_id}-r{rep} exit={record.get('exit')} "
            f"outcome={record.get('outcome')} solved={record.get('solved')} "
            f"sig={record.get('signature')} cost=${record.get('cost_usd', 0):.6f} "
            f"total=${cost_total:.6f}",
            flush=True,
        )
        if cost_total >= BUDGET_STOP:
            budget_stopped = True
            break
    v3 = {}
    for task_id in TASKS:
        items = [r for r in records if r["memory"] == "gen4" and r["task_id"] == task_id]
        v3[task_id] = {
            "runs": len(items),
            "passes": sum(1 for r in items if r.get("solved") == 1),
            "valid": sum(1 for r in items if r.get("outcome") in ("passed", "failed")),
            "rate": (sum(1 for r in items if r.get("solved") == 1) / len(items)) if items else None,
            "signature_changed": sum(1 for r in items if r.get("signature_changed")),
        }
    v2 = json.loads((ROOT / "probes/report/2c/memory-v2/verdict.json").read_text(encoding="utf-8"))
    v3_rates = v3
    x8_ok = v3["probe-order-x8"]["passes"] >= 1
    comparisons = []
    for task_id in ("probe-order-x6", "probe-order-x7", "probe-order-x8"):
        v2_rate = v2["v2"][task_id]["rate"]
        v3_rate = v3_rates[task_id]["rate"]
        comparisons.append((task_id, v2_rate, v3_rate))
    if any(r.get("outcome") not in ("passed", "failed") for r in records) or len(records) < 10:
        verdict = "partial"
    elif all(v3_rate >= v2_rate for _, v2_rate, v3_rate in comparisons) and x8_ok:
        verdict = "v3_better"
    elif all(v3_rate == v2_rate for _, v2_rate, v3_rate in comparisons):
        verdict = "no_difference"
    else:
        verdict = "v3_worse"
    x5_extension = [r for r in records if r["memory"] == "gen3"]
    v3_sig_changed = sum(1 for r in records if r["memory"] == "gen4" and r.get("signature_changed"))
    payload = {
        "verdict": verdict,
        "comparison": "v3 vs v2 pass rate on x6/x7/x8; x8 must be >=1/2",
        "v2": {task: v2["v2"][task] for task in ("probe-order-x6", "probe-order-x7", "probe-order-x8")},
        "v3": v3,
        "x8": {
            "passes": v3["probe-order-x8"]["passes"],
            "runs": 2,
            "result": "x8_fixed" if x8_ok else "x8_still_negative",
        },
        "x5_v3": v3["probe-order-x5"],
        "x5_gen3_extension": {
            "runs": x5_extension,
            "passes": sum(1 for r in x5_extension if r.get("solved") == 1),
            "runs_count": len(x5_extension),
        },
        "signature_drift": {
            "v2": "10/12",
            "v3": f"{v3_sig_changed}/{sum(1 for r in records if r['memory']=='gen4')}",
            "v3_runs": [
                f"{r['task_id']}-r{r['rep']}"
                for r in records
                if r["memory"] == "gen4" and r.get("signature_changed")
            ],
        },
        "cost_total": round(cost_total, 8),
        "reflection_cost_usd": reflection.get("cost_usd"),
        "budget_stopped": budget_stopped,
        "missing_runs": 10 - len(records),
        "gen4_genome_id": json.loads(
            (ROOT / "shells/probe/genomes/gen4.json").read_text(encoding="utf-8")
        )["genome_id"],
        "memory_sha256": reflection.get("gen4_sha256"),
        "runs": records,
    }
    (OUT / "verdict.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    main = json.loads((ROOT / "probes/report/2c/verdict.json").read_text(encoding="utf-8"))
    main["memory_v3"] = payload
    (ROOT / "probes/report/2c/verdict.json").write_text(
        json.dumps(main, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"verdict={verdict} v3={ {k:v['passes'] for k,v in v3.items()} } cost=${cost_total:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
