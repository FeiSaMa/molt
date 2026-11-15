"""Small, fail-closed execution workspace for the chat lab.

Design rules
------------
* The API process never executes model-proposed code directly.
* Code runs only through ``bwrap`` with a dedicated writable directory, a
  read-only system tree, no network namespace, no host ``/srv`` or ``/home``
  bind, and hard wall/CPU/memory/output limits.
* File tools are path-confined to the workspace; symlinks and ``..`` are
  rejected.
* If ``bwrap`` is missing or its self-test fails, the workspace is unavailable
  and the caller must refuse to run (fail closed).
"""

from __future__ import annotations

import difflib
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 256 * 1024
MAX_FILES = 16
MAX_OUTPUT_BYTES = 16 * 1024
DEFAULT_TIMEOUT = 15.0
MAX_WALL_SECONDS = 60.0

PROBE_TOKEN_PATH = "/srv/molt/state/agent/token"
PROBE_SCRIPT = f"""\
import os
import socket
import sys

if os.getcwd() != "/workspace":
    print("BAD_CWD", os.getcwd())
    sys.exit(11)

with open("probe.txt", "w", encoding="utf-8") as handle:
    handle.write("ok")
if open("probe.txt", encoding="utf-8").read() != "ok":
    print("BAD_WRITE")
    sys.exit(12)

for path in ("/etc/hostname", {PROBE_TOKEN_PATH!r}):
    try:
        open(path, encoding="utf-8").read()
    except OSError:
        pass
    else:
        print("LEAK", path)
        sys.exit(13)

try:
    sock = socket.create_connection(("1.1.1.1", 80), timeout=1)
except OSError:
    pass
else:
    sock.close()
    print("NETWORK")
    sys.exit(14)

try:
    open("/usr/molt-probe", "w", encoding="utf-8").write("x")
except OSError:
    pass
else:
    print("SYSTEM_WRITABLE")
    sys.exit(15)

print("PROBE_OK")
"""


class WorkspaceError(RuntimeError):
    """Base class for workspace failures safe to surface to the client."""

    code = "workspace_error"

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class SandboxUnavailable(WorkspaceError):
    code = "isolation_unavailable"


class PathRejected(WorkspaceError):
    code = "invalid_path"


class WorkspaceLimit(WorkspaceError):
    code = "workspace_limit"


@dataclass(frozen=True)
class SandboxStatus:
    available: bool
    backend: str
    reason: str | None = None
    detail: str | None = None

    def public(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "available": self.available,
            "backend": self.backend,
        }
        if self.reason:
            payload["reason"] = self.reason
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    truncated: bool

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def public(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "passed": self.passed,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "truncated": self.truncated,
        }


_SANDBOX_CACHE: SandboxStatus | None = None


def _bwrap() -> str | None:
    return shutil.which("bwrap")


def _python() -> str:
    # Must be a host path under /usr (bound read-only into the sandbox), not
    # the API process's virtualenv path.
    for candidate in ("/usr/bin/python3", "/usr/local/bin/python3"):
        if Path(candidate).is_file():
            return candidate
    return "/usr/bin/python3"


def _prlimit() -> str | None:
    return shutil.which("prlimit")


def _binds() -> list[str]:
    args: list[str] = ["--ro-bind", "/usr", "/usr"]
    for path in ("/lib", "/lib64", "/bin", "/sbin"):
        source = Path(path)
        if source.exists():
            args.extend(["--ro-bind-try", path, path])
    return args


def sandbox_command(workspace: Path, command: list[str]) -> list[str]:
    """Build the fixed bwrap command; no shell, no caller-provided flags."""

    bwrap = _bwrap()
    if bwrap is None:
        raise SandboxUnavailable("服务器未安装 bubblewrap", detail="bwrap_not_installed")
    if not workspace.is_dir():
        raise SandboxUnavailable("workspace 不存在", detail="workspace_missing")

    argv = [
        bwrap,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        *_binds(),
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--bind",
        str(workspace),
        "/workspace",
        "--chdir",
        "/workspace",
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "HOME",
        "/workspace",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--",
        *command,
    ]
    prlimit = _prlimit()
    if prlimit is None:
        return argv
    return [
        prlimit,
        "--as=536870912",
        "--cpu=10",
        "--nofile=128",
        "--nproc=4096",
        "--core=0",
        "--",
        *argv,
    ]


