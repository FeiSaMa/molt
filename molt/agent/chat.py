"""chat provider：``mock``（默认、确定性、零网络）与 ``openai-compatible``。

约定：

* key 只读 ``MOLT_CHAT_API_KEY``；不存在的 key 就不发 ``Authorization``，
  **不回退** ``DEEPSEEK_API_KEY`` / ``MOLT_PROPOSER_API_KEY``。
* ``openai-compatible`` 必须显式设置 ``MOLT_CHAT_MODEL``，否则启动即失败
  （``provider_not_configured``，不监听）。
* 请求体固定 ``stream=false``，包含两个工具声明；首调 ``tool_choice="auto"``，
  工具二调 ``tool_choice="none"``。不传 session_id。
* 上游错误映射成 :class:`ChatError`（SSE-only 错误码），由 turn 转成 ``error`` 事件；
  HTTP 层永远不返回 502/504。
* 任何响应 / 日志 / 异常都不能包含 key 或 base URL。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from molt.agent.errors import ErrorCode

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_TIMEOUT = 60.0
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_CONTENT_CHARS = 256 * 1024

SYSTEM_PROMPT = (
    "你是 molt 的本地只读实验助手。只能使用 experiments_list / experiments_get 读取"
    "实验投影；不得编造未返回的数字。回答使用中文，说明实验的 stage、口径与 caveat，"
    "不得把不同版本或不同分母的结果放在一起比较。"
)


class ProviderNotConfigured(RuntimeError):
    """启动期配置缺失（不监听，exit 1）。"""


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass
class ChatReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class ChatError(Exception):
    """上游失败；只在 SSE ``error`` 事件里出现。"""

    def __init__(self, code: ErrorCode, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class Transport(Protocol):
    def post_json(
        self, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
    ) -> dict[str, Any]: ...


def map_http_status(status: int) -> ChatError:
    """上游 HTTP 状态 → SSE-only 错误码（不产生 HTTP 502/504）。"""

    if status in (401, 403):
        return ChatError(ErrorCode.UPSTREAM_AUTH_FAILED, "上游鉴权失败", False)
    if status == 429:
        return ChatError(ErrorCode.UPSTREAM_RATE_LIMITED, "上游限流", True)
    return ChatError(ErrorCode.UPSTREAM_ERROR, f"上游返回 HTTP {status}", status >= 500)


def map_transport_exception(exc: BaseException) -> ChatError:
    """连接/超时异常 → SSE-only 错误码。"""

    if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            return ChatError(ErrorCode.UPSTREAM_TIMEOUT, "上游超时", True)
        return ChatError(ErrorCode.UPSTREAM_UNAVAILABLE, "上游不可用", True)
    if isinstance(exc, TimeoutError):
        return ChatError(ErrorCode.UPSTREAM_TIMEOUT, "上游超时", True)
    return ChatError(ErrorCode.UPSTREAM_UNAVAILABLE, "上游不可用", True)


class UrllibTransport:
    """最小 stdlib HTTP transport；只被 openai-compatible 使用。"""

    def post_json(
        self, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise map_http_status(code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise map_transport_exception(exc) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ChatError(ErrorCode.UPSTREAM_ERROR, "上游响应超过 1 MiB 上限", False)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ChatError(ErrorCode.UPSTREAM_ERROR, "上游响应不是合法 JSON", False) from None
        if not isinstance(parsed, dict):
            raise ChatError(ErrorCode.UPSTREAM_ERROR, "上游响应不是对象", False)
        return parsed


def _parse_tool_calls(raw_calls: Any) -> list[ToolCall]:
    if raw_calls is None:
        return []
    if not isinstance(raw_calls, list):
        raise ChatError(ErrorCode.UPSTREAM_ERROR, "上游 tool_calls 不是数组", False)
    calls: list[ToolCall] = []
    for item in raw_calls:
        if not isinstance(item, dict):
            raise ChatError(ErrorCode.UPSTREAM_ERROR, "上游 tool_call 不是对象", False)
        function = item.get("function") or {}
        name = function.get("name")
        arguments = function.get("arguments", "{}")
        if not isinstance(name, str) or not name:
            raise ChatError(ErrorCode.UPSTREAM_ERROR, "上游 tool_call 缺少 name", False)
        if isinstance(arguments, dict):
            arguments_text = json.dumps(arguments, ensure_ascii=False)
        elif isinstance(arguments, str):
            arguments_text = arguments
        else:
            raise ChatError(ErrorCode.UPSTREAM_ERROR, "上游 tool_call 参数不合法", False)
        calls.append(
            ToolCall(
                call_id=item.get("id") or "call-unknown",
                name=name,
                arguments=arguments_text,
            )
        )
    return calls


class MockAdapter:
    """确定性 mock：零网络、零成本；遇到「实验」触发一次 experiments_get。"""

    name = "mock"
    model = "mock-chat"
    real_model = False
    adapter_ready = True
    provider_error: str | None = None

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], tool_choice: str
    ) -> ChatReply:
        last_user = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                last_user = str(message.get("content", ""))
                break
        wants_tool = any(token in last_user for token in ("实验", "experiment", "probe"))
        if tool_choice == "auto" and wants_tool:
            return ChatReply(
                content="",
                tool_calls=[
                    ToolCall(
                        call_id="call-mock-001",
                        name="experiments_get",
                        arguments=json.dumps({"experiment_id": "2c-memory-v3"}, ensure_ascii=False),
                    )
                ],
            )
        if tool_choice == "none":
            content = (
                "已读取实验投影。结论只按该实验自己的 stage 与 caveat 解释："
                "2c-memory-v3 为 4/4（task 级），但版本字段缺失，不可与其他实验比较。"
            )
        else:
            trimmed = last_user.strip().replace("\n", " ")[:60]
            content = f"mock 回复：{trimmed}" if trimmed else "mock 回复：收到。"
        return ChatReply(content=content)


class OpenAICompatibleAdapter:
    """OpenAI ``/chat/completions`` 兼容后端（非流式）。"""

    name = "openai-compatible"
    real_model = True
    adapter_ready = True
    provider_error: str | None = None

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        transport: Transport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self._api_key = api_key
        self._transport = transport or UrllibTransport()
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], tool_choice: str
    ) -> ChatReply:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "stream": False,
        }
        raw = self._transport.post_json(self._endpoint, self._headers(), payload, self._timeout)
        if not isinstance(raw, dict):
            raise ChatError(ErrorCode.UPSTREAM_ERROR, "上游响应不是对象", False)
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ChatError(ErrorCode.UPSTREAM_ERROR, "上游响应缺少 choices", False)
        first = choices[0]
        if not isinstance(first, dict):
            raise ChatError(ErrorCode.UPSTREAM_ERROR, "上游 choice 不是对象", False)
        message = first.get("message")
        if not isinstance(message, dict):
            raise ChatError(ErrorCode.UPSTREAM_ERROR, "上游 choice 缺少 message", False)
        content = message.get("content") or ""
        if not isinstance(content, str):
            raise ChatError(ErrorCode.UPSTREAM_ERROR, "上游 content 不是字符串", False)
        if len(content) > MAX_CONTENT_CHARS:
            raise ChatError(ErrorCode.UPSTREAM_ERROR, "上游 content 超过 256 KiB 上限", False)
        return ChatReply(content=content, tool_calls=_parse_tool_calls(message.get("tool_calls")))


def build_adapter(provider: str) -> MockAdapter | OpenAICompatibleAdapter:
    if provider == "mock":
        return MockAdapter()
    if provider == "openai-compatible":
        model = os.environ.get("MOLT_CHAT_MODEL", "").strip()
        if not model:
            raise ProviderNotConfigured("openai-compatible 必须设置 MOLT_CHAT_MODEL")
        base_url = os.environ.get("MOLT_CHAT_BASE_URL", "").strip() or DEFAULT_BASE_URL
        api_key = os.environ.get("MOLT_CHAT_API_KEY") or None
        return OpenAICompatibleAdapter(base_url, model, api_key)
    raise ProviderNotConfigured(f"未知 provider: {provider}")


def adapter_health(adapter: MockAdapter | OpenAICompatibleAdapter) -> dict[str, Any]:
    """health 只暴露 provider/model/能力位，不暴露 base URL 或 key。"""

    return {
        "status": "ok",
        "provider": adapter.name,
        "model": adapter.model,
        "real_model": adapter.real_model,
        "adapter_ready": adapter.adapter_ready,
        "provider_error": adapter.provider_error,
    }
