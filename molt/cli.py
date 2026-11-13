"""molt 命令行入口。

命令：``schema`` / ``genome`` / ``scenario`` / ``search`` / ``propose`` / ``gate`` /
``adopt`` / ``revert`` / ``audit`` / ``calibrate`` / ``doctor`` / ``export`` / ``demo``。
``search`` / ``propose`` / ``audit`` / ``export`` / ``demo`` 的 ``--scenario`` 接受任意
``scenarios/<id>/scenario.json``（``--shell`` 为同一参数的别名，可按 shell_id 反查）；
``--runtime`` 支持 ``fixture`` / ``pi`` / ``deterministic``。
``export`` 只导出 ``web/data`` 数据，不含页面；``demo`` 是 B2-D7 的现场闭环；
``serve`` 是 loopback-only 的本地 agent API（默认 mock provider，端口 0）。
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
from molt.core.types import (
    DIMENSIONS,
    SCOPES,
    SWITCH_KEYS,
    Budget,
    Genome,
    Policy,
    Scenario,
    ShellManifest,
)
from molt.demo import allowed_runtimes, default_genome_path, resolve_runtime, run_scenario_audit

FIXED_TS = "2026-01-01T00:00:00Z"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="molt",
        description="自进化 agent 引擎的外层编排器（core v1）",
    )
    parser.add_argument("--root", type=Path, default=None, help="仓库根，默认自动向上查找")
    sub = parser.add_subparsers(
        dest="command",
        metavar="{schema,genome,scenario,search,propose,gate,adopt,revert,audit,calibrate,doctor,export,demo,serve}",
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

    p_search = sub.add_parser("search", help="跑一轮搜索集")
    p_search.add_argument(
        "--scenario",
        "--shell",
        dest="scenario",
        default="demo",
        help="scenario id；也接受 shell_id（按 scenarios/*/scenario.json 的 shell_id 解析）",
    )
    p_search.add_argument(
        "--genome", type=Path, default=None, help="缺省取该 scenario 的 v0 genome"
    )
    p_search.add_argument("--state", type=Path, default=Path("state/search"))
    p_search.add_argument("--seed-control", choices=("none", "fixed"), default="none")
    p_search.add_argument("--runtime", choices=("fixture", "pi", "deterministic"), default=None)
    p_search.add_argument("--task", action="append", default=[], help="只跑指定 task_id")

    p_propose = sub.add_parser("propose", help="从 SearchHistory 生成 Proposal（grid/llm）")
    p_propose.add_argument(
        "--scenario",
        "--shell",
        dest="scenario",
        default="demo",
        help="scenario id；也接受 shell_id",
    )
    p_propose.add_argument(
        "--genome", type=Path, default=None, help="缺省取该 scenario 的 v0 genome"
    )
    p_propose.add_argument("--state", type=Path, default=Path("state/propose"))
    p_propose.add_argument("--proposer", choices=("grid", "llm"), default="grid")
    p_propose.add_argument("--offline", action="store_true", help="强制 grid，不构造网络 transport")
    p_propose.add_argument("--runtime", choices=("fixture", "pi", "deterministic"), default=None)

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
    p_audit.add_argument(
        "--scenario",
        "--shell",
        dest="scenario",
        default="demo",
        help="scenario id；也接受 shell_id",
    )
    p_audit.add_argument("--state", type=Path, default=None)
    p_audit.add_argument("--runtime", choices=("fixture", "deterministic"), default=None)

    p_calibrate = sub.add_parser(
        "calibrate", help="fixture baseline 校准：search 首轮通过数 >= min-pass"
    )
    p_calibrate.add_argument("--scenario", default="demo", help="scenario id（默认 demo）")
    p_calibrate.add_argument("--min-pass", type=int, default=3, help="最少通过题数（默认 3）")
    p_calibrate.add_argument(
        "--state", type=Path, default=None, help="state 根；缺省用临时目录，不写仓库"
    )
    p_calibrate.add_argument(
        "--genome", type=Path, default=None, help="缺省取该 scenario 的 v0 genome"
    )

    p_doctor = sub.add_parser("doctor", help="依赖边界 / 事务恢复 / 仓库协作契约")
    p_doctor.add_argument("--deps", action="store_true", help="检查 core 依赖边界")
    p_doctor.add_argument("--recover", action="store_true", help="恢复未完成事务")
    p_doctor.add_argument(
        "--repo-policy",
        action="store_true",
        help="只读检查协作文件、冻结面与契约域；缺失退出 1",
    )
    p_doctor.add_argument("--state", type=Path, default=Path("state"))

    p_export = sub.add_parser("export", help="导出 web/data（静态 JSON + bundle.js，无页面）")
    p_export.add_argument(
        "--scenario",
        "--shell",
        dest="scenario",
        default="demo",
        help="scenario id；也接受 shell_id",
    )
    p_export.add_argument(
        "--state", type=Path, default=None, help="state 根；缺省用 ./state 或临时 demo state"
    )
    p_export.add_argument("--out", type=Path, default=Path("web/data"))
    p_export.add_argument(
        "--allow-empty", action="store_true", help="允许 0 run 的 state，生成空数据面"
    )

    p_demo = sub.add_parser(
        "demo",
        help="B2-D7 现场演示：1 live 候选 + 1 replay 基线 + 10 题扫描回放（180s 硬上限）",
    )
    p_demo.add_argument(
        "--scenario",
        "--shell",
        dest="scenario",
        default="demo",
        help="scenario id；也接受 shell_id",
    )
    p_demo.add_argument(
        "--mode",
        choices=("live", "replay"),
        default="live",
        help="live=真实 Pi 候选 + fixture 兜底；replay=全 fixture/replay（CI 用）",
    )
    p_demo.add_argument("--max-wall", type=float, default=180.0, help="端到端硬上限（秒）")
    p_demo.add_argument(
        "--baseline-state",
        type=Path,
        default=None,
        help="已录 baseline state；缺省录制/复用 <state>-baseline",
    )
    p_demo.add_argument(
        "--candidate-state",
        type=Path,
        default=None,
        help="已录候选 state（10 题扫描）；给出时其余 9 题复制为 replay，不再跑 fixture",
    )
    p_demo.add_argument("--state", type=Path, default=Path("state/demo"))
    p_demo.add_argument("--out", type=Path, default=Path("web/data"))
    p_demo.add_argument("--model", default="deepseek/deepseek-flash", help="live 候选的 Pi 模型")
    p_demo.add_argument("--pi-executable", default="pi")

    p_serve = sub.add_parser(
        "serve",
        help="本地 agent API（loopback only；默认 mock provider，端口 0）",
    )
    p_serve.add_argument("--host", default="127.0.0.1", help="只允许 127.0.0.1 / localhost")
    p_serve.add_argument("--port", type=int, default=0, help="监听端口；默认 0（临时端口）")
    p_serve.add_argument("--state", type=Path, default=Path("state"), help="state 根（token 默认落这里）")
    p_serve.add_argument(
        "--upper-state",
        type=Path,
        default=None,
        help="上层图谱 state（必须在仓库外）；缺省沿用 --state，仓库内则禁用 /api/upper",
    )
    p_serve.add_argument("--token-file", type=Path, default=None, help="token 输出路径；默认 <state>/token")
    p_serve.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help="精确追加允许的 Origin（可重复；禁止 *）",
    )
    p_serve.add_argument(
        "--provider",
        choices=("mock", "openai-compatible"),
        default="mock",
        help="chat provider；openai-compatible 需要 MOLT_CHAT_MODEL",
    )
    p_serve.add_argument(
        "--probe-index",
        type=Path,
        default=None,
        help="只读加载已导出的 probe index；缺省从 manifest 现场构建",
    )
    p_serve.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="只读任务结果数据目录；默认 <repo>/web/data（不写入）",
    )
    p_serve.add_argument(
        "--lab-root",
        type=Path,
        default=Path("/tmp/molt-lab"),
        help="隔离执行 workspace 的临时根目录（默认 /tmp/molt-lab）",
    )

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


def _scenario_path(root: Path, name: str) -> Path | None:
    """先按 scenario 目录名找，再按 ``shell_id`` 反查（``--shell`` 别名）。"""
    direct = root / "scenarios" / name / "scenario.json"
    if direct.is_file():
        return direct
    matches: list[Path] = []
    for path in sorted(root.glob("scenarios/*/scenario.json")):
        if _load_json(path).get("shell_id") == name:
            matches.append(path)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"shell_id={name!r} 对应多个 scenario: {[str(p) for p in matches]}")
    return None


def _load_scenario(root: Path, name: str) -> Scenario:
    path = _scenario_path(root, name)
    if path is None:
        raise FileNotFoundError(f"scenario 不存在: {name}")
    return Scenario.from_dict(_load_json(path))


def _genome_for_scenario(args: argparse.Namespace, root: Path, scenario: Scenario) -> Genome:
    """解析 genome 并检查 shell_id / scenario_version 与 scenario 一致。"""
    path = args.genome or default_genome_path(root, scenario)
    genome = _validated_genome(path, root)
    if genome.shell_id != scenario.shell_id:
        raise ValueError(
            f"genome.shell_id={genome.shell_id!r} 与 scenario.shell_id={scenario.shell_id!r} 不一致"
        )
    if genome.scenario_version != scenario.scenario_version:
        raise ValueError(
            f"genome.scenario_version={genome.scenario_version!r} 与 "
            f"scenario.scenario_version={scenario.scenario_version!r} 不一致"
        )
    return genome


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
    root = _repo_root(args)
    try:
        scenario = _load_scenario(root, args.scenario)
        genome = _genome_for_scenario(args, root, scenario)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        runtime = resolve_runtime(scenario, args.runtime)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    store = StateStore(args.state)
    from molt.demo import build_engine

    engine = build_engine(
        root,
        scenario,
        store,
        args.state / "work",
        args.seed_control,
        runtime=runtime,
    )
    task_ids = scenario.task_sets["search"]
    if args.task:
        unknown = sorted(set(args.task) - set(task_ids))
        if unknown:
            print(f"未知 task: {unknown}", file=sys.stderr)
            return 2
        task_ids = [task_id for task_id in task_ids if task_id in set(args.task)]
    tasks = [engine.task_provider.load_task(task_id) for task_id in task_ids]
    runs = engine.run_suite(genome, tasks, scope="search")
    result = engine.evaluate_search(
        genome,
        runs,
        task_ids,
        result_id=f"result-search-{genome.genome_id.split(':')[-1][:8]}",
    )
    print(
        f"search ok runs={len(runs)} result={result.result_id} solved={result.scores['search'].solved}"
    )
    return 0


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------


def _shell_domains(root: Path, scenario: Scenario) -> dict[str, list[str]] | None:
    path = root / "shells" / scenario.shell_id / "switches.schema.json"
    if not path.is_file():
        return None
    data = _load_json(path)
    dimensions = data.get("dimensions")
    if not isinstance(dimensions, dict):
        return None
    return {
        name: list(spec["values"])
        for name, spec in dimensions.items()
        if isinstance(spec, dict) and "values" in spec
    }


def cmd_propose(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    try:
        scenario = _load_scenario(root, args.scenario)
        genome = _genome_for_scenario(args, root, scenario)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    store = StateStore(args.state)
    store.ensure()
    domains = _shell_domains(root, scenario)

    llm_proposer = None
    if args.proposer == "llm" and not args.offline:
        from molt.adapters.llm_proposer import LLMProposer
        from molt.core.ledger import Ledger

        llm_proposer = LLMProposer(
            ledger=Ledger(store.root),
            max_cost_usd=scenario.policy.budget.search_overhead_budget_usd,
            domains=domains,
        ).propose

    from molt.demo import build_engine

    engine = build_engine(
        root,
        scenario,
        store,
        args.state / "work",
        domains=domains,
        llm_proposer=llm_proposer,
        runtime=args.runtime,
    )
    try:
        store.read_genome(genome.genome_id)
    except ValidationError:
        store.write_genome(genome)

    task_ids = list(scenario.task_sets["search"])
    by_task = {}
    for run in store.list_runs():
        if (
            run.genome_id != genome.genome_id
            or run.scope != "search"
            or run.scenario_version != scenario.scenario_version
            or run.attempt != 1
            or run.task_id not in task_ids
        ):
            continue
        by_task.setdefault(run.task_id, run)

    missing = [task_id for task_id in task_ids if task_id not in by_task]
    if missing:
        tasks = [engine.task_provider.load_task(task_id) for task_id in missing]
        for run in engine.run_suite(genome, tasks, scope="search"):
            by_task.setdefault(run.task_id, run)
    runs = [by_task[task_id] for task_id in task_ids if task_id in by_task]
    if not runs:
        print("没有可用的 search run，无法构造 SearchHistory", file=sys.stderr)
        return 1

    history = engine.search_history(genome, runs)
    proposal = engine.propose(genome, history)
    engine.record_proposal(proposal)
    ext = proposal.ext
    fallback = ext.get("fallback", {})
    print(
        f"propose ok proposal={proposal.proposal_id} source={ext.get('source')} "
        f"fallback={fallback.get('used')} reason={fallback.get('reason')} "
        f"cost_usd={proposal.cost_usd}"
    )
    return 0


# ---------------------------------------------------------------------------
# gate
# ---------------------------------------------------------------------------


SIDE_KEYS: tuple[str, ...] = (
    "genome_id",
    "limits",
    "invalid_ratio",
    "overhead_spend_usd",
    "tasks",
)


def _side(case: dict[str, Any], side: str, root: Path, scenario_version: str):
    payload = case[side]
    unknown = sorted(set(payload) - set(SIDE_KEYS))
    if unknown:
        raise ValueError(f"gate 夹具 {side} 含未知键: {unknown}")
    # 允许夹具逐侧覆盖版本（baseline_scenario_version / candidate_scenario_version），
    # 用于验证跨版本比较被拒绝；缺省回落到 case 级版本。
    version = str(case.get(f"{side}_scenario_version", scenario_version))
    tasks = []
    for item in payload["tasks"]:
        tasks.append(
            {
                "task_id": item["task_id"],
                "passed": item["passed"],
                "cost_usd": item["cost_usd"],
            }
        )
    return task_side(
        payload["genome_id"],
        tasks,
        Budget.from_dict(payload["limits"], f"{payload.get('genome_id', 'side')}.limits"),
        float(payload["invalid_ratio"]),
        scenario_version=version,
        overhead_spend_usd=float(payload.get("overhead_spend_usd", 0.0)),
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
        baseline = _side(case, "baseline", root, case["scenario_version"])
        candidate = _side(case, "candidate", root, case["scenario_version"])
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


def _validate_shell_contract(
    root: Path, scenario: Scenario, manifest: ShellManifest, genomes: Sequence[Genome]
) -> list[str]:
    """shell 侧契约：entrypoints / switches.schema / genome 值域。"""
    failures: list[str] = []
    for role, rel in manifest.entrypoints.items():
        if not (root / rel).is_file():
            failures.append(f"entrypoint {role} 不存在: {rel}")

    schema_rel = f"shells/{scenario.shell_id}/switches.schema.json"
    schema_path = root / schema_rel
    domains: dict[str, list[str]] = {}
    if not schema_path.is_file():
        failures.append(f"switches.schema.json 不存在: {schema_rel}")
    else:
        data = _load_json(schema_path)
        if data.get("shell_id") != scenario.shell_id:
            failures.append(f"{schema_rel}: shell_id 与 scenario 不一致")
        if data.get("locked") is not True:
            failures.append(f"{schema_rel}: locked 必须为 true")
        dimensions = data.get("dimensions")
        if not isinstance(dimensions, dict) or set(dimensions) != set(SWITCH_KEYS):
            failures.append(f"{schema_rel}: dimensions 必须且只能是 {list(SWITCH_KEYS)}")
        else:
            combinations = 1
            for dim in SWITCH_KEYS:
                spec = dimensions[dim]
                values = spec.get("values") if isinstance(spec, dict) else None
                if not isinstance(values, list) or not values:
                    failures.append(f"{schema_rel}: dimensions.{dim}.values 必须是非空数组")
                    continue
                if not all(isinstance(value, str) for value in values):
                    failures.append(f"{schema_rel}: dimensions.{dim}.values 必须是字符串")
                    continue
                outside = sorted(set(values) - set(DIMENSIONS[dim]))
                if outside:
                    failures.append(f"{schema_rel}: dimensions.{dim} 含 v1 未定义值 {outside}")
                declared = manifest.capabilities["switch_domains"][dim]
                if not set(values) <= set(declared):
                    failures.append(f"{schema_rel}: dimensions.{dim} 超出 manifest 声明的候选域")
                domains[dim] = list(values)
                combinations *= len(values)
            if data.get("combinations") != combinations:
                failures.append(
                    f"{schema_rel}: combinations={data.get('combinations')!r} "
                    f"与维度笛卡尔积 {combinations} 不一致"
                )

    for genome in genomes:
        for dim, values in domains.items():
            value = getattr(genome.switches, dim)
            if value not in values:
                failures.append(
                    f"genome {genome.genome_id} 的 switches.{dim}={value!r} 越出 shell 候选域"
                )
    return failures


def _matching_genomes(root: Path, scenario: Scenario, failures: list[str]) -> list[Genome]:
    from molt import schema

    shell_dir = root / "shells" / scenario.shell_id
    matching: list[Genome] = []
    for genome_path in sorted((shell_dir / "genomes").glob("*.json")):
        genome = schema.validate_record("genome", _load_json(genome_path), root)
        if genome.scenario_version != scenario.scenario_version:
            continue
        matching.append(genome)
        for error in verify_genome(genome, root):
            failures.append(f"{genome_path.relative_to(root)}: {error}")
    if not matching:
        failures.append(f"scenario_version={scenario.scenario_version} 没有任何 genome")
    return matching


def _validate_scenario_ext(scenario: Scenario) -> list[str]:
    """scenario.ext 里的显式契约（demo 用；未声明则不强制，避免误伤 svc-config）。

    - ``expected_task_counts = {"search": 10, "report": 5}``：题集规模硬约束；
    - ``require_positive_budget = true``：两个 USD cap 都必须 > 0。
    """
    failures: list[str] = []
    expected = scenario.ext.get("expected_task_counts")
    if expected is not None:
        if not isinstance(expected, dict):
            failures.append("ext.expected_task_counts 必须是 object")
        else:
            unknown = sorted(set(expected) - set(SCOPES))
            if unknown:
                failures.append(f"ext.expected_task_counts 含未知 scope: {unknown}")
            for scope in SCOPES:
                if scope not in expected:
                    failures.append(f"ext.expected_task_counts 缺少 {scope}")
                    continue
                want = expected[scope]
                if isinstance(want, bool) or not isinstance(want, int) or want < 0:
                    failures.append(f"ext.expected_task_counts.{scope} 必须是非负整数")
                    continue
                actual = len(scenario.task_sets[scope])
                if actual != want:
                    failures.append(
                        f"ext.expected_task_counts.{scope}: 期望 {want} 题，实际 {actual} 题"
                    )
    flag = scenario.ext.get("require_positive_budget")
    if flag is not None and not isinstance(flag, bool):
        failures.append("ext.require_positive_budget 必须是布尔值")
    elif flag:
        for name in ("execution_budget_usd", "search_overhead_budget_usd"):
            value = getattr(scenario.policy.budget, name)
            if not value > 0:
                failures.append(
                    f"policy.budget.{name}: 期望 > 0（ext.require_positive_budget=true），"
                    f"实际 {value}"
                )
    try:
        allowed_runtimes(scenario)
    except ValueError as exc:
        failures.append(f"ext.allowed_runtimes 非法: {exc}")
    return failures


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
    matching = _matching_genomes(root, scenario, failures)
    failures.extend(_validate_shell_contract(root, scenario, manifest, matching))
    failures.extend(_validate_scenario_ext(scenario))

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
    try:
        scenario = _load_scenario(root, args.scenario)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix=f"molt-audit-{scenario.scenario_id}-") as tmp:
        work = Path(tmp) if args.state is None else Path(args.state)
        work.mkdir(parents=True, exist_ok=True)
        summary, report = run_scenario_audit(root, work, scenario, runtime=args.runtime)
    print(
        f"{scenario.scenario_id} decision={summary['decision']} reason={summary['reason']} "
        f"candidate={summary['candidate_genome']}"
    )
    print(
        f"audit ok runs={report['runs']} results={report['results']} "
        f"decisions={report['decisions']} events={report['events']} "
        f"report_events={report['report_events']}"
    )
    return 0


# ---------------------------------------------------------------------------
# calibrate（D4：fixture baseline 校准）
# ---------------------------------------------------------------------------


def cmd_calibrate(args: argparse.Namespace) -> int:
    """用 fixture runtime 跑 search 题集，按首轮通过数判断 baseline 是否达标。

    强制 ``runtime="fixture"``：不调用 Pi、不产生真实模型费用；``--state`` 缺省
    用临时目录，因此默认不写仓库。
    """
    root = _repo_root(args)
    if args.min_pass < 0:
        print("--min-pass 必须 >= 0", file=sys.stderr)
        return 2
    try:
        scenario = _load_scenario(root, args.scenario)
        genome = _genome_for_scenario(args, root, scenario)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    task_ids = list(scenario.task_sets["search"])
    if not task_ids:
        print("search 题集为空，无法校准", file=sys.stderr)
        return 2

    from molt.core.metrics import metrics_from_runs
    from molt.demo import build_engine

    with tempfile.TemporaryDirectory(prefix=f"molt-calibrate-{scenario.scenario_id}-") as tmp:
        state_root = Path(args.state) if args.state is not None else Path(tmp)
        state_root.mkdir(parents=True, exist_ok=True)
        store = StateStore(state_root)
        engine = build_engine(
            root,
            scenario,
            store,
            state_root / "work",
            runtime="fixture",
            enforce_runtime=False,
        )
        tasks = [engine.task_provider.load_task(task_id) for task_id in task_ids]
        runs = engine.run_suite(genome, tasks, scope="search")
        solved = metrics_from_runs(runs, task_ids).solved
    print(f"calibrate solved={solved} min={args.min_pass}")
    return 0 if solved >= args.min_pass else 1


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


REPO_POLICY_FILES: tuple[str, ...] = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "docs/style.md",
    ".github/CODEOWNERS",
    ".github/pull_request_template.md",
    ".github/workflows/ci.yml",
)
REPO_POLICY_FROZEN: tuple[str, ...] = (
    "shells/*/harness.py",
    "shells/*/verifier.py",
    "shells/*/task_provider.py",
    "shells/*/switches.schema.json",
    "bench",
)
REPO_POLICY_CONTRACT: tuple[str, ...] = (
    "schemas",
    "molt/core/ports.py",
    "docs/protocol.md",
    "docs/decisions",
)


def repo_policy_missing(root: Path) -> list[str]:
    """只读检查仓库协作文件、冻结面与契约域；返回缺失项（空即通过）。"""
    missing: list[str] = []
    for rel in REPO_POLICY_FILES:
        if not (root / rel).is_file():
            missing.append(rel)
    for pattern in REPO_POLICY_FROZEN:
        if not any(root.glob(pattern)):
            missing.append(pattern)
    for rel in REPO_POLICY_CONTRACT:
        if not (root / rel).exists():
            missing.append(rel)
    return missing


def cmd_doctor(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    if args.recover:
        store = StateStore(args.state)
        actions = store.recover()
        print("recover: " + ("; ".join(actions) if actions else "nothing to do"))
        return 0
    if args.repo_policy:
        # 纯只读：不访问网络 / GitHub API，不写文件。
        print("repo-policy frozen: " + ", ".join(REPO_POLICY_FROZEN))
        print("repo-policy contract: " + ", ".join(REPO_POLICY_CONTRACT))
        missing = repo_policy_missing(root)
        if missing:
            for item in missing:
                print(f"repo-policy missing: {item}", file=sys.stderr)
            return 1
        print("repo-policy ok")
        return 0
    violations = core_import_violations(root)
    if violations:
        for violation in violations:
            print(f"FAIL {violation}", file=sys.stderr)
        return 1
    print("doctor --deps: core 只依赖 stdlib 与 molt.core.*")
    return 0


# ---------------------------------------------------------------------------
# export（B2d-0：只导出数据）
# ---------------------------------------------------------------------------


def cmd_export(args: argparse.Namespace) -> int:
    from molt.demo import build_scenario_state
    from molt.export import DATA_FILES, export_web_data

    root = _repo_root(args)
    try:
        scenario = _load_scenario(root, args.scenario)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    state_path: Path | None = args.state
    if state_path is None and (Path.cwd() / "state" / "runs").is_dir():
        state_path = Path.cwd() / "state"
    if state_path is not None:
        store = StateStore(state_path)
        if not (store.root / "runs").is_dir():
            print(f"state 不存在或没有 runs: {store.root}", file=sys.stderr)
            return 2
        if not store.list_runs() and not args.allow_empty:
            print(
                f"state 为空（0 runs）: {store.root}；如需空数据面请加 --allow-empty",
                file=sys.stderr,
            )
            return 2
        index = export_web_data(root, scenario, store, args.out)
        source = str(state_path)
    else:
        # 无 state 时按 scenario 重建证据链到临时目录，仓库内不落任何文件。
        with tempfile.TemporaryDirectory(prefix=f"molt-export-{scenario.scenario_id}-") as tmp:
            store, _, _ = build_scenario_state(root, Path(tmp), scenario=scenario)
            index = export_web_data(root, scenario, store, args.out)
        source = f"临时 {scenario.scenario_id} state（不写仓库）"

    print(
        f"export ok out={args.out} files={len(DATA_FILES)}（含 index.json） "
        f"revision={index['revision']}"
    )
    print(
        f"  scenario={index['scenario_id']} runs={index['counts']['runs']} "
        f"proposals={index['counts']['proposals']} events="
        f"{index['counts']['events_search']}/{index['counts']['events_report']} source={source}"
    )
    return 0


# ---------------------------------------------------------------------------
# demo（B2-D7：1 live 候选 + 1 replay 基线 + 10 题扫描回放）
# ---------------------------------------------------------------------------


def _fmt_cost(value: float) -> str:
    return f"${value:.4f}"


def _print_closure(summary: dict[str, Any]) -> None:
    closure = summary["closure"]
    baseline = closure["baseline"]
    candidate = closure["candidate"]
    print(
        f"closure task={closure['task_id']} "
        f"baseline={baseline['provenance']['kind']} run={baseline['run_id']} "
        f"replay_of={baseline['replay_of_run_id']} "
        f"outcome={baseline['outcome']} cost={_fmt_cost(baseline['cost_usd'])}"
    )
    print(
        f"closure task={closure['task_id']} "
        f"candidate={candidate['provenance']['kind']} run={candidate['run_id']} "
        f"outcome={candidate['outcome']} cost={_fmt_cost(candidate['cost_usd'])} "
        f"wall_ms={candidate['wall_ms']}"
    )


def _print_gate(summary: dict[str, Any]) -> None:
    gate = summary["gate"]
    print(
        f"gate decision={gate['decision_id']} verdict={gate['verdict']} "
        f"reason={gate['reason']} solved={gate['baseline_solved']}->{gate['candidate_solved']}"
    )


def _print_overhead(summary: dict[str, Any]) -> None:
    overhead = summary["overhead"]
    if not overhead.get("measured"):
        print(f"overhead n/a: {overhead.get('reason')}")
        return
    print(
        f"overhead total={overhead['total_wall_ms']}ms "
        f"pi_sub={overhead['pi_subprocess_wall_ms']}ms "
        f"extra={overhead['orchestration_overhead_ms']}ms "
        f"ratio={overhead['overhead_ratio'] * 100:.1f}% "
        f"threshold={overhead['threshold'] * 100:.0f}% "
        f"within={overhead['within_threshold']}"
    )
    if not overhead["within_threshold"]:
        print(
            "WARN 开销比超过 20%：按 ADR 0001 重开条件 2 记录，需触发 ADR 复查",
            file=sys.stderr,
        )


def cmd_demo(args: argparse.Namespace) -> int:
    from molt.demo import DemoTimeout, run_demo

    root = _repo_root(args)
    if args.max_wall <= 0:
        print("--max-wall 必须 > 0", file=sys.stderr)
        return 2
    try:
        scenario = _load_scenario(root, args.scenario)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        summary = run_demo(
            root,
            scenario,
            state_root=args.state,
            out_dir=args.out,
            mode=args.mode,
            max_wall_s=args.max_wall,
            baseline_state=args.baseline_state,
            candidate_state=args.candidate_state,
            model=args.model,
            pi_executable=args.pi_executable,
        )
    except DemoTimeout as exc:
        print(f"demo 失败：{exc}", file=sys.stderr)
        return 1
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"demo mode={summary['mode']} scenario={summary['scenario_id']} "
        f"elapsed={summary['elapsed_s']}s/{summary['max_wall_s']:.0f}s "
        f"state={summary['state']}"
    )
    print(
        f"propose proposal={summary['proposal']['proposal_id']} "
        f"source={summary['proposal']['source']} "
        f"cost={_fmt_cost(summary['proposal']['cost_usd'])} "
        f"switches={summary['proposal']['switches']}"
    )
    genomes = summary["genomes"]
    print(
        f"genomes baseline={genomes['baseline']['switches']} "
        f"({genomes['baseline']['source']}, {genomes['baseline']['genome_id'][:15]}) "
        f"candidate={genomes['candidate']['switches']} "
        f"({genomes['candidate']['source']}, {genomes['candidate']['genome_id'][:15]}) "
        f"proposal_matches={genomes['proposal_matches_candidate']} "
        f"runtime_variant={genomes['candidate'].get('runtime_variant')}"
    )
    _print_closure(summary)
    print(f"scan10 replay_runs={len(summary['scan10'])}（10 题扫描的回放，不是第二次 demo 运行）")
    print(
        f"candidate_scan runs={len(summary['candidate_scan'])}"
        "（closure 以外的候选题；--candidate-state 下为 replay，不是 live）"
    )
    _print_gate(summary)
    action = summary["action"]
    print(
        f"action={action['kind']} archive={action['archive_id']} "
        f"generation={action['generation']} baseline_archive={action['baseline_archive_id']}"
    )
    _print_overhead(summary)
    audit = summary["audit"]
    print(
        f"audit ok runs={audit['runs']} results={audit['results']} "
        f"decisions={audit['decisions']} events={audit['events']}"
    )
    export = summary["export"]
    print(f"export out={export['out']} files={export['files']} revision={export['revision']}")
    counts = summary["provenance_counts"]
    print(f"provenance live={counts['live']} replay={counts['replay']} fixture={counts['fixture']}")
    print(f"summary {Path(summary['state']) / 'demo-summary.json'}")
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    from molt.agent.server import run_server

    root = _repo_root(args)
    return run_server(
        root=root,
        host=args.host,
        port=args.port,
        state=args.state,
        upper_state=args.upper_state,
        token_file=args.token_file,
        allowed_origins=args.allow_origin,
        provider=args.provider,
        probe_index=args.probe_index,
        data_dir=args.data_dir or (root / "web" / "data"),
        lab_root=args.lab_root,
    )


HANDLERS = {
    "schema": cmd_schema,
    "genome": cmd_genome,
    "scenario": cmd_scenario,
    "search": cmd_search,
    "propose": cmd_propose,
    "gate": cmd_gate,
    "adopt": cmd_adopt,
    "revert": cmd_revert,
    "audit": cmd_audit,
    "calibrate": cmd_calibrate,
    "doctor": cmd_doctor,
    "export": cmd_export,
    "demo": cmd_demo,
    "serve": cmd_serve,
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