def _run_probe(workspace: Path) -> tuple[int, str]:
    probe = workspace / "probe.py"
    probe.write_text(PROBE_SCRIPT, encoding="utf-8")
    try:
        completed = subprocess.run(
            sandbox_command(workspace, [_python(), "probe.py"]),
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    output = (completed.stdout + completed.stderr).strip()
    return completed.returncode, output[:1000]


def probe_sandbox(refresh: bool = False) -> SandboxStatus:
    """Run a one-time isolation self-test; cache the result per process."""

    global _SANDBOX_CACHE
    if _SANDBOX_CACHE is not None and not refresh:
        return _SANDBOX_CACHE

    if _bwrap() is None:
        _SANDBOX_CACHE = SandboxStatus(
            available=False,
            backend="bwrap",
            reason="bwrap_not_installed",
            detail="服务器未安装 bubblewrap，执行端点已关闭",
        )
        return _SANDBOX_CACHE

    with tempfile.TemporaryDirectory(prefix="molt-sandbox-probe-") as temporary:
        workspace = Path(temporary)
        os.chmod(workspace, 0o700)
        code, output = _run_probe(workspace)

    if code == 0 and "PROBE_OK" in output:
        _SANDBOX_CACHE = SandboxStatus(available=True, backend="bwrap")
    else:
        _SANDBOX_CACHE = SandboxStatus(
            available=False,
            backend="bwrap",
            reason="sandbox_probe_failed",
            detail=output[:500] or f"exit {code}",
        )
    return _SANDBOX_CACHE


def _clean_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise PathRejected("路径不能为空")
    if "\x00" in relative:
        raise PathRejected("路径包含 NUL")
    pure = PurePosixPath(relative.replace("\\", "/"))
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise PathRejected("只允许 workspace 内的相对路径")
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise PathRejected("不允许符号链接")
    target = current.resolve(strict=False)
    if target != root and root not in target.parents:
        raise PathRejected("路径越出 workspace")
    return target


class Workspace:
    """A temporary directory with path-confined file helpers and a sandbox."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._active_proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.RLock()

    @classmethod
    def create(cls, base: Path, run_id: str) -> Workspace:
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(base, 0o700)
        root = Path(tempfile.mkdtemp(prefix=f"{run_id}-", dir=str(base)))
        os.chmod(root, 0o700)
        return cls(root)

    # ---- files -------------------------------------------------------------
    def write(self, relative: str, content: str) -> None:
        if not isinstance(content, str):
            raise WorkspaceLimit("文件内容必须是字符串")
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise WorkspaceLimit(f"单文件超过 {MAX_FILE_BYTES} 字节")
        target = _clean_path(self.root, relative)
        files = self.list_files()
        if target not in files and len(files) >= MAX_FILES:
            raise WorkspaceLimit(f"文件数超过 {MAX_FILES}")
        total = sum(path.stat().st_size for path in files if path.is_file())
        if target.exists():
            total -= target.stat().st_size
        if total + len(data) > MAX_TOTAL_BYTES:
            raise WorkspaceLimit(f"workspace 总大小超过 {MAX_TOTAL_BYTES} 字节")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(target.parent, 0o700)
        descriptor, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=".molt-")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def read(self, relative: str) -> str:
        target = _clean_path(self.root, relative)
        if not target.is_file():
            raise PathRejected("文件不存在")
        if target.stat().st_size > MAX_FILE_BYTES:
            raise WorkspaceLimit(f"单文件超过 {MAX_FILE_BYTES} 字节")
        return target.read_text(encoding="utf-8", errors="replace")

    def list_files(self) -> list[Path]:
        found: list[Path] = []
        for path in sorted(self.root.rglob("*")):
            if path.is_symlink():
                raise PathRejected("workspace 内出现符号链接")
            if path.is_file():
                found.append(path)
        return found

    def snapshot(self) -> dict[str, str]:
        return {
            path.relative_to(self.root).as_posix(): path.read_text(
                encoding="utf-8", errors="replace"
            )
            for path in self.list_files()
        }

    @staticmethod
    def diff(before: dict[str, str], after: dict[str, str]) -> str:
        chunks: list[str] = []
        for name in sorted(set(before) | set(after)):
            old = before.get(name, "")
            new = after.get(name, "")
            if old == new:
                continue
            chunks.extend(
                difflib.unified_diff(
                    old.splitlines(),
                    new.splitlines(),
                    fromfile=f"a/{name}",
                    tofile=f"b/{name}",
                    lineterm="",
                )
            )
        text = "\n".join(chunks)
        return text[:MAX_OUTPUT_BYTES]

    # ---- execution ---------------------------------------------------------
    def terminate(self) -> None:
        with self._lock:
            process = self._active_proc
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    process.kill()
                except ProcessLookupError:
                    pass

    def run_tests(
        self,
        *,
        cancel_event: Any | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> RunResult:
        """Run the fixed stdlib test command inside the sandbox."""

        # -B: round1 would otherwise leave __pycache__ whose timestamp+size can
        # match a same-size patch applied within the same second, making round2
        # import stale bytecode and fail even when the fix is correct.
        command = sandbox_command(self.root, [_python(), "-B", "-m", "unittest", "-q"])
        started = time.monotonic()
        timed_out = False
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                command,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
                close_fds=True,
            )
            with self._lock:
                self._active_proc = process
            try:
                while process.poll() is None:
                    if cancel_event is not None and cancel_event.is_set():
                        self.terminate()
                        break
                    if time.monotonic() - started > timeout:
                        timed_out = True
                        self.terminate()
                        break
                    time.sleep(0.05)
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                timed_out = True
                self.terminate()
                process.wait(timeout=3)
            finally:
                with self._lock:
                    self._active_proc = None
            stdout_file.seek(0)
            stderr_file.seek(0)
            raw_out = stdout_file.read(MAX_OUTPUT_BYTES + 1)
            raw_err = stderr_file.read(MAX_OUTPUT_BYTES + 1)
        truncated = len(raw_out) > MAX_OUTPUT_BYTES or len(raw_err) > MAX_OUTPUT_BYTES
        return RunResult(
            exit_code=process.returncode if process.returncode is not None else -1,
            stdout=raw_out[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
            stderr=raw_err[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
            duration_ms=int((time.monotonic() - started) * 1000),
            timed_out=timed_out,
            truncated=truncated,
        )

    def cleanup(self) -> None:
        self.terminate()
        shutil.rmtree(self.root, ignore_errors=True)
