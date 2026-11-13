"""code-repair shell 的 Verifier：把候选解复制到仓库外的私有 staging 后只读运行 pytest。

运行前后对**整个 ``bench/``** 取树哈希（全部 task.json + workspace + tests）；
任一文件变化即判废（``matches=False``），改其他题或 holdout 题也逃不掉。

候选工作区只作为 ``solution.py`` 的来源：测试子进程的 cwd、``PYTHONPATH`` 与
启动搜索路径都不包含它。候选旁置的 ``sitecustomize`` / ``pytest.py`` /
``pytest.ini`` / ``conftest.py`` 不会进入解释器或 pytest 的发现范围。
本模块不依赖 Pi，也不 import 任何第三方 Python 包。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from molt.bench import bench_tree_hash
from molt.core.hash import sha256_text
from molt.core.ports import VerifyOutcome
from molt.core.types import Task

STAGING_PREFIX = "molt-verify-"
TRUSTED_PATH = "/usr/bin:/bin"
_TRUSTED_SITECUSTOMIZE = '''"""Verifier-generated startup shim: keep the candidate-visible env at seven keys."""
import os

os.environ.pop("LC_CTYPE", None)
'''
_TRUSTED_PYTEST_PLUGIN = '''"""Verifier-generated pytest plugin: drop pytest's own version marker."""
import os


def pytest_configure(config):
    os.environ.pop("PYTEST_VERSION", None)
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


class PytestVerifier:
    """外部测试目录只读；失败 / 超时 / 哈希变化分别可辨。"""

    def __init__(self, repo_root: Path, *, python: str | None = None) -> None:
        self.repo_root = Path(repo_root)
        self.python = python or sys.executable

    def _tests_dir(self, task: Task) -> Path:
        path = (self.repo_root / task.tests_ref).resolve()
        if not path.exists():
            raise FileNotFoundError(f"外部测试目录不存在: {task.tests_ref}")
        return path

    def _fingerprint(self) -> str:
        """**整个 bench/** 的联合树哈希：任何 task 的题面/测试被改都可见。"""
        return bench_tree_hash(self.repo_root)

    def _fingerprint_after(self) -> str:
        try:
            return self._fingerprint()
        except Exception as exc:  # noqa: BLE001 - 题面被删/改坏也必须判废而不是崩溃
            return sha256_text(f"bench_unreadable:{type(exc).__name__}:{exc}")

    def guard(self, task: Task, workdir: Path) -> str:
        """agent 启动前的指纹；Harness 必须显式保存并传给 verify。"""
        return self._fingerprint()

    def verify(self, task: Task, workdir: Path, *, before: str | None = None) -> VerifyOutcome:
        tests_dir = self._tests_dir(task)
        digest_before = before if before is not None else self._fingerprint()
        staging = _create_staging()
        timed_out = False
        try:
            failure = _copy_solution(Path(workdir), staging)
            if failure is not None:
                after = self._fingerprint_after()
                return VerifyOutcome(
                    passed=False,
                    tests_hash_before=digest_before,
                    tests_hash_after=after,
                    matches=digest_before == after,
                    timed_out=False,
                    diagnostic={"returncode": -1, "output_tail": failure},
                )

            (staging / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
            (staging / "sitecustomize.py").write_text(
                _TRUSTED_SITECUSTOMIZE, encoding="utf-8"
            )
            (staging / "molt_verify_env.py").write_text(
                _TRUSTED_PYTEST_PLUGIN, encoding="utf-8"
            )
            env = _trusted_env(staging)
            try:
                completed = subprocess.run(
                    [
                        self.python,
                        "-P",
                        "-m",
                        "pytest",
                        str(tests_dir),
                        "-q",
                        "-p",
                        "no:cacheprovider",
                        "-p",
                        "molt_verify_env",
                        "-c",
                        str(staging / "pytest.ini"),
                        "--rootdir",
                        str(tests_dir),
                        "--confcutdir",
                        str(tests_dir),
                        "--disable-plugin-autoload",
                    ],
                    cwd=staging,
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
            except OSError as exc:
                returncode = -1
                output = f"startup_error: {type(exc).__name__}: {exc}"

            after = self._fingerprint_after()
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
        finally:
            _remove_tree(staging)
