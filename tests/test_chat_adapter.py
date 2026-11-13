"""chat adapter 单测：请求形状、2n/2n+2、工具拒绝、错误映射、key 不泄露。

全部使用 fake transport / 脚本 adapter，**不打开任何 socket**。
"""

from __future__ import annotations

import copy
import json
import urllib.error
from pathlib import Path

import pytest

from molt.agent.chat import (
    ChatError,
    ChatReply,
    MockAdapter,
    OpenAICompatibleAdapter,
    ProviderNotConfigured,
    ToolCall,
    UrllibTransport,
    build_adapter,
    map_http_status,
    map_transport_exception,
)
from molt.agent.errors import ErrorCode
from molt.agent.probe import build_index
from molt.agent.store import SessionStore
from molt.agent.tools import TOOLS
from molt.agent.turn import TurnRunner, build_chat_messages

ROOT = Path(__file__).resolve().parents[1]


class FakeTransport:
    """记录请求并返回预设响应；不联网。"""

    def __init__(self, replies: list[object]) -> None:
        self._replies = list(replies)
        self.calls: list[dict] = []

    def post_json(self, url, headers, payload, timeout):  # noqa: ANN001
        self.calls.append(
            {"url": url, "headers": dict(headers), "payload": copy.deepcopy(payload), "timeout": timeout}
        )
        if not self._replies:
            raise AssertionError("FakeTransport 没有更多预设响应")
        reply = self._replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


class RecordingAdapter:
    """按脚本返回 ChatReply / 抛 ChatError，并记录每次调用的 messages。"""

    name = "fake"
    model = "fake-model"
    real_model = True
    adapter_ready = True
    provider_error = None

    def __init__(self, script: list[object]) -> None:
        self._script = list(script)
        self.calls: list[dict] = []

    def complete(self, messages, tools, tool_choice):  # noqa: ANN001
        self.calls.append(
            {
                "messages": copy.deepcopy(messages),
                "tools": copy.deepcopy(tools),
                "tool_choice": tool_choice,
            }
        )
        if not self._script:
            raise AssertionError("RecordingAdapter 脚本耗尽")
        reply = self._script.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _completion(content: str = "ok", tool_calls=None) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def _tool_call(call_id: str = "call-1", name: str = "experiments_get", arguments: str = "{}") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


@pytest.fixture(scope="module")
def index() -> dict:
    return build_index(ROOT)


def _runner(index: dict, adapter) -> tuple[TurnRunner, SessionStore]:  # noqa: ANN001
    store = SessionStore(provider=adapter.name)
    return TurnRunner(store, index, adapter), store


def _event_types(session) -> list[str]:  # noqa: ANN001
    return [event.event for event in session.events]


def _error_events(session) -> list[dict]:  # noqa: ANN001
    return [event.data for event in session.events if event.event == "error"]


