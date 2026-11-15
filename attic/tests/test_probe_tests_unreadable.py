"""agent 的 bash 沙箱必须读不到隐藏测试目录。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "molt/adapters/pi/sandbox_bash.sh"
HIDDEN = ROOT / "bench/probe/probe-order-1/tests/check_cases.py"


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed")
def test_agent_cannot_read_hidden_tests(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "solution.py").write_text("VALUE = 1\n", encoding="utf-8")
    env = {
        "WORKDIR": str(workspace),
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
    }

    visible = subprocess.run(
        [str(WRAPPER), "-c", "cat solution.py"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert visible.returncode == 0
    assert "VALUE" in visible.stdout

    hidden = subprocess.run(
        [str(WRAPPER), "-c", f"cat {HIDDEN}"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert hidden.returncode != 0
    assert "case_01" not in hidden.stdout
    assert "top_k" not in hidden.stdout
