"""D4：``molt calibrate`` 用 fixture baseline 校准 search 通过数。

10+5 场景由 B2c-1 提供；本测试按「search 题集长度 == 10」自动发现该 scenario，
因此两个并行分支合并后即可运行，未合入时跳过而不是伪造题集。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(ROOT / "tests/run_cli.py"), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def _scenario_with_search_size(size: int) -> str | None:
    for path in sorted(ROOT.glob("scenarios/*/scenario.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if len(data.get("task_sets", {}).get("search", [])) == size:
            return str(data["scenario_id"])
    return None


@pytest.fixture(scope="module")
def ten_task_scenario() -> str:
    scenario_id = _scenario_with_search_size(10)
    if scenario_id is None:
        pytest.skip("10+5 场景尚未合入（B2c-1）；calibrate 的通用路径由 smoke 用例覆盖")
    return scenario_id


def test_calibrate_10_task_baseline_passes(ten_task_scenario: str, tmp_path: Path) -> None:
    completed = _run_cli(
        "calibrate",
        "--scenario",
        ten_task_scenario,
        "--min-pass",
        "3",
        "--state",
        str(tmp_path / "state"),
    )
    assert completed.returncode == 0, completed.stderr
    assert "calibrate solved=3 min=3" in completed.stdout


def test_calibrate_min_pass_above_baseline_fails(ten_task_scenario: str, tmp_path: Path) -> None:
    completed = _run_cli(
        "calibrate",
        "--scenario",
        ten_task_scenario,
        "--min-pass",
        "4",
        "--state",
        str(tmp_path / "state"),
    )
    assert completed.returncode == 1, completed.stderr
    assert "calibrate solved=3 min=4" in completed.stdout


def _tree_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.is_symlink():
            snapshot[str(relative)] = "symlink:" + os.readlink(path)
        elif path.is_dir():
            snapshot[str(relative)] = "dir"
        elif path.suffix == ".pyc":
            continue
        else:
            import hashlib

            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot[str(relative)] = f"file:{path.stat().st_size}:{digest}"
    return snapshot


def test_calibrate_default_state_does_not_write_repository(ten_task_scenario: str) -> None:
    before = _tree_snapshot(ROOT)
    completed = _run_cli("calibrate", "--scenario", ten_task_scenario, "--min-pass", "3")
    assert completed.returncode == 0, completed.stderr
    after = _tree_snapshot(ROOT)
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(key for key in set(before) & set(after) if before[key] != after[key])
    assert added == [], f"新增路径: {added}"
    assert removed == [], f"删除路径: {removed}"
    assert changed == [], f"内容变化: {changed}"


@pytest.mark.parametrize("scenario_id", ["demo", "svc-config"])
def test_calibrate_runs_on_current_scenarios(scenario_id: str, tmp_path: Path) -> None:
    """当前分支的通用路径冒烟：强 fixture、输出格式、退出码。"""
    completed = _run_cli(
        "calibrate",
        "--scenario",
        scenario_id,
        "--min-pass",
        "0",
        "--state",
        str(tmp_path / "state"),
    )
    assert completed.returncode == 0, completed.stderr
    assert "calibrate solved=" in completed.stdout
    assert "min=0" in completed.stdout
