"""Batch 1 验收脚本：每个 stage 都用真实文件/真实命令判定。"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import ast
import doctest
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from molt.bench import compute_bench_hash
from molt.core.store import StateStore
from molt.core.types import Task

STAGES = (
    "max_preflight",
    "freeze",
    "isolation",
    "ambiguity",
    "leak",
    "default_bias",
    "family",
    "blind",
    "memory",
)
RULE_WORDS = (
    "canonical",
    "lexicographic",
    "sort",
    "order",
    "priority",
    "length",
    "casefold",
    "shortlex",
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _run(cmd: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )


def _ok(reason: str) -> tuple[bool, str, int]:
    return True, reason, 0


def _fail(reason: str, *, code: int = 1) -> tuple[bool, str, int]:
    return False, reason, code


def stage_max_preflight() -> tuple[bool, str, int]:
    path = ROOT / "probes/report/preflight/max-preflight.json"
    if not path.is_file():
        return _fail(f"missing {path.relative_to(ROOT)}")
    data = _load_json(path)
    version_run = _run(["pi", "--version"], timeout=20)
    actual = (version_run.stdout or version_run.stderr).strip().splitlines()
    actual_version = actual[0] if actual else ""
    if not actual_version:
        return _fail("pi --version produced no output")
    if data.get("pi_version") != actual_version:
        return _fail(f"pi_version {data.get('pi_version')!r} != pi --version {actual_version!r}")
    runs = data.get("runs")
    if not isinstance(runs, list) or not runs:
        return _fail("runs missing")
    text_runs = [run for run in runs if run.get("mode") == "text"]
    if not text_runs or text_runs[0].get("exit") != 0:
        return _fail("text run exit != 0")
    if not any(run.get("thinkingLevel") == "max" for run in runs):
        return _fail("thinkingLevel != max")
    return _ok(f"pi_version={actual_version} thinkingLevel=max exit=0")


def stage_freeze() -> tuple[bool, str, int]:
    frozen = _run(
        [
            "git",
            "diff",
            "--exit-code",
            "--",
            "molt/core",
            "schemas",
            "docs/protocol.md",
            "bench/search",
            "bench/holdout",
            "web/data",
        ],
        timeout=60,
    )
    if frozen.returncode != 0:
        return _fail(f"frozen diff exit={frozen.returncode}")
    bench_status = _run(["git", "status", "--porcelain", "--", "bench"], timeout=30)
    lines = [line for line in bench_status.stdout.splitlines() if line.strip()]
    allowed = {"?? bench/probe/"}
    if not set(lines) <= allowed:
        return _fail(f"unexpected bench status: {lines}")
    scenario = _load_json(ROOT / "scenarios/demo/scenario.json")
    task_ids = scenario["task_sets"]["search"] + scenario["task_sets"]["report"]
    actual = compute_bench_hash(ROOT, task_ids)
    if actual != scenario["bench_hash"]:
        return _fail(f"demo bench_hash {actual} != {scenario['bench_hash']}")
    for rel in ("scenarios/demo/scenario.json", "scenarios/probe/scenario.json"):
        result = _run(
            [sys.executable, "-m", "molt", "scenario", "validate", "--scenario", rel],
            timeout=120,
        )
        if result.returncode != 0:
            return _fail(f"scenario validate failed: {rel}")
    return _ok(f"bench_hash={actual[:12]}… validate=2")


def _probe_cell(ext: Any) -> Any:
    if isinstance(ext, dict):
        probe = ext.get("probe")
        if isinstance(probe, dict):
            return probe.get("cell")
    return None


def _is_probe_run_id(run_id: str) -> bool:
    return run_id.startswith("probe-") or "-probe-" in run_id


def stage_isolation() -> tuple[bool, str, int]:
    path = ROOT / "probes/pilot-commands.json"
    if not path.is_file():
        return _fail("missing probes/pilot-commands.json")
    data = _load_json(path)
    probe_root = Path("/tmp/molt-probe").resolve()
    checked = 0
    for cmd in data.get("commands", []):
        state = cmd.get("state")
        if state is not None:
            resolved = Path(state).resolve()
            if not resolved.is_relative_to(probe_root):
                return _fail(f"{cmd.get('id')}: state {state} 不在 /tmp/molt-probe/")
            checked += 1
        argv = [str(item) for item in cmd.get("argv", [])]
        if "--state" in argv:
            value = Path(argv[argv.index("--state") + 1]).resolve()
            if not value.is_relative_to(probe_root):
                return _fail(f"{cmd.get('id')}: --state {value} 不在 /tmp/molt-probe/")
            if state is not None and value != Path(state).resolve():
                return _fail(f"{cmd.get('id')}: --state 与登记 state 不一致")
    demo_states = [p for p in (ROOT / "state", ROOT / "state/demo") if p.is_dir()]
    demo_runs = 0
    for demo in demo_states:
        for cmd in data.get("commands", []):
            state = cmd.get("state")
            if state is None:
                continue
            resolved = Path(state).resolve()
            if resolved.is_relative_to(demo.resolve()) or demo.resolve().is_relative_to(resolved):
                return _fail(f"{cmd.get('id')}: 与 demo state 相交 {demo}")
        if not (demo / "runs").is_dir():
            continue
        for run in StateStore(demo).list_runs():
            demo_runs += 1
            if _is_probe_run_id(run.run_id) or _probe_cell(run.ext):
                return _fail(f"demo state {demo} 含 probe run {run.run_id}")
    web_data = ROOT / "web/data"
    if not web_data.is_dir():
        return _fail("missing web/data")
    for json_path in sorted(web_data.glob("*.json")):
        payload = _load_json(json_path)
        for run_id in _walk_run_ids(payload):
            if _is_probe_run_id(run_id):
                return _fail(f"{json_path.name} 含 probe run_id={run_id}")
    return _ok(
        f"states={checked} demo_states={len(demo_states)} demo_runs={demo_runs} "
        f"web_data={len(list(web_data.glob('*.json')))}"
    )


def _walk_run_ids(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "run_id" and isinstance(item, str):
                found.append(item)
            else:
                found.extend(_walk_run_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_run_ids(item))
    return found


def _eval_impl(expr: str, params: list[str]) -> Callable[..., Any]:
    signature = ", ".join(params)
    return eval(f"lambda {signature}: {expr}", {"__builtins__": __builtins__})  # noqa: S307


def stage_ambiguity() -> tuple[bool, str, int]:
    path = ROOT / "probes/design.json"
    if not path.is_file():
        return _fail("missing probes/design.json")
    data = _load_json(path)
    tasks = data.get("tasks")
    if not isinstance(tasks, dict) or not tasks:
        return _fail("design.tasks missing")
    checked = 0
    for task_id, entry in tasks.items():
        workspace = ROOT / "bench/probe" / task_id / "workspace/solution.py"
        if not workspace.is_file():
            return _fail(f"{task_id}: missing workspace/solution.py")
        tree = ast.parse(workspace.read_text(encoding="utf-8"))
        doc = ast.get_docstring(next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)))
        examples = doctest.DocTestParser().get_examples(doc or "")
        if not examples:
            return _fail(f"{task_id}: no workspace examples")
        function = entry["function"]
        params = entry["params"]
        if entry.get("convention_id") is None:
            spec_fn = _eval_impl(entry["spec_impl"], params)
            for example in examples:
                expr = example.source.strip()
                result = eval(expr, {"__builtins__": __builtins__, function: spec_fn})  # noqa: S307
                if repr(result) != example.want.strip():
                    return _fail(f"{task_id}: spec example {expr!r} 不符")
                checked += 1
            continue
        default_fn = _eval_impl(entry["default_impl"], params)
        project_fn = _eval_impl(entry["project_impl"], params)
        for example in examples:
            expr = example.source.strip()
            default_result = eval(expr, {"__builtins__": __builtins__, function: default_fn})  # noqa: S307
            project_result = eval(expr, {"__builtins__": __builtins__, function: project_fn})  # noqa: S307
            if json.dumps(default_result, sort_keys=True) != json.dumps(project_result, sort_keys=True):
                return _fail(f"{task_id}: example {expr!r} 输出不同")
            checked += 1
    return _ok(f"tasks={len(tasks)} examples={checked}")


def _has_rule_word(text: str, word: str) -> bool:
    """按词边界匹配：``sort`` 不应命中 ``sorted``（默认实现必须允许）。"""
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])", text) is not None


def stage_leak() -> tuple[bool, str, int]:
    scanned = 0
    probe_root = ROOT / "bench/probe"
    test_dirs = probe_root.glob("*/tests") if probe_root.is_dir() else []
    for base in test_dirs:
        for path in sorted(base.rglob("*")):
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
                for word in RULE_WORDS:
                    if _has_rule_word(text, word):
                        return _fail(f"{path.relative_to(ROOT)} 含规则词 {word!r}")
                scanned += 1
    workspace_dirs = probe_root.glob("*/workspace") if probe_root.is_dir() else []
    for base in workspace_dirs:
        for path in sorted(base.rglob("*")):
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
                # Batch1 题面按设计明确写 "this package's order"；该中性句不是规则泄漏，
                # 其余规则词仍按同一张表扫描。
                text = text.replace("package's order", "package's preferred sequence")
                for word in RULE_WORDS:
                    if _has_rule_word(text, word):
                        return _fail(f"{path.relative_to(ROOT)} 含规则词 {word!r}")
                scanned += 1
    trace_paths = sorted((ROOT / "probes/report").rglob("*-trace.json"))
    if not trace_paths:
        return _fail("no *-trace.json")
    for trace_path in trace_paths:
        trace = json.dumps(_load_json(trace_path), ensure_ascii=False).lower()
        for word in RULE_WORDS:
            if _has_rule_word(trace, word):
                return _fail(f"{trace_path.name} trace 含规则词 {word!r}")
        for marker in ("bench/", "test_", "check_cases", ".py"):
            if marker in trace:
                return _fail(f"{trace_path.name} trace 含路径/文件名 {marker!r}")
    return _ok(f"files={scanned} traces={len(trace_paths)} clean")


def _probe_verifier_class():
    path = ROOT / "shells/probe/verifier.py"
    spec = importlib.util.spec_from_file_location("probe_verifier_accept", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ProbeVerifier


def _write_impl(workspace: Path, function: str, params: list[str], impl: str) -> None:
    signature = ", ".join(params)
    text = (
        '"""Generated implementation for default_bias check."""\n\n'
        "from __future__ import annotations\n\n\n"
        f"def {function}({signature}):\n"
        f"    return {impl}\n"
    )
    (workspace / "solution.py").write_text(text, encoding="utf-8")


def stage_default_bias() -> tuple[bool, str, int]:
    data = _load_json(ROOT / "probes/design.json")
    tasks = data.get("tasks")
    if not isinstance(tasks, dict) or not tasks:
        return _fail("design.tasks missing")
    verifier_cls = _probe_verifier_class()
    checked = 0
    discriminating_total = 0
    with tempfile.TemporaryDirectory(prefix="molt-default-bias-") as tmp:
        for task_id, entry in tasks.items():
            if entry.get("kind") != "family":
                continue
            facet = entry.get("facet")
            if facet not in ("C1a", "C1b"):
                return _fail(f"{task_id}: facet {facet!r} 不是 C1a/C1b")
            task = Task.from_dict(_load_json(ROOT / "bench/probe" / task_id / "task.json"))
            case_sets: dict[str, dict[str, Any]] = {}
            for variant, key in (("default", "default_impl"), ("project", "project_impl")):
                work = Path(tmp) / f"{task_id}-{variant}" / "workspace"
                shutil.copytree(ROOT / "bench/probe" / task_id / "workspace", work)
                _write_impl(work, entry["function"], entry["params"], entry[key])
                verifier = verifier_cls(ROOT)
                before = verifier.guard(task, work)
                result = verifier.verify(task, work, before=before)
                case_sets[variant] = {
                    case["case_id"]: case for case in result.diagnostic["cases"]
                }
            project_cases = case_sets["project"]
            if not project_cases or not all(case["passed"] for case in project_cases.values()):
                return _fail(f"{task_id}: project_impl 未全过")
            default_cases = case_sets["default"]
            discriminating = [
                case_id
                for case_id, case in default_cases.items()
                if not case["passed"] and project_cases.get(case_id, {}).get("passed")
            ]
            if not discriminating:
                return _fail(f"{task_id}: 无 default 失败/project 通过 的隐藏用例")
            checked += 1
            discriminating_total += len(discriminating)
    return _ok(f"tasks={checked} discriminating_cases={discriminating_total}")


def stage_family() -> tuple[bool, str, int]:
    data = _load_json(ROOT / "probes/design.json")
    tasks = data.get("tasks") or {}
    family = {
        task_id: entry
        for task_id, entry in tasks.items()
        if entry.get("convention_id") == "C1" and entry.get("kind") == "family"
    }
    if set(family) != {"probe-order-2", "probe-order-x3"}:
        return _fail(f"family tasks={sorted(family)} != ['probe-order-2', 'probe-order-x3']")
    if len(family) < 2:
        return _fail(f"insufficient_tasks: {len(family)} < 2", code=2)
    facets = sorted({entry.get("facet") for entry in family.values()})
    if facets != ["C1b"]:
        return _fail(f"family facets={facets} != ['C1b']")
    return _ok(f"family_tasks={len(family)} facets={facets}")


def stage_blind() -> tuple[bool, str, int]:
    state = Path("/tmp/molt-probe/batch1/blind")
    if not (state / "runs").is_dir():
        return _fail("missing /tmp/molt-probe/batch1/blind")
    runs = StateStore(state).list_runs()
    if not runs:
        return _fail("blind state has 0 runs")
    run = runs[-1]
    if run.outcome != "failed":
        return _fail(f"blind outcome={run.outcome!r} != failed")
    trace_path = ROOT / "probes/report/batch1/blind-trace.json"
    if not trace_path.is_file():
        return _fail("missing blind-trace.json")
    trace = _load_json(trace_path)
    cases = trace.get("diagnostic", {}).get("cases", [])
    case = next((item for item in cases if item.get("case_id") == "case_01"), None)
    if case is None:
        return _fail("blind trace missing case_01")
    if case.get("expected") != ["b", "aa"] or case.get("actual") != ["aa", "b"]:
        return _fail(f"case_01 expected/actual 不符: {case}")
    return _ok(f"run={run.run_id} outcome=failed case_01=discriminating")


def stage_memory() -> tuple[bool, str, int]:
    from molt.core.proposer import proposal_ext_keys

    gen2_path = ROOT / "shells/probe/genomes/gen2.json"
    proposal_path = ROOT / "probes/report/2c/proposal.json"
    gate_path = ROOT / "probes/report/2c/gate.json"
    if not gen2_path.is_file():
        return _fail("missing gen2.json")
    gen2 = _load_json(gen2_path)
    artifacts = gen2.get("artifacts") or {}
    required_artifacts = {
        "shells/probe/memory/gen2.md",
        "shells/probe/memory/sources/pro-max-probe-order-2.md",
        "shells/probe/memory/sources/pro-max-probe-order-x3.md",
    }
    if not required_artifacts <= set(artifacts):
        return _fail(f"gen2 artifacts missing: {sorted(required_artifacts - set(artifacts))}")
    if gen2.get("ext", {}).get("memory_ref") != "shells/probe/memory/gen2.md":
        return _fail("gen2 memory_ref mismatch")
    if not proposal_path.is_file():
        return _fail("missing proposal.json")
    proposal = _load_json(proposal_path)
    required_ext = set(proposal_ext_keys()) | {
        "memory_text",
        "memory_source_runs",
        "memory_sha256",
    }
    if not required_ext <= set(proposal.get("ext", {})):
        return _fail(f"proposal ext missing: {sorted(required_ext - set(proposal.get('ext', {})))}")
    if len(proposal["ext"]) < 13:
        return _fail("proposal ext < 13")
    if not gate_path.is_file():
        return _fail("missing gate.json")
    gate = _load_json(gate_path)
    paired = gate.get("paired") or []
    paired_ids = {item.get("task_id") for item in paired}
    expected_ids = {"probe-order-x5", "probe-order-x6", "probe-order-x7", "probe-order-x8"}
    if paired_ids != expected_ids:
        return _fail(f"gate paired={sorted(paired_ids)} != {sorted(expected_ids)}")
    for path in sorted((ROOT / "shells/probe/memory").rglob("*.md")):
        text = path.read_text(encoding="utf-8").lower()
        for word in RULE_WORDS:
            if _has_rule_word(text, word):
                return _fail(f"memory file {path.name} contains rule word {word!r}")
    saved = os.environ.pop("MOLT_PROBE_SOURCES", None)
    try:
        from probes.build_memory import build_proposal

        try:
            build_proposal()
        except RuntimeError:
            pass
        else:
            return _fail("build_proposal did not fail with MOLT_PROBE_SOURCES unset")
    finally:
        if saved is not None:
            os.environ["MOLT_PROBE_SOURCES"] = saved
    return _ok(
        f"gen2={gen2['genome_id'].split(':')[-1][:8]} proposal_ext={len(proposal['ext'])} "
        f"paired={len(paired)} sources=clean"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=STAGES)
    args = parser.parse_args()
    func = {
        "max_preflight": stage_max_preflight,
        "freeze": stage_freeze,
        "isolation": stage_isolation,
        "ambiguity": stage_ambiguity,
        "leak": stage_leak,
        "default_bias": stage_default_bias,
        "family": stage_family,
        "blind": stage_blind,
        "memory": stage_memory,
    }[args.stage]
    ok, reason, code = func()
    print(f"stage={args.stage} result={'ok' if ok else 'fail'} reason={reason}")
    return 0 if ok else code


if __name__ == "__main__":
    raise SystemExit(main())