# ---------------------------------------------------------------------------
# OpenAI-compatible 请求形状
# ---------------------------------------------------------------------------
def test_request_shape_has_two_tools_and_no_session_id() -> None:
    transport = FakeTransport([_completion("hi")])
    adapter = OpenAICompatibleAdapter(
        "http://127.0.0.1:8080/v1", "test-model", "sk-test-key", transport=transport
    )
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    reply = adapter.complete(messages, TOOLS, "auto")
    assert reply.content == "hi"
    assert reply.tool_calls == []

    call = transport.calls[0]
    payload = call["payload"]
    assert call["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-test-key"
    assert set(payload) == {"model", "messages", "tools", "tool_choice", "stream"}
    assert payload["stream"] is False
    assert payload["tool_choice"] == "auto"
    assert "session_id" not in payload
    assert [tool["function"]["name"] for tool in payload["tools"]] == [
        "experiments_list",
        "experiments_get",
    ]
    assert payload["tools"][0]["function"]["parameters"]["additionalProperties"] is False
    assert payload["tools"][1]["function"]["parameters"]["required"] == ["experiment_id"]


def test_request_without_tools_and_without_key() -> None:
    transport = FakeTransport([_completion("hi")])
    adapter = OpenAICompatibleAdapter("http://127.0.0.1:8080/v1", "m", None, transport=transport)
    adapter.complete([{"role": "user", "content": "u"}], TOOLS, "none")
    assert "Authorization" not in transport.calls[0]["headers"]
    assert transport.calls[0]["payload"]["tool_choice"] == "none"


def test_malformed_upstream_response_maps_to_upstream_error() -> None:
    transport = FakeTransport([{"nope": True}])
    adapter = OpenAICompatibleAdapter("http://127.0.0.1:8080/v1", "m", transport=transport)
    with pytest.raises(ChatError) as excinfo:
        adapter.complete([{"role": "user", "content": "u"}], TOOLS, "auto")
    assert excinfo.value.code is ErrorCode.UPSTREAM_ERROR
    assert excinfo.value.retryable is False


# ---------------------------------------------------------------------------
# 2n 历史与 2n+2 工具二调
# ---------------------------------------------------------------------------
def test_history_is_2n_and_tool_followup_appends_two(index: dict) -> None:
    tool_turn = ChatReply(
        tool_calls=[
            ToolCall(
                call_id="call-1",
                name="experiments_get",
                arguments=json.dumps({"experiment_id": "2c-memory-v3"}),
            )
        ]
    )
    adapter = RecordingAdapter(
        [
            ChatReply(content="a"),
            ChatReply(content="b"),
            ChatReply(content="c"),
            tool_turn,
            ChatReply(content="final"),
        ]
    )
    runner, store = _runner(index, adapter)
    session = store.create()
    for prompt in ("one", "two", "three"):
        runner.run(session, f"turn-{prompt}", prompt)

    assert len(session.messages) == 6  # 3 轮 × 2 条 = 2n
    runner.run(session, "turn-tool", "看一下实验")

    first_tool_call = adapter.calls[3]
    second_call = adapter.calls[4]
    assert first_tool_call["tool_choice"] == "auto"
    assert second_call["tool_choice"] == "none"

    first_messages = first_tool_call["messages"]
    assert len(first_messages) == 2 * 3 + 2  # system + 2n 历史 + 本轮 user
    assert first_messages[1:-1] == session.history_messages()[:6]
    assert first_messages[-1] == {"role": "user", "content": "看一下实验"}

    # 二调追加 assistant tool_calls + tool result 两条：历史 2n + 2 = 8
    second_messages = second_call["messages"]
    assert len(second_messages) == len(first_messages) + 2
    appended = second_messages[-2:]
    assert appended[0]["role"] == "assistant"
    assert appended[0]["tool_calls"][0]["id"] == "call-1"
    assert appended[1]["role"] == "tool"
    assert appended[1]["tool_call_id"] == "call-1"
    # 去掉 system 与当前 user 后 = 2n 历史 + 2 条工具追加 = 2n + 2
    assert len(second_messages) - 2 == 2 * 3 + 2

    completed = [event for event in session.events if event.event == "message.completed"]
    assert completed[-1].data["content"] == "final"
    assert session.status == "idle"
    assert session.successful_turns == 4


def test_content_and_tool_calls_prefers_tool(index: dict) -> None:
    adapter = RecordingAdapter(
        [
            ChatReply(
                content="应被丢弃的前文",
                tool_calls=[
                    ToolCall(call_id="c1", name="experiments_list", arguments="{}"),
                ],
            ),
            ChatReply(content="工具后的答案"),
        ]
    )
    runner, store = _runner(index, adapter)
    session = store.create()
    runner.run(session, "turn-tool", "列一下实验")
    completed = [event for event in session.events if event.event == "message.completed"]
    assert completed[-1].data["content"] == "工具后的答案"
    assert "应被丢弃的前文" not in json.dumps([event.data for event in session.events], ensure_ascii=False)


# ---------------------------------------------------------------------------
# 工具拒绝与错误
# ---------------------------------------------------------------------------
def test_multiple_tool_calls_are_rejected_without_execution(index: dict) -> None:
    adapter = RecordingAdapter(
        [
            ChatReply(
                tool_calls=[
                    ToolCall(call_id="c1", name="experiments_list", arguments="{}"),
                    ToolCall(call_id="c2", name="experiments_list", arguments="{}"),
                ]
            )
        ]
    )
    runner, store = _runner(index, adapter)
    session = store.create()
    runner.run(session, "turn-bad", "列实验")
    types = _event_types(session)
    assert "tool.call" not in types
    errors = _error_events(session)
    assert errors and errors[0]["code"] == "too_many_tool_calls"
    assert errors[0]["retryable"] is False
    assert session.messages == []
    assert session.successful_turns == 0
    assert len(adapter.calls) == 1


def test_second_call_tool_calls_are_also_rejected(index: dict) -> None:
    adapter = RecordingAdapter(
        [
            ChatReply(
                tool_calls=[
                    ToolCall(call_id="c1", name="experiments_get", arguments='{"experiment_id":"2c-main"}')
                ]
            ),
            ChatReply(
                tool_calls=[
                    ToolCall(call_id="c2", name="experiments_get", arguments='{"experiment_id":"batch1"}')
                ]
            ),
        ]
    )
    runner, store = _runner(index, adapter)
    session = store.create()
    runner.run(session, "turn-bad2", "看实验")
    assert "tool.call" in _event_types(session)
    assert "message.completed" not in _event_types(session)
    errors = _error_events(session)
    assert errors[-1]["code"] == "too_many_tool_calls"
    assert session.messages == []


def test_unknown_experiment_still_triggers_second_call(index: dict) -> None:
    adapter = RecordingAdapter(
        [
            ChatReply(
                tool_calls=[
                    ToolCall(
                        call_id="c1",
                        name="experiments_get",
                        arguments='{"experiment_id":"does-not-exist"}',
                    )
                ]
            ),
            ChatReply(content="没有这条实验"),
        ]
    )
    runner, store = _runner(index, adapter)
    session = store.create()
    runner.run(session, "turn-missing", "看实验")
    results = [event.data for event in session.events if event.event == "tool.result"]
    assert results[0]["status"] == "error"
    assert results[0]["reason"] == "not_found"
    assert len(adapter.calls) == 2
    tool_message = adapter.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert "not_found" in tool_message["content"]
    assert "message.completed" in _event_types(session)


def test_unknown_tool_is_denied_but_still_second_call(index: dict) -> None:
    adapter = RecordingAdapter(
        [
            ChatReply(
                tool_calls=[ToolCall(call_id="c1", name="experiments.delete", arguments="{}")]
            ),
            ChatReply(content="已拒绝"),
        ]
    )
    runner, store = _runner(index, adapter)
    session = store.create()
    runner.run(session, "turn-deny", "删实验")
    results = [event.data for event in session.events if event.event == "tool.result"]
    assert results[0]["status"] == "denied"
    assert results[0]["reason"] == "unknown_tool"
    assert len(adapter.calls) == 2


# ---------------------------------------------------------------------------
# 上游错误映射
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "code,retryable",
    [
        (ErrorCode.UPSTREAM_TIMEOUT, True),
        (ErrorCode.UPSTREAM_UNAVAILABLE, True),
        (ErrorCode.UPSTREAM_AUTH_FAILED, False),
        (ErrorCode.UPSTREAM_RATE_LIMITED, True),
        (ErrorCode.UPSTREAM_ERROR, True),
    ],
)
def test_upstream_error_becomes_sse_error_only(index: dict, code, retryable) -> None:  # noqa: ANN001
    adapter = RecordingAdapter([ChatError(code, "上游失败", retryable)])
    runner, store = _runner(index, adapter)
    session = store.create()
    runner.run(session, "turn-upstream", "hi")
    types = _event_types(session)
    assert "error" in types
    assert "done" not in types
    assert "message.completed" not in types
    error = _error_events(session)[0]
    assert error["code"] == code.value
    assert error["retryable"] is retryable
    assert session.status == "idle"
    assert session.messages == []


