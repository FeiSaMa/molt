"""现场 probe 演示：Run1 → 反思生成 memory → Run2，增量写 state.json 与最终 run.json。

用法::

    uv run python -m probes.run_demo \\
        --state /tmp/molt-probe/demo \\
        --out web/data/probe/state.json \\
        --profile fast \\
        --model deepseek/deepseek-v4-pro

``--profile`` 两档（默认 ``fast``）：

- ``fast``：Run1 只跑 ``probe-order-2``；Run2 只跑 ``probe-order-x5`` / ``x6``；
  默认总墙钟 360s；Run1 看不到 x3 的 ``k`` 用例，反思不应出现数量上限；
  Run2 两题全过 → ``v3_stable_headroom``，任一失败 → ``partial_transfer``。
- ``full``：Run1 跑 ``probe-order-2`` / ``x3``；Run2 跑 ``x5``–``x8``；
  默认总墙钟 420s；verdict 三档口径与既有证据一致。

流程（每一阶段都先检查墙钟 / 成本上限，超限写 ``phase=timeout`` 或 ``partial`` 后退出 2）：

1. Run1：``v0-pro-max`` 按 profile 跑 baseline 题各 1 次（无记忆）；
   每完成一题写一次 ``state.json``（``phase=run1``）。
2. 反思：从 Run1 失败 trace 取 ``case_id/input/expected/actual``，写
   ``shells/probe/memory/live.md``，用 ``pi --thinking max`` 生成 ≤5 行规则。
3. Run2：用新 memory 构造 genome（``child_genome`` + artifacts + ``ext.memory_ref``），
   按 profile 跑迁移题各 1 次；每完成一题写一次 ``state.json``（``phase=run2``）。
4. Done：算 verdict / cost / wall，写 ``run.json`` 与 ``state.json``（``phase=done``）。

超时语义：单题超过剩余预算时杀进程组并写 ``phase=partial``（保留已完成题）；
只有在阶段边界检查到总预算耗尽（``check_limits``）才写 ``phase=timeout``。

约束：
- ``state.json`` 严格符合 ``probes/report/probe_state_schema.json``，写入前自校验；
- 现场产出（``web/data/probe/state.json``、``run.json``、``shells/probe/memory/live.md``）
  均在 ``.gitignore`` 内，不污染 ``git status``；
- ``--state`` 必须在仓库之外，避免 ``molt search`` 把证据写进仓库；
- memory 快照按内容寻址发布到 ``<state_dir>/memory/run2-memory-<sha256>.md``：
  只绑定本轮完成发布（或确认字节一致）的最终文件，遗留 ``.tmp`` / 半写文件不参与发现；
- state/out 仍是单写者（不支持并发写同一 state）；快照保留不依赖锁，正常复用已有 state；
- **前置条件**：``run.json`` 每轮归档由调用方负责；复用同一 state/out 时后一轮会覆盖
  ``run.json``，本脚本不自动归档；快照文件按内容寻址保留，不会被本轮删除。
"""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = "probe"
STATE_SCHEMA = "probes/report/probe_state_schema.json"
BASE_GENOME = "shells/probe/genomes/v0-pro-max.json"
MEMORY_REF = "shells/probe/memory/live.md"
PROTECTED_PROBE_ROOT = "web/data/probe"
MEMORY_SNAPSHOT_DIR = "memory"
RUN1_TASKS = ("probe-order-2", "probe-order-x3")
RUN2_TASKS = ("probe-order-x5", "probe-order-x6", "probe-order-x7", "probe-order-x8")
HEADROOM_TASKS = RUN2_TASKS[:3]
VERDICT_NOTE = "x8 基线 2/2，不计入 headroom"
FAST_VERDICT_NOTE = "fast profile：x8 未跑，离线证据见 memory-v3 4/4"
DEFAULT_PROFILE = "fast"

# profile → 实际跑的题集与默认预算。题集只含真正执行的题，页面 total 按此计算，
# 不补空题；lineage 也只列这里实际跑过的 scope。
PROFILES: dict[str, dict[str, Any]] = {
    "fast": {
        "run1": ("probe-order-2",),
        "run2": ("probe-order-x5", "probe-order-x6"),
        "headroom": ("probe-order-x5", "probe-order-x6"),
        "max_wall": 360.0,
        "verdict_note": FAST_VERDICT_NOTE,
    },
    "full": {
        "run1": RUN1_TASKS,
        "run2": RUN2_TASKS,
        "headroom": HEADROOM_TASKS,
        "max_wall": 420.0,
        "verdict_note": VERDICT_NOTE,
    },
}
REFLECTION_HEADER = (
    "下面是同一代码库上一轮失败用例。请只用不超过 5 行描述这个库在分组、去重、排序上的规则。"
    "不要提及参数名、返回值类型、函数名或文件路径。不要写代码。"
)
REFLECTION_RETRY = (
    "\n\n上一次回答包含实现细节（函数名 / 参数名 / 返回类型）。请重写：只描述分组、去重、"
    "排序的规则，不要写代码，也不要提任何标识符。"
)
BANNED_TERMS = (
    "dedupe",
    "unique",
    "distinct_count",
    "names",
    "list[str]",
    "def ",
    "return",
    "->",
)

RUN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "mode",
        "phase",
        "started_at",
        "finished_at",
        "wall_ms",
        "cost_usd",
        "verdict",
        "verdict_note",
        "memory_ref",
        "memory_sha256",
        "pi_version",
        "genomes",
        "phases",
        "runs",
        "budget",
        "note",
    ],
    "properties": {
        "schema_version": {"const": "1.0.0"},
        "mode": {"const": "live"},
        "phase": {"enum": ["done", "partial", "timeout"]},
        "started_at": {"type": "string", "format": "date-time"},
        "finished_at": {"type": "string", "format": "date-time"},
        "wall_ms": {"type": "integer", "minimum": 0},
        "cost_usd": {"type": "number", "minimum": 0},
        "verdict": {
            "enum": ["", "partial_transfer", "v3_stable_all", "v3_stable_headroom"]
        },
        "verdict_note": {"type": "string"},
        "memory_ref": {"type": "string"},
        "memory_sha256": {"type": ["string", "null"]},
        "pi_version": {"type": "string"},
        "genomes": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "run1": {"$ref": "#/definitions/genome"},
                "run2": {"$ref": "#/definitions/genome"},
            },
        },
        "phases": {"type": "array", "items": {"$ref": "#/definitions/phase"}},
        "runs": {"type": "array", "items": {"$ref": "#/definitions/run"}},
        "budget": {
            "type": "object",
            "additionalProperties": False,
            "required": ["max_wall_s", "max_cost_usd", "exceeded", "reason"],
            "properties": {
                "max_wall_s": {"type": "number", "minimum": 0},
                "max_cost_usd": {"type": "number", "minimum": 0},
                "exceeded": {"type": "boolean"},
                "reason": {"type": "string"},
            },
        },
        "note": {"type": "string"},
    },
    "definitions": {
        "genome": {
            "type": "object",
            "additionalProperties": False,
            "required": ["genome_id", "path"],
            "properties": {
                "genome_id": {"type": "string", "minLength": 1},
                "path": {"type": "string", "minLength": 1},
            },
        },
        "phase": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "wall_ms", "cost_usd", "tasks"],
            "properties": {
                "name": {"enum": ["run1", "reflection", "run2"]},
                "wall_ms": {"type": "integer", "minimum": 0},
                "cost_usd": {"type": "number", "minimum": 0},
                "tasks": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "该阶段的题数；reflection 阶段记为反思调用次数",
                },
            },
        },
        "run": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "phase",
                "task_id",
                "run_id",
                "outcome",
                "passed",
                "cost_usd",
                "wall_ms",
            ],
            "properties": {
                "phase": {"enum": ["run1", "run2"]},
                "task_id": {"type": "string", "minLength": 1},
                "run_id": {"type": "string", "minLength": 1},
                "outcome": {"type": "string", "minLength": 1},
                "passed": {"type": "boolean"},
                "cost_usd": {"type": "number", "minimum": 0},
                "wall_ms": {"type": "integer", "minimum": 0},
            },
        },
    },
}


