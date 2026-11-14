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
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from molt.agent.errors import ErrorCode

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_TIMEOUT = 60.0
MAX_UPSTREAM_RESPONSE_BYTES = 256 * 1024
MAX_TRANSPORT_RESPONSE_BYTES = 1024 * 1024

SYSTEM_PROMPT = (
    "你是 molt 的通用 Agent 助手。先理解用户任务，再决定是否需要读取实验投影、"
    "使用已暴露的 Skill/MCP 工具或直接回答。不得编造未返回的数字、工具结果或执行状态。"
    "默认用中文，清楚区分建议、已执行动作与证据；涉及实验结果时说明 stage、口径与 caveat，"
    "不得把不同版本或不同分母的结果放在一起比较。当前工具与执行权限以本轮暴露的 allowlist 为准，"
    "没有工具时不要声称已经执行任务或修改代码。"
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
    # DeepSeek thinking mode requires the assistant's reasoning_content to be
    # passed back on the next request when tool calls are involved. It is not
    # displayed to the user and never stored in session history.
    reasoning_content: str = ""
    # Provider-reported usage for this call, if the provider returns it.
    usage: dict[str, int] | None = None


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
                raw = response.read(MAX_TRANSPORT_RESPONSE_BYTES + 1)
                if len(raw) > MAX_TRANSPORT_RESPONSE_BYTES:
                    raise ChatError(
                        ErrorCode.UPSTREAM_ERROR,
                        "上游响应超过 1 MiB",
                        False,
                    )
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise map_http_status(code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise map_transport_exception(exc) from None
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ChatError(ErrorCode.UPSTREAM_ERROR, "上游响应不是合法 JSON", False) from None
        if not isinstance(parsed, dict):
            raise ChatError(ErrorCode.UPSTREAM_ERROR, "上游响应不是对象", False)
        return parsed


_DSML_CALL_RE = re.compile(
    r'<｜｜DSML｜｜\s*invoke\s+name="(?P<name>[^"]+)"\s*>(?P<body>.*?)</｜｜DSML｜｜\s*invoke\s*>',
    re.DOTALL,
)
_DSML_PARAM_RE = re.compile(
    r'<｜｜DSML｜｜\s*parameter\s+name="(?P<name>[^"]+)"[^>]*>(?P<value>.*?)</｜｜DSML｜｜\s*parameter\s*>',
    re.DOTALL,
)


def _parse_dsml_tool_calls(content: str) -> list[ToolCall]:
    """Parse DeepSeek's legacy DSML tool markup when JSON tool_calls is absent."""
    calls: list[ToolCall] = []
    for index, match in enumerate(_DSML_CALL_RE.finditer(content)):
        args: dict[str, str] = {}
        for parameter in _DSML_PARAM_RE.finditer(match.group("body")):
            args[parameter.group("name")] = parameter.group("value").strip()
        calls.append(
            ToolCall(
                call_id=f"dsml-{index + 1}",
                name=match.group("name"),
                arguments=json.dumps(args, ensure_ascii=False),
            )
        )
    return calls


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
        self._usage_local = threading.local()

    def reset_usage(self) -> None:
        self._usage_local.items = []
        self._usage_local.calls = 0

    def _record_call(self) -> None:
        self._usage_local.calls = int(getattr(self._usage_local, "calls", 0)) + 1

    def _record_usage(self, usage: dict[str, int] | None) -> None:
        if usage:
            items = getattr(self._usage_local, "items", None)
            if items is None:
                items = self._usage_local.items = []
            items.append(dict(usage))

    def usage_snapshot(self) -> dict[str, int]:
        totals = {
            "model_calls": int(getattr(self._usage_local, "calls", 0)),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        for item in getattr(self._usage_local, "items", []) or []:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = item.get(key)
                if type(value) is int and value >= 0:
                    totals[key] += value
        return totals

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
        self._record_call()
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
        if len(content.encode("utf-8")) > MAX_UPSTREAM_RESPONSE_BYTES:
            raise ChatError(ErrorCode.UPSTREAM_ERROR, "上游 content 超过 256 KiB", False)
        reasoning = message.get("reasoning_content") or ""
        if not isinstance(reasoning, str):
            reasoning = ""
        tool_calls = _parse_tool_calls(message.get("tool_calls"))
        if not tool_calls and content and "<｜｜DSML｜｜" in content:
            tool_calls = _parse_dsml_tool_calls(content)
            if tool_calls:
                content = ""
        usage = raw.get("usage")
        if not isinstance(usage, dict):
            usage = None
        else:
            clean = {
                key: value
                for key, value in usage.items()
                if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                and type(value) is int
                and value >= 0
            }
            usage = clean or None
        self._record_usage(usage)
        return ChatReply(
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning,
            usage=usage,
        )


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
