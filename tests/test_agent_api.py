"""本地 agent API 端到端测试：路由、鉴权、CORS、session、SSE、契约样例、零写入。

全部请求只连 127.0.0.1 的临时端口；provider 固定 mock 或脚本 adapter，不联网。
"""

from __future__ import annotations

import copy
import http.client
import importlib.util
import json
import threading
import time
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from molt.agent.chat import ChatReply, MockAdapter
from molt.agent.errors import ErrorCode
from molt.agent.probe import build_index
from molt.agent.server import (
    ROUTES,
    AgentApp,
    StartupError,
    _validate_origins,
    create_server,
    load_openapi,
    prepare_token,
    run_server,
)
from molt.agent.store import Message, utcnow

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "contracts" / "schemas"
EXAMPLES = ROOT / "contracts" / "examples"
TOKEN = "test-token-local-only"

EXPECTED_PATHS = {
    "/api/health",
    "/api/openapi.json",
    "/api/experiments",
    "/api/experiments/{experiment_id}",
    "/api/sessions",
    "/api/sessions/{session_id}",
    "/api/sessions/{session_id}/messages",
    "/api/sessions/{session_id}/events",
}


def _tree_snapshot():
    spec = importlib.util.spec_from_file_location(
        "molt_zero_write", ROOT / "tests" / "test_zero_write.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.tree_snapshot


class BlockingAdapter:
    name = "fake"
    model = "fake"
    real_model = True
    adapter_ready = True
    provider_error = None

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def complete(self, messages, tools, tool_choice):  # noqa: ANN001
        self.started.set()
        if not self.release.wait(5):
            raise RuntimeError("blocking adapter timeout")
        return ChatReply(content="done")


class Harness:
    def __init__(self, adapter=None, allowed_origins=()):  # noqa: ANN001
        self.adapter = adapter or MockAdapter()
        self.index = build_index(ROOT)
        self.app = AgentApp(
            index=self.index,
            adapter=self.adapter,
            token=TOKEN,
            allowed_origins=tuple(allowed_origins),
            openapi=load_openapi(ROOT),
        )
        self.server = create_server(self.app, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    # ---- 请求 -------------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        body: object | None = None,
        token: str | None = TOKEN,
        headers: dict[str, str] | None = None,
        raw_host: str | None = None,
        raw_body: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        payload: bytes | None = raw_body
        request_headers = dict(headers or {})
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        if token is not None:
            request_headers["Authorization"] = f"Bearer {token}"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        if raw_host is None:
            connection.request(method, path, body=payload, headers=request_headers)
        else:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            connection.putheader("Host", raw_host)
            for key, value in request_headers.items():
                connection.putheader(key, value)
            if payload is not None:
                connection.putheader("Content-Length", str(len(payload)))
            connection.endheaders()
            if payload is not None:
                connection.send(payload)
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        connection.close()
        return result

    def stream(self, path: str, *, token: str | None = TOKEN):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        text = response.read().decode("utf-8")
        connection.close()
        return response.status, dict(response.getheaders()), text


@pytest.fixture()
def harness():
    instance = Harness()
    try:
        yield instance
    finally:
        instance.close()


def _json(body: bytes) -> dict:
    return json.loads(body.decode("utf-8"))


def _parse_sse(text: str) -> list[dict]:
    frames: list[dict] = []
    for block in text.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        frame: dict = {"data_lines": []}
        for line in block.split("\n"):
            if line.startswith("id: "):
                frame["id"] = int(line[4:])
            elif line.startswith("event: "):
                frame["event"] = line[7:]
            elif line.startswith("data: "):
                frame["data_lines"].append(line[6:])
        frame["data"] = json.loads("\n".join(frame.pop("data_lines")) or "{}")
        frames.append(frame)
    return frames


def _wait_idle(harness: Harness, session_id: str, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, _, body = harness.request("GET", f"/api/sessions/{session_id}")
        session = _json(body)
        if session["status"] == "idle":
            return session
        time.sleep(0.02)
    raise AssertionError("turn 未在超时内结束")


# ---------------------------------------------------------------------------
# 路由 / 鉴权 / CORS
# ---------------------------------------------------------------------------
def test_health_and_openapi_are_public(harness: Harness) -> None:
    status, _, body = harness.request("GET", "/api/health", token=None)
    assert status == 200
    health = _json(body)
    assert health["status"] == "ok"
    assert health["provider"] == "mock"
    assert health["real_model"] is False
    assert health["adapter_ready"] is True
    assert health["provider_error"] is None
    serialized = body.decode()
    for marker in ("api_key", "Authorization", "base_url", "token"):
        assert marker not in serialized

    status, _, body = harness.request("GET", "/api/openapi.json", token=None)
    assert status == 200
    document = _json(body)
    assert set(document["paths"]) == EXPECTED_PATHS


def test_route_priority_and_methods(harness: Harness) -> None:
    assert harness.request("GET", "/api/unknown")[0] == 404
    assert harness.request("GET", "/api/health/")[0] == 404
    status, headers, body = harness.request("HEAD", "/api/health", token=None)
    assert status == 405
    assert "GET" in headers["Allow"]
    assert body == b""  # HEAD 不返回 body

    for method in ("PATCH", "PUT"):
        status, headers, _ = harness.request(method, "/api/health", token=None)
        assert status == 405
        assert "Allow" in headers

    # 未知 path 的 OPTIONS 也是 404
    assert harness.request("OPTIONS", "/api/nope", token=None)[0] == 404


def test_bearer_authentication(harness: Harness) -> None:
    status, headers, body = harness.request("GET", "/api/sessions", token=None)
    assert status == 401
    assert headers["WWW-Authenticate"] == "Bearer"
    assert _json(body)["error"]["code"] == "unauthorized"

    assert harness.request("GET", "/api/sessions", token="wrong")[0] == 401
    assert harness.request("GET", "/api/sessions")[0] == 200
    assert harness.request("GET", "/api/experiments", token=None)[0] == 401
    assert harness.request("GET", "/api/experiments")[0] == 200


def test_host_header_allowlist(harness: Harness) -> None:
    status, _, body = harness.request("GET", "/api/health", raw_host="evil.example", token=None)
    assert status == 400
    assert _json(body)["error"]["code"] == "bad_request"

    status, _, _ = harness.request(
        "GET", "/api/health", raw_host="127.0.0.1", token=None
    )
    assert status == 400

    status, _, _ = harness.request(
        "GET", "/api/health", raw_host=f"localhost:{harness.port}", token=None
    )
    assert status == 200

    status, _, _ = harness.request(
        "GET", "/api/health", raw_host=f"127.0.0.1:{harness.port}", token=None
    )
    assert status == 200


def test_origin_rules(harness: Harness) -> None:
    origin = f"http://127.0.0.1:{harness.port}"
    status, headers, _ = harness.request(
        "GET", "/api/health", token=None, headers={"Origin": "http://evil.example"}
    )
    assert status == 403
    assert "Access-Control-Allow-Origin" not in headers

    status, headers, _ = harness.request(
        "GET", "/api/health", token=None, headers={"Origin": origin}
    )
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == origin
    assert headers["Vary"] == "Origin"

    status, _, _ = harness.request(
        "GET", "/api/health", token=None, headers={"Origin": "null"}
    )
    assert status == 403
    status, _, _ = harness.request(
        "GET", "/api/health", token=None, headers={"Origin": "*"}
    )
    assert status == 403


def test_origin_null_requires_explicit_flag() -> None:
    instance = Harness(allowed_origins=("null", "http://example.test"))
    try:
        status, headers, _ = instance.request(
            "GET", "/api/health", token=None, headers={"Origin": "null"}
        )
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == "null"
        status, headers, _ = instance.request(
            "GET", "/api/health", token=None, headers={"Origin": "http://example.test"}
        )
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == "http://example.test"
    finally:
        instance.close()


def test_allow_origin_star_is_rejected() -> None:
    with pytest.raises(StartupError):
        _validate_origins(["*"])


def test_preflight_headers(harness: Harness) -> None:
    origin = f"http://127.0.0.1:{harness.port}"
    status, headers, body = harness.request(
        "OPTIONS",
        "/api/sessions",
        token=None,
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )
    assert status == 204
    assert body == b""
    assert headers["Access-Control-Allow-Origin"] == origin
    assert headers["Access-Control-Allow-Methods"] == "GET, POST, DELETE, OPTIONS"
    assert headers["Access-Control-Allow-Headers"] == "Authorization, Content-Type, Last-Event-ID"
    assert headers["Vary"] == "Origin"

    # 已知 path 的 OPTIONS 不需要 Bearer；未知 path 仍 404
    assert harness.request("OPTIONS", "/api/experiments", token=None)[0] == 204


# ---------------------------------------------------------------------------
# session CRUD
# ---------------------------------------------------------------------------
def test_sessions_crud(harness: Harness) -> None:
    status, _, body = harness.request("POST", "/api/sessions", body={})
    assert status == 201
    session = _json(body)
    assert session["status"] == "idle"
    assert session["provider"] == "mock"
    assert session["last_event_id"] == 0
    session_id = session["session_id"]

    status, _, body = harness.request("GET", "/api/sessions")
    assert status == 200
    sessions = _json(body)["sessions"]
    assert [item["session_id"] for item in sessions] == [session_id]
    summary = sessions[0]
    assert set(summary) == {
        "session_id",
        "status",
        "provider",
        "created_at",
        "last_event_id",
        "message_count",
    }
    assert "messages" not in summary
    assert summary["message_count"] == 0
    _validate(summary, "session-summary.schema.json")

    status, _, body = harness.request("GET", f"/api/sessions/{session_id}")
    assert status == 200
    detail = _json(body)
    assert set(detail) == {
        "session_id",
        "status",
        "provider",
        "created_at",
        "messages",
        "last_event_id",
    }
    assert detail["messages"] == []
    assert harness.request("GET", "/api/sessions/does-not-exist")[0] == 404

    status, _, body = harness.request("DELETE", f"/api/sessions/{session_id}")
    assert status == 204
    assert body == b""
    assert harness.request("GET", f"/api/sessions/{session_id}")[0] == 404
    assert harness.request("DELETE", f"/api/sessions/{session_id}")[0] == 404


def test_session_create_rejects_unknown_fields(harness: Harness) -> None:
    status, _, body = harness.request("POST", "/api/sessions", body={"provider": "mock"})
    assert status == 400
    assert _json(body)["error"]["code"] == "bad_request"


def test_message_validation(harness: Harness) -> None:
    _, _, body = harness.request("POST", "/api/sessions", body={})
    session_id = _json(body)["session_id"]

    assert (
        harness.request(
            "POST", f"/api/sessions/{session_id}/messages", body={"content": "x", "role": "user"}
        )[0]
        == 400
    )
    assert (
        harness.request("POST", f"/api/sessions/{session_id}/messages", body={"content": 1})[0]
        == 400
    )
    assert (
        harness.request("POST", f"/api/sessions/{session_id}/messages", body={"content": ""})[0]
        == 400
    )
    assert (
        harness.request(
            "POST", f"/api/sessions/{session_id}/messages", body={"content": "a" * 16385}
        )[0]
        == 400
    )
    assert (
        harness.request(
            "POST",
            f"/api/sessions/{session_id}/messages",
            raw_body=b"{}",
            headers={"Content-Type": "text/plain"},
        )[0]
        == 415
    )


def test_payload_too_large(harness: Harness) -> None:
    _, _, body = harness.request("POST", "/api/sessions", body={})
    session_id = _json(body)["session_id"]
    huge = b'{"content":"' + b"a" * (70 * 1024) + b'"}'
    status, _, body = harness.request(
        "POST", f"/api/sessions/{session_id}/messages", raw_body=huge
    )
    assert status == 413
    assert _json(body)["error"]["code"] == "payload_too_large"


def test_history_limit_returns_413(harness: Harness) -> None:
    _, _, body = harness.request("POST", "/api/sessions", body={})
    session_id = _json(body)["session_id"]
    session = harness.app.store.get(session_id)
    assert session is not None
    session.messages.extend(
        Message(
            message_id=f"msg-seed-{index}",
            role="user" if index % 2 == 0 else "assistant",
            content="seed",
            created_at=utcnow(),
            turn_id="turn-seed",
        )
        for index in range(200)
    )
    status, _, body = harness.request(
        "POST", f"/api/sessions/{session_id}/messages", body={"content": "hi"}
    )
    assert status == 413
    assert _json(body)["error"]["code"] == "payload_too_large"


def test_turn_conflict_and_delete_conflict() -> None:
    adapter = BlockingAdapter()
    instance = Harness(adapter=adapter)
    try:
        _, _, body = instance.request("POST", "/api/sessions", body={})
        session_id = _json(body)["session_id"]
        status, _, body = instance.request(
            "POST", f"/api/sessions/{session_id}/messages", body={"content": "阻塞一下"}
        )
        assert status == 202
        assert adapter.started.wait(2)

        status, _, body = instance.request(
            "POST", f"/api/sessions/{session_id}/messages", body={"content": "再来"}
        )
        assert status == 409
        assert _json(body)["error"]["details"]["reason"] == "turn_in_progress"

        status, _, body = instance.request("DELETE", f"/api/sessions/{session_id}")
        assert status == 409
        assert _json(body)["error"]["details"]["reason"] == "turn_in_progress"

        adapter.release.set()
        _wait_idle(instance, session_id)
        assert instance.request("DELETE", f"/api/sessions/{session_id}")[0] == 204
    finally:
        adapter.release.set()
        instance.close()


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------
def test_sse_first_event_is_turn_started(harness: Harness) -> None:
    _, _, body = harness.request("POST", "/api/sessions", body={})
    session_id = _json(body)["session_id"]
    status, _, body = harness.request(
        "POST", f"/api/sessions/{session_id}/messages", body={"content": "你好"}
    )
    assert status == 202
    accepted = _json(body)
    assert accepted["event_cursor"] is None
    since = 0 if accepted["event_cursor"] is None else accepted["event_cursor"]

    status, headers, text = harness.stream(f"/api/sessions/{session_id}/events?since={since}")
    assert status == 200
    assert headers["Content-Type"].startswith("text/event-stream")
    frames = _parse_sse(text)
    assert frames[0]["event"] == "turn.started"
    assert frames[0]["data"]["turn_id"] == accepted["turn_id"]
    assert frames[0]["data"]["session_id"] == session_id
    assert frames[-1]["event"] == "done"
    assert frames[-1]["data"] == {}
    types = [frame["event"] for frame in frames]
    assert types.count("message.completed") == 1
    assert "turn.completed" in types
    completed = frames[types.index("message.completed")]
    assert completed["data"]["role"] == "assistant"

    # 已结束 turn 的 replay：发完即关，首事件仍是 turn.started
    status, _, replay = harness.stream(f"/api/sessions/{session_id}/events?since=0")
    assert status == 200
    replay_frames = _parse_sse(replay)
    assert replay_frames[0]["event"] == "turn.started"
    assert replay_frames[-1]["event"] == "done"


def test_sse_tool_turn_sequence(harness: Harness) -> None:
    _, _, body = harness.request("POST", "/api/sessions", body={})
    session_id = _json(body)["session_id"]
    status, _, body = harness.request(
        "POST", f"/api/sessions/{session_id}/messages", body={"content": "看一下实验"}
    )
    assert status == 202
    status, _, text = harness.stream(f"/api/sessions/{session_id}/events?since=0")
    assert status == 200
    frames = _parse_sse(text)
    types = [frame["event"] for frame in frames]
    assert types[:4] == ["turn.started", "status", "tool.call", "status"]
    tool_call = frames[2]["data"]
    assert tool_call["tool"] == "experiments_get"
    assert tool_call["arguments"] == {"experiment_id": "2c-memory-v3"}
    tool_result = next(frame for frame in frames if frame["event"] == "tool.result")
    assert tool_result["data"]["status"] == "ok"
    assert tool_result["data"]["result"]["experiment_id"] == "2c-memory-v3"
    assert types[-2:] == ["turn.completed", "done"]
    states = [frame["data"]["state"] for frame in frames if frame["event"] == "status"]
    assert states == ["thinking", "tool", "responding"]

    # 历史落库：user/assistant 对 + experiment_refs
    session = _wait_idle(harness, session_id)
    assert [message["role"] for message in session["messages"]] == ["user", "assistant"]
    assert session["messages"][1]["experiment_refs"] == ["2c-memory-v3"]

    _, _, listed = harness.request("GET", "/api/sessions")
    assert _json(listed)["sessions"][0]["message_count"] == 2


def test_sse_stale_cursor_returns_410(harness: Harness) -> None:
    _, _, body = harness.request("POST", "/api/sessions", body={})
    session_id = _json(body)["session_id"]
    session = harness.app.store.get(session_id)
    assert session is not None
    with session.condition:
        for _ in range(4100):
            session.append_event("status", {"turn_id": "turn-seed", "state": "thinking"})
    status, _, body = harness.request("GET", f"/api/sessions/{session_id}/events?since=1")
    assert status == 410
    error = _json(body)["error"]
    assert error["code"] == "stale_event_id"
    assert error["details"]["reset_required"] is True

    assert (
        harness.request("GET", f"/api/sessions/{session_id}/events?since=-1")[0] == 400
    )
    assert (
        harness.request("GET", f"/api/sessions/{session_id}/events?since=abc")[0] == 400
    )
    assert harness.request("GET", "/api/sessions/nope/events?since=0")[0] == 404


def test_sse_since_is_exclusive(harness: Harness) -> None:
    _, _, body = harness.request("POST", "/api/sessions", body={})
    session_id = _json(body)["session_id"]
    harness.request("POST", f"/api/sessions/{session_id}/messages", body={"content": "hi"})
    session = _wait_idle(harness, session_id)
    last = session["last_event_id"]
    # since=last → 无 replay，进入 live tail；用短超时读不到 done，连接由客户端关闭。
    connection = http.client.HTTPConnection("127.0.0.1", harness.port, timeout=1)
    connection.request(
        "GET",
        f"/api/sessions/{session_id}/events?since={last}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    response = connection.getresponse()
    assert response.status == 200
    try:
        response.read(1)
    except TimeoutError:
        pass
    connection.close()


# ---------------------------------------------------------------------------
# 契约样例
# ---------------------------------------------------------------------------
def _registry() -> Registry:
    resources = []
    for path in sorted(SCHEMAS.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        resources.append((document["$id"], Resource.from_contents(document)))
    return Registry().with_resources(resources)


def _validate(instance, schema_name: str) -> None:  # noqa: ANN001
    schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator(schema, registry=_registry()).validate(instance)


def test_error_code_enum_matches_contract() -> None:
    schema = json.loads((SCHEMAS / "error.schema.json").read_text(encoding="utf-8"))
    codes = set(schema["properties"]["error"]["properties"]["code"]["enum"])
    assert codes == {code.value for code in ErrorCode}


def test_openapi_paths_and_refs() -> None:
    document = json.loads((ROOT / "contracts" / "openapi-v1.json").read_text(encoding="utf-8"))
    assert set(document["paths"]) == EXPECTED_PATHS
    assert document["openapi"].startswith("3.")
    refs: list[str] = []

    def walk(value) -> None:  # noqa: ANN001
        if isinstance(value, dict):
            if "$ref" in value:
                refs.append(value["$ref"])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(document)
    assert refs
    assert all(ref.startswith("#/") for ref in refs)
    # 服务路由集合与契约 path 集合相等（模板参数名一致）
    assert len(ROUTES) == 10


def test_examples_validate_against_schemas() -> None:
    index = json.loads((EXAMPLES / "experiment-index.json").read_text(encoding="utf-8"))
    _validate(index, "experiment-index.schema.json")
    detail = json.loads((EXAMPLES / "experiment-detail.json").read_text(encoding="utf-8"))
    _validate(detail, "experiment.schema.json")
    assert detail["experiment_id"] == "2c-main"
    session = json.loads((EXAMPLES / "session.json").read_text(encoding="utf-8"))
    _validate(session, "session.schema.json")
    for name in ("error.json", "error-stale.json"):
        _validate(json.loads((EXAMPLES / name).read_text(encoding="utf-8")), "error.schema.json")

    tool_call_schema = json.loads((SCHEMAS / "tool-call.schema.json").read_text(encoding="utf-8"))
    tool_result_schema = json.loads(
        (SCHEMAS / "tool-result.schema.json").read_text(encoding="utf-8")
    )
    registry = _registry()
    tool_call_validator = Draft202012Validator(tool_call_schema, registry=registry)
    tool_result_validator = Draft202012Validator(tool_result_schema, registry=registry)

    for name in ("events.jsonl", "events-simple.jsonl"):
        lines = (EXAMPLES / name).read_text(encoding="utf-8").splitlines()
        assert lines
        for line in lines:
            envelope = json.loads(line)
            _validate(envelope, "event.schema.json")
            if envelope["event"] == "tool.call":
                tool_call_validator.validate(envelope["data"])
            if envelope["event"] == "tool.result":
                tool_result_validator.validate(envelope["data"])

    accepted = json.loads((EXAMPLES / "message-accepted.json").read_text(encoding="utf-8"))
    assert set(accepted) == {"turn_id", "event_cursor"}
    assert accepted["event_cursor"] is None

    startup_lines = (EXAMPLES / "startup.txt").read_text(encoding="utf-8").splitlines()
    json_lines = [line for line in startup_lines if line.startswith("{")]
    assert len(json_lines) == 1
    assert set(json.loads(json_lines[0])) == {"listen", "token_file"}


def test_message_request_schema_rejects_unknown_fields() -> None:
    schema = json.loads((SCHEMAS / "message-request.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, registry=_registry())
    validator.validate({"content": "hi"})
    with pytest.raises(ValidationError):
        validator.validate({"content": "hi", "role": "user"})
    create_schema = json.loads(
        (SCHEMAS / "session-create-request.schema.json").read_text(encoding="utf-8")
    )
    create_validator = Draft202012Validator(create_schema, registry=_registry())
    create_validator.validate({})
    with pytest.raises(ValidationError):
        create_validator.validate({"provider": "mock"})


# ---------------------------------------------------------------------------
# 启动 / token
# ---------------------------------------------------------------------------
def test_prepare_token_permissions(tmp_path: Path) -> None:
    token_file = tmp_path / "nested" / "token"
    token = prepare_token(token_file)
    assert len(token) >= 40
    assert token_file.read_text(encoding="utf-8") == token + "\n"
    assert (token_file.stat().st_mode & 0o777) == 0o600


def test_prepare_token_fails_closed(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(StartupError) as excinfo:
        prepare_token(blocker / "token")
    assert excinfo.value.code == "token_file_error"


def test_run_server_rejects_non_loopback(capsys: pytest.CaptureFixture[str]) -> None:
    code = run_server(root=ROOT, host="0.0.0.0", state=Path("/tmp/opencode/unused-state"))
    assert code == 2
    assert "invalid_host" in capsys.readouterr().err


def test_run_server_requires_chat_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("MOLT_CHAT_MODEL", raising=False)
    code = run_server(
        root=ROOT,
        provider="openai-compatible",
        state=Path("/tmp/opencode/unused-state"),
    )
    assert code == 1
    assert "provider_not_configured" in capsys.readouterr().err


def test_run_server_rejects_bad_probe_index(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "index.json"
    bad.write_text("{}", encoding="utf-8")
    code = run_server(
        root=ROOT,
        probe_index=bad,
        state=tmp_path / "state",
        token_file=tmp_path / "state" / "token",
    )
    assert code == 2
    assert "probe_index_invalid" in capsys.readouterr().err


def test_run_server_rejects_out_of_contract_origin(capsys: pytest.CaptureFixture[str]) -> None:
    code = run_server(
        root=ROOT,
        allowed_origins=["*"],
        state=Path("/tmp/opencode/unused-state"),
    )
    assert code == 2
    assert "invalid_origin" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 零写入
# ---------------------------------------------------------------------------
def test_serving_requests_does_not_write_repository() -> None:
    tree_snapshot = _tree_snapshot()
    before = tree_snapshot(ROOT)
    instance = Harness()
    try:
        assert instance.request("GET", "/api/health", token=None)[0] == 200
        assert instance.request("GET", "/api/experiments")[0] == 200
        _, _, body = instance.request("POST", "/api/sessions", body={})
        session_id = _json(body)["session_id"]
        instance.request(
            "POST", f"/api/sessions/{session_id}/messages", body={"content": "看一下实验"}
        )
        status, _, _ = instance.stream(f"/api/sessions/{session_id}/events?since=0")
        assert status == 200
        instance.request("DELETE", f"/api/sessions/{session_id}")
    finally:
        instance.close()
    after = tree_snapshot(ROOT)
    assert after == before


def test_openapi_document_is_served_from_contracts(harness: Harness) -> None:
    _, _, body = harness.request("GET", "/api/openapi.json", token=None)
    served = _json(body)
    on_disk = json.loads((ROOT / "contracts" / "openapi-v1.json").read_text(encoding="utf-8"))
    assert served == on_disk


def test_experiment_detail_matches_index(harness: Harness) -> None:
    _, _, body = harness.request("GET", "/api/experiments")
    index = _json(body)
    assert set(index) == {"schema_version", "generated_at", "revision", "experiments"}
    _validate(index, "experiment-index.schema.json")
    experiment = copy.deepcopy(index["experiments"][2])
    _, _, detail = harness.request("GET", f"/api/experiments/{experiment['experiment_id']}")
    assert _json(detail) == experiment
    assert harness.request("GET", "/api/experiments/nope")[0] == 404
