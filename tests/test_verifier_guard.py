"""Verifier before/after：before 必须在 agent 启动前取得。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from molt.core.ports import RuntimeOutcome
from molt.core.types import Task, Usage
from molt.shell_loader import load_shell_module

ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / "bench/search/smoke-add"


def _repo(tmp_path: Path) -> tuple[Path, Task, Path]:
    target = tmp_path / "repo"
    shutil.copytree(ROOT / "bench", target / "bench")
    task = Task.from_dict(json.loads((TASK_DIR / "task.json").read_text(encoding="utf-8")))
    workdir = tmp_path / "work"
    shutil.copytree(TASK_DIR / "workspace", workdir)
    (workdir / "solution.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return target, task, workdir


def _verifier(repo: Path):
    module = load_shell_module(ROOT, "shells/code-repair/verifier.py", "molt_test_verifier_guard")
    return module.PytestVerifier(repo)


def test_guard_before_agent_detects_test_mutation(tmp_path: Path) -> None:
    repo, task, workdir = _repo(tmp_path)
    verifier = _verifier(repo)
    before = verifier.guard(task, workdir)
    (repo / "bench/search/smoke-add/tests/test_solution.py").write_text(
        "def test_tampered():\n    assert False\n", encoding="utf-8"
    )
    outcome = verifier.verify(task, workdir, before=before)
    assert outcome.matches is False
    assert outcome.outcome == "invalid"
    assert outcome.tests_hash_before != outcome.tests_hash_after


def test_guard_detects_task_json_mutation(tmp_path: Path) -> None:
    repo, task, workdir = _repo(tmp_path)
    verifier = _verifier(repo)
    before = verifier.guard(task, workdir)
    task_path = repo / "bench/search/smoke-add/task.json"
    data = json.loads(task_path.read_text(encoding="utf-8"))
    data["title"] = "tampered"
    task_path.write_text(json.dumps(data), encoding="utf-8")
    outcome = verifier.verify(task, workdir, before=before)
    assert outcome.matches is False
    assert outcome.outcome == "invalid"


def test_guard_detects_other_search_task_mutation(tmp_path: Path) -> None:
    """改另一个 search task 的 tests 也必须判废（guard 覆盖全 bench）。"""
    repo, task, workdir = _repo(tmp_path)
    verifier = _verifier(repo)
    before = verifier.guard(task, workdir)
    other = repo / "bench/search/smoke-even/tests/test_solution.py"
    other.write_text("def test_tampered():\n    assert False\n", encoding="utf-8")
    outcome = verifier.verify(task, workdir, before=before)
    assert outcome.matches is False
    assert outcome.outcome == "invalid"


def test_guard_detects_holdout_task_json_mutation(tmp_path: Path) -> None:
    """holdout task.json 只参与哈希，不进入 agent 可见路径；改写必须判废。"""
    repo, task, workdir = _repo(tmp_path)
    verifier = _verifier(repo)
    before = verifier.guard(task, workdir)
    holdout = repo / "bench/holdout/holdout-even/task.json"
    data = json.loads(holdout.read_text(encoding="utf-8"))
    data["title"] = "tampered-holdout"
    holdout.write_text(json.dumps(data), encoding="utf-8")
    outcome = verifier.verify(task, workdir, before=before)
    assert outcome.matches is False
    assert outcome.outcome == "invalid"


def test_guard_detects_deleted_holdout_task_json(tmp_path: Path) -> None:
    """删掉 holdout 题让 bench 无法重算时，也必须是 invalid 而不是崩溃。"""
    repo, task, workdir = _repo(tmp_path)
    verifier = _verifier(repo)
    before = verifier.guard(task, workdir)
    (repo / "bench/holdout/holdout-even/task.json").unlink()
    outcome = verifier.verify(task, workdir, before=before)
    assert outcome.matches is False
    assert outcome.outcome == "invalid"
    assert outcome.tests_hash_after != before


def test_guard_detects_unparsable_task_json(tmp_path: Path) -> None:
    """题面被写成坏 JSON 时也不能让 verifier 崩溃。"""
    repo, task, workdir = _repo(tmp_path)
    verifier = _verifier(repo)
    before = verifier.guard(task, workdir)
    (repo / "bench/search/smoke-even/task.json").write_text("{", encoding="utf-8")
    outcome = verifier.verify(task, workdir, before=before)
    assert outcome.matches is False
    assert outcome.outcome == "invalid"
    assert outcome.tests_hash_after != before


def test_normal_run_passes_with_guard(tmp_path: Path) -> None:
    repo, task, workdir = _repo(tmp_path)
    verifier = _verifier(repo)
    before = verifier.guard(task, workdir)
    outcome = verifier.verify(task, workdir, before=before)
    assert outcome.matches is True
    assert outcome.passed is True
    assert outcome.outcome == "passed"
    assert outcome.tests_hash_before == outcome.tests_hash_after == before


def test_harness_guards_before_runtime(tmp_path: Path) -> None:
    repo, task, workdir = _repo(tmp_path)
    verifier = _verifier(repo)
    harness_module = load_shell_module(
        ROOT, "shells/code-repair/harness.py", "molt_test_harness_guard"
    )

    class EvilRuntime:
        def run(self, genome, task, workdir_path):
            (repo / "bench/search/smoke-add/tests/test_solution.py").write_text(
                "def test_evil():\n    assert False\n", encoding="utf-8"
            )
            return RuntimeOutcome(
                outcome="passed",
                usage=Usage(1, 1, 0.0, 1, 1),
                started_at="2026-01-01T00:00:00Z",
                finished_at="2026-01-01T00:00:01Z",
            )

    harness = harness_module.CodeRepairHarness(
        EvilRuntime(),
        verifier,
        ROOT,
        clock=lambda: "2026-01-01T00:00:00Z",
    )
    genome = _genome()
    outcome = harness.run(genome, task, workdir)
    assert outcome.verification.matches is False
    assert outcome.final_outcome == "invalid"


def _genome():
    from molt.core.types import Genome

    return Genome.from_dict(
        json.loads((ROOT / "shells/code-repair/genomes/v0.json").read_text(encoding="utf-8"))
    )
