"""probe shell 的 Verifier：只回脱敏 case 结果，不保存 pytest 原始输出。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from molt.core.hash import sha256_file, sha256_text
from molt.core.ports import VerifyOutcome
from molt.core.types import Task

CASE_KEYS = ("case_id", "expected", "actual", "passed")


class ProbeVerifier:
    def __init__(self, repo_root: Path, *, python: str | None = None) -> None:
        self.repo_root = Path(repo_root)
        self.python = python or sys.executable

    def _tests_dir(self, task: Task) -> Path:
        path = self.repo_root / task.tests_ref
        if not path.is_dir():
            raise FileNotFoundError(f"外部测试目录不存在: {task.tests_ref}")
        return path

    def _fingerprint(self, task: Task) -> str:
        files = {
            path.relative_to(self._tests_dir(task)).as_posix(): sha256_file(path)
            for path in sorted(self._tests_dir(task).rglob("*"))
            if path.is_file()
        }
        return sha256_text(json.dumps(files, sort_keys=True))

    def guard(self, task: Task, workdir: Path) -> str:
        return self._fingerprint(task)

    @staticmethod
    def _sanitize_cases(raw: Any) -> list[dict[str, Any]]:
        cases: list[dict[str, Any]] = []
        if not isinstance(raw, list):
            return cases
        for item in raw:
            if not isinstance(item, dict):
                continue
            if not all(key in item for key in CASE_KEYS):
                continue
            cases.append({key: item[key] for key in CASE_KEYS})
        return cases

    def verify(self, task: Task, workdir: Path, *, before: str | None = None) -> VerifyOutcome:
        tests_dir = self._tests_dir(task)
        checker = tests_dir / "check_cases.py"
        digest_before = before if before is not None else self._fingerprint(task)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(workdir),
            "PYTHONHASHSEED": "0",
        }
        timed_out = False
        returncode = -1
        stdout = ""
        try:
            completed = subprocess.run(
                [self.python, str(checker)],
                cwd=workdir,
                env=env,
                capture_output=True,
                text=True,
                timeout=task.timeout_s,
                check=False,
            )
            returncode = completed.returncode
            stdout = completed.stdout
        except subprocess.TimeoutExpired:
            timed_out = True
        except Exception:
            returncode = -1

        payload: dict[str, Any] = {}
        if not timed_out:
            try:
                parsed = json.loads(stdout.strip() or "{}")
                payload = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                payload = {}
        cases = self._sanitize_cases(payload.get("cases"))
        error = payload.get("error")
        if error is None and not cases:
            error = "checker_unreadable"
        if error is None and returncode != 0:
            error = "checker_failed"

        try:
            digest_after = self._fingerprint(task)
        except Exception:
            digest_after = sha256_text("tests_unreadable")
        matches = digest_before == digest_after
        passed = (
            returncode == 0
            and matches
            and not timed_out
            and error is None
            and bool(cases)
            and all(case["passed"] is True for case in cases)
        )
        return VerifyOutcome(
            passed=passed,
            tests_hash_before=digest_before,
            tests_hash_after=digest_after,
            matches=matches,
            timed_out=timed_out,
            diagnostic={"cases": cases, "error": error, "timed_out": timed_out},
        )


def build_verifier(repo_root: Path, **_: Any) -> ProbeVerifier:
    return ProbeVerifier(repo_root)
