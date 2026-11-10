"""P3：bench_hash 覆盖题面内容，未引用候选文件不影响。

严格性说明：``scenario validate`` 建索引时会解析 ``bench/**`` 下全部 ``task.json``，
未引用的残缺 task.json 也会让它失败；这是有意的严格性（见 ADR-0002）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from molt.bench import compute_bench_hash
from molt.cli import main

ROOT = Path(__file__).resolve().parents[1]
REFERENCED = ["smoke-add", "smoke-even", "holdout-even"]


def _copy_repo(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    for name in ("schemas", "scenarios", "bench", "shells"):
        shutil.copytree(ROOT / name, target / name)
    return target


def _mutate_task_json(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["ext"]["fixture"]["solution"] = "def add(a, b):\n    return 0\n"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_hash_changes_when_referenced_task_content_changes(tmp_path: Path) -> None:
    _copy_repo(tmp_path)
    before = compute_bench_hash(tmp_path / "repo", REFERENCED)
    _mutate_task_json(tmp_path / "repo/bench/search/smoke-add/task.json")
    assert compute_bench_hash(tmp_path / "repo", REFERENCED) != before


def test_hash_ignores_unreferenced_candidate_files(tmp_path: Path) -> None:
    _copy_repo(tmp_path)
    root = tmp_path / "repo"
    before = compute_bench_hash(root, REFERENCED)
    candidate = root / "bench/search/unused-candidate"
    (candidate / "workspace").mkdir(parents=True)
    (candidate / "workspace/solution.py").write_text("x = 1\n", encoding="utf-8")
    (candidate / "task.json").write_text(
        json.dumps({"task_id": "unused-candidate"}), encoding="utf-8"
    )
    assert compute_bench_hash(root, REFERENCED) == before


def test_scenario_validate_accepts_repository_fixture(tmp_path: Path) -> None:
    root = _copy_repo(tmp_path)
    assert main(["--root", str(root), "scenario", "validate"]) == 0


def test_scenario_validate_rejects_mutated_task_content(tmp_path: Path) -> None:
    root = _copy_repo(tmp_path)
    _mutate_task_json(root / "bench/search/smoke-add/task.json")
    assert main(["--root", str(root), "scenario", "validate"]) == 1


def test_scenario_validate_ignores_unreferenced_candidate(tmp_path: Path) -> None:
    root = _copy_repo(tmp_path)
    candidate = root / "bench/search/experimental"
    (candidate / "workspace").mkdir(parents=True)
    (candidate / "workspace/solution.py").write_text("x = 1\n", encoding="utf-8")
    assert main(["--root", str(root), "scenario", "validate"]) == 0
