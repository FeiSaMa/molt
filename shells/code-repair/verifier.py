"""code-repair shell 的 Verifier：在候选工作区之外只读运行 pytest。

运行前后对测试目录取树哈希；不一致即判废（``matches=False``）。
本模块不依赖 Pi，也不 import 任何第三方 Python 包。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from molt.core.hash import sha256_file, tree_files, tree_hash_from_files
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

    def _fingerprint(self, tests_dir: Path) -> str:
        """测试目录 + 题面 task.json 的联合树哈希（题集也在保护范围）。"""
        files = {f"tests/{rel}": digest for rel, digest in tree_files(tests_dir).items()}
        task_file = tests_dir.parent / "task.json"
        if task_file.is_file():
            files["task.json"] = sha256_file(task_file)
        return tree_hash_from_files(files)

    def guard(self, task: Task, workdir: Path) -> str:
        """agent 启动前的指纹；Harness 必须显式保存并传给 verify。"""
        return self._fingerprint(self._tests_dir(task))

    def verify(self, task: Task, workdir: Path, *, before: str | None = None) -> VerifyOutcome:
        tests_dir = self._tests_dir(task)
        digest_before = before if before is not None else self._fingerprint(tests_dir)
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
        after = self._fingerprint(tests_dir)
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
