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
5. 工具循环持续到 provider 返回最终文本；不设工具调用次数/输出预算；
   单次 provider 请求与单个 MCP 请求仍受各自协议超时约束
6. MCP profile 校验/启动失败 → ``error`` code=``mcp_unavailable``（allowlist
   完全未配置时只发 ``runtime.warning``，显式降级为技能上下文）
7. MCP 失败映射 ``tool.result`` 的 ``status=error`` + ``reason``，不拼进 system prompt

旧路径（``runtime is None`` 或 mode=off）同样支持一轮多个工具调用：每个
``tool.call`` 都先经过工具名/allowlist 检查，再逐项发 ``tool.result``，所有结果按
``tool_call_id`` 回传 provider。后续轮仍请求 ``tool_choice="none"``，但 provider 若
返回工具调用也不会被拒绝，而是继续逐项执行。**一次 turn 内 binding 不再读取**：
中途换绑定只影响下一次 turn。
"""

from __future__ import annotations

import hashlib
import inspect
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


class TurnCancelled(Exception):
    """Cooperative cancellation requested by the client."""


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
        evolution: Any | None = None,
    ) -> None:
        self._store = store
        self._index = index
        self._adapter = adapter
        self._runtime = runtime
        self._evolution = evolution
        self._local = threading.local()

    # ---- 生命周期 ---------------------------------------------------------
    def start(
        self, session: Session, turn_id: str, user_content: str, mode: str = "general"
    ) -> None:
        thread = threading.Thread(
            target=self.run,
            args=(session, turn_id, user_content, mode),
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

    def cancel(self, session: Session, turn_id: str) -> None:
        try:
            with session.condition:
                if self._store.get(session.session_id) is not session:
                    return
                session.append_event(
                    "turn.cancelled",
                    {
                        "turn_id": turn_id,
                        "note": "已取消；本轮不写入 user/assistant 历史",
                        "usage": self._usage_snapshot(),
                    },
                )
                session.append_event("done", {})
        except (SessionClosed, ValueError):
            return

    def _alive(self, session: Session) -> None:
        if self._store.get(session.session_id) is not session:
            raise SessionClosed(session.session_id)

    @staticmethod
    def _cancelled(session: Session, turn_id: str) -> bool:
        with session.condition:
            return session.cancel_requested_turn == turn_id

    def _check_cancel(self, session: Session, turn_id: str) -> None:
        if self._cancelled(session, turn_id):
            raise TurnCancelled

    def _complete(
        self,
        session: Session,
        turn_id: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str,
    ) -> ChatReply:
        self._check_cancel(session, turn_id)
        reply = self._adapter.complete(messages, tools, tool_choice)
        self._local.model_calls = int(getattr(self._local, "model_calls", 0)) + 1
        usage = getattr(reply, "usage", None)
        if isinstance(usage, dict):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage.get(key)
                if type(value) is int and value >= 0:
                    setattr(self._local, key, int(getattr(self._local, key, 0)) + value)
        self._check_cancel(session, turn_id)
        return reply

    def _usage_snapshot(self) -> dict[str, int]:
        snapshot = getattr(self._adapter, "usage_snapshot", None)
        if callable(snapshot):
            try:
                payload = snapshot()
            except Exception:  # noqa: BLE001
                payload = None
            if isinstance(payload, dict):
                result = {
                    key: int(payload.get(key, 0))
                    for key in (
                        "model_calls",
                        "prompt_tokens",
                        "completion_tokens",
                        "total_tokens",
                    )
                    if type(payload.get(key)) is int
                }
                result.setdefault("model_calls", 0)
                result["tool_calls"] = int(getattr(self._local, "tool_calls", 0))
                return result
        return {
            "model_calls": int(getattr(self._local, "model_calls", 0)),
            "tool_calls": int(getattr(self._local, "tool_calls", 0)),
            "prompt_tokens": int(getattr(self._local, "prompt_tokens", 0)),
            "completion_tokens": int(getattr(self._local, "completion_tokens", 0)),
            "total_tokens": int(getattr(self._local, "total_tokens", 0)),
        }

    # ---- 主流程 -----------------------------------------------------------
    def run(
        self, session: Session, turn_id: str, user_content: str, mode: str = "general"
    ) -> None:
        runtime_started = False
        self._local.snapshot = None
        self._local.mode = mode
        self._local.turn = None
        self._local.tool_calls = 0
        self._local.model_calls = 0
        self._local.prompt_tokens = 0
        self._local.completion_tokens = 0
        self._local.total_tokens = 0
        reset_usage = getattr(self._adapter, "reset_usage", None)
        if callable(reset_usage):
            try:
                reset_usage()
            except Exception:  # noqa: BLE001
                pass
        context = None
        plan = {"mode": "unverified", "reason": "not_started"}
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
            if self._evolution is not None:
                context = self._evolution.start(
                    session, turn_id, user_content, reuse_assets=mode == "general"
                )
                self._local.snapshot = context["working"]
                self._check_cancel(session, turn_id)
                if mode == "general":
                    self._evolution.emit(
                        session,
                        turn_id,
                        "evolution.started",
                        {
                            "phase": "acceptance",
                            "note": "先冻结验收；候选未提交前旧快照仍生效",
                        },
                    )
                    plan = self._evolution.plan(user_content)
                    self._check_cancel(session, turn_id)
                    self._evolution.emit(
                        session,
                        turn_id,
                        "evolution.plan",
                        {
                            "mode": plan.get("mode"),
                            "verifier_hash": plan.get("verifier_hash"),
                            "reason": plan.get("reason"),
                            "n": 1,
                        },
                    )

            if runtime is None:
                messages = build_chat_messages(session, user_content, system_prompt=self._system_prompt())
                reply: ChatReply = self._complete(session, turn_id, messages, TOOLS, "auto")
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

            baseline = content
            candidate = None
            candidate_answer = None
            candidate_refs = None
            decision = None
            if context is not None and mode == "general":
                if (
                    plan.get("mode") != "unverified"
                    and getattr(self._adapter, "real_model", False)
                ):
                    self._evolution.emit(
                        session,
                        turn_id,
                        "evolution.round1",
                        {
                            "content_hash": hashlib.sha256(baseline.encode("utf-8")).hexdigest(),
                            "note": "round1 使用当前会话快照；候选尚未生效",
                        },
                    )
                    try:
                        turn = getattr(self._local, "turn", None)
                        available = list(turn.allowed_tools) if turn is not None else []
                        candidate = self._evolution.propose(
                            context,
                            baseline,
                            available,
                            session=session,
                            experiment_refs=experiment_refs,
                        )
                        self._check_cancel(session, turn_id)
                        self._evolution.emit(
                            session,
                            turn_id,
                            "evolution.candidate",
                            {
                                "title": candidate.get("title"),
                                "triggers": candidate.get("triggers"),
                                "skill": (
                                    {
                                        "asset_id": candidate["skill"]["asset_id"],
                                        "skill_id": candidate["skill"]["skill_id"],
                                        "version": candidate["skill"]["version"],
                                        "package_hash": candidate["skill"]["package_hash"],
                                        "authorship": candidate["skill"].get("authorship"),
                                        "calls_model": candidate["skill"].get("calls_model"),
                                        "baseline_version": candidate["skill"].get("baseline_version"),
                                    }
                                    if candidate.get("skill")
                                    else None
                                ),
                                "mcp": (
                                    {
                                        "tools": candidate["mcp"]["tools"],
                                        "recipes": candidate["mcp"]["recipes"],
                                    }
                                    if candidate.get("mcp")
                                    else None
                                ),
                                "memory": candidate.get("memory"),
                                "bottom_evidence": {
                                    "probe": len((context.get("bottom_evidence") or {}).get("probe_experiments", [])),
                                    "probe_runs": len((context.get("bottom_evidence") or {}).get("probe_runs", [])),
                                    "lab": len((context.get("bottom_evidence") or {}).get("lab_runs", [])),
                                },
                                "diff": candidate.get("diff"),
                                "scope": "candidate; not active",
                            },
                        )
                        candidate_answer, candidate_refs = self._run_candidate_turn(
                            session, turn_id, user_content, runtime, candidate
                        )
                        self._check_cancel(session, turn_id)
                        self._evolution.emit(
                            session,
                            turn_id,
                            "evolution.round2",
                            {
                                "content_hash": hashlib.sha256(
                                    candidate_answer.encode("utf-8")
                                ).hexdigest(),
                                "mode": plan.get("mode"),
                            },
                        )
                        decision = self._evolution.decide(
                            plan,
                            baseline,
                            candidate_answer,
                            domain=plan.get("mode", "general"),
                        )
                        self._check_cancel(session, turn_id)
                        self._evolution.emit(
                            session,
                            turn_id,
                            "evolution.evaluation",
                            {
                                "adopt_suggested": bool(decision.get("adopt")),
                                "decision": "adopt_suggested"
                                if decision.get("adopt")
                                else "retain",
                                "verifier": decision.get("verifier"),
                                "n": decision.get("n", 1),
                                "reason": decision.get("reason"),
                                "limitation": decision.get("limitation"),
                                "note": "尚未提交；Skill 还需固定 transfer/regression 证据",
                            },
                        )
                    except Exception:  # noqa: BLE001 - candidate failure retains the old snapshot
                        candidate = None
                        candidate_answer = None
                        candidate_refs = None
                        decision = {
                            "adopt": False,
                            "verifier": "none",
                            "n": 1,
                            "reason": "candidate_or_verifier_failed; previous snapshot retained",
                            "limitation": "候选未形成或验证失败，不产生采纳依据",
                        }
                        self._evolution.emit(
                            session,
                            turn_id,
                            "evolution.evaluation",
                            {
                                "adopt_suggested": False,
                                "decision": "retain",
                                **decision,
                                "note": "候选失败；旧会话快照保留",
                            },
                        )
                else:
                    decision = {
                        "adopt": False,
                        "verifier": "none",
                        "n": 1,
                        "reason": "acceptance_unavailable" if plan.get("mode") == "unverified" else "mock_provider",
                        "limitation": "没有冻结验收或没有真实 provider，本轮不采纳候选",
                    }
                    self._evolution.emit(
                        session, turn_id, "evolution.evaluation", decision
                    )

            if context is not None:
                self._check_cancel(session, turn_id)
                context["usage"] = self._usage_snapshot()
                content, _state, outcome = self._evolution.finish(
                    session,
                    context,
                    baseline,
                    candidate,
                    candidate_answer,
                    decision or {"adopt": False, "n": 1, "reason": "not_evaluated"},
                    plan,
                    candidate_refs if decision and decision.get("adopt") else experiment_refs,
                    mode=mode,
                )
                if outcome == "adopt" and candidate_refs is not None:
                    experiment_refs = candidate_refs
            self._finish(session, turn_id, user_content, content, experiment_refs)
        except TurnCancelled:
            self.cancel(session, turn_id)
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
            self._local.snapshot = None
            self._local.mode = None
            self._local.turn = None
            if runtime_started and self._runtime is not None:
                self._runtime.clear_turn()
            with session.condition:
                if session.cancel_requested_turn == turn_id:
                    session.cancel_requested_turn = None
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

    # ---- 工具循环 ---------------------------------------------------------
    def _system_prompt(self) -> str:
        snapshot = getattr(self._local, "snapshot", None)
        if snapshot is None or self._evolution is None:
            return SYSTEM_PROMPT
        return snapshot.get("system_prompt", SYSTEM_PROMPT) + self._evolution.system_block(snapshot)

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
        *,
        system_prompt: str | None = None,
        strategy_tools: list[str] | tuple[str, ...] | None = None,
        emit_skill: bool = True,
    ) -> tuple[str, list[str] | None]:
        snapshot = getattr(self._local, "snapshot", None)
        parameters = inspect.signature(runtime.begin_turn).parameters
        if "binding_snapshot" in parameters:
            turn = runtime.begin_turn(
                user_content,
                binding_snapshot=snapshot.get("binding") if snapshot is not None else None,
                strategy_tools=strategy_tools,
            )
        else:  # compatibility with small test doubles
            turn = runtime.begin_turn(user_content)
        self._local.turn = turn
        skill = turn.skill
        prompt = system_prompt or self._system_prompt()
        if skill is not None and emit_skill:
            with session.condition:
                self._alive(session)
                session.append_event("skill.loaded", skill.event_payload(turn_id))
            prompt = f"{prompt}\n\n{skill.system_block}"

        messages = build_chat_messages(session, user_content, system_prompt=prompt)
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
        if not any(name.startswith("mcp__") for name in turn.allowed_tools):
            reply = self._complete(session, turn_id, messages, TOOLS, "auto")
            if reply.tool_calls:
                content, experiment_refs = self._run_tool_phase(
                    session, turn_id, messages, reply
                )
            else:
                content, experiment_refs = reply.content, None
            return content, experiment_refs
        specs = (
            build_tool_specs(runtime.tool_specs(turn))
            if mcp.available
            else build_tool_specs([])
        )
        return self._run_runtime_tool_loop(
            session, turn_id, messages, specs, runtime, turn
        )

    def _run_candidate_turn(
        self,
        session: Session,
        turn_id: str,
        user_content: str,
        runtime: Any | None,
        candidate: dict[str, Any],
    ) -> tuple[str, list[str] | None]:
        """Round 2: same tool surface, candidate strategy only in the prompt."""

        block = self._evolution.candidate_block(candidate) if self._evolution else ""
        snapshot = getattr(self._local, "snapshot", None)
        base = (
            snapshot.get("system_prompt", SYSTEM_PROMPT)
            if snapshot is not None
            else SYSTEM_PROMPT
        )
        prompt = base + block
        strategy_tools = None
        mcp = candidate.get("mcp")
        if isinstance(mcp, dict) and isinstance(mcp.get("tools"), list) and mcp["tools"]:
            strategy_tools = [item for item in mcp["tools"] if isinstance(item, str)]
            # The candidate may select a subset; it can never add a tool.
            available = set(getattr(self._local, "turn", None).allowed_tools or []) if getattr(self._local, "turn", None) else set()
            if available:
                strategy_tools = [item for item in strategy_tools if item in available]
                if not strategy_tools:
                    strategy_tools = None
        if runtime is not None:
            return self._run_runtime_turn(
                session,
                turn_id,
                user_content,
                runtime,
                system_prompt=prompt,
                strategy_tools=strategy_tools,
                emit_skill=False,
            )
        messages = build_chat_messages(session, user_content, system_prompt=prompt)
        reply = self._adapter.complete(messages, TOOLS, "auto")
        if reply.tool_calls:
            return self._run_tool_phase(session, turn_id, messages, reply)
        return reply.content, None

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
    ) -> tuple[str, list[str] | None]:
        status_tool_sent = False
        experiment_refs: list[str] = []
        tool_calls_used = 0
        output_bytes = 0
        rounds = 0

        while True:
            rounds += 1
            if rounds > MAX_RUNTIME_ROUNDS:
                raise ChatError(
                    ErrorCode.TOOL_BUDGET_EXCEEDED,
                    f"本次 turn 的轮数预算（{MAX_RUNTIME_ROUNDS}）已耗尽",
                    False,
                )
            reply: ChatReply = self._complete(session, turn_id, messages, specs, "auto")
            if not reply.tool_calls:
                return reply.content, experiment_refs or None
            calls = reply.tool_calls
            if len(calls) > MAX_RUNTIME_TOOL_CALLS_PER_ROUND:
                raise ChatError(
                    ErrorCode.TOO_MANY_TOOL_CALLS,
                    f"单轮最多 {MAX_RUNTIME_TOOL_CALLS_PER_ROUND} 个工具调用，已拒绝执行",
                    False,
                )
            if tool_calls_used + len(calls) > MAX_RUNTIME_TOOL_CALLS:
                raise ChatError(
                    ErrorCode.TOOL_BUDGET_EXCEEDED,
                    f"本次 turn 的工具调用次数预算（{MAX_RUNTIME_TOOL_CALLS}）已耗尽",
                    False,
                )

            assistant: dict[str, Any] = {
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
            if getattr(reply, "reasoning_content", ""):
                assistant["reasoning_content"] = reply.reasoning_content
            messages = [*messages, assistant]

            if not status_tool_sent:
                with session.condition:
                    self._alive(session)
                    session.append_event("status", {"turn_id": turn_id, "state": "tool"})
                status_tool_sent = True

            for call in calls:
                self._check_cancel(session, turn_id)
                with session.condition:
                    self._alive(session)
                    session.append_event(
                        "tool.call", self._tool_call_payload(turn_id, call, runtime, turn)
                    )

                self._local.tool_calls = int(getattr(self._local, "tool_calls", 0)) + 1
                try:
                    outcome = execute_tool_dispatch(
                        call.name, call.arguments, self._index, runtime, turn
                    )
                except AgentRuntimeError as exc:
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

                self._check_cancel(session, turn_id)
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

    # ---- 收尾 -------------------------------------------------------------
    def _finish(
        self,
        session: Session,
        turn_id: str,
        user_content: str,
        content: str,
        experiment_refs: list[str] | None,
    ) -> None:
        self._check_cancel(session, turn_id)
        message_id = new_id("msg")
        usage = self._usage_snapshot()
        with session.condition:
            self._alive(session)
            session.append_event("status", {"turn_id": turn_id, "state": "responding"})
        for chunk in chunk_text(content):
            self._check_cancel(session, turn_id)
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
                    usage=usage,
                )
            )
            session.successful_turns += 1
            session.append_event(
                "turn.completed",
                {
                    "turn_id": turn_id,
                    "session_id": session.session_id,
                    "turn_count": session.successful_turns,
                    "usage": usage,
                },
            )
            session.append_event("done", {})