def test_http_status_mapping() -> None:
    assert map_http_status(401).code is ErrorCode.UPSTREAM_AUTH_FAILED
    assert map_http_status(403).code is ErrorCode.UPSTREAM_AUTH_FAILED
    assert map_http_status(429).code is ErrorCode.UPSTREAM_RATE_LIMITED
    assert map_http_status(500).code is ErrorCode.UPSTREAM_ERROR
    assert map_http_status(503).retryable is True
    assert map_http_status(404).retryable is False


def test_transport_exception_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise urllib.error.URLError(TimeoutError("slow"))

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(ChatError) as excinfo:
        UrllibTransport().post_json("http://127.0.0.1:9/x", {}, {}, 1.0)
    assert excinfo.value.code is ErrorCode.UPSTREAM_TIMEOUT

    def refuse(*args, **kwargs):  # noqa: ANN002, ANN003
        raise urllib.error.URLError(ConnectionRefusedError("no"))

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    with pytest.raises(ChatError) as excinfo:
        UrllibTransport().post_json("http://127.0.0.1:9/x", {}, {}, 1.0)
    assert excinfo.value.code is ErrorCode.UPSTREAM_UNAVAILABLE

    assert map_transport_exception(TimeoutError()).code is ErrorCode.UPSTREAM_TIMEOUT


