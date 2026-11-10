"""Sandbox probe：网络三连 + 仓库不可见 + 写入可用。

bash 侧必须失败的项目：provider、example、仓库 ``bench/``。读工具不经过
``shellPath``，其隔离由 argv 的 ``--exclude-tools read,grep,find,ls`` 承担
（实测见 ADR-0006）。
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from molt.core.isolation import IsolationPolicy

ProbeRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class SandboxProbe:
    available: bool
    checks: dict[str, bool]
    wrapper: str
    errors: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "checks": dict(self.checks),
            "wrapper": self.wrapper,
            "errors": list(self.errors),
        }


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


def run_sandbox_probe(
    wrapper: Path,
    *,
    workdir: Path,
    repo_root: Path,
    runner: ProbeRunner | None = None,
    timeout_s: float = 10.0,
) -> SandboxProbe:
    wrapper = Path(wrapper).resolve()
    workdir = Path(workdir).resolve()
    repo_root = Path(repo_root).resolve()
    if not wrapper.is_file():
        return SandboxProbe(False, {}, str(wrapper), (f"wrapper_missing:{wrapper}",))
    if not os.access(wrapper, os.X_OK):
        return SandboxProbe(False, {}, str(wrapper), (f"wrapper_not_executable:{wrapper}",))

    workdir.mkdir(parents=True, exist_ok=True)
    marker = workdir / "sandbox-probe.txt"
    marker.write_text("sandbox-marker\n", encoding="utf-8")
    runner = runner or _default_runner
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "WORKDIR": str(workdir),
    }

    def call(command: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return runner(
                [str(wrapper), "-c", command],
                cwd=workdir,
                env=env,
                timeout=timeout_s,
            )
        except Exception:  # noqa: BLE001
            return None

    checks: dict[str, bool] = {}
    errors: list[str] = []
    functional = call("printf sandbox-ok >/dev/null && cat sandbox-probe.txt >/dev/null")
    checks["workdir_writable"] = functional is not None and functional.returncode == 0
    if not checks["workdir_writable"]:
        errors.append("workdir_writable_failed")

    for name, url in (
        ("provider_blocked", "https://api.deepseek.com"),
        ("example_blocked", "https://example.com"),
    ):
        completed = call(f"curl --max-time 2 -sS {url} >/dev/null")
        checks[name] = completed is None or completed.returncode != 0
        if not checks[name]:
            errors.append(f"{name}_failed")

    for name, relative in (
        ("repo_hidden", "bench/holdout"),
        ("web_hidden", "web"),
        ("evidence_hidden", "evidence"),
    ):
        completed = call(f"test -e {repo_root / relative}")
        checks[name] = completed is None or completed.returncode != 0
        if not checks[name]:
            errors.append(f"{name}_failed")

    proxy = call("env | grep -iE '^(http|https|all|no)_proxy=' || true")
    checks["proxy_unset"] = proxy is None or not (proxy.stdout or "").strip()
    if not checks["proxy_unset"]:
        errors.append("proxy_present")

    available = all(checks.values())
    return SandboxProbe(available, checks, str(wrapper), tuple(errors))


def resolve_isolation(network: str, sandbox_available: bool) -> IsolationPolicy:
    """三档映射 + 无法建沙箱时的 permissions-only 降级。"""
    return IsolationPolicy.resolve(network, sandbox_available=sandbox_available)


def isolation_payload(policy: IsolationPolicy) -> dict[str, Any]:
    return {
        "network": policy.network,
        "level": policy.level,
        "degraded": policy.degraded,
        "sandbox_unavailable": policy.sandbox_unavailable,
        "claim_marker": policy.claim_marker,
    }
