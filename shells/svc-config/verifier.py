"""svc-config shell 的 Verifier：在候选工作区之外运行外部校验脚本 + 黄金 diff。

- 校验脚本位于题目的 ``tests_ref``（不在 workspace 内），只依赖 stdlib；
- 通过条件是全部脚本退出码 0、未超时、且运行前后 ``bench/`` 树哈希一致；
- 与 code-repair 的 ``PytestVerifier`` 同签名（``guard`` / ``verify(before=...)``），
  但不用 pytest、不 import 任何第三方包，成本恒为 0。
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

VALIDATOR_GLOB = "validate*.py"


class SvcConfigVerifier:
    """外部 JSON 校验脚本 + 黄金 diff；失败 / 超时 / 哈希变化分别可辨。"""

    def __init__(self, repo_root: Path, *, python: str | None = None) -> None:
        self.repo_root = Path(repo_root)
        self.python = python or sys.executable

    def _tests_dir(self, task: Task) -> Path:
        path = self.repo_root / task.tests_ref
        if not path.exists():
            raise FileNotFoundError(f"外部校验目录不存在: {task.tests_ref}")
        return path

    def _scripts(self, task: Task) -> list[Path]:
        scripts = sorted(self._tests_dir(task).glob(VALIDATOR_GLOB))
        if not scripts:
            raise FileNotFoundError(f"{task.tests_ref} 下没有 {VALIDATOR_GLOB}")
        return scripts

    def _fingerprint(self) -> str:
        """**整个 bench/** 的联合树哈希：任何题面/测试/黄金文件被改都可见。"""
        return bench_tree_hash(self.repo_root)

    def guard(self, task: Task, workdir: Path) -> str:
        return self._fingerprint()

    def verify(self, task: Task, workdir: Path, *, before: str | None = None) -> VerifyOutcome:
        digest_before = before if before is not None else self._fingerprint()
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(workdir),
            "PYTHONHASHSEED": "0",
        }
        returncodes: list[int] = []
        outputs: list[str] = []
        timed_out = False
        try:
            scripts = self._scripts(task)
        except FileNotFoundError as exc:
            scripts = []
            outputs.append(str(exc))
            returncodes.append(-1)
        for script in scripts:
            try:
                completed = subprocess.run(
                    [self.python, str(script)],
                    cwd=workdir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=task.timeout_s,
                    check=False,
                )
                returncodes.append(completed.returncode)
                outputs.append(f"{script.name}: {(completed.stdout + completed.stderr)[-2000:]}")
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                returncodes.append(-1)
                outputs.append(f"{script.name}: timeout after {task.timeout_s}s: {exc}")
        try:
            after = self._fingerprint()
        except Exception as exc:  # noqa: BLE001 - 题面被删/改坏也必须判废而不是崩溃
            after = sha256_text(f"bench_unreadable:{type(exc).__name__}:{exc}")
        matches = digest_before == after
        passed = bool(returncodes) and all(code == 0 for code in returncodes) and matches
        passed = passed and not timed_out
        return VerifyOutcome(
            passed=passed,
            tests_hash_before=digest_before,
            tests_hash_after=after,
            matches=matches,
            timed_out=timed_out,
            diagnostic={"returncodes": returncodes, "output_tail": "\n".join(outputs)[-4000:]},
        )
