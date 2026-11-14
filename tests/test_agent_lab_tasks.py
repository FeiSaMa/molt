"""Self-check for chat-lab coding fixtures.

Every task must fail its round-1 tests and pass after its deterministic
``expected_fix``. This keeps the fixtures honest without starting the sandbox.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from molt.agent.lab_tasks import TASKS


def _run_tests(directory: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "-q"],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _apply_fix(task, directory: Path) -> None:
    patch = task.expected_fix
    target = directory / patch["file"]
    if "content" in patch:
        target.write_text(patch["content"], encoding="utf-8")
        return
    current = target.read_text(encoding="utf-8")
    assert patch["old"] in current, f"{task.task_id}: expected_fix old 片段不存在"
    target.write_text(current.replace(patch["old"], patch["new"], 1), encoding="utf-8")


@pytest.mark.parametrize("task_id", sorted(TASKS))
def test_fixture_fails_before_fix_and_passes_after(task_id: str, tmp_path: Path) -> None:
    task = TASKS[task_id]
    for name, content in task.files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    before = _run_tests(tmp_path)
    assert before.returncode != 0, f"{task_id} 的 round1 测试不应通过"
    _apply_fix(task, tmp_path)
    after = _run_tests(tmp_path)
    assert after.returncode == 0, (
        f"{task_id} 在 expected_fix 之后仍未通过:\n{after.stdout}\n{after.stderr}"
    )
