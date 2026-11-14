"""``GET /api/tasks`` 的只读数据装配。

只读 ``<data_dir>/scenario.json`` 与 ``<data_dir>/runs.json``：前者给出题目与顺序，
后者按 ``demo_group`` / ``provenance`` 为每题选出 first / second 两条 run。

选择规则（完全确定，不读时钟、不写文件）：

* first：``demo_group="scan10"``；缺失时退回 ``demo_group="closure"`` 且
  ``provenance.kind="replay"``。
* second：``demo_group="candidate-scan"``；缺失时退回 ``demo_group="closure"`` 且
  ``provenance.live is true``。
* 同一组多条候选时按 ``run_id`` 字典序取第一条；无 run 时 ``cost=0.0`` /
  ``passed=false``。

文件缺失、JSON 非法或结构不符一律 fail closed（:class:`TaskResultsError`），
由 HTTP 层映射成 503 ``data_unavailable``。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCENARIO_NAME = "scenario.json"
RUNS_NAME = "runs.json"


class TaskResultsError(ValueError):
    """任务结果数据不可用（缺文件 / JSON 非法 / 结构不符）。"""


def _fail(message: str) -> TaskResultsError:
    return TaskResultsError(message)


def _load_json(data_dir: Path, name: str) -> Any:
    path = data_dir / name
    try:
        raw = path.read_bytes()
    except OSError:
        raise _fail(f"无法读取 {name}") from None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _fail(f"{name} 不是合法 JSON") from None


def _provenance(run: dict[str, Any]) -> dict[str, Any]:
    provenance = run.get("provenance")
    return provenance if isinstance(provenance, dict) else {}


def _is_replay(run: dict[str, Any]) -> bool:
    return _provenance(run).get("kind") == "replay"


def _is_live(run: dict[str, Any]) -> bool:
    return _provenance(run).get("live") is True


def _select(
    runs: list[dict[str, Any]],
    group: str,
    predicate: Callable[[dict[str, Any]], bool] = lambda _run: True,
) -> dict[str, Any] | None:
    """按 ``demo_group`` + 谓词筛候选；同组多条按 ``run_id`` 排序取第一条。"""

    candidates = [run for run in runs if run.get("demo_group") == group and predicate(run)]
    if not candidates:
        return None
    candidates.sort(key=lambda run: str(run.get("run_id", "")))
    return candidates[0]


def _score(run: dict[str, Any] | None) -> tuple[float, bool]:
    if run is None:
        return 0.0, False
    cost = run.get("cost_usd")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
        raise _fail("run.cost_usd 必须是非负数")
    solved = run.get("solved")
    if not isinstance(solved, bool):
        raise _fail("run.solved 必须是布尔值")
    return float(cost), solved


def _search_tasks(scenario: Any) -> list[str]:
    if not isinstance(scenario, dict):
        raise _fail(f"{SCENARIO_NAME} 必须是对象")
    task_sets = scenario.get("task_sets")
    if not isinstance(task_sets, dict):
        raise _fail(f"{SCENARIO_NAME}.task_sets 必须是对象")
    search = task_sets.get("search")
    if not isinstance(search, list) or not search:
        raise _fail(f"{SCENARIO_NAME}.task_sets.search 必须是非空数组")
    if not all(isinstance(task_id, str) and task_id for task_id in search):
        raise _fail(f"{SCENARIO_NAME}.task_sets.search 只能是非空字符串")
    return list(search)


def load_task_results(data_dir: Path | str) -> dict[str, Any]:
    """返回 ``{"results": [...]}``，顺序与 ``scenario.json`` 的搜索集一致。"""

    root = Path(data_dir)
    tasks = _search_tasks(_load_json(root, SCENARIO_NAME))

    document = _load_json(root, RUNS_NAME)
    runs = document.get("runs") if isinstance(document, dict) else None
    if not isinstance(runs, list):
        raise _fail(f"{RUNS_NAME} 缺少 runs 数组")

    results: list[dict[str, Any]] = []
    for task_id in tasks:
        task_runs = [run for run in runs if isinstance(run, dict) and run.get("task_id") == task_id]
        first = _select(task_runs, "scan10")
        if first is None:
            first = _select(task_runs, "closure", _is_replay)
        second = _select(task_runs, "candidate-scan")
        if second is None:
            second = _select(task_runs, "closure", _is_live)
        cost_first, passed_first = _score(first)
        cost_second, passed_second = _score(second)
        results.append(
            {
                "id": task_id,
                "cost_first": cost_first,
                "passed_first": passed_first,
                "cost_second": cost_second,
                "passed_second": passed_second,
            }
        )
    return {"results": results}


def _probe_cost(row: dict[str, Any], task_id: str, rep: int) -> float:
    value = row.get("cost_usd")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise _fail(f"probe cost_usd invalid for {task_id} rep {rep}")
    return float(value)


def _probe_solved(row: dict[str, Any], task_id: str, rep: int) -> bool:
    value = row.get("solved")
    if value not in (0, 1, True, False):
        raise _fail(f"probe solved invalid for {task_id} rep {rep}")
    return bool(value)


def load_probe_task_results(repo_root: Path | str) -> dict[str, Any]:
    """Build page4 pairs from real probe 2c baseline vs memory-v3 rows.

    These are historical real-Pi rows, not a fresh model run. The page labels
    this source explicitly; the fixture/replay demo remains the default.
    """

    root = Path(repo_root)
    baseline_doc = _load_json(root / "probes" / "report" / "2c", "rows.json")
    candidate_doc = _load_json(
        root / "probes" / "report" / "2c" / "memory-v3", "rows.json"
    )
    baseline_rows = baseline_doc.get("runs") if isinstance(baseline_doc, dict) else None
    candidate_rows = candidate_doc.get("runs") if isinstance(candidate_doc, dict) else None
    if not isinstance(baseline_rows, list) or not isinstance(candidate_rows, list):
        raise _fail("probe rows 缺少 runs 数组")

    baseline_index: dict[tuple[str, int], dict[str, Any]] = {}
    for row in baseline_rows:
        if not isinstance(row, dict) or row.get("group") != "baseline":
            continue
        task_id, rep = row.get("task_id"), row.get("rep")
        if isinstance(task_id, str) and isinstance(rep, int):
            baseline_index.setdefault((task_id, rep), row)
    candidate_index: dict[tuple[str, int], dict[str, Any]] = {}
    for row in candidate_rows:
        if not isinstance(row, dict):
            continue
        task_id, rep = row.get("task_id"), row.get("rep")
        if isinstance(task_id, str) and isinstance(rep, int):
            candidate_index.setdefault((task_id, rep), row)

    results: list[dict[str, Any]] = []
    for (task_id, rep), baseline in sorted(baseline_index.items()):
        candidate = candidate_index.get((task_id, rep))
        if candidate is None:
            raise _fail(f"probe candidate missing for {task_id} rep {rep}")
        results.append(
            {
                "id": f"{task_id}-r{rep}",
                "cost_first": _probe_cost(baseline, task_id, rep),
                "passed_first": _probe_solved(baseline, task_id, rep),
                "cost_second": _probe_cost(candidate, task_id, rep),
                "passed_second": _probe_solved(candidate, task_id, rep),
            }
        )
    if not results:
        raise _fail("probe rows 为空")
    return {
        "results": results,
        "source": "probe-2c-memory-v3",
        "note": "historical real Pi rows; not a new model run",
    }
