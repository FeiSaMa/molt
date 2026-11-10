"""Pi 项目 trust 预检：证明 `.pi/settings.json` 的 ``shellPath`` 真的生效。

``--approve`` 只是一次性 trust 覆盖，不等于项目设置已加载；这里用一次真实 Pi
调用让 bash 工具经过 wrapper 并在 sentinel/log 上留痕。缓存键
``(shell_version, settings_hash)``，避免每个 run 重复付费。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from molt.adapters.pi.argv import build_pi_argv, safe_session_id
from molt.adapters.pi.ledger import parse_session_file
from molt.core.events import utc_now
from molt.core.hash import canonical_json_text, sha256_text

TrustRunner = Callable[..., subprocess.CompletedProcess[str]]

TRUST_PROMPT = """Run the bash tool exactly once with this command and nothing else:
printf molt-trust-ok > preflight.txt
This is a disposable sandbox connectivity check. Do not use any other tool.
Reply DONE after it succeeds."""


@dataclass(frozen=True, slots=True)
class TrustResult:
    ok: bool
    project_trust: str
    reason: str | None
    cache_hit: bool
    settings_hash: str
    settings_path: str
    sentinel_path: str
    log_path: str
    preflight_tokens: int = 0
    preflight_cost_usd: float = 0.0
    session_id: str = ""
    session_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "project_trust": self.project_trust,
            "reason": self.reason,
            "cache_hit": self.cache_hit,
            "settings_hash": self.settings_hash,
            "settings_path": self.settings_path,
            "sentinel_path": self.sentinel_path,
            "log_path": self.log_path,
            "preflight_tokens": self.preflight_tokens,
            "preflight_cost_usd": self.preflight_cost_usd,
            "session_id": self.session_id,
            "session_path": self.session_path,
        }


def write_project_settings(workdir: Path, shell_path: Path) -> tuple[Path, str, str]:
    """写 ``.pi/settings.json``，返回 ``(path, canonical_text, settings_hash)``。"""
    settings = {"shellPath": str(Path(shell_path).resolve())}
    text = canonical_json_text(settings)
    path = Path(workdir) / ".pi" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path, text, sha256_text(text)


def _default_runner(
    argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=str(cwd),
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class TrustProbe:
    def __init__(
        self,
        *,
        pi_executable: str = "pi",
        model: str = "deepseek/deepseek-flash",
        cache_dir: Path | None = None,
        runner: TrustRunner | None = None,
        timeout_s: float = 180.0,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.pi_executable = pi_executable
        self.model = model
        self.cache_dir = Path(cache_dir).resolve() if cache_dir else None
        self.runner = runner or _default_runner
        self.timeout_s = timeout_s
        self.clock = clock

    def _cache_path(self, shell_version: str, settings_hash: str) -> Path | None:
        if self.cache_dir is None:
            return None
        key = sha256_text(f"{shell_version}:{settings_hash}")[:32]
        return self.cache_dir / f"trust-{key}.json"

    def _read_cache(self, path: Path) -> TrustResult | None:
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not data.get("ok"):
                return None
            data["cache_hit"] = True
            return TrustResult(**data)
        except (json.JSONDecodeError, TypeError):
            return None

    def _write_cache(self, path: Path, result: TrustResult) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = result.to_dict()
        data["cache_hit"] = False
        path.write_text(canonical_json_text(data), encoding="utf-8")

    def ensure(
        self,
        workdir: Path,
        *,
        shell_version: str,
        shell_path: Path,
    ) -> TrustResult:
        workdir = Path(workdir).resolve()
        settings_path, _, settings_hash = write_project_settings(workdir, shell_path)
        cache_path = self._cache_path(shell_version, settings_hash)
        if cache_path is not None:
            cached = self._read_cache(cache_path)
            if cached is not None:
                return cached

        log_path = workdir / ".molt" / "shell-path.log"
        sentinel = workdir / "preflight.txt"
        for path in (log_path, sentinel):
            if path.exists():
                path.unlink()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        executable = self.pi_executable
        if os.sep in executable:
            available = Path(executable).is_file()
        else:
            available = shutil.which(executable) is not None
        if not available:
            return TrustResult(
                ok=False,
                project_trust="trust_missing",
                reason="pi_unavailable",
                cache_hit=False,
                settings_hash=settings_hash,
                settings_path=str(settings_path),
                sentinel_path=str(sentinel),
                log_path=str(log_path),
            )

        session_dir = workdir / ".pi-trust-sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        session_id = safe_session_id(f"molt-trust-{settings_hash[:12]}")
        argv = build_pi_argv(
            model=self.model,
            thinking="off",
            session_id=session_id,
            session_dir=str(session_dir),
            tools_args=("--tools", "bash"),
            builtin_excludes=(),
            prompt=TRUST_PROMPT,
        )
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "WORKDIR": str(workdir),
            "MOLT_SHELL_LOG": str(log_path),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        for name in (
            "DEEPSEEK_API_KEY",
            "OPENAI_API_KEY",
            "LANG",
            "LC_ALL",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
        ):
            if name in os.environ:
                env[name] = os.environ[name]
        try:
            completed = self.runner(argv, cwd=workdir, env=env, timeout=self.timeout_s)
            returncode = int(completed.returncode)
            stderr = (completed.stderr or "")[-2000:]
        except Exception as exc:  # noqa: BLE001 - 预检失败统一 trust_missing
            returncode = -1
            stderr = f"{type(exc).__name__}: {exc}"

        session_path = _newest_session(session_dir, session_id)
        tokens = 0
        cost = 0.0
        if session_path is not None:
            try:
                usage = parse_session_file(session_path)
                tokens = usage.total_tokens
                cost = usage.cost_usd
            except (OSError, ValueError):
                pass
        ok = (
            returncode == 0
            and sentinel.is_file()
            and log_path.is_file()
            and log_path.stat().st_size > 0
        )
        result = TrustResult(
            ok=ok,
            project_trust="cli_approve" if ok else "trust_missing",
            reason=None if ok else f"preflight_failed:{returncode}:{stderr[-200:]}",
            cache_hit=False,
            settings_hash=settings_hash,
            settings_path=str(settings_path),
            sentinel_path=str(sentinel),
            log_path=str(log_path),
            preflight_tokens=tokens,
            preflight_cost_usd=cost,
            session_id=session_id,
            session_path=str(session_path) if session_path else "",
        )
        if ok and cache_path is not None:
            self._write_cache(cache_path, result)
        return result


def _newest_session(session_dir: Path, session_id: str) -> Path | None:
    matches = sorted(session_dir.glob(f"*_{session_id}.jsonl"))
    if not matches:
        matches = sorted(session_dir.glob("*.jsonl"))
    return matches[-1] if matches else None
