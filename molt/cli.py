"""molt 命令行入口。

B1 命令：``schema`` / ``genome`` / ``search`` / ``gate`` / ``adopt`` / ``revert`` /
``audit`` / ``doctor``。``export`` 与 ``demo`` 留给 B2。
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from molt.core.errors import MoltError, ValidationError
from molt.core.genome import compute_genome_id, verify_genome
from molt.core.policy import decide, task_side
from molt.core.store import StateStore
from molt.core.types import Budget, Genome, Policy, Scenario, ShellManifest
from molt.demo import run_demo_audit

FIXED_TS = "2026-01-01T00:00:00Z"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="molt",
        description="自进化 agent 引擎的外层编排器（core v1）",
    )
    parser.add_argument("--root", type=Path, default=None, help="仓库根，默认自动向上查找")
    sub = parser.add_subparsers(
        dest="command", metavar="{schema,genome,scenario,search,gate,adopt,revert,audit,doctor}"
    )

    p_schema = sub.add_parser("schema", help="schema 校验与文档生成")
    schema_sub = p_schema.add_subparsers(dest="schema_command")
    p_validate = schema_sub.add_parser("validate", help="校验 schema 与仓库内记录")
    p_validate.add_argument("--all", action="store_true", help="校验全部记录")
    p_docs = schema_sub.add_parser("docs", help="从 schema 生成 docs/protocol.md")
    p_docs.add_argument("--output", type=Path, default=None)

    p_genome = sub.add_parser("genome", help="genome 哈希 / 校验")
    genome_sub = p_genome.add_subparsers(dest="genome_command")
    for name in ("hash", "verify", "validate"):
        cmd = genome_sub.add_parser(name)
        cmd.add_argument("path", type=Path)

    p_search = sub.add_parser("search", help="跑一轮搜索集（B1 仅 fixture）")
    p_search.add_argument("--scenario", default="demo")
    p_search.add_argument("--genome", type=Path, default=Path("shells/code-repair/genomes/v0.json"))
    p_search.add_argument("--state", type=Path, default=Path("state/search"))
    p_search.add_argument("--seed-control", choices=("none", "fixed"), default="none")

    p_gate = sub.add_parser("gate", help="跑门禁夹具 G1–G8")
    p_gate.add_argument("--fixture", type=Path, required=True)

    p_scenario = sub.add_parser("scenario", help="场景与 genome 一致性校验")
    scenario_sub = p_scenario.add_subparsers(dest="scenario_command")
    p_scenario_validate = scenario_sub.add_parser("validate")
    p_scenario_validate.add_argument(
        "--scenario", type=Path, default=Path("scenarios/demo/scenario.json")
    )

    p_adopt = sub.add_parser("adopt", help="单事务采纳一个 genome")
    p_adopt.add_argument("--state", type=Path, default=Path("state"))
    p_adopt.add_argument("--genome", type=Path, required=True)
    p_adopt.add_argument("--manifest", type=Path, default=Path("shells/code-repair/manifest.json"))
    p_adopt.add_argument("--result", action="append", default=[])
    p_adopt.add_argument("--note", default="adopt")
    p_adopt.add_argument("--at", default=None)

    p_revert = sub.add_parser("revert", help="切回某个归档代际")
    p_revert.add_argument("--state", type=Path, default=Path("state"))
    p_revert.add_argument("--to", required=True, dest="archive_id")
    p_revert.add_argument("--at", default=None)

    p_audit = sub.add_parser("audit", help="从 run/result/ledger 重算")
    p_audit.add_argument("--scenario", default="demo")
    p_audit.add_argument("--state", type=Path, default=None)

    p_doctor = sub.add_parser("doctor", help="依赖边界 / 事务恢复")
    p_doctor.add_argument("--deps", action="store_true", help="检查 core 依赖边界")
    p_doctor.add_argument("--recover", action="store_true", help="恢复未完成事务")
    p_doctor.add_argument("--state", type=Path, default=Path("state"))

    return parser


def _repo_root(args: argparse.Namespace) -> Path:
    if args.root:
        return args.root.resolve()
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "schemas" / "v1").is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"期望 object: {path}")
    return data


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def cmd_schema(args: argparse.Namespace) -> int:
    from molt import schema

    root = _repo_root(args)
    command = args.schema_command
    if command == "validate":
        if not args.all:
            print("schema validate 需要 --all")
            return 2
        validated = schema.validate_all(root)
        for path, kind in validated:
            print(f"ok {kind} {path.relative_to(root)}")
        print(f"schema validate --all: {len(validated)} 条记录通过")
        return 0
    if command == "docs":
        if args.output:
            # --output 只写目标文件，不碰仓库内 docs/protocol.md。
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(schema.render_protocol_docs(root), encoding="utf-8")
            path = args.output
        else:
            path = schema.write_protocol_docs(root)
        print(f"wrote {path}")
        return 0
    print("用法: molt schema {validate --all|docs}")
    return 2


# ---------------------------------------------------------------------------
# genome
# ---------------------------------------------------------------------------


def _validated_genome(path: Path, root: Path) -> Genome:
    from molt import schema

    data = _load_json(path)
    return schema.validate_record("genome", data, root)


def cmd_genome(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    command = args.genome_command
    if command is None:
        print("用法: molt genome {hash|verify|validate} <path>")
        return 2
    if command == "hash":
        data = _load_json(args.path)
        print(compute_genome_id(data))
        return 0
    if command == "validate":
        genome = _validated_genome(args.path, root)
        print(f"genome ok {genome.genome_id}")
        return 0
    genome = _validated_genome(args.path, root)
    errors = verify_genome(genome, root)
    if errors:
        for error in errors:
            print(f"FAIL {error}", file=sys.stderr)
        return 1
    print(f"genome verify ok {genome.genome_id}")
    return 0


# ---------------------------------------------------------------------------
# search（B1：fixture runtime）
# ---------------------------------------------------------------------------


def cmd_search(args: argparse.Namespace) -> int:
    if args.scenario != "demo":
        print(f"B1 只支持 --scenario demo；{args.scenario!r} 属于 B2", file=sys.stderr)
        return 2
    root = _repo_root(args)
    genome = _validated_genome(args.genome, root)
    scenario = Scenario.from_dict(_load_json(root / "scenarios/demo/scenario.json"))
    store = StateStore(args.state)
    from molt.demo import build_engine

    engine = build_engine(root, scenario, store, args.state / "work", args.seed_control)
    tasks = [engine.task_provider.load_task(task_id) for task_id in scenario.task_sets["search"]]
    runs = engine.run_suite(genome, tasks, scope="search")
    result = engine.evaluate_search(
        genome,
        runs,
        scenario.task_sets["search"],
        result_id=f"result-search-{genome.genome_id.split(':')[-1][:8]}",
    )
    print(
        f"search ok runs={len(runs)} result={result.result_id} solved={result.scores['search'].solved}"
    )
    return 0


# ---------------------------------------------------------------------------
# gate
# ---------------------------------------------------------------------------


def _side(case: dict[str, Any], root: Path, scenario_version: str):
    tasks = []
    for item in case["tasks"]:
        tasks.append(
            {
                "task_id": item["task_id"],
                "passed": item["passed"],
                "cost_usd": item["cost_usd"],
            }
        )
    return task_side(
        case["genome_id"],
        tasks,
        Budget.from_dict(case["limits"], f"{case.get('genome_id', 'side')}.limits"),
        float(case["invalid_ratio"]),
        scenario_version=scenario_version,
        overhead_spend_usd=float(case.get("overhead_spend_usd", 0.0)),
    )


def _gate_cases(path: Path) -> list[dict[str, Any]]:
    files = sorted(path.glob("*.json"))
    if not files:
        raise ValueError(f"fixture 目录没有 JSON: {path}")
    cases = []
    for file in files:
        data = _load_json(file)
        if "cases" in data:
            for index, item in enumerate(data["cases"]):
                case = dict(item)
                case.setdefault("name", f"{data.get('id', file.stem)}-{index + 1}")
                case.setdefault("policy", data["policy"])
                case.setdefault("scenario_version", data["scenario_version"])
                cases.append(case)
        else:
            data.setdefault("name", data.get("id", file.stem))
            cases.append(data)
    return cases


def _compare(expected: dict[str, Any], actual: dict[str, Any], prefix: str = "") -> list[str]:
    errors = []
    for key, value in expected.items():
        if key not in actual:
            errors.append(f"{prefix}{key}: 缺失（actual 无此键）")
            continue
        got = actual[key]
        if isinstance(value, dict) and isinstance(got, dict):
            errors.extend(_compare(value, got, f"{prefix}{key}."))
        elif got != value:
            errors.append(f"{prefix}{key}: 期望 {value!r} 实际 {got!r}")
    return errors


def _paired_errors(case: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """夹具逐题断言配对表与 total；不依赖 expected 是否写了这些键。"""
    errors: list[str] = []
    baseline_tasks = case["baseline"]["tasks"]
    candidate_tasks = case["candidate"]["tasks"]
    if len(baseline_tasks) != len(candidate_tasks):
        return [
            f"夹具 baseline/candidate 题数不一致：{len(baseline_tasks)} vs {len(candidate_tasks)}"
        ]
    expected_rows = [
        {
            "task_id": base["task_id"],
            "baseline_passed": bool(base["passed"]),
            "candidate_passed": bool(cand["passed"]),
            "baseline_cost_usd": float(base["cost_usd"]),
            "candidate_cost_usd": float(cand["cost_usd"]),
        }
        for base, cand in zip(baseline_tasks, candidate_tasks, strict=False)
    ]
    rows = actual.get("paired")
    if not isinstance(rows, list) or len(rows) != len(expected_rows):
        return [f"paired: 期望 {len(expected_rows)} 行，实际 {rows!r}"]
    for index, (expected, row) in enumerate(zip(expected_rows, rows, strict=True)):
        errors.extend(_compare(expected, row, f"paired[{index}]."))
    for side in ("baseline", "candidate"):
        expected_total = len(case[side]["tasks"])
        expected_solved = sum(1 for task in case[side]["tasks"] if task["passed"])
        scores = actual.get("scores", {}).get(side, {})
        for key, value in (("total", expected_total), ("solved", expected_solved)):
            if scores.get(key) != value:
                errors.append(f"scores.{side}.{key}: 期望 {value!r} 实际 {scores.get(key)!r}")
    return errors


def cmd_gate(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    failures = 0
    for case in _gate_cases(args.fixture):
        policy = Policy.from_dict(case["policy"])
        baseline = _side(case["baseline"], root, case["scenario_version"])
        candidate = _side(case["candidate"], root, case["scenario_version"])
        decision = decide(
            policy,
            baseline,
            candidate,
            decision_id=f"gate-{case['name']}",
            scenario_version=case["scenario_version"],
            shell_version=case.get("shell_version", "1.0.0"),
            verifier_hash=case.get("verifier_hash", "a" * 64),
            created_at=FIXED_TS,
        )
        expected = dict(case.get("expected", {}))
        expected.setdefault("verdict", None)
        expected.setdefault("reason", None)
        expected = {key: value for key, value in expected.items() if value is not None}
        actual = decision.to_dict()
        errors = _compare(expected, actual)
        errors.extend(_paired_errors(case, actual))
        baseline_score = decision.scores["baseline"]
        candidate_score = decision.scores["candidate"]
        print(
            f"{case['name']} verdict={decision.verdict} reason={decision.reason} "
            f"solved={baseline_score['solved']}/{baseline_score['total']}"
            f"->{candidate_score['solved']}/{candidate_score['total']} "
            f"cps={baseline_score['cost_per_solved_usd']}"
            f"->{candidate_score['cost_per_solved_usd']} "
            f"limits_identical={decision.limits_identical} "
            f"budget=exec:{decision.budget_check['execution_exceeded']},"
            f"over:{decision.budget_check['overhead_exceeded']}"
        )
        for error in errors:
            failures += 1
            print(f"  FAIL {error}", file=sys.stderr)
    if failures:
        print(f"gate: {failures} 处不一致", file=sys.stderr)
        return 1
    print("gate: G1–G8 全部一致")
    return 0


# ---------------------------------------------------------------------------
# scenario
# ---------------------------------------------------------------------------


def _task_index(root: Path) -> dict[str, Any]:
    from molt.core.types import Task

    index: dict[str, Any] = {}
    for path in sorted(root.glob("bench/**/task.json")):
        task = Task.from_dict(_load_json(path))
        index[task.task_id] = task
    return index


def cmd_scenario(args: argparse.Namespace) -> int:
    from molt import schema

    root = _repo_root(args)
    if getattr(args, "scenario_command", None) != "validate":
        print("用法: molt scenario validate [--scenario PATH]")
        return 2
    scenario_path = args.scenario
    if not scenario_path.is_absolute():
        scenario_path = root / scenario_path
    scenario = schema.validate_record("scenario", _load_json(scenario_path), root)

    shell_dir = root / "shells" / scenario.shell_id
    manifest_path = shell_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"FAIL shell manifest 不存在: {manifest_path}", file=sys.stderr)
        return 1
    manifest = schema.validate_record("shell_manifest", _load_json(manifest_path), root)
    if manifest.shell_version != scenario.shell_version:
        print(
            f"FAIL scenario.shell_version={scenario.shell_version} "
            f"与 manifest.shell_version={manifest.shell_version} 不一致",
            file=sys.stderr,
        )
        return 1

    failures: list[str] = []
    matching = []
    for genome_path in sorted((shell_dir / "genomes").glob("*.json")):
        genome = schema.validate_record("genome", _load_json(genome_path), root)
        if genome.scenario_version != scenario.scenario_version:
            continue
        matching.append(genome)
        for error in verify_genome(genome, root):
            failures.append(f"{genome_path.relative_to(root)}: {error}")
    if not matching:
        failures.append(f"scenario_version={scenario.scenario_version} 没有任何 genome")

    tasks = _task_index(root)
    for scope in ("search", "report"):
        for task_id in scenario.task_sets[scope]:
            task = tasks.get(task_id)
            if task is None:
                failures.append(f"task 不存在: {task_id}")
                continue
            if task.scope != scope:
                failures.append(f"task {task_id} scope={task.scope} 与 {scope} 不一致")
            if task.scenario_version != scenario.scenario_version:
                failures.append(
                    f"task {task_id} scenario_version={task.scenario_version} "
                    f"与 {scenario.scenario_version} 不一致"
                )
            if task.shell_id != scenario.shell_id:
                failures.append(f"task {task_id} shell_id 与 scenario 不一致")

    # 题面内容也必须进哈希：只改 metadata 或题面都会让 validate 失败。
    from molt.bench import compute_bench_hash

    referenced = [*scenario.task_sets["search"], *scenario.task_sets["report"]]
    try:
        actual_hash = compute_bench_hash(root, referenced)
    except (ValidationError, FileNotFoundError) as exc:
        failures.append(f"bench_hash 无法重算: {exc}")
    else:
        if actual_hash != scenario.bench_hash:
            failures.append(
                f"bench_hash 不一致: declared={scenario.bench_hash} recomputed={actual_hash}"
            )

    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print(
        f"scenario validate ok {scenario.scenario_id} "
        f"genomes={len(matching)} tasks={sum(len(v) for v in scenario.task_sets.values())}"
    )
    return 0


# ---------------------------------------------------------------------------
# adopt / revert
# ---------------------------------------------------------------------------


def cmd_adopt(args: argparse.Namespace) -> int:
    from molt.core.events import utc_now

    root = _repo_root(args)
    genome = _validated_genome(args.genome, root)
    manifest = ShellManifest.from_dict(_load_json(root / args.manifest))
    if genome.shell_id != manifest.shell_id:
        print("genome 与 manifest 的 shell_id 不一致", file=sys.stderr)
        return 2
    roots = {name: root / name for name in manifest.artifact_roots}
    store = StateStore(args.state)
    archive = store.adopt(
        genome,
        roots,
        result_refs=list(args.result),
        note=args.note,
        adopted_at=args.at or utc_now(),
    )
    print(f"adopt ok archive={archive.archive_id} generation={archive.generation}")
    return 0


def cmd_revert(args: argparse.Namespace) -> int:
    from molt.core.events import utc_now

    store = StateStore(args.state)
    archive = store.revert(args.archive_id, adopted_at=args.at or utc_now())
    print(f"revert ok archive={archive.archive_id} generation={archive.generation}")
    return 0


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


def cmd_audit(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    if args.scenario != "demo":
        print(f"B1 只支持 --scenario demo；{args.scenario!r} 属于 B2", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="molt-audit-demo-") as tmp:
        if args.state is not None:
            work = Path(args.state)
            work.mkdir(parents=True, exist_ok=True)
            summary, report = run_demo_audit(root, work)
        else:
            summary, report = run_demo_audit(root, Path(tmp))
    print(
        f"demo decision={summary['decision']} reason={summary['reason']} "
        f"candidate={summary['candidate_genome']}"
    )
    print(
        f"audit ok runs={report['runs']} results={report['results']} "
        f"decisions={report['decisions']} events={report['events']} "
        f"report_events={report['report_events']}"
    )
    return 0


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def core_import_violations(root: Path) -> list[str]:
    """``molt/core/**`` 只允许 stdlib 与 ``molt.core.*``。"""
    allowed = set(sys.stdlib_module_names) | {"__future__"}
    violations: list[str] = []
    for path in sorted((root / "molt" / "core").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module not in allowed and module != "molt":
                        violations.append(f"{path}: import {alias.name}")
                    elif alias.name == "molt":
                        violations.append(f"{path}: import molt（只允许 molt.core.*）")
                    elif alias.name.startswith("molt.") and not alias.name.startswith("molt.core"):
                        violations.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                module = node.module or ""
                top = module.split(".")[0]
                if module == "molt" or module.startswith("molt."):
                    if module != "molt.core" and not module.startswith("molt.core."):
                        violations.append(f"{path}: from {module} import ...")
                elif top not in allowed:
                    violations.append(f"{path}: from {module} import ...")
    return violations


def cmd_doctor(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    if args.recover:
        store = StateStore(args.state)
        actions = store.recover()
        print("recover: " + ("; ".join(actions) if actions else "nothing to do"))
        return 0
    violations = core_import_violations(root)
    if violations:
        for violation in violations:
            print(f"FAIL {violation}", file=sys.stderr)
        return 1
    print("doctor --deps: core 只依赖 stdlib 与 molt.core.*")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

HANDLERS = {
    "schema": cmd_schema,
    "genome": cmd_genome,
    "scenario": cmd_scenario,
    "search": cmd_search,
    "gate": cmd_gate,
    "adopt": cmd_adopt,
    "revert": cmd_revert,
    "audit": cmd_audit,
    "doctor": cmd_doctor,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return HANDLERS[args.command](args)
    except MoltError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 1
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