# ---------------------------------------------------------------------------
# provider 配置 / key
# ---------------------------------------------------------------------------
def test_provider_requires_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOLT_CHAT_MODEL", raising=False)
    with pytest.raises(ProviderNotConfigured):
        build_adapter("openai-compatible")


def test_key_only_reads_molt_chat_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOLT_CHAT_MODEL", "test-model")
    monkeypatch.delenv("MOLT_CHAT_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-should-not-be-used")
    monkeypatch.setenv("MOLT_PROPOSER_API_KEY", "sk-proposer-should-not-be-used")
    adapter = build_adapter("openai-compatible")
    assert adapter._api_key is None  # noqa: SLF001 - 断言不回退
    assert adapter._endpoint == "http://127.0.0.1:8080/v1/chat/completions"  # noqa: SLF001


def test_key_never_appears_in_health_or_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from molt.agent.chat import adapter_health

    secret = "sk-secret-value-123"
    transport = FakeTransport([{"nope": True}])
    adapter = OpenAICompatibleAdapter("http://127.0.0.1:8080/v1", "m", secret, transport=transport)
    health = json.dumps(adapter_health(adapter), ensure_ascii=False)
    assert secret not in health
    assert "127.0.0.1" not in health
    with pytest.raises(ChatError) as excinfo:
        adapter.complete([{"role": "user", "content": "u"}], TOOLS, "auto")
    assert secret not in str(excinfo.value)
    assert secret not in json.dumps(transport.calls[0]["payload"])


def test_mock_is_deterministic_and_ignores_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOLT_CHAT_MODEL", "should-be-ignored")
    monkeypatch.setenv("MOLT_CHAT_BASE_URL", "http://127.0.0.1:1/v1")
    adapter = build_adapter("mock")
    assert isinstance(adapter, MockAdapter)
    assert adapter.name == "mock"
    messages = [{"role": "user", "content": "你好"}]
    assert adapter.complete(messages, TOOLS, "auto") == adapter.complete(messages, TOOLS, "auto")


def test_mock_triggers_one_experiments_get() -> None:
    adapter = MockAdapter()
    reply = adapter.complete([{"role": "user", "content": "看一下实验"}], TOOLS, "auto")
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].name == "experiments_get"
    followup = adapter.complete(
        [
            {"role": "user", "content": "看一下实验"},
            {"role": "assistant", "content": ""},
            {"role": "tool", "tool_call_id": "call-mock-001", "content": "{}"},
        ],
        TOOLS,
        "none",
    )
    assert followup.tool_calls == []


def test_build_chat_messages_uses_2n_history(index: dict) -> None:
    adapter = RecordingAdapter([ChatReply(content="a")] * 3)
    runner, store = _runner(index, adapter)
    session = store.create()
    for prompt in ("a", "b", "c"):
        runner.run(session, f"turn-{prompt}", prompt)
    messages = build_chat_messages(session, "next")
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "next"}
    assert len(messages[1:-1]) == 6
