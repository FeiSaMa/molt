"""一个 turn 的执行流程（thinking → tool → responding → done）。

调用顺序与事件顺序是契约的一部分（``tests/test_agent_api.py`` 会断言）：

1. ``turn.started``（含 session_id）
2. ``status`` state=thinking
3. 首调上游（``tool_choice="auto"``，两个工具声明）
4. 若返回 tool_calls：``tool.call`` → ``status`` state=tool → ``tool.result`` →
   二调上游（追加 assistant tool_calls + tool result，``tool_choice="none"``）
5. ``status`` state=responding → ``message.delta``（40 字符窗口）→
   ``message.completed`` → ``turn.completed`` → ``done``

失败语义：任何上游错误只走 ``error`` 事件（随后关闭 SSE），HTTP 层已返回 202；
失败 turn 不写历史（user/assistant 都不落库）。

**Runtime 模式（默认关闭）**：当 ``TurnRunner`` 持有 ``runtime`` 且该 session 被
``POST /api/sessions/{id}/runtime`` 设为 ``active`` 时，走独立的受预算循环：

1. ``turn.started`` → ``status:thinking``（与旧路径一致）
2. ``runtime.begin_turn(task_text)`` 一次读取 binding，返回不可变 TurnSnapshot；
   命中技能则发 ``skill.loaded``（无命中不发）
3. system prompt = ``SYSTEM_PROMPT`` + 空行 + ``render_system_block(pkg)``
4. 工具集 = ``build_tool_specs(turn.specs)``，只含 skill 权限与 MCP 批准交集
5. 最多 3 轮、总计 5 次工具调用、墙钟 60s、累计工具输出 64 KiB；
   每轮 ``tool_choice="auto"``，每轮最多 3 个 tool_calls
6. 预算耗尽 → ``error`` code=``tool_budget_exceeded``，不写历史
7. MCP profile 校验/启动失败 → ``error`` code=``mcp_unavailable``（allowlist
   完全未配置时只发 ``runtime.warning``，显式降级为技能上下文）
8. MCP 失败映射 ``tool.result`` 的 ``status=error`` + ``reason``，不拼进 system prompt

旧路径（``runtime is None`` 或 mode=off）保持单 tool + 二调 ``tool_choice="none"``
的语义不变。**一次 turn 内 binding 不再读取**：中途换绑定只影响下一次 turn。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from molt.agent.chat import SYSTEM_PROMPT, ChatError, ChatReply, ToolCall
from molt.agent.errors import ErrorCode
from molt.agent.probe import find_experiment
from molt.agent.runtime import AgentRuntimeError
from molt.agent.store import Message, Session, SessionClosed, SessionStore, new_id, utcnow
from molt.agent.tools import (
    TOOLS,
    ToolOutcome,
    build_tool_specs,
    execute_tool,
    execute_tool_dispatch,
)

CHUNK_SIZE = 40
MAX_RUNTIME_ROUNDS = 3
MAX_RUNTIME_TOOL_CALLS = 5
MAX_RUNTIME_TOOL_CALLS_PER_ROUND = 3
RUNTIME_WALL_SECONDS = 60.0
RUNTIME_OUTPUT_BYTES = 64 * 1024


def build_chat_messages(
    session: Session, user_content: str, system_prompt: str = SYSTEM_PROMPT
) -> list[dict[str, Any]]:
    """system + 成功历史对（2n）+ 本轮 user；不传 session_id。"""

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(session.history_messages())
    messages.append({"role": "user", "content": user_content})
    return messages


def chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)]


def _parsed_arguments(call: ToolCall) -> dict[str, Any] | str:
    try:
        parsed = json.loads(call.arguments)
    except (TypeError, json.JSONDecodeError):
        return call.arguments
    return parsed if isinstance(parsed, dict) else call.arguments


def _tool_followup_messages(
    messages: list[dict[str, Any]], call: ToolCall, outcome: ToolOutcome
) -> list[dict[str, Any]]:
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call.call_id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
        ],
    }
    tool_message = {
        "role": "tool",
        "tool_call_id": call.call_id,
        "content": outcome.tool_message_content(),
    }
    return [*messages, assistant, tool_message]


class TurnRunner:
    def __init__(
        self,
        store: SessionStore,
        index: dict[str, Any],
        adapter: Any,
        runtime: Any | None = None,
    ) -> None:
        self._store = store
        self._index = index
        self._adapter = adapter
        self._runtime = runtime

    # ---- 生命周期 ---------------------------------------------------------
    def start(self, session: Session, turn_id: str, user_content: str) -> None:
        thread = threading.Thread(
            target=self.run,
            args=(session, turn_id, user_content),
            name=f"turn-{turn_id}",
            daemon=True,
        )
        thread.start()

    def fail(self, session: Session, code: ErrorCode, message: str, retryable: bool) -> None:
        try:
            with session.condition:
                if self._store.get(session.session_id) is not session:
                    return
                turn_id = session.active_turn_id or ""
                session.append_event(
                    "error",
                    {
                        "turn_id": turn_id,
                        "code": code.value,
                        "message": message,
                        "retryable": retryable,
                    },
                )
        except (SessionClosed, ValueError):
            return

    def _alive(self, session: Session) -> None:
        if self._store.get(session.session_id) is not session:
            raise SessionClosed(session.session_id)

    # ---- 主流程 -----------------------------------------------------------
    def run(self, session: Session, turn_id: str, user_content: str) -> None:
        runtime_started = False
        try:
            with session.condition:
                self._alive(session)
                session.status = "in_progress"
                session.active_turn_id = turn_id
                session.append_event(
                    "turn.started", {"turn_id": turn_id, "session_id": session.session_id}
                )
                session.append_event("status", {"turn_id": turn_id, "state": "thinking"})

            runtime = self._active_runtime(session)
            if runtime is None:
                messages = build_chat_messages(session, user_content)
                reply: ChatReply = self._adapter.complete(messages, TOOLS, "auto")
                if reply.tool_calls:
                    content, experiment_refs = self._run_tool_phase(
                        session, turn_id, messages, reply
                    )
                else:
                    content, experiment_refs = reply.content, None
            else:
                runtime_started = True
                content, experiment_refs = self._run_runtime_turn(
                    session, turn_id, user_content, runtime
                )

            self._finish(session, turn_id, user_content, content, experiment_refs)
        except ChatError as exc:
            self.fail(session, exc.code, exc.message, exc.retryable)
        except AgentRuntimeError as exc:
            code = (
                ErrorCode.SKILL_INTEGRITY_MISMATCH
                if exc.code == "skill_integrity_mismatch"
                else ErrorCode.INTERNAL
            )
            self.fail(session, code, f"技能运行时失败：{exc.code}", False)
        except SessionClosed:
            return
        except Exception:  # noqa: BLE001 - 任何内部异常都收敛成 error 事件
            self.fail(session, ErrorCode.INTERNAL, "内部错误", False)
        finally:
            if runtime_started and self._runtime is not None:
                self._runtime.clear_turn()
            with session.condition:
                session.status = "idle"
                session.active_turn_id = None
                session.condition.notify_all()

    def _active_runtime(self, session: Session) -> Any | None:
        runtime = self._runtime
        if runtime is None:
            return None
        enabled_for = getattr(runtime, "enabled_for", None)
        if callable(enabled_for):
            return runtime if enabled_for(session.session_id) else None
        return runtime if getattr(runtime, "enabled", False) else None

    # ---- 旧路径（保持不变） ----------------------------------------------
    def _run_tool_phase(
        self,
        session: Session,
        turn_id: str,
        messages: list[dict[str, Any]],
        reply: ChatReply,
    ) -> tuple[str, list[str] | None]:
        if len(reply.tool_calls) > 1:
            raise ChatError(
                ErrorCode.TOO_MANY_TOOL_CALLS, "上游返回了多个工具调用，已拒绝执行", False
            )
        call = reply.tool_calls[0]
        with session.condition:
            self._alive(session)
            session.append_event(
                "tool.call",
                {
                    "turn_id": turn_id,
                    "call_id": call.call_id,
                    "tool": call.name,
                    "arguments": _parsed_arguments(call),
                },
            )
            session.append_event("status", {"turn_id": turn_id, "state": "tool"})

        outcome = execute_tool(call.name, call.arguments, self._index)

        with session.condition:
            self._alive(session)
            session.append_event(
                "tool.result",
                {"turn_id": turn_id, **outcome.event_payload(call.call_id, call.name)},
            )

        experiment_refs: list[str] | None = None
        if outcome.status == "ok" and isinstance(outcome.result, dict):
            experiment_id = outcome.result.get("experiment_id")
            if isinstance(experiment_id, str) and find_experiment(self._index, experiment_id):
                experiment_refs = [experiment_id]

        followup = _tool_followup_messages(messages, call, outcome)
        second = self._adapter.complete(followup, TOOLS, "none")
        if second.tool_calls:
            raise ChatError(
                ErrorCode.TOO_MANY_TOOL_CALLS, "上游返回了多个工具调用，已拒绝执行", False
            )
        return second.content, experiment_refs

    # ---- runtime 路径 -----------------------------------------------------
    def _run_runtime_turn(
        self,
        session: Session,
        turn_id: str,
        user_content: str,
        runtime: Any,
    ) -> tuple[str, list[str] | None]:
        turn = runtime.begin_turn(user_content)
        skill = turn.skill
        system_prompt = SYSTEM_PROMPT
        if skill is not None:
            with session.condition:
                self._alive(session)
                session.append_event("skill.loaded", skill.event_payload(turn_id))
            system_prompt = f"{SYSTEM_PROMPT}\n\n{skill.system_block}"

        messages = build_chat_messages(session, user_content, system_prompt=system_prompt)
        mcp = turn.mcp
        if not mcp.available:
            if mcp.reason == "not_configured":
                with session.condition:
                    self._alive(session)
                    session.append_event(
                        "runtime.warning",
                        {
                            "turn_id": turn_id,
                            "code": "mcp_not_configured",
                            "message": "MCP 未配置；本次只加载技能上下文，不暴露 MCP 工具",
                        },
                    )
            else:
                raise ChatError(
                    ErrorCode.MCP_UNAVAILABLE,
                    f"MCP 不可用：{mcp.reason or 'unknown'}",
                    False,
                )
        specs = (
            build_tool_specs(runtime.tool_specs(turn))
            if mcp.available
            else build_tool_specs([])
        )
        deadline = time.monotonic() + RUNTIME_WALL_SECONDS
        return self._run_runtime_tool_loop(
            session, turn_id, messages, specs, runtime, turn, deadline
        )

    def _check_wall(self, deadline: float) -> None:
        if time.monotonic() > deadline:
            raise ChatError(
                ErrorCode.TOOL_BUDGET_EXCEEDED,
                "本次 turn 的工具墙钟预算（60s）已耗尽",
                False,
            )

    def _tool_call_payload(
        self, turn_id: str, call: ToolCall, runtime: Any, turn: Any
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "turn_id": turn_id,
            "call_id": call.call_id,
            "tool": call.name,
            "arguments": _parsed_arguments(call),
            "binding_revision": turn.revision,
        }
        descriptor = runtime.describe_tool(call.name) if runtime is not None else {}
        if descriptor:
            payload.update(descriptor)
        return payload

    def _tool_result_payload(
        self, turn_id: str, call: ToolCall, outcome: ToolOutcome, runtime: Any, turn: Any
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "turn_id": turn_id,
            **outcome.event_payload(call.call_id, call.name),
            "binding_revision": turn.revision,
        }
        descriptor = runtime.describe_tool(call.name) if runtime is not None else {}
        if descriptor:
            payload.update(descriptor)
        return payload

    def _run_runtime_tool_loop(
        self,
        session: Session,
        turn_id: str,
        messages: list[dict[str, Any]],
        specs: list[dict[str, Any]],
        runtime: Any,
        turn: Any,
        deadline: float,
    ) -> tuple[str, list[str] | None]:
        tool_calls_used = 0
        output_bytes = 0
        status_tool_sent = False
        experiment_refs: list[str] = []

        for _round in range(MAX_RUNTIME_ROUNDS):
            self._check_wall(deadline)
            reply: ChatReply = self._adapter.complete(messages, specs, "auto")
            self._check_wall(deadline)
            if not reply.tool_calls:
                return reply.content, experiment_refs or None
            if len(reply.tool_calls) > MAX_RUNTIME_TOOL_CALLS_PER_ROUND:
                raise ChatError(
                    ErrorCode.TOO_MANY_TOOL_CALLS,
                    f"单轮最多 {MAX_RUNTIME_TOOL_CALLS_PER_ROUND} 个工具调用，已拒绝执行",
                    False,
                )
            if tool_calls_used + len(reply.tool_calls) > MAX_RUNTIME_TOOL_CALLS:
                raise ChatError(
                    ErrorCode.TOOL_BUDGET_EXCEEDED,
                    f"本次 turn 的工具调用次数预算（{MAX_RUNTIME_TOOL_CALLS}）已耗尽",
                    False,
                )

            assistant = {
                "role": "assistant",
                "content": reply.content,
                "tool_calls": [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                    for call in reply.tool_calls
                ],
            }
            messages = [*messages, assistant]

            if not status_tool_sent:
                with session.condition:
                    self._alive(session)
                    session.append_event("status", {"turn_id": turn_id, "state": "tool"})
                status_tool_sent = True

            for call in reply.tool_calls:
                self._check_wall(deadline)
                with session.condition:
                    self._alive(session)
                    session.append_event(
                        "tool.call", self._tool_call_payload(turn_id, call, runtime, turn)
                    )

                try:
                    outcome = execute_tool_dispatch(
                        call.name, call.arguments, self._index, runtime, turn
                    )
                except AgentRuntimeError as exc:
                    if exc.code == "tool_budget_exceeded":
                        raise ChatError(
                            ErrorCode.TOOL_BUDGET_EXCEEDED,
                            "本次 turn 的工具输出预算（64 KiB）已耗尽",
                            False,
                        ) from None
                    outcome = ToolOutcome(status="error", reason=exc.code)

                with session.condition:
                    self._alive(session)
                    session.append_event(
                        "tool.result",
                        self._tool_result_payload(turn_id, call, outcome, runtime, turn),
                    )

                if outcome.status == "ok" and isinstance(outcome.result, dict):
                    experiment_id = outcome.result.get("experiment_id")
                    if (
                        isinstance(experiment_id, str)
                        and experiment_id not in experiment_refs
                        and find_experiment(self._index, experiment_id)
                    ):
                        experiment_refs.append(experiment_id)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "content": outcome.tool_message_content(),
                    }
                )
                output_bytes += len(outcome.tool_message_content().encode("utf-8"))
                if output_bytes > RUNTIME_OUTPUT_BYTES:
                    raise ChatError(
                        ErrorCode.TOOL_BUDGET_EXCEEDED,
                        "本次 turn 的工具输出预算（64 KiB）已耗尽",
                        False,
                    )
                tool_calls_used += 1

        raise ChatError(
            ErrorCode.TOOL_BUDGET_EXCEEDED,
            f"本次 turn 的轮数预算（{MAX_RUNTIME_ROUNDS}）已耗尽",
            False,
        )

    # ---- 收尾 -------------------------------------------------------------
    def _finish(
        self,
        session: Session,
        turn_id: str,
        user_content: str,
        content: str,
        experiment_refs: list[str] | None,
    ) -> None:
        message_id = new_id("msg")
        with session.condition:
            self._alive(session)
            session.append_event("status", {"turn_id": turn_id, "state": "responding"})
        for chunk in chunk_text(content):
            with session.condition:
                self._alive(session)
                session.append_event(
                    "message.delta", {"turn_id": turn_id, "message_id": message_id, "text": chunk}
                )
        now = utcnow()
        with session.condition:
            self._alive(session)
            session.append_event(
                "message.completed",
                {
                    "turn_id": turn_id,
                    "message_id": message_id,
                    "role": "assistant",
                    "content": content,
                },
            )
            session.append_message(
                Message(
                    message_id=new_id("msg"),
                    role="user",
                    content=user_content,
                    created_at=now,
                    turn_id=turn_id,
                )
            )
            session.append_message(
                Message(
                    message_id=message_id,
                    role="assistant",
                    content=content,
                    created_at=now,
                    turn_id=turn_id,
                    experiment_refs=experiment_refs,
                )
            )
            session.successful_turns += 1
            session.append_event(
                "turn.completed",
                {
                    "turn_id": turn_id,
                    "session_id": session.session_id,
                    "turn_count": session.successful_turns,
                },
            )
            session.append_event("done", {})
