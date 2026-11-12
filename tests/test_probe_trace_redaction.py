"""probe verifier 的 trace 必须只含 case_id/expected/actual/passed，不含规则词与路径。"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

from molt.core.types import Task

ROOT = Path(__file__).resolve().parents[1]
TASK_PATH = ROOT / "bench/probe/probe-order-1/task.json"
TESTS_DIR = ROOT / "bench/probe/probe-order-1/tests"
SOLUTIONS = ROOT / "bench/probe/probe-order-1/solutions"
FORBIDDEN = (
    "canonical",
    "lexicographic",
    "sort",
    "order",
    "priority",
    "length",
    "casefold",
    "shortlex",
)


def _load_verifier():
    path = ROOT / "shells/probe/verifier.py"
    spec = importlib.util.spec_from_file_location("probe_verifier_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ProbeVerifier


def _task() -> Task:
    return Task.from_dict(json.loads(TASK_PATH.read_text(encoding="utf-8")))


def _workspace(tmp_path: Path, solution_name: str) -> Path:
    workdir = tmp_path / "work"
    shutil.copytree(ROOT / "bench/probe/probe-order-1/workspace", workdir)
    (workdir / "solution.py").write_text(
        (SOLUTIONS / solution_name).read_text(encoding="utf-8"), encoding="utf-8"
    )
    return workdir


def _verdict(solution_name: str, tmp_path: Path):
    verifier = _load_verifier()(ROOT)
    task = _task()
    workdir = _workspace(tmp_path, solution_name)
    before = verifier.guard(task, workdir)
    return verifier.verify(task, workdir, before=before)


def test_reference_passes_and_stub_fails(tmp_path: Path) -> None:
    reference = _verdict("reference.py", tmp_path / "ref")
    stub = _verdict("stub.py", tmp_path / "stub")
    assert reference.passed is True
    assert stub.passed is False
    assert reference.diagnostic["cases"]
    assert stub.diagnostic["cases"]


def test_forced_failure_trace_is_redacted(tmp_path: Path) -> None:
    outcome = _verdict("stub.py", tmp_path)
    assert outcome.passed is False
    assert outcome.diagnostic["timed_out"] is False
    cases = outcome.diagnostic["cases"]
    assert cases
    assert all(set(case) == {"case_id", "expected", "actual", "passed"} for case in cases)

    text = json.dumps(outcome.diagnostic, ensure_ascii=False).lower()
    for word in FORBIDDEN:
        assert word not in text, f"trace 泄漏规则词: {word}"
    for marker in ("bench/", "check_cases", "test_", "probe-order-1", ".py"):
        assert marker not in text, f"trace 泄漏路径/文件名: {marker}"


def test_hidden_tests_contain_no_rule_words() -> None:
    for path in sorted(TESTS_DIR.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").lower()
        for word in FORBIDDEN:
            assert word not in text, f"{path.name} 含规则词: {word}"
        assert "test_" not in text
