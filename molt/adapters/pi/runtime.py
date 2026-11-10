"""Pi 1.5.0 Runtime adapter。

一个 run = 一次 ``pi -p --mode json``；只从 session JSONL / JSON 流读取 usage，
不猜成本。bash 由 ``.pi/settings.json`` 的 ``shellPath`` 指向 bwrap wrapper；
读工具不经过 shellPath，因此 argv 固定排除 read/grep/find/ls。
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from molt.adapters.pi.argv import (
    BUILTIN_READ_TOOLS,
    assert_required_flags,
    build_pi_argv,
    safe_session_id,
)
from molt.adapters.pi.ledger import (
    SessionUsage,
    cross_check,
    parse_session_file,
    parse_stream_text,
)
from molt.adapters.pi.probe import (
    SandboxProbe,
    isolation_payload,
    resolve_isolation,
    run_sandbox_probe,
)
from molt.adapters.pi.trust import TrustProbe, TrustResult
from molt.core.hash import sha256_file
from molt.core.ports import RuntimeOutcome
from molt.core.types import Genome, Task, Usage

DEFAULT_MODEL = "deepseek/deepseek-flash"
EXCLUDED_ARTIFACT_DIRS = frozenset(
    {
        ".pi",
        ".molt",
        ".pi-sessions",
        ".pi-trust-sessions",
        "sessions",
        ".git",
        "__pycache__",
    }
)


def _workspace_files(workdir: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(Path(workdir).rglob("*")):
        rel = path.relative_to(workdir)
        if any(part in EXCLUDED_ARTIFACT_DIRS for part in rel.parts):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        if path.name in ("sandbox-probe.txt", "preflight.txt"):
            continue
        files[rel.as_posix()] = sha256_file(path)
    return files


def _changed_artifacts(before: Mapping[str, str], after: Mapping[str, str]) -> dict[str, str]:
    changed: dict[str, str] = {}
    for path, digest in after.items():
        if before.get(path) != digest:
            changed[path] = digest
    return changed


@dataclass(slots=True)
class PiRuntime:
    repo_root: Path
    tools_argv_fn: Callable[[str], Sequence[str]]
    sandbox_wrapper: Path | None = None
    trust_probe: TrustProbe | None = None
    probe_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None
    spawn: Callable[..., subprocess.Popen] | None = None
    pi_executable: str = "pi"
    model: str = DEFAULT_MODEL
    network: str = "provider_only"
    builtin_excludes: tuple[str, ...] = ()
    timeout_grace_s: float = 5.0
    _sandbox: SandboxProbe | None = field(default=None, init=False)

    def _model_for(self, genome: Genome) -> str:
        ext = genome.ext.get("pi")
        if isinstance(ext, Mapping):
            model = ext.get("model")
            if isinstance(model, str) and model:
                return model
        return self.model

    def _sandbox_probe(self, workdir: Path) -> SandboxProbe:
        if self._sandbox is not None:
            return self._sandbox
        if self.sandbox_wrapper is None:
            self._sandbox = SandboxProbe(False, {}, "", ("wrapper_not_configured",))
            return self._sandbox
        probe_dir = (Path(workdir).parent / ".molt-sandbox-probe").resolve()
        self._sandbox = run_sandbox_probe(
            self.sandbox_wrapper,
            workdir=probe_dir,
            repo_root=self.repo_root,
            runner=self.probe_runner,
        )
        return self._sandbox

    def _trust(self, workdir: Path, genome: Genome) -> TrustResult | None:
        if self.sandbox_wrapper is None:
            return None
        if self.trust_probe is None:
            self.trust_probe = TrustProbe(
                pi_executable=self.pi_executable,
                model=self._model_for(genome),
                cache_dir=Path(workdir).parent / ".molt-trust-cache",
            )
        return self.trust_probe.ensure(
            workdir,
            shell_version=genome.shell_version,
            shell_path=self.sandbox_wrapper,
        )

    def run(self, genome: Genome, task: Task, workdir: Path) -> RuntimeOutcome:
        workdir = Path(workdir).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        before = _workspace_files(workdir)
        started_at = _now_iso()
        started = time.monotonic()
        prompt_path = workdir / ".molt" / "prompt.txt"
        prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else task.title

        sandbox = self._sandbox_probe(workdir)
        if sandbox.available:
            trust = self._trust(workdir, genome)
            if trust is not None and not trust.ok:
                return self._failure(
                    outcome="invalid",
                    started_at=started_at,
                    started=started,
                    workdir=workdir,
                    before=before,
                    reason="trust_missing",
                    task=task,
                    genome=genome,
                    trust=trust,
                    sandbox=sandbox,
                    error=trust.reason,
                )
        else:
            trust = None
            settings = workdir / ".pi" / "settings.json"
            if settings.exists():
                settings.unlink()

        policy = resolve_isolation(self.network, sandbox.available)
        session_dir = (workdir / ".pi-sessions").resolve()
        session_dir.mkdir(parents=True, exist_ok=True)
        session_id = safe_session_id(f"molt-{uuid.uuid4().hex}")
        model = self._model_for(genome)
        argv = build_pi_argv(
            model=model,
            thinking=genome.switches.thinking,
            session_id=session_id,
            session_dir=str(session_dir),
            tools_args=self.tools_argv_fn(genome.switches.tools),
            builtin_excludes=self.builtin_excludes,
            prompt=prompt,
        )
        assert_required_flags(argv)

        log_path = (workdir / ".molt" / "pi.stdout.jsonl").resolve()
        err_path = (workdir / ".molt" / "pi.stderr.log").resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update(
            {
                "WORKDIR": str(workdir),
                "MOLT_SHELL_LOG": str(workdir / ".molt" / "shell-path.log"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        wall_s = genome.budget.wall_ms / 1000.0
        timed_out = False
        returncode = -1
        spawn = self.spawn or subprocess.Popen
        try:
            with (
                log_path.open("w", encoding="utf-8") as stdout,
                err_path.open("w", encoding="utf-8") as stderr,
            ):
                process = spawn(
                    argv,
                    cwd=str(workdir),
                    env=env,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                    text=True,
                )
                try:
                    process.communicate(timeout=wall_s + self.timeout_grace_s)
                    returncode = int(process.returncode)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.communicate()
                    returncode = -signal.SIGKILL
        except FileNotFoundError:
            returncode = -1
        except Exception as exc:  # noqa: BLE001
            return self._failure(
                outcome="error",
                started_at=started_at,
                started=started,
                workdir=workdir,
                before=before,
                reason="spawn_error",
                task=task,
                genome=genome,
                trust=trust,
                sandbox=sandbox,
                error=f"{type(exc).__name__}: {exc}",
            )

        wall_ms = int((time.monotonic() - started) * 1000)
        session_path = _newest_session(session_dir, session_id)
        ledger: SessionUsage | None = None
        ledger_error = None
        if session_path is not None:
            try:
                ledger = parse_session_file(session_path)
            except (OSError, ValueError) as exc:
                ledger_error = f"{type(exc).__name__}: {exc}"
        else:
            ledger_error = "session_missing"
        try:
            stream = parse_stream_text(
                log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
            )
        except ValueError as exc:
            stream = SessionUsage()
            ledger_error = ledger_error or f"stream_unreadable: {exc}"
        cross_ok, cross_reason = (True, None)
        if ledger is not None:
            cross_ok, cross_reason = cross_check(ledger, stream)
            if ledger.matches and not cross_ok:
                ledger = replace(ledger, matches=False, mismatch_reason=cross_reason)

        usage_source = ledger or stream
        tokens_in = usage_source.input
        tokens_out = usage_source.output
        turns = usage_source.turns
        cost_usd = usage_source.cost_usd if ledger is not None else stream.cost_usd
        cost_source = usage_source.cost_source

        budget_reasons: list[str] = []
        if timed_out:
            budget_reasons.append("wall_timeout")
        if turns > genome.budget.turns:
            budget_reasons.append("turns_exceeded")
        if usage_source.total_tokens > genome.budget.tokens:
            budget_reasons.append("tokens_exceeded")
        if ledger is not None and not ledger.matches:
            budget_reasons.append("ledger_mismatch")
        if ledger_error:
            budget_reasons.append("ledger_unreadable")

        if budget_reasons:
            outcome = "invalid"
        elif returncode != 0:
            outcome = "error"
        else:
            outcome = "passed"

        after = _workspace_files(workdir)
        artifacts = _changed_artifacts(before, after)
        usage = Usage(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            wall_ms=wall_ms,
            turns=turns,
        )
        diagnostic = {
            "runtime": {
                "adapter": "pi",
                "pi_version": "1.5.0",
                "provenance": "live",
                "model": model,
                "argv": argv,
                "returncode": returncode,
                "timed_out": timed_out,
                "project_trust": trust.project_trust if trust else "not_required",
                "trust": trust.to_dict() if trust else None,
                "session_id": session_id,
                "session_path": str(session_path) if session_path else "",
                "log_ref": str(log_path),
                "stderr_ref": str(err_path),
                "isolation": isolation_payload(policy),
                "sandbox": sandbox.to_dict(),
                "tools_allowlist": ["bash"],
                "read_tools_excluded": list(BUILTIN_READ_TOOLS),
                "cost_source": cost_source,
                "usage": usage_source.to_dict(),
                "budget": {
                    "wall_ms": genome.budget.wall_ms,
                    "turns": genome.budget.turns,
                    "tokens": genome.budget.tokens,
                    "exceeded": budget_reasons,
                },
                "ledger": ledger.to_dict() if ledger else None,
                "ledger_error": ledger_error,
            }
        }
        return RuntimeOutcome(
            outcome=outcome,
            usage=usage,
            started_at=started_at,
            finished_at=_now_iso(),
            artifacts=artifacts,
            diagnostic=diagnostic,
        )

    def _failure(
        self,
        *,
        outcome: str,
        started_at: str,
        started: float,
        workdir: Path,
        before: Mapping[str, str],
        reason: str,
        task: Task,
        genome: Genome,
        trust: TrustResult | None,
        sandbox: SandboxProbe,
        error: str | None,
    ) -> RuntimeOutcome:
        wall_ms = int((time.monotonic() - started) * 1000)
        after = _workspace_files(workdir)
        return RuntimeOutcome(
            outcome=outcome,
            usage=Usage(0, 0, 0.0, wall_ms, 0),
            started_at=started_at,
            finished_at=_now_iso(),
            artifacts=_changed_artifacts(before, after),
            diagnostic={
                "runtime": {
                    "adapter": "pi",
                    "pi_version": "1.5.0",
                    "provenance": "live",
                    "model": self._model_for(genome),
                    "project_trust": trust.project_trust if trust else "not_required",
                    "trust": trust.to_dict() if trust else None,
                    "reason": reason,
                    "error": error,
                    "isolation": isolation_payload(
                        resolve_isolation(self.network, sandbox.available)
                    ),
                    "sandbox": sandbox.to_dict(),
                    "tools_allowlist": ["bash"],
                    "read_tools_excluded": list(BUILTIN_READ_TOOLS),
                }
            },
        )


def _newest_session(session_dir: Path, session_id: str) -> Path | None:
    matches = sorted(session_dir.glob(f"*_{session_id}.jsonl"))
    if not matches:
        matches = sorted(session_dir.glob("*.jsonl"))
    return matches[-1] if matches else None


def _now_iso() -> str:
    from molt.core.events import utc_now

    return utc_now()


def default_tools_argv(mode: str) -> list[str]:
    """兜底：未注入 shell 映射时不允许工具模式静默生效。"""
    if mode == "default":
        return []
    raise ValueError(f"未注入 tools_mode 映射，无法处理 {mode!r}")
