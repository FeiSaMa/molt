"""Two-round coding lab, strictly gated on real isolation.

This is deliberately **not** the engine's genome evolution. It is a small,
auditable coding loop used by the chat page:

    seed fixture -> round1 tests -> proposed patch -> real diff -> round2 tests

The mock provider contributes a deterministic patch and the run is labelled
``source=mock-deterministic`` / ``real_model=false``. A real provider is used
only when the adapter reports ``real_model=true``; provider failures do not
fall back to mock.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from molt.agent.lab_tasks import LabTask, get_task, public_tasks
from molt.agent.store import Event, new_id, utcnow
from molt.agent.workspace import (
    MAX_OUTPUT_BYTES,
    SandboxStatus,
    Workspace,
    WorkspaceError,
    probe_sandbox,
)

MAX_RUNS = 20
MAX_WALL_SECONDS = 60.0
MAX_ROUNDS = 2
MAX_EVENTS = 4096
MAX_PROVIDER_CHARS = 64 * 1024


class LabError(RuntimeError):
    def __init__(self, code: str, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class LabUnavailable(LabError):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__("isolation_unavailable", message, detail=detail)


class LabBusy(LabError):
    def __init__(self) -> None:
        super().__init__("lab_busy", "已有执行任务在进行")


class LabTaskNotFound(LabError):
    def __init__(self, task_id: str) -> None:
        super().__init__("not_found", f"未知任务: {task_id}")


class LabCancelled(RuntimeError):
    pass


@dataclass
class LabRun:
    run_id: str
    task_id: str
    session_id: str | None
    provider: str
    model: str
    real_model: bool
    created_at: str
    status: str = "queued"
    last_event_id: int = 0
    events: deque[Event] = field(default_factory=lambda: deque(maxlen=MAX_EVENTS))
    condition: threading.Condition = field(default_factory=threading.Condition)
    closed: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)
    workspace: Workspace | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    def append_event(self, event: str, data: dict[str, Any]) -> Event:
        if event != "done" and "run_id" not in data:
            raise ValueError(f"事件 {event} 的 data 必须含 run_id")
        if self.closed:
            raise LabError("cancelled", "run 已关闭")
        self.last_event_id += 1
        item = Event(event_id=self.last_event_id, event=event, data=dict(data))
        self.events.append(item)
        self.condition.notify_all()
        return item

    def public(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "real_model": self.real_model,
            "created_at": self.created_at,
            "last_event_id": self.last_event_id,
            "result": self.result,
            "error": self.error,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "status": self.status,
            "provider": self.provider,
            "real_model": self.real_model,
            "created_at": self.created_at,
            "last_event_id": self.last_event_id,
        }

    def min_event_id(self) -> int | None:
        return self.events[0].event_id if self.events else None

    def events_since(self, since: int | None) -> list[Event]:
        if since is None or since == 0:
            return list(self.events)
        return [item for item in self.events if item.event_id > since]

    def wait_for_events(self, cursor: int, timeout: float) -> bool:
        if self.closed or self.last_event_id > cursor:
            return True
        self.condition.wait(timeout)
        return self.closed or self.last_event_id > cursor


class LabManager:
    def __init__(
        self,
        adapter: Any,
        *,
        lab_root: Path | None = None,
        sandbox: SandboxStatus | None = None,
        max_wall: float = MAX_WALL_SECONDS,
        command_timeout: float = 15.0,
    ) -> None:
        self.adapter = adapter
        self.lab_root = (lab_root or Path("/tmp/molt-lab")).resolve()
        self.runs_root = self.lab_root / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.sandbox = sandbox or probe_sandbox()
        self.max_wall = max_wall
        self.command_timeout = command_timeout
        self._runs: dict[str, LabRun] = {}
        self._lock = threading.RLock()
        self._semaphore = threading.BoundedSemaphore(1)

    # ---- capability --------------------------------------------------------
    def capabilities(self) -> dict[str, Any]:
        execution = self.sandbox.public()
        execution["limits"] = {
            "concurrency": 1,
            "rounds": MAX_ROUNDS,
            "wall_ms": int(self.max_wall * 1000),
            "command_ms": int(self.command_timeout * 1000),
            "output_bytes": MAX_OUTPUT_BYTES,
        }
        provider = {
            "name": self.adapter.name,
            "model": self.adapter.model,
            "real_model": self.adapter.real_model,
            "ready": self.adapter.adapter_ready,
            "error": self.adapter.provider_error,
        }
        if self.adapter.real_model:
            mode = "live"
            evolution_reason = None if self.sandbox.available else "isolation_unavailable"
        else:
            mode = "mock"
            evolution_reason = None if self.sandbox.available else "isolation_unavailable"
        return {
            "execution": execution,
            "provider": provider,
            "evolution": {
                "available": self.sandbox.available,
                "mode": mode,
                "real_model": self.adapter.real_model,
                "reason": evolution_reason,
                "note": (
                    "与历史 demo/probe 数据分离的 coding fixture；不是引擎 genome evolution"
                ),
            },
        }

    def tasks(self) -> list[dict[str, Any]]:
        return public_tasks()

    # ---- lifecycle ---------------------------------------------------------
    def create(self, task_id: str, *, session_id: str | None = None) -> LabRun:
        task = get_task(task_id)
        if task is None:
            raise LabTaskNotFound(task_id)
        if not self.sandbox.available:
            raise LabUnavailable(
                "隔离不可用，执行端点已关闭",
                detail=self.sandbox.reason or "isolation_unavailable",
            )
        if not self._semaphore.acquire(blocking=False):
            raise LabBusy()

        run = LabRun(
            run_id=new_id("lab"),
            task_id=task.task_id,
            session_id=session_id,
            provider=self.adapter.name,
            model=self.adapter.model,
            real_model=bool(self.adapter.real_model),
            created_at=utcnow(),
        )
        with self._lock:
            self._runs[run.run_id] = run
            self._prune_locked()
        thread = threading.Thread(
            target=self._execute,
            args=(run, task),
            name=f"lab-{run.run_id}",
            daemon=True,
        )
        try:
            thread.start()
        except Exception:
            with self._lock:
                self._runs.pop(run.run_id, None)
            run.status = "failed"
            run.error = {"code": "internal", "message": "无法启动执行线程"}
            self._semaphore.release()
            raise
        return run

    def get(self, run_id: str) -> LabRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def runs_for_session(
        self, session_id: str | None, *, full: bool = False
    ) -> list[dict[str, Any]]:
        if not isinstance(session_id, str) or not session_id:
            return []
        with self._lock:
            runs = [
                (run.public() if full else run.summary())
                for run in self._runs.values()
                if run.session_id == session_id
            ]
        return sorted(runs, key=lambda item: item.get("created_at") or "")

    def _persist(self, run: LabRun) -> None:
        try:
            payload = run.public()
            payload["task_id"] = run.task_id
            path = self.runs_root / f"{run.run_id}.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            return

    def cancel(self, run_id: str) -> bool:
        run = self.get(run_id)
        if run is None:
            return False
        if run.status in {"completed", "failed", "cancelled"}:
            return True
        run.cancel_event.set()
        workspace = run.workspace
        if workspace is not None:
            workspace.terminate()
        return True

    def _prune_locked(self) -> None:
        if len(self._runs) <= MAX_RUNS:
            return
        terminal = [
            run
            for run in self._runs.values()
            if run.status in {"completed", "failed", "cancelled"}
        ]
        for run in sorted(terminal, key=lambda item: item.created_at)[: len(self._runs) - MAX_RUNS]:
            self._runs.pop(run.run_id, None)

    # ---- execution ---------------------------------------------------------
    def _execute(self, run: LabRun, task: LabTask) -> None:
        started = time.monotonic()
        workspace: Workspace | None = None
        try:
            run.status = "running"
            self._emit(
                run,
                "run.started",
                {
                    "run_id": run.run_id,
                    "task_id": task.task_id,
                    "provider": run.provider,
                    "model": run.model,
                    "real_model": run.real_model,
                    "provenance": {
                        "kind": "lab_fixture",
                        "historical": False,
                        "source": "molt/agent/lab_tasks.py",
                    },
                    "isolation": self.sandbox.public(),
                },
            )
            self._check_cancel(run)
            workspace = Workspace.create(self.lab_root, run.run_id)
            run.workspace = workspace
            for name, content in task.files.items():
                workspace.write(name, content)
            before = workspace.snapshot()

            round1 = workspace.run_tests(
                cancel_event=run.cancel_event, timeout=self.command_timeout
            )
            self._check_cancel(run)
            if round1.timed_out:
                raise LabError("command_timeout", "round1 超过单命令时间预算")
            self._emit(
                run,
                "round1.completed",
                {"run_id": run.run_id, "round": 1, **round1.public()},
            )
            self._check_wall(started)

            proposal = self._propose(run, task, before, round1)
            self._check_wall(started)
            self._emit(
                run,
                "evolution.proposed",
                {
                    "run_id": run.run_id,
                    "source": proposal["source"],
                    "real_model": proposal["real_model"],
                    "file": proposal["file"],
                    "note": proposal.get("note"),
                },
            )
            self._check_cancel(run)
            self._apply(workspace, task, proposal)
            after = workspace.snapshot()
            diff = Workspace.diff(before, after)
            self._emit(
                run,
                "evolution.diff",
                {
                    "run_id": run.run_id,
                    "diff": diff,
                    "files": sorted(after),
                },
            )
            self._check_cancel(run)
            self._check_wall(started)

            round2 = workspace.run_tests(
                cancel_event=run.cancel_event, timeout=self.command_timeout
            )
            self._check_cancel(run)
            if round2.timed_out:
                raise LabError("command_timeout", "round2 超过单命令时间预算")
            self._emit(
                run,
                "round2.completed",
                {"run_id": run.run_id, "round": 2, **round2.public()},
            )
            self._check_wall(started)

            result = {
                "status": "completed",
                "adopted": round2.passed,
                "round1": round1.public(),
                "round2": round2.public(),
                "diff": diff,
                "provenance": {
                    "kind": "lab_fixture",
                    "historical": False,
                    "source": "molt/agent/lab_tasks.py",
                    "patch_source": proposal["source"],
                    "real_model": proposal["real_model"],
                },
                "provider": {
                    "name": run.provider,
                    "model": run.model,
                    "real_model": run.real_model,
                },
                "isolation": self.sandbox.public(),
                "usage": {
                    "wall_ms": int((time.monotonic() - started) * 1000),
                    "test_commands": 2,
                },
            }
            run.result = result
            run.status = "completed"
            self._emit(run, "run.completed", {"run_id": run.run_id, **result})
            self._done(run)
        except LabCancelled:
            run.status = "cancelled"
            run.error = {"code": "cancelled", "message": "任务已取消"}
            self._emit(run, "run.cancelled", {"run_id": run.run_id, **run.error})
            self._done(run)
        except LabError as exc:
            run.status = "failed"
            run.error = {"code": exc.code, "message": str(exc)}
            if exc.detail:
                run.error["detail"] = exc.detail
            self._emit(run, "run.failed", {"run_id": run.run_id, **run.error})
            self._done(run)
        except WorkspaceError as exc:
            run.status = "failed"
            run.error = {"code": exc.code, "message": str(exc)}
            if exc.detail:
                run.error["detail"] = exc.detail
            self._emit(run, "run.failed", {"run_id": run.run_id, **run.error})
            self._done(run)
        except Exception as exc:  # noqa: BLE001 - 不泄漏内部细节
            run.status = "failed"
            run.error = {"code": "internal", "message": "执行内部错误"}
            self._emit(run, "run.failed", {"run_id": run.run_id, **run.error})
            self._done(run)
            del exc
        finally:
            if workspace is not None:
                workspace.cleanup()
            run.workspace = None
            self._persist(run)
            self._semaphore.release()

    def _propose(
        self,
        run: LabRun,
        task: LabTask,
        before: dict[str, str],
        round1: Any,
    ) -> dict[str, Any]:
        if not self.adapter.real_model:
            return {
                **task.expected_fix,
                "source": "mock-deterministic",
                "real_model": False,
                "note": "MOCK 演示：真实模型未配置，patch 来自确定性 fixture",
            }

        target = next(
            (name for name in before if name != task.test_file),
            next(iter(before), "solution.py"),
        )
        system = (
            "You are a coding agent in a sandbox. Return ONLY a JSON object. "
            f'Use either {{"file":"{target}","old":"...","new":"..."}} to replace an exact '
            f'substring, or {{"file":"{target}","content":"..."}} to replace the whole file. '
            "Only one file can be patched; do not edit test files. "
            "Make the smallest correct change."
        )
        user = (
            f"Task: {task.title}\n\nFiles:\n"
            + "\n".join(f"--- {name} ---\n{text}" for name, text in before.items())
            + "\n\nRound 1 tests failed:\n"
            + (round1.stdout or "")
            + (round1.stderr or "")
            + "\nReturn only the JSON patch."
        )
        reply = self.adapter.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            [],
            "none",
        )
        content = (reply.content or "").strip()
        if len(content) > MAX_PROVIDER_CHARS:
            raise LabError("provider_error", "provider 输出超过 64 KiB 上限")
        if content.startswith("```"):
            content = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", content, flags=re.S).strip()
        match = re.search(r"\{.*\}", content, flags=re.S)
        if not match:
            raise LabError("provider_error", "provider 没有返回合法 JSON patch")
        try:
            patch = json.loads(match.group(0))
        except json.JSONDecodeError:
            raise LabError("provider_error", "provider 返回的 JSON 无法解析") from None
        if not isinstance(patch, dict):
            raise LabError("provider_error", "provider patch 不是对象")
        file = patch.get("file")
        if not isinstance(file, str) or file not in task.files:
            raise LabError("patch_rejected", "provider patch 指向未知文件")
        if "content" in patch:
            if not isinstance(patch["content"], str):
                raise LabError("patch_rejected", "content 必须是字符串")
        elif "old" in patch and "new" in patch:
            if not isinstance(patch["old"], str) or not isinstance(patch["new"], str):
                raise LabError("patch_rejected", "old/new 必须是字符串")
        else:
            raise LabError("patch_rejected", "patch 缺少 content 或 old/new")
        return {
            **patch,
            "source": "provider",
            "real_model": True,
            "note": None,
        }

    @staticmethod
    def _apply(workspace: Workspace, task: LabTask, patch: dict[str, Any]) -> None:
        file = patch["file"]
        if file not in task.files:
            raise LabError("patch_rejected", "patch 指向未知文件")
        if "content" in patch:
            workspace.write(file, patch["content"])
            return
        current = workspace.read(file)
        old = patch["old"]
        if old not in current:
            raise LabError("patch_rejected", "patch 的 old 片段不在文件中")
        workspace.write(file, current.replace(old, patch["new"], 1))

    def _check_wall(self, started: float) -> None:
        if time.monotonic() - started > self.max_wall:
            raise LabError("budget_exceeded", "执行超过墙钟预算")

    @staticmethod
    def _check_cancel(run: LabRun) -> None:
        if run.cancel_event.is_set():
            raise LabCancelled()

    @staticmethod
    def _emit(run: LabRun, event: str, data: dict[str, Any]) -> None:
        with run.condition:
            if run.closed:
                return
            run.append_event(event, data)

    @staticmethod
    def _done(run: LabRun) -> None:
        with run.condition:
            if run.closed:
                return
            run.append_event("done", {})
            run.condition.notify_all()
