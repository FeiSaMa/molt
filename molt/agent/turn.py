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
"""

from __future__ import annotations

import json
import threading
from typing import Any

from molt.agent.chat import SYSTEM_PROMPT, ChatError, ChatReply, ToolCall
from molt.agent.errors import ErrorCode
from molt.agent.probe import find_experiment
from molt.agent.store import Message, Session, SessionClosed, SessionStore, new_id, utcnow
from molt.agent.tools import TOOLS, ToolOutcome, execute_tool

CHUNK_SIZE = 40


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
    def __init__(self, store: SessionStore, index: dict[str, Any], adapter: Any) -> None:
        self._store = store
        self._index = index
        self._adapter = adapter

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
        try:
            with session.condition:
                self._alive(session)
                session.status = "in_progress"
                session.active_turn_id = turn_id
                session.append_event(
                    "turn.started", {"turn_id": turn_id, "session_id": session.session_id}
                )
                session.append_event("status", {"turn_id": turn_id, "state": "thinking"})

            messages = build_chat_messages(session, user_content)
            reply: ChatReply = self._adapter.complete(messages, TOOLS, "auto")
            if reply.tool_calls:
                content, experiment_refs = self._run_tool_phase(
                    session, turn_id, messages, reply
                )
            else:
                content, experiment_refs = reply.content, None

            self._finish(session, turn_id, user_content, content, experiment_refs)
        except ChatError as exc:
            self.fail(session, exc.code, exc.message, exc.retryable)
        except SessionClosed:
            return
        except Exception:  # noqa: BLE001 - 任何内部异常都收敛成 error 事件
            self.fail(session, ErrorCode.INTERNAL, "内部错误", False)
        finally:
            with session.condition:
                session.status = "idle"
                session.active_turn_id = None
                session.condition.notify_all()

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
