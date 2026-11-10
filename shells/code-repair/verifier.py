"""code-repair shell 的 Verifier：在候选工作区之外只读运行 pytest。

运行前后对**整个 ``bench/``** 取树哈希（全部 task.json + workspace + tests）；
任一文件变化即判废（``matches=False``），改其他题或 holdout 题也逃不掉。
本模块不依赖 Pi，也不 import 任何第三方 Python 包。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from molt.bench import bench_tree_hash
from molt.core.hash import sha256_text
from molt.core.ports import VerifyOutcome
from molt.core.types import Task


class PytestVerifier:
    """外部测试目录只读；失败 / 超时 / 哈希变化分别可辨。"""

    def __init__(self, repo_root: Path, *, python: str | None = None) -> None:
        self.repo_root = Path(repo_root)
        self.python = python or sys.executable

    def _tests_dir(self, task: Task) -> Path:
        path = self.repo_root / task.tests_ref
        if not path.exists():
            raise FileNotFoundError(f"外部测试目录不存在: {task.tests_ref}")
        return path

    def _fingerprint(self) -> str:
        """**整个 bench/** 的联合树哈希：任何 task 的题面/测试被改都可见。"""
        return bench_tree_hash(self.repo_root)

    def guard(self, task: Task, workdir: Path) -> str:
        """agent 启动前的指纹；Harness 必须显式保存并传给 verify。"""
        return self._fingerprint()

    def verify(self, task: Task, workdir: Path, *, before: str | None = None) -> VerifyOutcome:
        tests_dir = self._tests_dir(task)
        digest_before = before if before is not None else self._fingerprint()
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(workdir),
            "PYTHONHASHSEED": "0",
        }
        timed_out = False
        try:
            completed = subprocess.run(
                [
                    self.python,
                    "-m",
                    "pytest",
                    str(tests_dir),
                    "-q",
                    "-p",
                    "no:cacheprovider",
                ],
                cwd=workdir,
                env=env,
                capture_output=True,
                text=True,
                timeout=task.timeout_s,
                check=False,
            )
            returncode = completed.returncode
            output = (completed.stdout + completed.stderr)[-4000:]
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = -1
            output = f"timeout after {task.timeout_s}s: {exc}"
        try:
            after = self._fingerprint()
        except Exception as exc:  # noqa: BLE001 - 题面被删/改坏也必须判废而不是崩溃
            after = sha256_text(f"bench_unreadable:{type(exc).__name__}:{exc}")
        matches = digest_before == after
        passed = returncode == 0 and matches and not timed_out
        return VerifyOutcome(
            passed=passed,
            tests_hash_before=digest_before,
            tests_hash_after=after,
            matches=matches,
            timed_out=timed_out,
            diagnostic={"returncode": returncode, "output_tail": output},
        )
