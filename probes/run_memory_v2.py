"""Memory v2: run gen3 (rule-based reflection) on x5/x6/x7, compare pass rates to v1."""

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

OUT = ROOT / "probes/report/2c/memory-v2"
STATE_ROOT = Path("/tmp/molt-probe/2c-v2")
BUDGET_STOP = 0.8
BUDGET_LIMIT = 1.0
TASKS = ("probe-order-x5", "probe-order-x6", "probe-order-x7")
FUNCTIONS = {
    "probe-order-x5": "unique",
    "probe-order-x6": "dedupe_merge",
    "probe-order-x7": "dedupe_min",
}


def _run_one(task_id: str, rep: int) -> dict:
    state = STATE_ROOT / f"{task_id}-r{rep}"
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
        "shells/probe/genomes/gen3.json",
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
            function = FUNCTIONS[task_id]

            def segment(text: str) -> str | None:
                for node in ast.parse(text).body:
                    if isinstance(node, ast.FunctionDef) and node.name == function:
                        return ast.get_source_segment(text, node)
                return None

            target = segment(current)
            baseline = segment(stub)
            args = [
                node
                for node in ast.parse(current).body
                if isinstance(node, ast.FunctionDef) and node.name == function
            ]
            signature = "missing"
            if args:
                fn = args[0]
                names = [arg.arg for arg in fn.args.args]
                ret = ast.unparse(fn.returns) if fn.returns else "None"
                signature = f"({', '.join(names)}) -> {ret}"
            dest = OUT / "solutions" / f"{task_id}-r{rep}.py.txt"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(workspace, dest)
            record.update(
                {
                    "target_changed": bool(target and baseline and target != baseline),
                    "signature": signature,
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
    for task_id in TASKS:
        for rep in (1, 2):
            record = _run_one(task_id, rep)
            records.append(record)
            cost_total += float(record.get("cost_usd", 0.0) or 0.0)
            (OUT / "rows.json").write_text(
                json.dumps({"runs": records, "cost_total": round(cost_total, 8)},
                           ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(
                f"{task_id}-r{rep} exit={record.get('exit')} outcome={record.get('outcome')} "
                f"solved={record.get('solved')} sig={record.get('signature')} "
                f"cost=${record.get('cost_usd', 0):.6f} total=${cost_total:.6f}",
                flush=True,
            )
            if cost_total >= BUDGET_STOP:
                budget_stopped = True
                break
        if budget_stopped:
            break

    main = json.loads((ROOT / "probes/report/2c/verdict.json").read_text(encoding="utf-8"))
    stability = main["stability"]
    v1 = {
        "x5": (stability["x5"]["memory_passes"], stability["x5"]["memory_runs"]),
        "x6": (stability["x6"]["memory_passes"], stability["x6"]["memory_runs"]),
        "x7": (stability["x7"]["memory_passes"], stability["x7"]["memory_runs"]),
    }
    v2_counts: dict[str, dict[str, int]] = {}
    for task_id in TASKS:
        items = [r for r in records if r.get("task_id") == task_id]
        v2_counts[task_id] = {
            "runs": len(items),
            "passes": sum(1 for r in items if r.get("solved") == 1),
            "valid": sum(1 for r in items if r.get("outcome") in ("passed", "failed")),
            "signature_changed": sum(
                1
                for r in items
                if r.get("signature")
                not in (
                    "(names) -> list[str]"
                    if task_id == "probe-order-x5"
                    else ("(a, b) -> list[str]" if task_id == "probe-order-x6" else "(names) -> str | None"),
                )
            ),
        }
    missing = 6 - len(records)
    better = []
    worse = []
    for key, task_id in (("x5", "probe-order-x5"), ("x6", "probe-order-x6"), ("x7", "probe-order-x7")):
        v1_p, v1_n = v1[key]
        v2_p = v2_counts[task_id]["passes"]
        v2_n = 2
        if v2_p / v2_n > v1_p / v1_n:
            better.append(key)
        elif v2_p / v2_n < v1_p / v1_n:
            worse.append(key)
    if missing:
        verdict = "partial"
    elif not worse and better:
        verdict = "reflection_better"
    elif not worse and not better:
        verdict = "no_difference"
    else:
        verdict = "reflection_worse"
    payload = {
        "verdict": verdict,
        "comparison": "pass_rate; v1 has 4 runs, v2 has 2 runs",
        "v1": {k: {"passes": v[0], "runs": v[1], "rate": round(v[0] / v[1], 4)} for k, v in v1.items()},
        "v2": {
            k: {
                "passes": v["passes"],
                "runs": v["runs"],
                "rate": round(v["passes"] / v["runs"], 4) if v["runs"] else None,
                "valid": v["valid"],
                "signature_changed": v["signature_changed"],
            }
            for k, v in v2_counts.items()
        },
        "better": better,
        "worse": worse,
        "budget_stopped": budget_stopped,
        "missing_runs": missing,
        "cost_total": round(cost_total, 8),
        "reflection_cost_usd": reflection.get("cost_usd"),
        "gen3_genome_id": json.loads(
            (ROOT / "shells/probe/genomes/gen3.json").read_text(encoding="utf-8")
        )["genome_id"],
        "memory_sha256": reflection.get("gen3_sha256"),
        "signature_changed_runs": [
            f"{r['task_id']}-r{r['rep']}" for r in records if r.get("signature") not in (
                "(names) -> list[str]" if r["task_id"] == "probe-order-x5"
                else ("(a, b) -> list[str]" if r["task_id"] == "probe-order-x6" else "(names) -> str | None"),
            )
        ],
        "runs": records,
    }
    (OUT / "verdict.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    main["memory_v2"] = payload
    (ROOT / "probes/report/2c/verdict.json").write_text(
        json.dumps(main, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"verdict={verdict} v2={ {k:v['passes'] for k,v in v2_counts.items()} } cost=${cost_total:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
