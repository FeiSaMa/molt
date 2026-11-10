"""Pi trust preflight：settings/sentinel/shellPath 生效证明与缓存。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from molt.adapters.pi.trust import TrustProbe, write_project_settings

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "molt/adapters/pi/sandbox_bash.sh"

pytestmark = pytest.mark.pi


def completed(argv, returncode=0):
    return subprocess.CompletedProcess(argv, returncode, "", "")


def runner_factory(*, write_sentinel=True, write_log=True, returncode=0):
    calls = []

    def runner(argv, *, cwd, env, timeout):
        calls.append(argv)
        workdir = Path(cwd)
        sentinel = workdir / "preflight.txt"
        log = Path(env["MOLT_SHELL_LOG"])
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        if write_sentinel:
            sentinel.write_text("molt-trust-ok\n", encoding="utf-8")
        if write_log:
            log.write_text("bash -c printf molt-trust-ok\n", encoding="utf-8")
        return completed(argv, returncode)

    return runner, calls


def test_settings_are_written_with_absolute_shell_path(tmp_path: Path) -> None:
    path, text, digest = write_project_settings(tmp_path, WRAPPER)
    data = json.loads(text)
    assert data["shellPath"] == str(WRAPPER.resolve())
    assert path == tmp_path / ".pi" / "settings.json"
    assert len(digest) == 64


def test_preflight_success_and_cache(tmp_path: Path) -> None:
    runner, calls = runner_factory()
    probe = TrustProbe(
        pi_executable=sys.executable,
        cache_dir=tmp_path / "cache",
        runner=runner,
    )
    work = tmp_path / "work"
    result = probe.ensure(work, shell_version="1.0.0", shell_path=WRAPPER)
    assert result.ok is True
    assert result.project_trust == "cli_approve"
    assert result.cache_hit is False
    assert Path(result.sentinel_path).is_file()
    assert Path(result.log_path).stat().st_size > 0
    assert len(calls) == 1
    argv = calls[0]
    for required in ("-p", "--approve", "--session-id", "--session-dir", "--mode", "json"):
        assert required in argv
    assert "--tools" in argv and "bash" in argv
    assert ("--no-" + "tools") not in argv

    cached = probe.ensure(work, shell_version="1.0.0", shell_path=WRAPPER)
    assert cached.ok is True
    assert cached.cache_hit is True
    assert len(calls) == 1


def test_shell_path_not_effective_is_trust_missing(tmp_path: Path) -> None:
    runner, _ = runner_factory(write_log=False)
    probe = TrustProbe(
        pi_executable=sys.executable,
        cache_dir=tmp_path / "cache",
        runner=runner,
    )
    result = probe.ensure(tmp_path / "work", shell_version="1.0.0", shell_path=WRAPPER)
    assert result.ok is False
    assert result.project_trust == "trust_missing"
    assert "preflight_failed" in (result.reason or "")


def test_preflight_nonzero_is_trust_missing(tmp_path: Path) -> None:
    runner, _ = runner_factory(returncode=2)
    probe = TrustProbe(
        pi_executable=sys.executable,
        cache_dir=tmp_path / "cache",
        runner=runner,
    )
    result = probe.ensure(tmp_path / "work", shell_version="1.0.0", shell_path=WRAPPER)
    assert result.ok is False
    assert result.project_trust == "trust_missing"


def test_missing_pi_binary_is_trust_missing(tmp_path: Path) -> None:
    runner, calls = runner_factory()
    probe = TrustProbe(
        pi_executable=str(tmp_path / "missing-pi"),
        cache_dir=tmp_path / "cache",
        runner=runner,
    )
    result = probe.ensure(tmp_path / "work", shell_version="1.0.0", shell_path=WRAPPER)
    assert result.ok is False
    assert result.reason == "pi_unavailable"
    assert calls == []


def test_settings_changes_invalidate_cache(tmp_path: Path) -> None:
    runner, calls = runner_factory()
    probe = TrustProbe(
        pi_executable=sys.executable,
        cache_dir=tmp_path / "cache",
        runner=runner,
    )
    work = tmp_path / "work"
    first = probe.ensure(work, shell_version="1.0.0", shell_path=WRAPPER)
    assert first.cache_hit is False

    other = tmp_path / "other-wrapper.sh"
    other.write_text(WRAPPER.read_text(encoding="utf-8"), encoding="utf-8")
    other.chmod(0o755)
    second = probe.ensure(work, shell_version="1.0.0", shell_path=other)
    assert second.cache_hit is False
    assert len(calls) == 2
