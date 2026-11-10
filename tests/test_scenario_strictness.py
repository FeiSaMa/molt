"""D4-j：``scenario.ext`` 的显式题集规模 / 正预算契约。

demo（10+5）在 ``scenarios/demo/scenario.json`` 的 ext 里声明：

```json
{"expected_task_counts": {"search": 10, "report": 5}, "require_positive_budget": true}
```

未声明这些 ext 的 scenario（svc-config 2/1、预算 0）不受影响。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from molt.bench import compute_bench_hash
from molt.cli import main

ROOT = Path(__file__).resolve().parents[1]
DEMO_SCENARIO = Path("scenarios/demo/scenario.json")


def _copy_repo(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    for name in ("schemas", "scenarios", "bench", "shells"):
        shutil.copytree(ROOT / name, target / name)
    return target


def _clone_task(root: Path, source_rel: str, new_id: str, scope: str) -> str:
    scope_dir = "search" if scope == "search" else "holdout"
    source = root / source_rel
    target = root / "bench" / scope_dir / new_id
    shutil.copytree(source, target)
    task_path = target / "task.json"
    data = json.loads(task_path.read_text(encoding="utf-8"))
    data["task_id"] = new_id
    data["scope"] = scope
    data["workspace_ref"] = f"bench/{scope_dir}/{new_id}/workspace"
    data["tests_ref"] = f"bench/{scope_dir}/{new_id}/tests"
    task_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return new_id


def _grow_tasks(root: Path, search_total: int, report_total: int) -> tuple[list[str], list[str]]:
    search = ["smoke-add", "smoke-even"]
    for index in range(len(search), search_total):
        search.append(_clone_task(root, "bench/search/smoke-add", f"demo-s{index:02d}", "search"))
    report = ["holdout-even"]
    for index in range(len(report), report_total):
        report.append(
            _clone_task(root, "bench/holdout/holdout-even", f"demo-h{index:02d}", "report")
        )
    return search, report


def _configure_demo(
    root: Path,
    search: list[str],
    report: list[str],
    *,
    expected_counts: bool,
    positive_budget: bool,
    budget: tuple[float, float] | None = None,
) -> None:
    path = root / DEMO_SCENARIO
    data = json.loads(path.read_text(encoding="utf-8"))
    data["task_sets"] = {"search": search, "report": report}
    if expected_counts:
        data["ext"]["expected_task_counts"] = {"search": 10, "report": 5}
    else:
        data["ext"].pop("expected_task_counts", None)
    if positive_budget:
        data["ext"]["require_positive_budget"] = True
    else:
        data["ext"].pop("require_positive_budget", None)
    if budget is not None:
        data["policy"]["budget"] = {
            "execution_budget_usd": budget[0],
            "search_overhead_budget_usd": budget[1],
        }
    data["bench_hash"] = compute_bench_hash(root, [*search, *report])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate(root: Path, scenario: str = str(DEMO_SCENARIO)) -> int:
    return main(["--root", str(root), "scenario", "validate", "--scenario", scenario])


def test_nine_task_demo_with_repinned_hash_fails(tmp_path: Path, capsys) -> None:
    root = _copy_repo(tmp_path)
    search, report = _grow_tasks(root, 9, 1)
    _configure_demo(root, search, report, expected_counts=True, positive_budget=True)
    assert _validate(root) == 1
    captured = capsys.readouterr()
    assert "ext.expected_task_counts.search: 期望 10 题，实际 9 题" in captured.err
    assert "ext.expected_task_counts.report: 期望 5 题，实际 1 题" in captured.err


def test_zero_budget_demo_fails(tmp_path: Path, capsys) -> None:
    root = _copy_repo(tmp_path)
    _configure_demo(
        root,
        ["smoke-add", "smoke-even"],
        ["holdout-even"],
        expected_counts=False,
        positive_budget=True,
        budget=(0.0, 0.0),
    )
    assert _validate(root) == 1
    captured = capsys.readouterr()
    assert "policy.budget.execution_budget_usd: 期望 > 0" in captured.err
    assert "policy.budget.search_overhead_budget_usd: 期望 > 0" in captured.err


def test_ten_plus_five_demo_passes(tmp_path: Path) -> None:
    root = _copy_repo(tmp_path)
    search, report = _grow_tasks(root, 10, 5)
    _configure_demo(root, search, report, expected_counts=True, positive_budget=True)
    assert _validate(root) == 0


def test_svc_config_two_and_one_zero_budget_still_passes() -> None:
    assert (
        main(
            [
                "--root",
                str(ROOT),
                "scenario",
                "validate",
                "--scenario",
                "scenarios/svc-config/scenario.json",
            ]
        )
        == 0
    )


def test_current_demo_without_ext_contract_passes() -> None:
    """未声明 ext 契约的 2/1 demo 不被误伤。"""
    assert main(["--root", str(ROOT), "scenario", "validate"]) == 0
