"""本地 agent HTTP 服务（stdlib ``ThreadingHTTPServer``，默认 loopback、端口 0）。

路由见 :data:`ROUTES`；请求处理顺序固定为：

1. 未知 path（含未知 path 的 OPTIONS）→ 404
2. 方法不匹配 → 405 + ``Allow``
3. ``Host`` 非法 → 400
4. ``Origin`` 不允许 → 403
5. 缺/错 Bearer → 401
6. 合法 token 后未知 id → 404

``GET /api/health`` 与 ``GET /api/openapi.json`` 免鉴权；已知 path 的 OPTIONS 免鉴权。
SSE 的 ``error`` 事件只使用 SSE-only 错误码，HTTP 层不会返回 502/504。
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import sys
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from molt.agent.chat import adapter_health, build_adapter
from molt.agent.errors import ApiError, ErrorCode
from molt.agent.lab import LabBusy, LabError, LabManager, LabTaskNotFound, LabUnavailable
from molt.agent.probe import (
    ProbeIndexError,
    build_index,
    find_experiment,
    index_public_view,
    load_index,
)
from molt.agent.store import MAX_MESSAGES, TERMINAL_EVENTS, Session, SessionStore, new_id
from molt.agent.tasks import TaskResultsError, load_task_results
from molt.agent.turn import TurnRunner

MAX_BODY_BYTES = 64 * 1024
MAX_CONTENT_BYTES = 16384
PING_INTERVAL = 15.0
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 应用状态
# ---------------------------------------------------------------------------
@dataclass
class AgentApp:
    index: dict[str, Any]
    adapter: Any
    token: str
    allowed_origins: tuple[str, ...]
    openapi: dict[str, Any]
    data_dir: Path
    root: Path | None = None
    lab_root: Path | None = None
    lab: LabManager | None = None
    lab_openapi: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.store = SessionStore(provider=self.adapter.name)
        self.runner = TurnRunner(self.store, self.index, self.adapter)
        if self.lab is None:
            self.lab = LabManager(self.adapter, lab_root=self.lab_root)

    def health(self) -> dict[str, Any]:
        return adapter_health(self.adapter)


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
Route = tuple[str, re.Pattern[str], str]

ROUTES: tuple[Route, ...] = (
    ("GET", re.compile(r"^/api/health$"), "health"),
    ("GET", re.compile(r"^/api/openapi\.json$"), "openapi"),
    ("GET", re.compile(r"^/api/experiments$"), "experiments_list"),
    ("GET", re.compile(r"^/api/experiments/(?P<experiment_id>[^/]+)$"), "experiment_detail"),
    ("GET", re.compile(r"^/api/tasks$"), "tasks"),
    ("GET", re.compile(r"^/api/lab/openapi\.json$"), "lab_openapi"),
    ("GET", re.compile(r"^/api/lab/capabilities$"), "lab_capabilities"),
    ("GET", re.compile(r"^/api/lab/tasks$"), "lab_tasks"),
    ("POST", re.compile(r"^/api/lab/runs$"), "lab_runs_create"),
    ("GET", re.compile(r"^/api/lab/runs/(?P<run_id>[^/]+)$"), "lab_run_detail"),
    ("DELETE", re.compile(r"^/api/lab/runs/(?P<run_id>[^/]+)$"), "lab_run_cancel"),
    ("GET", re.compile(r"^/api/lab/runs/(?P<run_id>[^/]+)/events$"), "lab_run_events"),
    ("POST", re.compile(r"^/api/sessions$"), "sessions_create"),
    ("GET", re.compile(r"^/api/sessions$"), "sessions_list"),
    ("GET", re.compile(r"^/api/sessions/(?P<session_id>[^/]+)$"), "session_detail"),
    ("DELETE", re.compile(r"^/api/sessions/(?P<session_id>[^/]+)$"), "session_delete"),
    ("POST", re.compile(r"^/api/sessions/(?P<session_id>[^/]+)/messages$"), "messages_create"),
    ("GET", re.compile(r"^/api/sessions/(?P<session_id>[^/]+)/events$"), "events"),
)

PUBLIC_HANDLERS = frozenset({"health", "openapi", "lab_openapi"})


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------
class AgentRequestHandler(BaseHTTPRequestHandler):
    server_version = "molt-agent/1.0"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    server: ApiHTTPServer

    # 不打印访问日志（避免任何 header 进入日志，也避免 token 泄漏风险）。
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 覆盖父类签名
        return

    # ---- HTTP 方法 --------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_HEAD(self) -> None:  # noqa: N802 - HEAD 一律 405
        self._handle("HEAD")

    def do_PUT(self) -> None:  # noqa: N802 - 不支持 PUT，按方法不匹配处理
        self._handle("PUT")

    def do_PATCH(self) -> None:  # noqa: N802 - 不支持 PATCH
        self._handle("PATCH")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._handle("OPTIONS")

    # ---- 入口 -------------------------------------------------------------
    def _handle(self, method: str) -> None:
        path = urlsplit(self.path).path
        matches = [(route_method, match, name) for route_method, pattern, name in ROUTES
                   if (match := pattern.match(path))]
        if not matches:
            self._send_error(ApiError(ErrorCode.NOT_FOUND, "未知路径"))
            return

        if method == "OPTIONS":
            self._preflight(matches)
            return

        allowed = sorted({route_method for route_method, _, _ in matches})
        if method not in allowed:
            self._send_error(
                ApiError(
                    ErrorCode.METHOD_NOT_ALLOWED,
                    "方法不允许",
                    headers={"Allow": ", ".join([*allowed, "OPTIONS"])},
                )
            )
            return

        if not self._host_allowed():
            self._send_error(ApiError(ErrorCode.BAD_REQUEST, "Host 不被允许"))
            return

        origin = self.headers.get("Origin")
        if origin is not None and not self._origin_allowed(origin):
            self._send_error(ApiError(ErrorCode.FORBIDDEN_ORIGIN, "Origin 不被允许"))
            return

        selected = next(
            (item for item in matches if item[0] == method),
            None,
        )
        if selected is None:  # pragma: no cover - 上面的 allowed 检查已覆盖
            self._send_error(ApiError(ErrorCode.METHOD_NOT_ALLOWED, "方法不允许"))
            return
        _, match, handler_name = selected

        if handler_name not in PUBLIC_HANDLERS and not self._authorized():
            self._send_error(
                ApiError(
                    ErrorCode.UNAUTHORIZED,
                    "缺少或错误的 Bearer token",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            )
            return

        handler: Callable[[re.Match[str]], None] = getattr(self, f"_handle_{handler_name}")
        try:
            handler(match)
        except ApiError as exc:
            self._send_error(exc)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:  # noqa: BLE001 - 兜底 500，不泄漏内部细节
            self._send_error(ApiError(ErrorCode.INTERNAL, "内部错误"))

    # ---- 安全检查 ---------------------------------------------------------
    def _host_allowed(self) -> bool:
        port = self.server.server_address[1]
        host = self.headers.get("Host")
        if host is None:
            return False
        return host in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _origin_allowed(self, origin: str) -> bool:
        port = self.server.server_address[1]
        if origin in self.server.app.allowed_origins:
            return True
        return origin in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization")
        if not header:
            return False
        scheme, _, supplied = header.partition(" ")
        if scheme.lower() != "bearer" or not supplied.strip():
            return False
        return hmac.compare_digest(supplied.strip(), self.server.app.token)

    # ---- 响应工具 ---------------------------------------------------------
    def _cors_headers(self) -> dict[str, str]:
        origin = self.headers.get("Origin")
        if origin is None or not self._origin_allowed(origin):
            return {}
        return {
            "Access-Control-Allow-Origin": origin,
            "Vary": "Origin",
        }

    def _send_json(
        self, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        for key, value in {**self._cors_headers(), **(headers or {})}.items():
            self.send_header(key, value)
        self.end_headers()
        self.close_connection = True
        if status != 204 and self.command != "HEAD":
            self.wfile.write(body)

    def _send_error(self, error: ApiError) -> None:
        if error.status in (413, 415):
            self.close_connection = True
        try:
            self._send_json(error.status, error.to_payload(), error.headers)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _preflight(self, matches: list[tuple[str, re.Match[str], str]]) -> None:
        if not self._host_allowed():
            self._send_error(ApiError(ErrorCode.BAD_REQUEST, "Host 不被允许"))
            return
        origin = self.headers.get("Origin")
        if origin is not None and not self._origin_allowed(origin):
            self._send_error(ApiError(ErrorCode.FORBIDDEN_ORIGIN, "Origin 不被允许"))
            return
        del matches
        headers = {
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type, Last-Event-ID",
            "Vary": "Origin",
            "Content-Length": "0",
            "Connection": "close",
        }
        if origin is not None:
            headers["Access-Control-Allow-Origin"] = origin
        self.send_response(204)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.close_connection = True

    # ---- 请求体 -----------------------------------------------------------
    def _read_json_body(self, *, allow_empty: bool = False) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            length = 0
        else:
            try:
                length = int(raw_length)
            except ValueError:
                raise ApiError(ErrorCode.BAD_REQUEST, "Content-Length 不合法") from None
            if length < 0:
                raise ApiError(ErrorCode.BAD_REQUEST, "Content-Length 不合法")
        if length > MAX_BODY_BYTES:
            raise ApiError(
                ErrorCode.PAYLOAD_TOO_LARGE,
                "请求体超过 64 KiB",
                {"limit_bytes": MAX_BODY_BYTES},
            )
        if length > 0:
            media_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if media_type != "application/json":
                raise ApiError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, "只接受 application/json")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ApiError(ErrorCode.BAD_REQUEST, "请求体不完整")
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ApiError(ErrorCode.BAD_REQUEST, "请求体不是合法 JSON") from None
        elif allow_empty:
            payload = {}
        else:
            raise ApiError(ErrorCode.BAD_REQUEST, "请求体为空")
        if not isinstance(payload, dict):
            raise ApiError(ErrorCode.BAD_REQUEST, "请求体必须是 JSON 对象")
        return payload

    # ---- 辅助 -------------------------------------------------------------
    def _session(self, match: re.Match[str]) -> Session:
        session_id = unquote(match.group("session_id"))
        session = self.server.app.store.get(session_id)
        if session is None:
            raise ApiError(ErrorCode.NOT_FOUND, "session 不存在")
        return session

    # ---- 端点 -------------------------------------------------------------
    def _handle_health(self, match: re.Match[str]) -> None:
        del match
        self._send_json(200, self.server.app.health())

    def _handle_openapi(self, match: re.Match[str]) -> None:
        del match
        self._send_json(200, self.server.app.openapi)

    def _handle_experiments_list(self, match: re.Match[str]) -> None:
        del match
        self._send_json(200, index_public_view(self.server.app.index))

    def _handle_experiment_detail(self, match: re.Match[str]) -> None:
        experiment_id = unquote(match.group("experiment_id"))
        experiment = find_experiment(self.server.app.index, experiment_id)
        if experiment is None:
            raise ApiError(ErrorCode.NOT_FOUND, "experiment 不存在")
        self._send_json(200, experiment)

    def _handle_tasks(self, match: re.Match[str]) -> None:
        del match
        try:
            payload = load_task_results(self.server.app.data_dir)
        except TaskResultsError:
            raise ApiError(
                ErrorCode.DATA_UNAVAILABLE, "任务结果数据不可用"
            ) from None
        self._send_json(200, payload)

    # ---- lab -------------------------------------------------------------
    def _lab_run(self, match: re.Match[str]) -> Any:
        run_id = unquote(match.group("run_id"))
        lab = self.server.app.lab
        run = lab.get(run_id) if lab is not None else None
        if run is None:
            raise ApiError(ErrorCode.NOT_FOUND, "run 不存在")
        return run

    def _handle_lab_openapi(self, match: re.Match[str]) -> None:
        del match
        app = self.server.app
        if app.lab_openapi is None:
            app.lab_openapi = load_lab_openapi(app.root or REPO_ROOT)
        self._send_json(200, app.lab_openapi)

    def _handle_lab_capabilities(self, match: re.Match[str]) -> None:
        del match
        lab = self.server.app.lab
        if lab is None:  # pragma: no cover - __post_init__ 创建
            raise ApiError(ErrorCode.ADAPTER_UNAVAILABLE, "执行工作台未初始化")
        self._send_json(200, lab.capabilities())

    def _handle_lab_tasks(self, match: re.Match[str]) -> None:
        del match
        lab = self.server.app.lab
        if lab is None:  # pragma: no cover
            raise ApiError(ErrorCode.ADAPTER_UNAVAILABLE, "执行工作台未初始化")
        self._send_json(200, {"tasks": lab.tasks()})

    def _handle_lab_runs_create(self, match: re.Match[str]) -> None:
        del match
        lab = self.server.app.lab
        if lab is None:  # pragma: no cover
            raise ApiError(ErrorCode.ADAPTER_UNAVAILABLE, "执行工作台未初始化")
        payload = self._read_json_body()
        unknown = sorted(set(payload) - {"task_id"})
        if unknown:
            raise ApiError(
                ErrorCode.BAD_REQUEST,
                "只接受 task_id 字段",
                {"unknown_fields": unknown},
            )
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ApiError(ErrorCode.BAD_REQUEST, "task_id 必须是非空字符串")
        try:
            run = lab.create(task_id.strip())
        except LabUnavailable as exc:
            raise ApiError(
                ErrorCode.ADAPTER_UNAVAILABLE,
                str(exc),
                {"reason": "isolation_unavailable", "detail": exc.detail},
            ) from None
        except LabBusy:
            raise ApiError(
                ErrorCode.CONFLICT,
                "已有执行任务在进行",
                {"reason": "lab_busy"},
            ) from None
        except LabTaskNotFound:
            raise ApiError(ErrorCode.NOT_FOUND, "未知任务") from None
        except LabError as exc:
            raise ApiError(
                ErrorCode.ADAPTER_UNAVAILABLE,
                str(exc),
                {"reason": exc.code},
            ) from None
        self._send_json(
            202,
            {"run_id": run.run_id, "status": run.status, "event_cursor": None},
        )

    def _handle_lab_run_detail(self, match: re.Match[str]) -> None:
        self._send_json(200, self._lab_run(match).public())

    def _handle_lab_run_cancel(self, match: re.Match[str]) -> None:
        run = self._lab_run(match)
        lab = self.server.app.lab
        if lab is not None:
            lab.cancel(run.run_id)
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        for key, value in self._cors_headers().items():
            self.send_header(key, value)
        self.end_headers()
        self.close_connection = True

    def _handle_lab_run_events(self, match: re.Match[str]) -> None:
        run = self._lab_run(match)
        self._stream_events(run, self._parse_since())

    def _handle_sessions_create(self, match: re.Match[str]) -> None:
        del match
        payload = self._read_json_body(allow_empty=True)
        if payload:
            raise ApiError(ErrorCode.BAD_REQUEST, "POST /api/sessions 只接受 {}")
        session = self.server.app.store.create()
        self._send_json(201, session.public())

    def _handle_sessions_list(self, match: re.Match[str]) -> None:
        del match
        sessions = [session.summary() for session in self.server.app.store.list()]
        self._send_json(200, {"sessions": sessions})

    def _handle_session_detail(self, match: re.Match[str]) -> None:
        self._send_json(200, self._session(match).public())

    def _handle_session_delete(self, match: re.Match[str]) -> None:
        session = self._session(match)
        with session.condition:
            if session.status == "in_progress":
                raise ApiError(
                    ErrorCode.CONFLICT,
                    "turn 进行中，不能删除 session",
                    {"reason": "turn_in_progress"},
                )
        if not self.server.app.store.delete(session.session_id):
            raise ApiError(ErrorCode.NOT_FOUND, "session 不存在")
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        for key, value in self._cors_headers().items():
            self.send_header(key, value)
        self.end_headers()
        self.close_connection = True

    def _handle_messages_create(self, match: re.Match[str]) -> None:
        session = self._session(match)
        payload = self._read_json_body()
        unknown = sorted(set(payload) - {"content"})
        if unknown:
            raise ApiError(
                ErrorCode.BAD_REQUEST,
                "只接受 content 字段",
                {"unknown_fields": unknown},
            )
        content = payload.get("content")
        if not isinstance(content, str):
            raise ApiError(ErrorCode.BAD_REQUEST, "content 必须是字符串")
        size = len(content.encode("utf-8"))
        if size < 1 or size > MAX_CONTENT_BYTES:
            raise ApiError(
                ErrorCode.BAD_REQUEST,
                "content 必须是 1–16384 UTF-8 字节",
                {"bytes": size, "limit_bytes": MAX_CONTENT_BYTES},
            )

        with session.condition:
            if self.server.app.store.get(session.session_id) is not session:
                raise ApiError(ErrorCode.NOT_FOUND, "session 不存在")
            if session.status == "in_progress":
                raise ApiError(
                    ErrorCode.CONFLICT,
                    "已有 turn 在进行",
                    {"reason": "turn_in_progress"},
                )
            if len(session.messages) >= MAX_MESSAGES:
                raise ApiError(
                    ErrorCode.PAYLOAD_TOO_LARGE,
                    "会话历史已达 200 条上限",
                    {"messages": len(session.messages), "limit": MAX_MESSAGES},
                )
            cursor = session.last_event_id
            turn_id = new_id("turn")
            session.status = "in_progress"
            session.active_turn_id = turn_id

        self._send_json(202, {"turn_id": turn_id, "event_cursor": cursor or None})
        self.server.app.runner.start(session, turn_id, content)

    def _handle_events(self, match: re.Match[str]) -> None:
        session = self._session(match)
        self._stream_events(session, self._parse_since())

    def _stream_events(self, source: Any, since: int | None) -> None:
        with source.condition:
            minimum = source.min_event_id()
            if since is not None and since > 0 and (minimum is None or since < minimum):
                raise ApiError(
                    ErrorCode.STALE_EVENT_ID,
                    "事件游标已过期，需要重新同步",
                    {"reset_required": True},
                )

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        for key, value in self._cors_headers().items():
            self.send_header(key, value)
        self.end_headers()
        self.close_connection = True

        cursor = since if since else 0
        while True:
            with source.condition:
                if source.closed:
                    return
                pending = source.events_since(cursor if cursor else None)
                ping = False
                if not pending:
                    woken = source.wait_for_events(cursor, PING_INTERVAL)
                    if source.closed:
                        return
                    if not woken:
                        ping = True
                    else:
                        pending = source.events_since(cursor if cursor else None)
            if ping:
                if not self._write_stream(": ping\n\n"):
                    return
                continue
            for event in pending:
                frame = (
                    f"id: {event.event_id}\n"
                    f"event: {event.event}\n"
                    f"data: {json.dumps(event.data, ensure_ascii=False, separators=(',', ':'))}\n\n"
                )
                if not self._write_stream(frame):
                    return
                cursor = event.event_id
                if event.event in TERMINAL_EVENTS:
                    return

    def _parse_since(self) -> int | None:
        query = urlsplit(self.path).query
        params = parse_qs(query, keep_blank_values=True)
        raw: str | None
        if "since" in params:
            values = params["since"]
            if len(values) != 1:
                raise ApiError(ErrorCode.BAD_REQUEST, "since 只能出现一次")
            raw = values[0]
        else:
            header = self.headers.get("Last-Event-ID")
            raw = header.strip() if header is not None else None
        if raw is None or raw.strip().lower() == "null":
            return None
        if raw == "":
            raise ApiError(ErrorCode.BAD_REQUEST, "since 必须是非负整数")
        try:
            value = int(raw)
        except ValueError:
            raise ApiError(ErrorCode.BAD_REQUEST, "since 必须是非负整数") from None
        if value < 0:
            raise ApiError(ErrorCode.BAD_REQUEST, "since 必须是非负整数")
        return value

    def _write_stream(self, frame: str) -> bool:
        try:
            self.wfile.write(frame.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False
        return True


class ApiHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: AgentApp) -> None:
        super().__init__(address, AgentRequestHandler)
        self.app = app


def create_server(app: AgentApp, host: str = "127.0.0.1", port: int = 0) -> ApiHTTPServer:
    bind_host = "127.0.0.1" if host == "localhost" else host
    return ApiHTTPServer((bind_host, port), app)


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
class StartupError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def load_openapi(root: Path) -> dict[str, Any]:
    path = root / "contracts" / "openapi-v1.json"
    try:
        payload = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StartupError("contracts_invalid", f"无法加载 OpenAPI 契约: {exc}") from None
    if not isinstance(payload, dict):
        raise StartupError("contracts_invalid", "OpenAPI 契约必须是对象")
    return payload


def load_lab_openapi(root: Path) -> dict[str, Any]:
    path = root / "contracts" / "openapi-lab-v1.json"
    try:
        payload = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StartupError("contracts_invalid", f"无法加载 lab OpenAPI 契约: {exc}") from None
    if not isinstance(payload, dict):
        raise StartupError("contracts_invalid", "lab OpenAPI 契约必须是对象")
    return payload


def prepare_token(token_file: Path) -> str:
    token = secrets.token_urlsafe(32)
    try:
        token_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, (token + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise StartupError(
            "token_file_error", f"无法写入 token 文件 {token_file}: {exc.strerror or exc}"
        ) from None
    mode = token_file.stat().st_mode & 0o777
    if mode != 0o600:
        raise StartupError("token_file_error", f"token 文件权限不是 0600: {oct(mode)}")
    return token


def _validate_origins(allowed_origins: list[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for origin in allowed_origins:
        if origin == "*":
            raise StartupError("invalid_origin", "--allow-origin 不允许 *")
        if not origin:
            raise StartupError("invalid_origin", "--allow-origin 不能为空")
        cleaned.append(origin)
    return tuple(cleaned)


def run_server(
    *,
    root: Path,
    host: str = "127.0.0.1",
    port: int = 0,
    state: Path | None = None,
    token_file: Path | None = None,
    allowed_origins: list[str] | None = None,
    provider: str = "mock",
    probe_index: Path | None = None,
    data_dir: Path | None = None,
    lab_root: Path | None = None,
) -> int:
    """启动服务；成功时阻塞在 ``serve_forever``，返回进程退出码。"""

    if host not in ALLOWED_HOSTS:
        print(f"invalid_host: 只允许 {', '.join(ALLOWED_HOSTS)}，拒绝 {host}", file=sys.stderr)
        return 2
    if port < 0 or port > 65535:
        print("invalid_port: 端口必须在 0–65535", file=sys.stderr)
        return 2
    try:
        origins = _validate_origins(list(allowed_origins or []))
    except StartupError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2

    try:
        adapter = build_adapter(provider)
    except Exception as exc:  # ProviderNotConfigured / ValueError
        print(f"provider_not_configured: {exc}", file=sys.stderr)
        return 1

    try:
        if probe_index is not None:
            index = load_index(probe_index)
        else:
            index = build_index(root)
    except ProbeIndexError as exc:
        print(f"probe_index_invalid: {exc}", file=sys.stderr)
        return 2

    try:
        openapi = load_openapi(root)
    except StartupError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2

    state_root = (state or Path("state")).resolve()
    token_path = (token_file or (state_root / "token")).resolve()
    try:
        token = prepare_token(token_path)
    except StartupError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2

    app = AgentApp(
        index=index,
        adapter=adapter,
        token=token,
        allowed_origins=origins,
        openapi=openapi,
        data_dir=(data_dir or (root / "web" / "data")).resolve(),
        root=root,
        lab_root=(lab_root or Path("/tmp/molt-lab")),
    )
    try:
        server = create_server(app, host, port)
    except OSError as exc:
        print(f"listen_failed: {exc.strerror or exc}", file=sys.stderr)
        return 2

    bound_port = server.server_address[1]
    listen = f"http://127.0.0.1:{bound_port}"
    print(json.dumps({"listen": listen, "token_file": str(token_path)}, ensure_ascii=False), flush=True)
    print(f"listening on {listen}", file=sys.stderr, flush=True)
    print(f"token file: {token_path}", file=sys.stderr, flush=True)
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    """独立入口（测试与调试用）；CLI 子命令见 ``molt/cli.py``。"""

    parser = argparse.ArgumentParser(prog="molt-agent-server", description="molt 本地 agent API")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--state", type=Path, default=Path("state"))
    parser.add_argument("--token-file", type=Path, default=None)
    parser.add_argument("--allow-origin", action="append", default=[])
    parser.add_argument("--provider", choices=("mock", "openai-compatible"), default="mock")
    parser.add_argument("--probe-index", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--lab-root", type=Path, default=Path("/tmp/molt-lab"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_server(
        root=args.root.resolve(),
        host=args.host,
        port=args.port,
        state=args.state,
        token_file=args.token_file,
        allowed_origins=args.allow_origin,
        provider=args.provider,
        probe_index=args.probe_index,
        data_dir=args.data_dir,
        lab_root=args.lab_root,
    )


if __name__ == "__main__":
    sys.exit(main())
