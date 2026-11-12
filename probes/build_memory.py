"""Build deterministic memory sources and a Proposal from failed matrix traces."""

# ruff: noqa: E402

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from molt.core.hash import content_hash, sha256_text
from molt.core.types import Genome, Proposal, Task

MEMORY_ROOT = ROOT / "shells/probe/memory"
SOURCES = MEMORY_ROOT / "sources"
GEN2 = MEMORY_ROOT / "gen2.md"
MATRIX_RUNS = ROOT / "probes/report/matrix/runs.json"
TRACES = ROOT / "probes/report/2c/memory-traces"
PROPOSAL = ROOT / "probes/report/2c/proposal.json"
TASKS = ("probe-order-2", "probe-order-x3")
TASK_LABELS = {"probe-order-2": "task-a", "probe-order-x3": "task-b"}
SOURCE_ENV = "MOLT_PROBE_SOURCES"


def _verifier_class():
    path = ROOT / "shells/probe/verifier.py"
    spec = importlib.util.spec_from_file_location("probe_verifier_memory", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ProbeVerifier


def _case_inputs(task_id: str) -> dict[str, str]:
    path = ROOT / "bench/probe" / task_id / "tests/check_cases.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cases = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "cases":
                    cases = ast.literal_eval(node.value)
    if not isinstance(cases, tuple):
        raise ValueError(f"{task_id}: cases tuple missing")
    rendered: dict[str, str] = {}
    for entry in cases:
        case_id = entry[0]
        args = entry[1:-1]
        if len(args) == 1:
            text = json.dumps(args[0], ensure_ascii=False)
        elif len(args) == 2:
            text = f"{json.dumps(args[0], ensure_ascii=False)}, k={args[1]}"
        else:
            text = json.dumps(list(args), ensure_ascii=False)
        rendered[case_id] = text
    return rendered


def _task(task_id: str) -> Task:
    return Task.from_dict(
        json.loads((ROOT / "bench/probe" / task_id / "task.json").read_text(encoding="utf-8"))
    )


def _workspace(state: str) -> Path:
    matches = sorted((Path(state) / "work").glob("*/workspace"))
    if not matches:
        raise FileNotFoundError(f"workspace missing: {state}")
    return matches[-1]


def generate_sources() -> dict[str, object]:
    runs = json.loads(MATRIX_RUNS.read_text(encoding="utf-8"))["runs"]
    selected = [
        run
        for run in runs
        if run.get("cell") == "pro-max" and run.get("task_id") in TASKS
    ]
    selected.sort(key=lambda run: (run["task_id"], run["rep"]))
    verifier_cls = _verifier_class()
    sources: dict[str, list[str]] = {task_id: [] for task_id in TASKS}
    source_runs: list[str] = []
    traces: list[dict[str, object]] = []
    for run in selected:
        task_id = str(run["task_id"])
        task = _task(task_id)
        work = _workspace(str(run["state"]))
        verifier = verifier_cls(ROOT)
        before = verifier.guard(task, work)
        outcome = verifier.verify(task, work, before=before)
        failed = [case for case in outcome.diagnostic["cases"] if not case["passed"]]
        inputs = _case_inputs(task_id)
        trace = {
            "task_id": task_id,
            "rep": run["rep"],
            "run_id": run.get("run_id"),
            "state": run["state"],
            "cases": failed,
        }
        traces.append(trace)
        if failed:
            source_runs.append(str(run.get("run_id")))
        seen = set(re.findall(r"case_id:\s*(\S+)", "\n".join(sources[task_id])))
        for case in failed:
            case_id = str(case["case_id"])
            if case_id in seen:
                continue
            sources[task_id].append(
                f"- case_id: {case_id}\n"
                f"  input: {inputs.get(case_id, 'unknown')}\n"
                f"  expected: {json.dumps(case['expected'], ensure_ascii=False)}\n"
                f"  actual: {json.dumps(case['actual'], ensure_ascii=False)}\n"
                f"  run: r{run['rep']}\n"
            )
            seen.add(case_id)

    SOURCES.mkdir(parents=True, exist_ok=True)
    TRACES.mkdir(parents=True, exist_ok=True)
    for task_id in TASKS:
        body = "\n".join(sources[task_id]) or "- no failed cases\n"
        (SOURCES / f"pro-max-{task_id}.md").write_text(
            f"## {TASK_LABELS[task_id]}\n\n{body}\n", encoding="utf-8"
        )
    for trace in traces:
        path = TRACES / f"pro-max-{trace['task_id']}-r{trace['rep']}.json"
        path.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    bodies = "\n\n".join(
        (SOURCES / f"pro-max-{task_id}.md").read_text(encoding="utf-8").strip()
        for task_id in TASKS
    )
    GEN2.write_text(
        f"# Failures observed in the previous run\n\n{bodies}\n",
        encoding="utf-8",
    )
    return {"source_runs": sorted(set(source_runs)), "tasks": list(TASKS)}


def _sources_from_env() -> tuple[Path, Path | None]:
    raw = os.environ.get(SOURCE_ENV)
    if not raw:
        raise RuntimeError(f"{SOURCE_ENV} unset; refusing to read state")
    path = Path(raw)
    if path.is_dir() and (path / "sources").is_dir():
        return path / "sources", path / "gen2.md"
    if path.is_dir():
        return path, path.parent / "gen2.md"
    raise RuntimeError(f"{SOURCE_ENV} is not a directory: {raw}")


def build_proposal() -> Proposal:
    sources_dir, gen2_path = _sources_from_env()
    source_files = sorted(sources_dir.glob("*.md"))
    if not source_files:
        raise RuntimeError(f"no source files under {sources_dir}")
    if gen2_path is not None and gen2_path.is_file():
        memory_text = gen2_path.read_text(encoding="utf-8")
    else:
        memory_text = "# Failures observed in the previous run\n\n" + "\n".join(
            path.read_text(encoding="utf-8").strip() for path in source_files
        )
    source_runs = sorted(
        {
            str(json.loads(path.read_text(encoding="utf-8"))["run_id"])
            for path in TRACES.glob("*.json")
        }
    )
    if not source_runs:
        raise RuntimeError("memory trace files missing")
    parent = Genome.from_dict(
        json.loads((ROOT / "shells/probe/genomes/v0-pro-max.json").read_text(encoding="utf-8"))
    )
    memory_sha = sha256_text(memory_text)
    ext = {
        "diagnosis": "transfer from failed cases",
        "model": "deterministic",
        "prompt_sha256": memory_sha,
        "response_sha256": memory_sha,
        "usage": {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "wall_ms": 0, "turns": 0},
        "cost_source": "none",
        "counted_as": "memory",
        "fallback": {"used": False, "reason": None},
        "source": "memory",
        "history": {"scenario_version": parent.scenario_version, "entries": len(source_runs), "excluded": 0},
        "memory_text": memory_text,
        "memory_source_runs": source_runs,
        "memory_sha256": memory_sha,
    }
    proposal_id = "prop-" + content_hash(
        {
            "parent": parent.genome_id,
            "switches": parent.switches.to_dict(),
            "memory_sha256": memory_sha,
            "source": "memory",
        }
    )[:16]
    payload = {
        "schema_version": "1.0.0",
        "proposal_id": proposal_id,
        "scenario_version": parent.scenario_version,
        "proposer_id": "memory",
        "parent_id": parent.genome_id,
        "switches": parent.switches.to_dict(),
        "rationale": "deterministic transfer from failed cases",
        "created_at": "2026-11-12T00:00:00Z",
        "cost_usd": 0.0,
        "ext": ext,
    }
    proposal = Proposal.from_dict(payload)
    PROPOSAL.parent.mkdir(parents=True, exist_ok=True)
    PROPOSAL.write_text(json.dumps(proposal.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return proposal


def main() -> int:
    print(generate_sources())
    proposal = build_proposal()
    print(proposal.proposal_id, proposal.ext["memory_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
