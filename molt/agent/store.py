"""进程内 session / message / 事件缓冲。

语义（与 ``docs/api/probe-agent-api.md`` 对齐）：

* 一个 session 一条事件流，事件 id 从 1 递增；缓冲上限 4096，淘汰最旧。
* 每个 session 一把 ``threading.Condition``；SSE 订阅者各自游标，互不影响。
* ``since`` 缺省 / 0 / null → 从最旧保留开始；正数排他；``since < 最旧保留`` 由
  调用方转成 410 ``stale_event_id``（这里只负责给出最旧 id）。
* 历史只保存成功 turn 的 user/assistant 对；失败 turn 不落库。
"""

from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

MAX_EVENTS = 4096
MAX_MESSAGES = 200

TERMINAL_EVENTS = frozenset({"done", "error"})


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


@dataclass
class Message:
    message_id: str
    role: str
    content: str
    created_at: str
    turn_id: str
    experiment_refs: list[str] | None = None

    def public(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
            "turn_id": self.turn_id,
        }
        if self.experiment_refs:
            payload["experiment_refs"] = list(self.experiment_refs)
        return payload


@dataclass
class Event:
    event_id: int
    event: str
    data: dict[str, Any]


@dataclass
class Session:
    session_id: str
    provider: str
    created_at: str
    status: str = "idle"
    messages: list[Message] = field(default_factory=list)
    last_event_id: int = 0
    successful_turns: int = 0
    events: deque[Event] = field(default_factory=lambda: deque(maxlen=MAX_EVENTS))
    condition: threading.Condition = field(default_factory=threading.Condition)
    closed: bool = False
    active_turn_id: str | None = None

    # ---- 读取 -------------------------------------------------------------
    def public(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "provider": self.provider,
            "created_at": self.created_at,
            "messages": [message.public() for message in self.messages],
            "last_event_id": self.last_event_id,
        }

    def summary(self) -> dict[str, Any]:
        """列表用摘要：固定 6 键，不含 messages。"""

        return {
            "session_id": self.session_id,
            "status": self.status,
            "provider": self.provider,
            "created_at": self.created_at,
            "last_event_id": self.last_event_id,
            "message_count": len(self.messages),
        }

    def history_messages(self) -> list[dict[str, str]]:
        """2n 条成功历史（user/assistant 交替）。"""

        return [{"role": message.role, "content": message.content} for message in self.messages]

    def min_event_id(self) -> int | None:
        return self.events[0].event_id if self.events else None

    # ---- 写入（调用方持锁） ------------------------------------------------
    def append_event(self, event: str, data: dict[str, Any]) -> Event:
        if event != "done" and "turn_id" not in data:
            raise ValueError(f"事件 {event} 的 data 必须含 turn_id")
        if self.closed:
            raise SessionClosed(self.session_id)
        self.last_event_id += 1
        item = Event(event_id=self.last_event_id, event=event, data=dict(data))
        self.events.append(item)
        self.condition.notify_all()
        return item

    def append_message(self, message: Message) -> None:
        self.messages.append(message)

    # ---- SSE 读取（调用方持锁） -------------------------------------------
    def events_since(self, since: int | None) -> list[Event]:
        if since is None or since == 0:
            return list(self.events)
        return [item for item in self.events if item.event_id > since]

    def wait_for_events(self, cursor: int, timeout: float) -> bool:
        """等待游标之后的新事件；返回是否被唤醒（False = 超时或关闭）。"""

        if self.closed or self.last_event_id > cursor:
            return True
        self.condition.wait(timeout)
        return self.closed or self.last_event_id > cursor

    def is_terminal(self, since: int | None) -> bool:
        """按 since 语义判断「回放结束后是否应立即关闭」。"""

        items = self.events_since(since)
        return bool(items) and items[-1].event in TERMINAL_EVENTS


class SessionClosed(Exception):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"session {session_id} closed")
        self.session_id = session_id


class SessionStore:
    """session 字典；进程重启即丢失（MVP 明确不做持久化）。"""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}

    def create(self) -> Session:
        session = Session(
            session_id=new_id("sess"),
            provider=self.provider,
            created_at=utcnow(),
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def list(self) -> list[Session]:
        with self._lock:
            return list(self._sessions.values())

    def delete(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        with session.condition:
            session.closed = True
            session.condition.notify_all()
        return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