class DemoStop(Exception):
    """中止演示：``phase`` 取 ``timeout`` 或 ``partial``。"""

    def __init__(self, phase: str, message: str) -> None:
        super().__init__(message)
        self.phase = phase
        self.message = message


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _join_note(existing: str, extra: str) -> str:
    return f"{existing} | {extra}" if existing else extra


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _content_hash(payload: dict[str, Any]) -> str:
    from molt.core.hash import content_hash

    return content_hash(payload)


def _state_schema() -> dict[str, Any]:
    return json.loads((ROOT / STATE_SCHEMA).read_text(encoding="utf-8"))


def _validate(payload: Any, schema: dict[str, Any], label: str) -> None:
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - dev extra 缺失时只警告
        print(f"warning: jsonschema 不可用，跳过 {label} 自校验", file=sys.stderr)
        return
    jsonschema.Draft7Validator(schema).validate(payload)


def _run_capture(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout_s: float,
) -> subprocess.CompletedProcess[str]:
    """跑子进程；超时按进程组整体杀掉，避免孤儿 pi 继续烧钱。"""
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_group(process)
        process.communicate()
        raise
    except BaseException:
        _kill_group(process)
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _child_pids(pid: int) -> list[int]:
    """列出直接子进程 PID；用于杀掉 molt search 拉起的独立 session（pi）。"""
    children: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            fields = stat[stat.rindex(")") + 2 :].split()
        except ValueError:
            continue
        if len(fields) >= 2 and fields[1].isdigit() and int(fields[1]) == pid:
            children.append(int(entry.name))
    return children


