"""probe / probe-c2 shell 的 Verifier：只回脱敏 case 结果，不保存 pytest 原始输出。

候选解先被复制到仓库外的私有 staging；checker 在 staging 中运行，``PYTHONPATH``
与 cwd 都不含原工作区，因此候选旁置的 ``sitecustomize`` / ``json.py`` /
``decimal.py`` 不会进入解释器或 checker 的导入路径。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from molt.core.hash import sha256_file, sha256_text
from molt.core.ports import VerifyOutcome
from molt.core.types import Task

CASE_KEYS = ("case_id", "expected", "actual", "passed")

STAGING_PREFIX = "molt-verify-"
TRUSTED_PATH = "/usr/bin:/bin"
_TRUSTED_SITECUSTOMIZE = '''"""Verifier-generated startup shim: keep the candidate-visible env at seven keys."""
import os

os.environ.pop("LC_CTYPE", None)
'''


def _trusted_env(staging: Path) -> dict[str, str]:
    """从零构造子进程 env：七个键，全部由 verifier 决定，不继承调用方环境。"""
    return {
        "PATH": TRUSTED_PATH,
        "HOME": str(staging / "home"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": str(staging),
        "PWD": str(staging),
    }


def _create_staging() -> Path:
    staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX))
    os.chmod(staging, 0o700)
    home = staging / "home"
    home.mkdir()
    os.chmod(home, 0o700)
    return staging


def _remove_tree(path: Path) -> None:
    """删除 staging；候选若把内容改成只读，先 chmod 再重试。"""

    def _force(func, target, _exc) -> None:
        try:
            os.chmod(target, 0o700)
        except OSError:
            pass
        try:
            func(target)
        except OSError:
            pass

    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_force)
    else:  # pragma: no cover - 3.11 兼容
        shutil.rmtree(path, onerror=_force)


def _copy_solution(workdir: Path, staging: Path) -> str | None:
    """不跟随链接地复制 ``<workdir>/solution.py`` 到 staging。

    返回 ``None`` 表示成功，否则返回可放进 diagnostic 的失败原因。缺失、目录、
    symlink、FIFO 及其他非普通文件一律拒绝；FIFO 用 ``O_NONBLOCK`` 打开，不会阻塞。
    """
    source = Path(workdir) / "solution.py"
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return "invalid_solution"
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return "invalid_solution"
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        data = b"".join(chunks)
    except OSError:
        return "invalid_solution"
    finally:
        os.close(fd)
    if len(data) != info.st_size:
        return "invalid_solution"

    target = staging / "solution.py"
    try:
        out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError:
        return "invalid_solution"
    try:
        view = memoryview(data)
        while view:
            written = os.write(out, view)
            view = view[written:]
    except OSError:
        return "invalid_solution"
    finally:
        os.close(out)

    copied = os.lstat(target)
    if not stat.S_ISREG(copied.st_mode) or copied.st_size != len(data):
        return "invalid_solution"
    return None


class ProbeVerifier:
    def __init__(self, repo_root: Path, *, python: str | None = None) -> None:
        self.repo_root = Path(repo_root)
        self.python = python or sys.executable

    def _tests_dir(self, task: Task) -> Path:
        path = (self.repo_root / task.tests_ref).resolve()
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
        staging = _create_staging()
        timed_out = False
        returncode = -1
        stdout = ""
        failure: str | None = None
        try:
            failure = _copy_solution(Path(workdir), staging)
            if failure is None:
                (staging / "sitecustomize.py").write_text(
                    _TRUSTED_SITECUSTOMIZE, encoding="utf-8"
                )
                env = _trusted_env(staging)
                try:
                    completed = subprocess.run(
                        [self.python, "-P", str(checker)],
                        cwd=staging,
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
        finally:
            _remove_tree(staging)

        payload: dict[str, Any] = {}
        if not timed_out:
            try:
                parsed = json.loads(stdout.strip() or "{}")
                payload = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                payload = {}
        cases = self._sanitize_cases(payload.get("cases"))
        error = payload.get("error")
        if failure is not None:
            cases = []
            error = failure
        elif error is None and not cases:
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
