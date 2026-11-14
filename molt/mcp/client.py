"""Sandboxed stdio MCP client (legacy transport, protocol 2025-06-18).

The server process always runs under bubblewrap when isolation is requested:
``/workspace`` is a **read-only** bind of a throwaway copy of the server code
and corpus, ``/tmp`` is a tmpfs (the only writable place), the repository is
never mounted, and the environment is rebuilt from scratch. ``prlimit`` wraps
the sandbox with the same resource caps as ``molt.agent.workspace``; if it is
missing the client reports ``limits="unavailable"`` instead of pretending to
be limited. If ``bwrap`` is missing the client refuses to start rather than
falling back to the host.

``bwrap=False`` exists for protocol tests only: it runs ``spec.command`` in
the repository with a minimal environment and no isolation.

Handshake state: ``new → initialized_sent → ready → closed``. Tool calls are
rejected unless the client is ``ready``. Responses are validated (jsonrpc
version, exactly one of result/error, matching id); id-less messages are only
tolerated as known ``notifications/*`` frames.

stdout is budgeted: one frame ≤ 1 MiB, ≤ 10_000 frames and ≤ 16 MiB per
session, ≤ 256 queued frames. Any breach kills the process group and raises
``McpBudgetError`` rather than dropping data and continuing.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from molt.mcp.allowlist import McpServerSpec
from molt.mcp.jsonrpc import (
    PROTOCOL_VERSION,
    McpBudgetError,
    McpError,
    McpFrameError,
    McpPermissionError,
    McpProtocolError,
    McpRemoteError,
    McpTimeout,
    McpToolError,
    McpUnavailable,
    encode_message,
    read_frame,
)

CLIENT_INFO = {"name": "molt-mcp-client", "version": "1.0.0"}
SANDBOX_PATH = "/usr/bin:/bin"
SANDBOX_HOME = "/tmp"
SANDBOX_WORKSPACE = "/workspace"
STDERR_BUFFER_BYTES = 4096
CLOSE_GRACE_S = 1.0

MAX_FRAME_BYTES = 1024 * 1024
MAX_SESSION_FRAMES = 10_000
MAX_SESSION_BYTES = 16 * 1024 * 1024
QUEUE_MAX_FRAMES = 256
QUEUE_PUT_TIMEOUT_S = 1.0
POLL_INTERVAL_S = 0.25

PRLIMIT_LIMITS = ("--as=536870912", "--cpu=10", "--nofile=128", "--nproc=4096", "--core=0")

STATE_NEW = "new"
STATE_INITIALIZED_SENT = "initialized_sent"
STATE_READY = "ready"
STATE_CLOSED = "closed"

NOTIFICATION_PREFIX = "notifications/"


class McpClient:
    """One MCP server session: handshake, tool calls, transcript, teardown."""

    def __init__(
        self,
        spec: McpServerSpec,
        repo_root: str | Path,
        *,
        bwrap: bool = True,
        transcript: str | Path | None = None,
    ) -> None:
        self.spec = spec
        self.repo_root = Path(repo_root).resolve()
        self.use_bwrap = bwrap
        self.transcript_path = Path(transcript) if transcript is not None else None
        self.workspace: Path | None = None
        self.server_info: dict[str, Any] = {}
        self.protocol_version: str | None = None
        self.state = STATE_NEW
        self.limits = "unavailable"
        self.warnings: list[str] = []
        self.frame_count = 0
        self.byte_count = 0

        self._proc: subprocess.Popen[bytes] | None = None
        self._messages: queue.Queue[dict[str, Any] | BaseException] = queue.Queue(
            maxsize=QUEUE_MAX_FRAMES
        )
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._stderr_chunks: list[bytes] = []
        self._stderr_bytes = 0
        self._stderr_lock = threading.Lock()
        self._failure: BaseException | None = None
        self._failure_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._request_lock = threading.Lock()
        self._transcript_lock = threading.Lock()
        self._seq = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._proc is not None:
            raise McpProtocolError("already_started", self.spec.server_id)
        if self.state == STATE_CLOSED:
            raise McpProtocolError("client_closed", self.spec.server_id)
        env = self._base_env()
        if self.use_bwrap:
            self.workspace = self._prepare_workspace()
            argv = self._limit_prefix() + self._bwrap_argv(self.workspace, list(self.spec.command))
            cwd = None
        else:
            argv = list(self.spec.command)
            cwd = str(self.repo_root / self.spec.cwd)
            self.limits = "none"
            self.warnings.append("isolation_disabled")
        try:
            proc = subprocess.Popen(  # noqa: S603 - argv is allowlist-built, never a shell
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                bufsize=0,
            )
        except FileNotFoundError as exc:
            if self.use_bwrap:
                raise McpUnavailable("isolation_unavailable", "bwrap_not_found") from exc
            raise McpUnavailable("server_unavailable", str(exc)) from exc
        self._proc = proc
        self._reader = threading.Thread(target=self._read_loop, name="mcp-stdout", daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._stderr_loop, name="mcp-stderr", daemon=True
        )
        self._stderr_reader.start()

    def close(self) -> None:
        if self.state == STATE_CLOSED:
            return
        self.state = STATE_CLOSED
        proc = self._proc
        if proc is not None:
            try:
                if proc.stdin is not None and not proc.stdin.closed:
                    proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=CLOSE_GRACE_S)
            except subprocess.TimeoutExpired:
                self._terminate_group(proc, signal.SIGTERM)
                try:
                    proc.wait(timeout=CLOSE_GRACE_S)
                except subprocess.TimeoutExpired:
                    self._terminate_group(proc, signal.SIGKILL)
                    try:
                        proc.wait(timeout=CLOSE_GRACE_S)
                    except subprocess.TimeoutExpired:
                        pass
            self._close_pipes()
        if self.workspace is not None:
            shutil.rmtree(self.workspace, ignore_errors=True)
            self.workspace = None

    def __enter__(self) -> McpClient:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- protocol ----------------------------------------------------------

    def initialize(self) -> dict[str, Any]:
        if self.state == STATE_CLOSED:
            raise McpProtocolError("client_closed", self.spec.server_id)
        if self.state != STATE_NEW:
            raise McpProtocolError("already_initialized", self.spec.server_id)
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": dict(CLIENT_INFO),
            },
        )
        if not isinstance(result, dict):
            raise McpProtocolError("initialize_result_not_an_object")
        version = result.get("protocolVersion")
        if version is None:
            raise McpProtocolError("missing_protocol_version")
        if version != PROTOCOL_VERSION:
            raise McpProtocolError("unsupported_protocol_version", str(version))
        server_info = result.get("serverInfo")
        if not isinstance(server_info, dict) or not isinstance(server_info.get("name"), str):
            raise McpProtocolError("missing_server_info")
        if not server_info["name"].strip():
            raise McpProtocolError("missing_server_info")
        self.protocol_version = version
        self.server_info = server_info
        self.state = STATE_INITIALIZED_SENT
        self._notify("notifications/initialized")
        self.state = STATE_READY
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        self._require_ready("tools/list")
        result = self._request("tools/list", {})
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise McpProtocolError("tools_list_invalid")
        for tool in tools:
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                raise McpProtocolError("tools_list_invalid")
        return tools

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._require_ready("tools/call")
        if not self.spec.allows(name):
            raise McpPermissionError("tool_not_allowed", f"{self.spec.server_id}/{name}")
        payload = dict(arguments or {})
        result = self._request("tools/call", {"name": name, "arguments": payload})
        if not isinstance(result, dict):
            raise McpProtocolError("tool_result_not_an_object")
        if result.get("isError") is True:
            raise McpToolError(name, result)
        return result

    def ping(self) -> dict[str, Any]:
        self._require_ready("ping")
        result = self._request("ping", {})
        return result if isinstance(result, dict) else {}

    def _require_ready(self, operation: str) -> None:
        if self.state == STATE_CLOSED:
            raise McpProtocolError("client_closed", operation)
        if self.state != STATE_READY:
            raise McpProtocolError("not_initialized", f"{operation}@{self.state}")

    # -- process helpers ---------------------------------------------------

    def _base_env(self) -> dict[str, str]:
        env = {
            "PATH": SANDBOX_PATH,
            "HOME": SANDBOX_HOME,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        # spec.env is restricted to the whitelist at load time; it only applies
        # when the client runs without isolation, never inside bwrap.
        if not self.use_bwrap:
            env.update(dict(self.spec.env))
        env["PYTHONPATH"] = SANDBOX_WORKSPACE if self.use_bwrap else str(self.repo_root)
        return env

    def _limit_prefix(self) -> list[str]:
        prlimit = shutil.which("prlimit")
        if prlimit is None:
            self.limits = "unavailable"
            self.warnings.append("prlimit_unavailable")
            return []
        self.limits = "prlimit"
        return [prlimit, *PRLIMIT_LIMITS, "--"]

    def _prepare_workspace(self) -> Path:
        workspace = Path(tempfile.mkdtemp(prefix=f"mcp-{self.spec.server_id}-"))
        package = self.spec.module.split(".")[:-1]
        source_root = self.repo_root.joinpath(*package)
        if not source_root.is_dir():
            shutil.rmtree(workspace, ignore_errors=True)
            raise McpUnavailable("server_code_missing", str(source_root))
        for index in range(1, len(package) + 1):
            name = package[:index]
            init = self.repo_root.joinpath(*name, "__init__.py")
            target_init = workspace.joinpath(*name, "__init__.py")
            target_init.parent.mkdir(parents=True, exist_ok=True)
            if init.is_file():
                shutil.copyfile(init, target_init)
            else:
                target_init.write_text("", encoding="utf-8")
        shutil.copytree(
            source_root,
            workspace.joinpath(*package),
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        if self.spec.corpus:
            corpus_src = self.repo_root / self.spec.corpus
            if not corpus_src.is_dir():
                shutil.rmtree(workspace, ignore_errors=True)
                raise McpUnavailable("corpus_missing", self.spec.corpus)
            corpus_dst = workspace / self.spec.corpus
            if corpus_dst.exists():
                shutil.rmtree(corpus_dst)
            shutil.copytree(
                corpus_src,
                corpus_dst,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
        return workspace

    def _bwrap_argv(self, workspace: Path, command: list[str]) -> list[str]:
        bwrap = shutil.which("bwrap")
        if bwrap is None:
            raise McpUnavailable("isolation_unavailable", "bwrap_not_found")
        argv = [bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL"]
        argv += ["--ro-bind", "/usr", "/usr"]
        for path in ("/lib", "/lib64", "/bin", "/sbin"):
            if Path(path).exists():
                argv += ["--ro-bind", path, path]
        argv += ["--proc", "/proc", "--dev", "/dev"]
        argv += ["--tmpfs", "/tmp"]
        argv += ["--ro-bind", str(workspace), SANDBOX_WORKSPACE]
        argv += ["--chdir", SANDBOX_HOME]
        argv += ["--clearenv"]
        argv += ["--setenv", "PATH", SANDBOX_PATH]
        argv += ["--setenv", "HOME", SANDBOX_HOME]
        argv += ["--setenv", "PYTHONDONTWRITEBYTECODE", "1"]
        argv += ["--setenv", "PYTHONPATH", SANDBOX_WORKSPACE]
        argv += ["--", *command]
        return argv

    def _terminate_group(self, proc: subprocess.Popen[bytes], sig: int) -> None:
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, PermissionError):
            return
        if pgid == os.getpgid(0):
            try:
                proc.send_signal(sig)
            except OSError:
                pass
            return
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def _close_pipes(self) -> None:
        proc = self._proc
        if proc is None:
            return
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None and not stream.closed:
                    stream.close()
            except (OSError, ValueError):
                pass

    def _fail(self, exc: BaseException) -> None:
        """Record the first fatal error, kill the group and unblock waiters."""

        with self._failure_lock:
            if self._failure is None:
                self._failure = exc
        proc = self._proc
        if proc is not None:
            self._terminate_group(proc, signal.SIGKILL)
        self._close_pipes()
        try:
            self._messages.put_nowait(exc)
        except queue.Full:
            pass

    def _pending_failure(self) -> BaseException | None:
        with self._failure_lock:
            return self._failure

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                frame = read_frame(proc.stdout, MAX_FRAME_BYTES)
                if frame is None:
                    if self.state != STATE_CLOSED:
                        self._fail(McpProtocolError("server_closed_stdout"))
                    return
                message, size = frame
                self.frame_count += 1
                self.byte_count += size
                if self.frame_count > MAX_SESSION_FRAMES:
                    self._fail(
                        McpBudgetError(
                            "frame_budget_exceeded",
                            f"{self.frame_count} > {MAX_SESSION_FRAMES} frames",
                        )
                    )
                    return
                if self.byte_count > MAX_SESSION_BYTES:
                    self._fail(
                        McpBudgetError(
                            "byte_budget_exceeded",
                            f"{self.byte_count} > {MAX_SESSION_BYTES} bytes",
                        )
                    )
                    return
                self._record("recv", message)
                try:
                    self._messages.put(message, timeout=QUEUE_PUT_TIMEOUT_S)
                except queue.Full:
                    self._fail(
                        McpBudgetError("queue_full", f">{QUEUE_MAX_FRAMES} pending frames")
                    )
                    return
        except McpError as exc:
            if isinstance(exc, McpFrameError) and exc.code == "frame_too_large":
                self._fail(McpBudgetError("frame_budget_exceeded", exc.detail))
            else:
                self._fail(exc)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the waiting request
            self._fail(exc)

    def _stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        while True:
            try:
                chunk = proc.stderr.read(1024)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            with self._stderr_lock:
                self._stderr_chunks.append(chunk)
                self._stderr_bytes += len(chunk)
                while self._stderr_bytes > STDERR_BUFFER_BYTES and self._stderr_chunks:
                    excess = self._stderr_bytes - STDERR_BUFFER_BYTES
                    head = self._stderr_chunks[0]
                    if len(head) <= excess:
                        self._stderr_chunks.pop(0)
                        self._stderr_bytes -= len(head)
                    else:
                        self._stderr_chunks[0] = head[excess:]
                        self._stderr_bytes -= excess

    def stderr_tail(self) -> bytes:
        with self._stderr_lock:
            return b"".join(self._stderr_chunks)

    # -- request plumbing --------------------------------------------------

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def _request(
        self,
        method: str,
        params: dict[str, Any] | None,
        *,
        timeout: float | None = None,
    ) -> Any:
        proc = self._proc
        if proc is None:
            raise McpProtocolError("not_started", self.spec.server_id)
        request_id = next(self._ids)
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        deadline = time.monotonic() + (timeout if timeout is not None else self.spec.timeout_s)
        with self._request_lock:
            failure = self._pending_failure()
            if failure is not None:
                raise failure
            self._send(message)
            while True:
                failure = self._pending_failure()
                if failure is not None:
                    raise failure
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._kill_after_timeout(method)
                try:
                    item = self._messages.get(timeout=min(remaining, POLL_INTERVAL_S))
                except queue.Empty:
                    continue
                if isinstance(item, BaseException):
                    raise item
                if self._is_notification(item):
                    continue
                self._validate_response(item, request_id)
                if "error" in item:
                    error = item.get("error") or {}
                    raise McpRemoteError(
                        int(error.get("code", 0)),
                        str(error.get("message", "")),
                        error.get("data"),
                    )
                return item.get("result")

    def _is_notification(self, item: dict[str, Any]) -> bool:
        if "id" in item:
            return False
        method = item.get("method")
        if isinstance(method, str) and method.startswith(NOTIFICATION_PREFIX):
            return True
        raise McpProtocolError("missing_id", str(method))

    def _validate_response(self, item: dict[str, Any], request_id: int) -> None:
        if item.get("id") != request_id:
            raise McpProtocolError(
                "response_id_mismatch", f"expected {request_id}, got {item.get('id')!r}"
            )
        if item.get("jsonrpc") != "2.0":
            raise McpProtocolError("invalid_jsonrpc_version", str(item.get("jsonrpc")))
        has_result = "result" in item
        has_error = "error" in item
        if has_result == has_error:
            raise McpProtocolError("invalid_response_shape", f"{has_result}/{has_error}")

    def _kill_after_timeout(self, method: str) -> None:
        self._fail(McpTimeout("request_timeout", method))
        raise McpTimeout("request_timeout", method)

    def _send(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise McpProtocolError("not_started", self.spec.server_id)
        payload = encode_message(message)
        self._record("send", message)
        try:
            proc.stdin.write(payload)
            proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise McpFrameError("send_failed", str(exc)) from exc

    def _record(self, direction: str, message: dict[str, Any]) -> None:
        if self.transcript_path is None:
            return
        with self._transcript_lock:
            self._seq += 1
            entry = {"dir": direction, "seq": self._seq, "message": message}
            self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
            with self.transcript_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def workspace_digest(path: Path) -> str:
    """Content digest of a workspace tree (used to prove it stayed read-only)."""

    digest = hashlib.sha256()
    for entry in sorted(path.rglob("*")):
        relative = entry.relative_to(path).as_posix()
        if entry.is_dir():
            digest.update(f"d:{relative}\n".encode())
        else:
            digest.update(f"f:{relative}\n".encode())
            digest.update(entry.read_bytes())
    return digest.hexdigest()