def _terminate_pid(pid: int, sig: int) -> None:
    """优先杀进程组；不是组长时只杀单进程，避免误伤外层组。"""
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return
    try:
        if pgid == pid:
            os.killpg(pgid, sig)
        else:
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _kill_group(process: subprocess.Popen[str]) -> None:
    # molt search 的 pi 子进程用 start_new_session 另起 session，必须先收集。
    targets = [process.pid, *_child_pids(process.pid)]
    for pid in targets:
        _terminate_pid(pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    for pid in targets:
        _terminate_pid(pid, signal.SIGKILL)


# ---------------------------------------------------------------------------
# 题面 / case 证据
# ---------------------------------------------------------------------------


def _task_json(task_id: str) -> dict[str, Any]:
    return json.loads(
        (ROOT / "bench/probe" / task_id / "task.json").read_text(encoding="utf-8")
    )


def _function_name(task_id: str) -> str:
    probe = (_task_json(task_id).get("ext") or {}).get("probe") or {}
    function = probe.get("function")
    if not isinstance(function, str) or not function:
        raise DemoStop("partial", f"{task_id}: task.json 缺少 ext.probe.function")
    return function


def _stub(task_id: str) -> str:
    return (ROOT / "bench/probe" / task_id / "workspace/solution.py").read_text(
        encoding="utf-8"
    )


def _source_cases(task_id: str) -> list[list[Any]]:
    """从 check_cases.py 的 ``cases`` 元组读出入参声明（不改题面）。"""
    source = (ROOT / "bench/probe" / task_id / "tests/check_cases.py").read_text(
        encoding="utf-8"
    )
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "cases":
                return [_jsonable(row) for row in ast.literal_eval(node.value)]
    raise ValueError(f"{task_id}: cases 元组不存在")


def _run_checker(task_id: str, solution: str) -> list[dict[str, Any]]:
    """在隔离目录里跑题目的 check_cases.py，取回逐 case 实际值。"""
    with tempfile.TemporaryDirectory(prefix="molt-probe-demo-") as temporary:
        workdir = Path(temporary)
        shutil.copyfile(
            ROOT / "bench/probe" / task_id / "tests/check_cases.py",
            workdir / "check_cases.py",
        )
        (workdir / "solution.py").write_text(solution, encoding="utf-8")
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"}
        completed = subprocess.run(
            [sys.executable, "check_cases.py"],
            cwd=str(workdir),
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"checker exit={completed.returncode}")
        payload = json.loads(completed.stdout.strip() or "{}")
        if payload.get("error") is not None:
            raise RuntimeError(str(payload["error"]))
        reported = payload.get("cases")
        return reported if isinstance(reported, list) else []


def _cases(task_id: str, solution: str) -> list[dict[str, Any]]:
    declared = _source_cases(task_id)
    reported = _run_checker(task_id, solution)
    if len(declared) != len(reported):
        raise RuntimeError(
            f"{task_id}: case 数不一致 declared={len(declared)} reported={len(reported)}"
        )
    cases: list[dict[str, Any]] = []
    for row, report in zip(declared, reported, strict=True):
        case_id = row[0]
        expected = row[-1]
        middle = list(row[1:-1])
        case_input = middle[0] if len(middle) == 1 else middle
        if not isinstance(report, dict) or report.get("case_id") != case_id:
            raise RuntimeError(f"{task_id}: case 顺序不一致 {case_id}")
        if _jsonable(report.get("expected")) != _jsonable(expected):
            raise RuntimeError(f"{task_id}/{case_id}: expected 与题面不一致")
        actual = _jsonable(report.get("actual"))
        cases.append(
            {
                "case_id": case_id,
                "input": _jsonable(case_input),
                "expected": _jsonable(expected),
                "actual": actual,
                "passed": bool(report.get("passed")),
            }
        )
    return cases


def _signature(source: str, function: str) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "missing"
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function:
            arguments = ", ".join(argument.arg for argument in node.args.args)
            returns = ast.unparse(node.returns) if node.returns else "None"
            return f"({arguments}) -> {returns}"
    return "missing"


def _diff_lines(before: str, after: str) -> list[dict[str, str]]:
    """行级 difflib 流：same / del / add（replace 拆成先 del 再 add）。"""
    left = before.splitlines()
    right = after.splitlines()
    matcher = difflib.SequenceMatcher(a=left, b=right, autojunk=False)
    lines: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            lines.extend({"type": "same", "text": text} for text in left[i1:i2])
        elif tag == "delete":
            lines.extend({"type": "del", "text": text} for text in left[i1:i2])
        elif tag == "insert":
            lines.extend({"type": "add", "text": text} for text in right[j1:j2])
        elif tag == "replace":
            lines.extend({"type": "del", "text": text} for text in left[i1:i2])
            lines.extend({"type": "add", "text": text} for text in right[j1:j2])
    return lines


# ---------------------------------------------------------------------------
# genome
# ---------------------------------------------------------------------------


def _prepare_run1_genome(model: str, path: Path):
    from molt.core.genome import compute_genome_id, verify_genome
    from molt.core.types import Genome

    data = json.loads((ROOT / BASE_GENOME).read_text(encoding="utf-8"))
    ext = dict(data.get("ext") or {})
    ext["pi"] = {**(ext.get("pi") or {}), "model": model}
    data["ext"] = ext
    data["genome_id"] = compute_genome_id(data)
    genome = Genome.from_dict(data)
    errors = verify_genome(genome, ROOT)
    if errors:
        raise DemoStop("partial", f"run1 genome 校验失败: {'; '.join(errors)}")
    _write_json(path, genome.to_dict())
    return genome


def _prepare_run2_genome(
    run1_genome,
    model: str,
    path: Path,
    *,
    memory_snapshot: Path | None,
    memory_sha256: str | None,
):
    """生成 run2 genome；只接受本轮已绑定的快照 hash。

    进入 genome 生成前必须确认：hash 非空、快照存在且 hash 匹配、共享 memory hash
    也匹配。``verify_genome`` 仍会对共享 artifact 做二次校验（hash 漂移即失败）。
    """
    if not memory_sha256:
        raise DemoStop("partial", "run2 memory 未绑定（memory_sha256 为空）")
    if memory_snapshot is None:
        raise DemoStop("partial", "run2 memory 快照路径缺失（memory_sha256 已绑定）")
    from molt.core.errors import MoltError
    from molt.core.genome import child_genome, compute_genome_id, verify_genome
    from molt.core.hash import sha256_file
    from molt.core.types import Genome

    state_root = path.parent.parent
    try:
        snapshot_ref = memory_snapshot.relative_to(state_root).as_posix()
    except ValueError as exc:
        raise DemoStop(
            "partial", f"run2 memory 快照必须位于 state 根目录内: {memory_snapshot}"
        ) from exc
    if not memory_snapshot.is_file():
        raise DemoStop("partial", f"run2 memory 快照不存在: {memory_snapshot}")
    snapshot_hash = sha256_file(memory_snapshot)
    if snapshot_hash != memory_sha256:
        raise DemoStop(
            "partial",
            f"run2 memory 快照 hash 不匹配: {snapshot_hash} != {memory_sha256}",
        )
    memory_path = ROOT / MEMORY_REF
    if not memory_path.is_file():
        raise DemoStop("partial", f"memory 文件不存在: {MEMORY_REF}")
    shared_hash = sha256_file(memory_path)
    if shared_hash != memory_sha256:
        raise DemoStop(
            "partial", f"共享 memory hash 不匹配: {shared_hash} != {memory_sha256}"
        )
    artifacts = {**run1_genome.artifacts, MEMORY_REF: memory_sha256}
    child = child_genome(
        run1_genome, run1_genome.switches, repo_root=ROOT, artifacts=artifacts
    )
    payload = child.to_dict()
    ext = dict(payload.get("ext") or {})
    ext["pi"] = {**(ext.get("pi") or {}), "model": model}
    ext["memory_ref"] = MEMORY_REF
    ext["memory_snapshot"] = snapshot_ref
    ext["memory_sha256"] = memory_sha256
    ext["probe"] = {**(ext.get("probe") or {}), "cell": "live-memory"}
    ext["note"] = "live demo run2: rule-only memory from run1 failures"
    payload["ext"] = ext
    payload["genome_id"] = compute_genome_id(payload)
    genome = Genome.from_dict(payload)
    try:
        errors = verify_genome(genome, ROOT)
    except MoltError as exc:
        raise DemoStop("partial", f"run2 genome 校验失败: {exc}") from exc
    if errors:
        raise DemoStop("partial", f"run2 genome 校验失败: {'; '.join(errors)}")
    _write_json(path, genome.to_dict())
    return genome


# ---------------------------------------------------------------------------
# 单题执行
# ---------------------------------------------------------------------------


def _execute(
    *,
    run_dir: Path,
    genome_path: Path,
    task_id: str,
    timeout_s: float,
) -> tuple[dict[str, Any], str | None]:
    """跑一题并组装 state 的 ``task`` 块；返回 ``(task, 基础设施错误)``。"""
    if run_dir.exists():
        shutil.rmtree(run_dir)
    command = [
        sys.executable,
        "-m",
        "molt",
        "search",
        "--scenario",
        SCENARIO,
        "--runtime",
        "pi",
        "--genome",
        str(genome_path),
        "--state",
        str(run_dir),
        "--task",
        task_id,
    ]
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        completed = _run_capture(
            command, cwd=ROOT, environment=environment, timeout_s=timeout_s
        )
    except subprocess.TimeoutExpired as exc:
        # 单题超时只判 partial：保留已完成题；只有阶段边界的总预算检查写 timeout。
        raise DemoStop(
            "partial", f"{task_id}: 单题超过剩余预算（{timeout_s:.0f}s），已杀掉进程组"
        ) from exc

    from molt.core.store import StateStore

    try:
        runs = StateStore(run_dir).list_runs()
    except Exception as exc:  # noqa: BLE001 - 记录真实失败原因，不伪装
        raise DemoStop("partial", f"{task_id}: 读不到 run 记录: {exc}") from exc
    matches = [run for run in runs if run.task_id == task_id]
    if not matches:
        tail = (completed.stderr or completed.stdout or "").strip()[-400:]
        raise DemoStop(
            "partial",
            f"{task_id}: molt search 未产出 run（exit={completed.returncode}）{tail}",
        )
    run = matches[-1]

    stub = _stub(task_id)
    solution_path = run_dir / "work" / run.run_id / "workspace" / "solution.py"
    code_after = solution_path.read_text(encoding="utf-8") if solution_path.is_file() else stub
    function = _function_name(task_id)
    note = ""
    if not solution_path.is_file():
        note = "solution 缺失，code_after 回落到 stub"
    cases: list[dict[str, Any]] = []
    try:
        cases = _cases(task_id, code_after)
    except Exception as exc:  # noqa: BLE001 - case 不可读时保留 run 级证据
        note = _join_note(note, f"cases 不可读: {type(exc).__name__}: {exc}")
    if run.outcome == "passed" and cases and not all(case["passed"] for case in cases):
        note = _join_note(note, "outcome=passed 但存在失败 case（口径冲突）")

    infra: str | None = None
    if run.outcome == "error":
        detail = run.ext.get("error") if isinstance(run.ext, dict) else None
        infra = f"{task_id}: runtime error: {str(detail)[:200]}"
        note = _join_note(note, "outcome=error（基础设施失败）")
    elif run.outcome != "passed":
        note = _join_note(note, f"outcome={run.outcome}")

    task = {
        "id": task_id,
        "run_id": run.run_id,
        "function": function,
        "signature": _signature(code_after, function),
        "passed": run.outcome == "passed",
        "passes": 1 if run.outcome == "passed" else 0,
        "runs": 1,
        "cases": cases,
        "code_before": stub,
        "code_after": code_after,
        "diff_lines": _diff_lines(stub, code_after),
        # 私有字段只服务 run.json 汇总，写 state 前会被剥掉。
        "_cost_usd": float(run.usage.cost_usd or 0.0),
        "_wall_ms": int(run.usage.wall_ms or 0),
        "_outcome": run.outcome,
    }
    if note:
        task["note"] = note
    return task, infra


# ---------------------------------------------------------------------------
# 反思
# ---------------------------------------------------------------------------


def _clean_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        lines.append(line)
    return lines[:5]


def _banned_hits(lines: list[str], functions: set[str]) -> list[str]:
    haystack = "\n".join(lines).casefold()
    terms = tuple(functions) + BANNED_TERMS
    return [term for term in terms if term.casefold() in haystack]


def _call_pi(
    prompt: str,
    *,
    state_dir: Path,
    model: str,
    attempt: int,
    timeout_s: float,
    timeout_phase: str,
) -> tuple[str, float]:
    reflection_dir = state_dir / "reflection"
    reflection_dir.mkdir(parents=True, exist_ok=True)
    log_path = reflection_dir / f"response-attempt{attempt}.jsonl"
    command = [
        "pi",
        "-p",
        "--mode",
        "json",
        "--approve",
        "--no-mcp",
        "--no-extensions",
        "--no-builtin-tools",
        "--no-skills",
        "--no-prompt-templates",
        "--no-context-files",
        "--session-id",
        f"probe-live-reflect-{attempt}",
        "--session-dir",
        str(state_dir / "pi-sessions"),
        "--model",
        model,
        "--thinking",
        "max",
        "--",
        prompt,
    ]
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        completed = _run_capture(
            command, cwd=reflection_dir, environment=environment, timeout_s=timeout_s
        )
    except subprocess.TimeoutExpired as exc:
        if timeout_phase == "timeout":
            message = f"反思调用耗尽总预算（{timeout_s:.0f}s）"
        else:
            message = f"反思调用超过单次上限（{timeout_s:.0f}s），已杀掉进程组"
        raise DemoStop(timeout_phase, message) from exc
    log_path.write_text(completed.stdout or "", encoding="utf-8")
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip()[-400:]
        raise DemoStop(
            "partial", f"反思调用失败（exit={completed.returncode}）{tail}"
        )

    texts: list[str] = []
    cost = 0.0
    for line in (completed.stdout or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "message_end":
            continue
        message = event.get("message") or {}
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        usage = message.get("usage") or {}
        total = (usage.get("cost") or {}).get("total") if isinstance(usage, dict) else None
        if isinstance(total, (int, float)):
            cost += float(total)
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(str(part.get("text") or ""))
    return "\n".join(text for text in texts if text.strip()), cost


def _reflect(
    prompt: str,
    *,
    state_dir: Path,
    model: str,
    timeout_s: float,
    timeout_phase: str,
    functions: set[str],
) -> tuple[list[str], float, int, list[str]]:
    request = prompt
    total_cost = 0.0
    hits: list[str] = []
    lines: list[str] = []
    attempts = 0
    for attempt in (1, 2):
        attempts = attempt
        text, cost = _call_pi(
            request,
            state_dir=state_dir,
            model=model,
            attempt=attempt,
            timeout_s=timeout_s,
            timeout_phase=timeout_phase,
        )
        total_cost += cost
        candidate = _clean_lines(text)
        if not candidate:
            raise DemoStop("partial", "反思没有返回可用文本")
        lines = candidate
        hits = _banned_hits(candidate, functions)
        if not hits:
            break
        if attempt == 1:
            request = prompt + REFLECTION_RETRY
    return lines, total_cost, attempts, hits


def _memory_bytes(lines: list[str]) -> bytes:
    """本轮 memory 的唯一字节表示；快照、共享文件与 hash 都从这一份字节派生。"""
    return ("\n".join(lines).rstrip("\n") + "\n").encode("utf-8")


def _snapshot_ref(digest: str) -> str:
    return f"{MEMORY_SNAPSHOT_DIR}/run2-memory-{digest}.md"


def _read_regular_file(path: Path, label: str) -> bytes:
    """读取已发布的普通文件；symlink / 目录 / 硬链接一律 fail closed。"""
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise DemoStop("partial", f"{label} 不存在: {path}") from None
    if stat.S_ISLNK(info.st_mode):
        raise DemoStop("partial", f"{label} 必须是常规文件（拒绝 symlink）: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise DemoStop("partial", f"{label} 不是常规文件: {path}")
    if info.st_nlink != 1:
        raise DemoStop("partial", f"{label} 存在硬链接（拒绝复用）: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise DemoStop("partial", f"{label} 不可读: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise DemoStop("partial", f"{label} 读取时不再是唯一的常规文件: {path}")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _publish_memory_snapshot(data: bytes, state_dir: Path) -> tuple[str, str]:
    """把本轮字节原子发布到内容寻址快照；返回 ``(相对路径, sha256)``。

    同内容复用同一路径；目标已存在且字节不同即拒绝覆盖。只有最终文件（``os.replace``
    完成或字节确认一致）才可被绑定，``.tmp`` / 半写文件不参与发现。
    """
    digest = hashlib.sha256(data).hexdigest()
    ref = _snapshot_ref(digest)
    directory = Path(state_dir) / MEMORY_SNAPSHOT_DIR
    target = Path(state_dir) / ref
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DemoStop("partial", f"memory 快照目录不可创建: {exc}") from exc
    if os.path.lexists(target):
        if _read_regular_file(target, "memory 快照目标") != data:
            raise DemoStop(
                "partial", f"memory 快照已存在且字节不同（拒绝覆盖）: {ref}"
            )
        return ref, digest
    try:
        handle, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=directory
        )
    except OSError as exc:
        raise DemoStop("partial", f"memory 快照临时文件创建失败: {exc}") from exc
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(target):
            # 单写者假设下不应出现；若并发出现，同样只允许字节一致。
            if _read_regular_file(target, "memory 快照目标") != data:
                raise DemoStop(
                    "partial", f"memory 快照并发出现且字节不同（拒绝覆盖）: {ref}"
                )
            os.unlink(temporary)
            return ref, digest
        try:
            os.replace(temporary, target)
        except OSError as exc:
            raise DemoStop("partial", f"memory 快照原子发布失败: {exc}") from exc
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return ref, digest


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """唯一临时文件 + ``os.replace``；失败时清理临时文件，不留下可绑定残骸。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
    except OSError as exc:
        raise DemoStop("partial", f"临时文件创建失败: {exc}") from exc
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write_shared_memory(data: bytes) -> Path:
    """把同一份字节写入 harness 读取的共享 memory。"""
    path = ROOT / MEMORY_REF
    _atomic_write_bytes(path, data)
    return path


def _publish_memory(demo: Demo, lines: list[str]) -> tuple[str, str]:
    """发布本轮 memory：先快照并绑定，再写共享文件。

    前置条件：单写者（state/out 与共享 memory 不被并发改写）。快照记录本轮**预期**
    注入字节；harness 每题仍重读共享文件，前后检查只能发现检查点之间的漂移，
    不证明实际读取字节必然等于快照，也不消除 TOCTOU。
    """
    data = _memory_bytes(lines)
    ref, digest = _publish_memory_snapshot(data, demo.state_dir)
    demo.bind_memory(ref, digest)
    demo.memory["sources"] = [MEMORY_REF]
    demo.note = _join_note(
        demo.note,
        "memory 快照记录本轮预期输入（单写者假设）；harness 每题重读共享文件，"
        "边界检查只发现漂移，不证明实际读取字节等于快照",
    )
    try:
        _write_shared_memory(data)
    except DemoStop:
        raise
    except OSError as exc:
        raise DemoStop(
            "partial", f"memory 快照已发布 {ref}，但共享 memory 写入失败: {exc}"
        ) from exc
    return ref, digest


def _failures(tasks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    failures: list[list[dict[str, Any]]] = []
    for task in tasks:
        failed = [case for case in task["cases"] if not case["passed"]]
        if failed:
            failures.append(failed)
    return failures


def _verdict_for(passed: dict[str, bool], profile: str = DEFAULT_PROFILE) -> str:
    """按 profile 的实际题集判 verdict。

    fast：Run2 两题（x5/x6）全过 → headroom；任一失败 → partial_transfer（x8 未跑）。
    full：x5–x7 全过 → headroom，再加 x8 通过 → all。
    """
    headroom_ok = all(
        passed.get(task_id, False) for task_id in PROFILES[profile]["headroom"]
    )
    if not headroom_ok:
        return "partial_transfer"
    if profile == "fast" or not passed.get("probe-order-x8", False):
        return "v3_stable_headroom"
    return "v3_stable_all"


def _reflection_prompt(failures: list[list[dict[str, Any]]]) -> str:
    blocks: list[str] = []
    for index, cases in enumerate(failures):
        label = f"task-{chr(ord('a') + index)}"
        body = [f"## {label}", ""]
        for case in cases:
            body.extend(
                [
                    f"- case_id: {case['case_id']}",
                    f"  input: {json.dumps(case['input'], ensure_ascii=False)}",
                    f"  expected: {json.dumps(case['expected'], ensure_ascii=False)}",
                    f"  actual: {json.dumps(case['actual'], ensure_ascii=False)}",
                    "  run: r1",
                    "",
                ]
            )
        blocks.append("\n".join(body))
    return REFLECTION_HEADER + "\n\n" + "\n".join(blocks)


# ---------------------------------------------------------------------------
# state / run 文档
# ---------------------------------------------------------------------------


class Demo:
    def __init__(
        self,
        *,
        state_dir: Path,
        out: Path,
        run_out: Path,
        max_wall: float,
        max_cost: float,
        model: str,
        profile: str = DEFAULT_PROFILE,
    ) -> None:
        if profile not in PROFILES:
            raise ValueError(f"未知 profile: {profile}")
        spec = PROFILES[profile]
        self.profile = profile
        self.run1_scope: tuple[str, ...] = spec["run1"]
        self.run2_scope: tuple[str, ...] = spec["run2"]
        self.headroom_tasks: tuple[str, ...] = spec["headroom"]
        self.state_dir = state_dir
        self.out = out
        self.run_out = run_out
        self.max_wall = max_wall
        self.max_cost = max_cost
        self.model = model
        self.started_at = _now()
        self.started = time.monotonic()
        self.phase = "run1"
        self.pi_version = "unknown"
        self.run1_tasks: list[dict[str, Any]] = []
        self.run2_tasks: list[dict[str, Any]] = []
        self.memory: dict[str, Any] = {
            "lines": [],
            "source_cases": [],
            "sources": [],
            "attempts": 0,
            "reflection_model": "",
            "cost_usd": 0.0,
            "snapshot": None,
            "sha256": None,
        }
        self.memory_snapshot: str | None = None
        self.memory_sha256: str | None = None
        self.expected_memory_sha256: str | None = None
        self.verdict = ""
        self.verdict_note = ""
        self.note = ""
        self.reflection_wall_ms = 0
        self.genomes: dict[str, dict[str, str]] = {}

    # -- 计时与上限 -----------------------------------------------------
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def run1_cost(self) -> float:
        return sum(float(task.get("_cost_usd", 0.0)) for task in self.run1_tasks)

    def run2_cost(self) -> float:
        return sum(float(task.get("_cost_usd", 0.0)) for task in self.run2_tasks)

    def cost_so_far(self) -> float:
        return self.run1_cost() + float(self.memory.get("cost_usd", 0.0)) + self.run2_cost()

    def check_limits(self, label: str) -> None:
        elapsed = self.elapsed()
        if elapsed >= self.max_wall:
            raise DemoStop("timeout", f"{label}: 已达到 {self.max_wall:.0f}s 墙钟上限")
        if self.max_wall - elapsed < 20:
            raise DemoStop("timeout", f"{label}: 剩余墙钟不足 20s")
        if self.cost_so_far() >= self.max_cost:
            raise DemoStop("partial", f"{label}: 已达到 ${self.max_cost:.2f} 成本上限")

    def bind_memory(self, snapshot: str, sha256: str) -> None:
        """绑定本轮快照；只允许一次，不从共享文件或旧 state 自动恢复。"""
        if self.memory_sha256 is not None:
            raise DemoStop("partial", "memory 快照已绑定，拒绝二次绑定")
        self.memory_snapshot = snapshot
        self.memory_sha256 = sha256
        self.expected_memory_sha256 = sha256
        self.memory["snapshot"] = snapshot
        self.memory["sha256"] = sha256

    # -- 文档 -----------------------------------------------------------
    @staticmethod
    def _public(task: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in task.items() if not key.startswith("_")}

    def _run_block(self, tasks: list[dict[str, Any]], headroom: bool) -> dict:
        public = [self._public(task) for task in tasks]
        return {
            "solved": sum(1 for task in tasks if task["passed"]),
            "total": len(tasks),
            "headroom_solved": (
                sum(
                    1
                    for task in tasks
                    if task["passed"] and task["id"] in self.headroom_tasks
                )
                if headroom
                else 0
            ),
            "cost_usd": round(sum(float(task.get("_cost_usd", 0.0)) for task in tasks), 6),
            "wall_ms": sum(int(task.get("_wall_ms", 0)) for task in tasks),
            "tasks": public,
        }

    def _lineage(self) -> list[dict[str, Any]]:
        run1_ids = [task["id"] for task in self.run1_tasks]
        run2_ids = [task["id"] for task in self.run2_tasks]
        run1_note = "live baseline（无记忆）"
        if run1_ids:
            run1_note += "；族题 " + " / ".join(run1_ids)
        run2_note = f"live memory: {MEMORY_REF}"
        run2_note += "；x8 未跑" if self.profile == "fast" else "；x8 不计入 headroom"
        nodes = [
            {
                "gen": "v0",
                "label": "baseline",
                "passes": sum(1 for task in self.run1_tasks if task["passed"]),
                "runs": len(self.run1_tasks),
                "scope": run1_ids,
                "note": run1_note,
            },
            {
                "gen": "live",
                "label": "反思",
                "passes": sum(1 for task in self.run2_tasks if task["passed"]),
                "runs": len(self.run2_tasks),
                "scope": run2_ids,
                "note": run2_note,
            },
        ]
        # 只列实际跑过的代际；没开始的节点不画占位。
        return [node for node in nodes if node["runs"] > 0]

    def state_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema_version": "1.0.0",
            "live": True,
            "phase": self.phase,
            "started_at": self.started_at,
            "updated_at": _now(),
            "revision": "",
            "lineage": self._lineage(),
            "run1": self._run_block(self.run1_tasks, headroom=False),
            "memory": self.memory,
            "run2": self._run_block(self.run2_tasks, headroom=True),
            "verdict": self.verdict,
            "verdict_note": self.verdict_note,
            "cost_usd": round(self.cost_so_far(), 6),
            "wall_ms": int(self.elapsed() * 1000),
            "note": self.note,
        }
        document["revision"] = _content_hash(
            {key: value for key, value in document.items() if key != "revision"}
        )
        return document

    def write_state(self) -> None:
        document = self.state_document()
        _validate(document, _state_schema(), "state.json")
        _write_json(self.out, document)

    def _phase_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for phase, tasks in (("run1", self.run1_tasks), ("run2", self.run2_tasks)):
            for task in tasks:
                rows.append(
                    {
                        "phase": phase,
                        "task_id": task["id"],
                        "run_id": task.get("run_id", ""),
                        "outcome": task.get("_outcome", "unknown"),
                        "passed": bool(task["passed"]),
                        "cost_usd": round(float(task.get("_cost_usd", 0.0)), 6),
                        "wall_ms": int(task.get("_wall_ms", 0)),
                    }
                )
        return rows

    def run_document(self, phase: str, reason: str) -> dict[str, Any]:
        """构造 run.json；``memory_sha256`` 只用本轮绑定值，不回读共享文件。

        前置条件：每轮 ``run.json`` 的归档由调用方负责；``write_run`` 会覆盖同一路径。
        """
        phases = [
            {
                "name": "run1",
                "wall_ms": sum(int(task.get("_wall_ms", 0)) for task in self.run1_tasks),
                "cost_usd": round(self.run1_cost(), 6),
                "tasks": len(self.run1_tasks),
            },
            {
                "name": "reflection",
                "wall_ms": self.reflection_wall_ms,
                "cost_usd": round(float(self.memory.get("cost_usd", 0.0)), 6),
                "tasks": int(self.memory.get("attempts", 0)),
            },
            {
                "name": "run2",
                "wall_ms": sum(int(task.get("_wall_ms", 0)) for task in self.run2_tasks),
                "cost_usd": round(self.run2_cost(), 6),
                "tasks": len(self.run2_tasks),
            },
        ]
        document = {
            "schema_version": "1.0.0",
            "mode": "live",
            "phase": phase,
            "started_at": self.started_at,
            "finished_at": _now(),
            "wall_ms": int(self.elapsed() * 1000),
            "cost_usd": round(self.cost_so_far(), 6),
            "verdict": self.verdict,
            "verdict_note": self.verdict_note,
            "memory_ref": MEMORY_REF,
            "memory_sha256": self.memory_sha256,
            "pi_version": self.pi_version,
            "genomes": self.genomes,
            "phases": phases,
            "runs": self._phase_rows(),
            "budget": {
                "max_wall_s": self.max_wall,
                "max_cost_usd": self.max_cost,
                "exceeded": phase in {"partial", "timeout"},
                "reason": reason,
            },
            "note": self.note,
        }
        return document

    def write_run(self, phase: str, reason: str) -> None:
        document = self.run_document(phase, reason)
        _validate(document, RUN_SCHEMA, "run.json")
        _write_json(self.run_out, document)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _pi_version() -> str | None:
    try:
        completed = subprocess.run(
            ["pi", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    output = (completed.stdout or completed.stderr).strip()
    if not output:
        return None
    return output.splitlines()[0]


def _append_task(demo: Demo, phase: str, entry: dict[str, Any]) -> None:
    (demo.run1_tasks if phase == "run1" else demo.run2_tasks).append(entry)


def _memory_problem(demo: Demo) -> str | None:
    """检查点上的 memory 一致性；``None`` 表示通过。"""
    if demo.expected_memory_sha256 is None:
        return "run2 memory 未绑定（memory_sha256 为空）"
    expected = demo.expected_memory_sha256
    candidates = [(Path(ROOT) / MEMORY_REF, "共享 memory")]
    if demo.memory_snapshot is not None:
        candidates.append((Path(demo.state_dir) / demo.memory_snapshot, "memory 快照"))
    problems: list[str] = []
    from molt.core.hash import sha256_file

    for path, label in candidates:
        if not path.is_file():
            problems.append(f"{label} 不存在: {path}")
            continue
        try:
            actual = sha256_file(path)
        except OSError as exc:
            problems.append(f"{label} 不可读: {exc}")
            continue
        if actual != expected:
            problems.append(f"{label} hash 漂移: expected={expected} actual={actual}")
    return "; ".join(problems) if problems else None


def _check_memory_boundary(demo: Demo, phase: str, task_id: str, boundary: str) -> None:
    if phase != "run2":
        return
    problem = _memory_problem(demo)
    if problem is not None:
        raise DemoStop("partial", f"{phase}/{task_id} {boundary}检查失败: {problem}")


def _run_phase(
    demo: Demo, phase: str, task_ids: tuple[str, ...], genome_path: Path
) -> None:
    """逐题执行。

    完整性边界（条件式口径）：
    - 正常证据的前提是单写者：state/out 与共享 memory 不被并发或外部改写。
    - harness 每题执行前重读共享文件；before/after 检查只能发现检查点之间的漂移，
      不证明 harness 实际读取的字节等于快照，也不保证「边界间改后恢复」能被发现。
    - 并发或外部改写情况下，本检查不构成完整性证明；因此这些检查不消除 TOCTOU，
      也不否定遵守单写者假设的正常证据；after-check 失败的题不得计为成功。
    """
    for task_id in task_ids:
        demo.check_limits(f"{phase}/{task_id}")
        _check_memory_boundary(demo, phase, task_id, "before")
        remaining = demo.max_wall - demo.elapsed()
        timeout_s = max(1.0, min(remaining, 660.0))
        entry: dict[str, Any] | None = None
        error: Exception | None = None
        drift: DemoStop | None = None
        try:
            entry, infra = _execute(
                run_dir=demo.state_dir / phase / task_id,
                genome_path=genome_path,
                task_id=task_id,
                timeout_s=timeout_s,
            )
            if infra is not None:
                error = DemoStop("partial", infra)
        except Exception as exc:  # noqa: BLE001 - 保留原始错误，after-check 后再判定
            error = exc
        finally:
            # finally 语义：无论执行正常、抛错还是超时，都必须做 after-check。
            try:
                _check_memory_boundary(demo, phase, task_id, "after")
            except DemoStop as exc:
                drift = exc
        if drift is not None:
            problem = drift.message
            if entry is not None:
                # 保留诊断条目，但不得作为 success。
                entry["passed"] = False
                entry["passes"] = 0
                entry["_outcome"] = "memory_drift"
                entry["outcome"] = "memory_drift"
                entry["note"] = _join_note(
                    str(entry.get("note") or ""), f"memory_drift: {problem}"
                )
                _append_task(demo, phase, entry)
                demo.write_state()
            if error is not None:
                phase_name = getattr(error, "phase", "partial")
                message = f"{error}；同时 {problem}"
                demo.note = _join_note(demo.note, f"{phase}/{task_id}: {message}")
                demo.write_state()
                raise DemoStop(
                    "timeout" if phase_name == "timeout" else "partial", message
                ) from error
            demo.note = _join_note(demo.note, f"{phase}/{task_id}: {problem}")
            demo.write_state()
            raise drift
        if error is not None:
            if entry is not None:
                _append_task(demo, phase, entry)
                demo.write_state()
            raise error
        assert entry is not None
        _append_task(demo, phase, entry)
        demo.write_state()


def _abort(demo: Demo, error: DemoStop) -> int:
    demo.phase = error.phase
    demo.note = _join_note(demo.note, f"aborted: {error.message}")
    try:
        demo.write_state()
        demo.write_run(error.phase, error.message)
    except Exception as exc:  # noqa: BLE001 - 保底：至少把错误讲清楚
        print(f"error: 写降级证据失败: {exc}", file=sys.stderr)
    print(f"error: {error.message}", file=sys.stderr)
    return 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m probes.run_demo",
        description=(
            "现场 probe 演示：Run1（v0-pro-max）→ pi 反思生成 5 行 memory → "
            "Run2（按 profile 选题），增量写 state.json，最后写 run.json。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  uv run python -m probes.run_demo --profile fast \\\n"
            "      --state /tmp/molt-probe/demo --out web/data/probe/state.json\n"
            "  uv run python -m probes.run_demo --profile full --max-wall 420\n"
            "  uv run python -m probes.run_demo --validate  # 只校验已有产出\n"
        ),
    )
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default=DEFAULT_PROFILE,
        help=(
            "题集档位（默认 %(default)s）：fast = T2 + x5/x6；"
            "full = T2/x3 + x5–x8"
        ),
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("/tmp/molt-probe/demo"),
        help="临时 state 根目录（必须在仓库外），默认 /tmp/molt-probe/demo",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("web/data/probe/state.json"),
        help="state.json 输出路径（相对路径按仓库根解析）",
    )
    parser.add_argument(
        "--run-out",
        type=Path,
        default=None,
        help="run.json 输出路径，默认与 --out 同目录的 run.json",
    )
    parser.add_argument(
        "--max-wall",
        type=float,
        default=None,
        help=(
            "整场演示的墙钟硬上限（秒）；默认 fast=360、full=420，"
            "显式传入则覆盖"
        ),
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=1.2,
        help="整场演示的 USD 成本上限，默认 1.2（验收预算）",
    )
    parser.add_argument(
        "--model",
        default="deepseek/deepseek-v4-pro",
        help="Run1/Run2/反思使用的模型，默认 deepseek/deepseek-v4-pro",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=True,
        help="现场模式（固定 true；保留旗标用于命令自解释）",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="不跑演示，只校验 --out / --run-out 是否满足各自 schema",
    )
    return parser.parse_args(argv)


def _resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    state_dir = Path(args.state).expanduser().resolve()
    out = Path(args.out).expanduser()
    if not out.is_absolute():
        out = ROOT / out
    out = out.resolve()
    run_out = args.run_out
    if run_out is None:
        run_out = out.with_name("run.json")
    else:
        run_out = Path(run_out).expanduser()
        if not run_out.is_absolute():
            run_out = ROOT / run_out
        run_out = run_out.resolve()
    return state_dir, out, run_out


def _tracked_probe_files() -> set[Path] | None:
    """``git ls-files`` 列出的 probe 数据文件（真实路径）。

    返回 ``None`` 表示无法取得清单（git 不可用 / 非 git 仓库 / 命令失败），
    调用方必须 fail closed，不得回退为空集合。
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "--", PROTECTED_PROBE_ROOT],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return {(ROOT / name).resolve() for name in completed.stdout.split("\0") if name}


def _check_paths(state_dir: Path, out: Path, run_out: Path) -> str | None:
    state_dir = Path(state_dir).resolve()
    out = Path(out).resolve()
    run_out = Path(run_out).resolve()
    if state_dir == ROOT or state_dir.is_relative_to(ROOT):
        return f"--state 必须在仓库外（现在是 {state_dir}）"
    data_root = (ROOT / "web/data").resolve()
    for label, path in (("--out", out), ("--run-out", run_out)):
        if path.is_relative_to(data_root) and not path.is_relative_to(data_root / "probe"):
            return f"{label} 只能写 web/data/probe/**（现在是 {path}）"
    protected = _tracked_probe_files()
    if protected is None:
        return "无法读取 tracked probe 文件清单（git ls-files 失败），拒绝写入以免覆盖受保护证据"
    for label, path in (("--out", out), ("--run-out", run_out)):
        if path in protected:
            return f"{label} 不得覆盖 tracked probe 数据文件（{path}）"
    return None


def _validate_outputs(state_dir: Path, out: Path, run_out: Path) -> int:
    del state_dir
    failures = 0
    try:
        _validate(
            json.loads(out.read_text(encoding="utf-8")), _state_schema(), "state.json"
        )
        print(f"state ok {out}")
    except Exception as exc:  # noqa: BLE001 - 校验模式要把原因打出来
        print(f"state FAIL {exc}", file=sys.stderr)
        failures += 1
    try:
        _validate(json.loads(run_out.read_text(encoding="utf-8")), RUN_SCHEMA, "run.json")
        print(f"run ok {run_out}")
    except Exception as exc:  # noqa: BLE001
        print(f"run FAIL {exc}", file=sys.stderr)
        failures += 1
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    state_dir, out, run_out = _resolve_paths(args)
    problem = _check_paths(state_dir, out, run_out)
    if problem is not None:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    if args.validate:
        return _validate_outputs(state_dir, out, run_out)

    max_wall = (
        args.max_wall
        if args.max_wall is not None
        else float(PROFILES[args.profile]["max_wall"])
    )
    demo = Demo(
        state_dir=state_dir,
        out=out,
        run_out=run_out,
        max_wall=max_wall,
        max_cost=args.max_cost,
        model=args.model,
        profile=args.profile,
    )
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        version = _pi_version()
        if version is None:
            raise DemoStop("partial", "pi 不可用：`pi --version` 失败")
        demo.pi_version = version
        demo.write_state()

        run1_genome = _prepare_run1_genome(args.model, state_dir / "genomes/run1.json")
        demo.genomes["run1"] = {
            "genome_id": run1_genome.genome_id,
            "path": "genomes/run1.json",
        }
        _run_phase(demo, "run1", demo.run1_scope, state_dir / "genomes/run1.json")

        demo.check_limits("reflection")
        demo.phase = "reflection"
        failures = _failures(demo.run1_tasks)
        demo.memory["source_cases"] = [
            f"{task['id']}/{case['case_id']}"
            for task in demo.run1_tasks
            for case in task["cases"]
            if not case["passed"]
        ]
        demo.write_state()

        if not failures:
            demo.memory["lines"] = ["Run1 全部用例通过，没有可反思的失败。"]
            _publish_memory(demo, demo.memory["lines"])
            demo.note = _join_note(demo.note, "run1 无失败：memory 为空规则集")
        else:
            reflection_started = time.monotonic()
            prompt = _reflection_prompt(failures)
            (state_dir / "reflection").mkdir(parents=True, exist_ok=True)
            (state_dir / "reflection/prompt.txt").write_text(prompt, encoding="utf-8")
            functions = {
                _function_name(task_id)
                for task_id in demo.run1_scope + demo.run2_scope
            }
            remaining = demo.max_wall - demo.elapsed()
            reflection_timeout = max(1.0, min(remaining, 240.0))
            lines, cost, attempts, hits = _reflect(
                prompt,
                state_dir=state_dir,
                model=args.model,
                timeout_s=reflection_timeout,
                timeout_phase="timeout" if remaining <= 240.0 else "partial",
                functions=functions,
            )
            demo.reflection_wall_ms = int((time.monotonic() - reflection_started) * 1000)
            demo.memory["lines"] = lines
            demo.memory["cost_usd"] = round(cost, 6)
            demo.memory["attempts"] = attempts
            demo.memory["reflection_model"] = args.model
            if hits:
                demo.note = _join_note(
                    demo.note, f"反思输出含可疑标识符: {', '.join(hits)}"
                )
            _publish_memory(demo, lines)

        demo.write_state()
        demo.check_limits("run2")
        demo.phase = "run2"
        snapshot_path = (
            state_dir / demo.memory_snapshot if demo.memory_snapshot else None
        )
        run2_genome = _prepare_run2_genome(
            run1_genome,
            args.model,
            state_dir / "genomes/run2.json",
            memory_snapshot=snapshot_path,
            memory_sha256=demo.memory_sha256,
        )
        demo.genomes["run2"] = {
            "genome_id": run2_genome.genome_id,
            "path": "genomes/run2.json",
        }
        _run_phase(demo, "run2", demo.run2_scope, state_dir / "genomes/run2.json")

        passed = {task["id"]: bool(task["passed"]) for task in demo.run2_tasks}
        demo.verdict = _verdict_for(passed, demo.profile)
        demo.verdict_note = PROFILES[demo.profile]["verdict_note"]
        demo.phase = "done"
        run1_solved = sum(1 for task in demo.run1_tasks if task["passed"])
        run2_solved = sum(1 for task in demo.run2_tasks if task["passed"])
        demo.note = _join_note(
            demo.note,
            f"live demo {args.model}（{demo.profile}）："
            f"run1 {run1_solved}/{len(demo.run1_scope)}，"
            f"run2 {run2_solved}/{len(demo.run2_scope)}",
        )
        demo.write_state()
        demo.write_run("done", "")
    except DemoStop as error:
        return _abort(demo, error)

    print(
        json.dumps(
            {
                "profile": demo.profile,
                "phase": demo.phase,
                "verdict": demo.verdict,
                "verdict_note": demo.verdict_note,
                "cost_usd": round(demo.cost_so_far(), 6),
                "wall_ms": int(demo.elapsed() * 1000),
                "state": str(out),
                "run": str(run_out),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
