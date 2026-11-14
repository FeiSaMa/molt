"""Bottom-layer probe evolution bridge (fixture/replay, no live model).

The chat agent already reads probe projections. This service adds a controlled
way to execute the core molt pipeline from the agent:

    python -m molt demo --mode replay --state <outside-repo>/state --out <...>

It records the core gate/action/archive/provenance summary so a session
snapshot can reference the bottom-layer run. The replay mode executes fixture
data only; it never claims a live model run.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from molt.agent.store import new_id, utcnow

MAX_OUTPUT_TAIL = 8000
MAX_WALL_S = 80.0
MAX_TIMEOUT_S = 120.0


class ProbeConflict(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ProbeRun:
    run_id: str
    session_id: str
    created_at: str
    status: str = "queued"
    started_at: str | None = None
    finished_at: str | None = None
    elapsed_s: float | None = None
    gate: dict[str, Any] | None = None
    action: dict[str, Any] | None = None
    proposal: dict[str, Any] | None = None
    provenance_counts: dict[str, Any] | None = None
    audit: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    state_dir: str = ""
    out_dir: str = ""
    summary: dict[str, Any] | None = None
    revert: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "elapsed_s": self.elapsed_s,
            "gate": self.gate,
            "action": self.action,
            "proposal": self.proposal,
            "provenance_counts": self.provenance_counts,
            "audit": self.audit,
            "error": self.error,
            "summary_available": self.summary is not None,
            "revert": self.revert,
        }

    def summary_public(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "elapsed_s": self.elapsed_s,
            "gate": self.gate,
            "action": self.action,
            "provenance_counts": self.provenance_counts,
            "revert": self.revert,
        }

    def internal(self) -> dict[str, Any]:
        return {**self.public(), "state_dir": self.state_dir, "out_dir": self.out_dir, "summary": self.summary}


class ProbeEvolutionManager:
    def __init__(
        self,
        root: Path | str,
        repo_root: Path | str,
        *,
        python_executable: str | None = None,
        max_wall_s: float = MAX_WALL_S,
        timeout_s: float = MAX_TIMEOUT_S,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.python = python_executable or sys.executable
        self.max_wall_s = min(float(max_wall_s), MAX_TIMEOUT_S - 5)
        self.timeout_s = float(timeout_s)
        self._lock = threading.RLock()
        self._runs: dict[str, ProbeRun] = {}
        self._semaphore = threading.BoundedSemaphore(1)
        self._revert_lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        for path in sorted(self.root.glob("probe-*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            try:
                run = ProbeRun(
                    run_id=str(payload["run_id"]),
                    session_id=str(payload["session_id"]),
                    created_at=str(payload["created_at"]),
                    status=str(payload.get("status") or "completed"),
                    started_at=payload.get("started_at"),
                    finished_at=payload.get("finished_at"),
                    elapsed_s=payload.get("elapsed_s"),
                    gate=payload.get("gate"),
                    action=payload.get("action"),
                    proposal=payload.get("proposal"),
                    provenance_counts=payload.get("provenance_counts"),
                    audit=payload.get("audit"),
                    error=payload.get("error"),
                    stdout_tail=str(payload.get("stdout_tail") or ""),
                    stderr_tail=str(payload.get("stderr_tail") or ""),
                    state_dir=str(payload.get("state_dir") or ""),
                    out_dir=str(payload.get("out_dir") or ""),
                    summary=payload.get("summary"),
                    revert=payload.get("revert"),
                )
            except (KeyError, TypeError, ValueError):
                continue
            self._runs[run.run_id] = run

    def _persist(self, run: ProbeRun) -> None:
        try:
            path = self.root / f"{run.run_id}.json"
            path.write_text(
                json.dumps(run.internal(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            return

    def get(self, run_id: str) -> ProbeRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def runs_for_session(self, session_id: str | None) -> list[dict[str, Any]]:
        if not isinstance(session_id, str) or not session_id:
            return []
        with self._lock:
            runs = [run.summary_public() for run in self._runs.values() if run.session_id == session_id]
        return sorted(runs, key=lambda item: item.get("created_at") or "")

    def create_detached(self, *, force: bool = False) -> ProbeRun:
        """Create (or attach to) the presentation run for page4.

        ``force=True`` starts a fresh run when the previous one is terminal;
        an active run is still returned so two clients never execute the same
        fixture concurrently.
        """

        with self._lock:
            for run in self._runs.values():
                if run.session_id == "page4" and run.status in {"queued", "running"}:
                    return run
        return self._create("page4")

    def create(self, session_id: str) -> ProbeRun:
        if not session_id.startswith("sess-"):
            raise ValueError("invalid_session_id")
        return self._create(session_id)

    def _create(self, session_id: str) -> ProbeRun:
        if not self._semaphore.acquire(blocking=False):
            raise RuntimeError("probe_busy")
        run = ProbeRun(
            run_id=new_id("probe"),
            session_id=session_id,
            created_at=utcnow(),
            state_dir="",
            out_dir="",
        )
        base = self.root / run.run_id
        run.state_dir = str(base / "state")
        run.out_dir = str(base / "out")
        with self._lock:
            self._runs[run.run_id] = run
        thread = threading.Thread(target=self._execute, args=(run,), name=f"probe-{run.run_id}", daemon=True)
        try:
            thread.start()
        except Exception:
            with self._lock:
                self._runs.pop(run.run_id, None)
            self._semaphore.release()
            raise
        return run

    def revert(self, run_id: str, expected_generation: int) -> ProbeRun:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise ProbeConflict("probe_not_found", "probe run 不存在")
            if run.status != "completed" or not isinstance(run.action, dict):
                raise ProbeConflict("probe_not_completed", "只有完成的 probe run 可以回退")
            if run.revert is not None:
                raise ProbeConflict("already_reverted", "该 probe run 已经回退过")
            if run.action.get("kind") != "adopt":
                raise ProbeConflict("probe_not_adopted", "该 probe run 不是 adopt")
            baseline = run.action.get("baseline_archive_id")
            generation = run.action.get("generation")
            if not isinstance(baseline, str) or not baseline:
                raise ProbeConflict("baseline_archive_missing", "缺少 baseline archive")
            if type(generation) is not int or generation != expected_generation:
                raise ProbeConflict("stale_generation", "probe generation 已变化")
            state_dir = run.state_dir
        with self._revert_lock:
            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            command = [
                self.python,
                "-m",
                "molt",
                "revert",
                "--state",
                state_dir,
                "--to",
                baseline,
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(self.repo_root),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    env=env,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                raise ProbeConflict("probe_revert_timeout", "probe revert 超时") from None
            if completed.returncode != 0:
                raise ProbeConflict("probe_revert_failed", f"revert_exit_{completed.returncode}")
            match = re.search(r"revert ok archive=(\S+) generation=(\d+)", completed.stdout or "")
            with self._lock:
                run.revert = {
                    "archive_id": match.group(1) if match else baseline,
                    "generation": int(match.group(2)) if match else None,
                    "at": utcnow(),
                }
                self._persist(run)
                return run

    def _execute(self, run: ProbeRun) -> None:
        started = time.monotonic()
        run.status = "running"
        run.started_at = utcnow()
        try:
            Path(run.state_dir).parent.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            command = [
                self.python,
                "-m",
                "molt",
                "demo",
                "--mode",
                "replay",
                "--state",
                run.state_dir,
                "--out",
                run.out_dir,
                "--max-wall",
                str(self.max_wall_s),
            ]
            completed = subprocess.run(
                command,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                env=env,
                check=False,
            )
            run.stdout_tail = (completed.stdout or "")[-MAX_OUTPUT_TAIL:]
            run.stderr_tail = (completed.stderr or "")[-MAX_OUTPUT_TAIL:]
            run.elapsed_s = round(time.monotonic() - started, 3)
            if completed.returncode != 0:
                raise RuntimeError(f"demo_exit_{completed.returncode}")
            summary_path = Path(run.state_dir) / "demo-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if not isinstance(summary, dict):
                raise RuntimeError("demo_summary_invalid")
            run.summary = summary
            run.gate = summary.get("gate")
            run.action = summary.get("action")
            run.proposal = summary.get("proposal")
            run.provenance_counts = summary.get("provenance_counts")
            run.audit = summary.get("audit")
            run.status = "completed"
        except subprocess.TimeoutExpired:
            run.error = {"code": "probe_timeout", "message": "probe replay 超时"}
            run.status = "failed"
            run.elapsed_s = round(time.monotonic() - started, 3)
        except Exception as exc:  # noqa: BLE001
            run.error = {"code": "probe_failed", "message": str(exc)[:500]}
            run.status = "failed"
            run.elapsed_s = round(time.monotonic() - started, 3)
        finally:
            run.finished_at = utcnow()
            self._persist(run)
            self._semaphore.release()
