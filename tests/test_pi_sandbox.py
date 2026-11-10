"""Sandbox：bwrap wrapper 无网络、仓库不可见、proxy 清理；read 工具例外。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from molt.adapters.pi.argv import BUILTIN_READ_TOOLS
from molt.adapters.pi.probe import run_sandbox_probe

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "molt/adapters/pi/sandbox_bash.sh"
BWRAP = shutil.which("bwrap")
pytestmark = [
    pytest.mark.pi,
    pytest.mark.skipif(BWRAP is None, reason="bwrap 不可用"),
]


def call(workdir: Path, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WRAPPER), "-c", command],
        cwd=workdir,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": "/tmp",
            "WORKDIR": str(workdir),
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_probe_reports_sandbox_available(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    probe = run_sandbox_probe(WRAPPER, workdir=workdir, repo_root=ROOT)
    assert probe.available is True, probe.to_dict()
    assert probe.checks["workdir_writable"] is True
    assert probe.checks["provider_blocked"] is True
    assert probe.checks["example_blocked"] is True
    assert probe.checks["repo_hidden"] is True
    assert probe.checks["proxy_unset"] is True


def test_probe_missing_wrapper(tmp_path: Path) -> None:
    probe = run_sandbox_probe(tmp_path / "missing.sh", workdir=tmp_path / "work", repo_root=ROOT)
    assert probe.available is False
    assert "wrapper_missing" in probe.errors[0]


def test_bash_cannot_reach_provider_or_example(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    for url in ("https://api.deepseek.com", "https://example.com"):
        completed = call(workdir, f"curl --max-time 2 -sS {url} >/dev/null")
        assert completed.returncode != 0, url


def test_bash_cannot_read_holdout_or_repo(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    completed = call(workdir, f"test -e {ROOT / 'bench/holdout/holdout-even/task.json'}")
    assert completed.returncode != 0
    repo = call(workdir, f"ls {ROOT} >/dev/null")
    assert repo.returncode != 0


def test_bash_has_no_proxy_env(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    completed = call(workdir, "env | grep -iE '^(http|https|all|no)_proxy=' || true")
    assert (completed.stdout or "").strip() == ""


def test_pi_argv_allows_only_bash() -> None:
    from molt.adapters.pi.argv import build_pi_argv

    argv = build_pi_argv(
        model="deepseek/deepseek-flash",
        thinking="off",
        session_id="molt-read-check",
        session_dir="/tmp/sessions",
        tools_args=("--tools", "bash"),
    )
    assert argv[argv.index("--tools") + 1] == "bash"
    for excluded in BUILTIN_READ_TOOLS + ("write", "edit"):
        assert excluded not in argv


def test_repository_never_generates_no_tools_flag() -> None:
    needle = "--no-" + "tools"
    roots = [ROOT / "molt", ROOT / "shells", ROOT / "tests", ROOT / "docs"]
    hits = []
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".md", ".sh", ".json"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if needle in text:
                hits.append(str(path.relative_to(ROOT)))
    assert hits == []
