"""P2：跨 runtime 必须在入口被拒绝，不能静默 0 分。

``scenario.ext.allowed_runtimes`` 是产品策略；``calibrate`` 的强制 fixture 与
adapter conformance 走 ``enforce_runtime=False``，不受该策略影响。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from molt.core.store import StateStore
from molt.core.types import Scenario
from molt.demo import build_engine

ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(ROOT / "tests/run_cli.py"), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def _scenario(path: str) -> Scenario:
    return Scenario.from_dict(json.loads((ROOT / path).read_text(encoding="utf-8")))


def test_svc_config_rejects_fixture(tmp_path: Path) -> None:
    completed = _run_cli(
        "search",
        "--scenario",
        "svc-config",
        "--runtime",
        "fixture",
        "--state",
        str(tmp_path / "state"),
    )
    assert completed.returncode == 1
    assert "allowed_runtimes" in completed.stderr
    assert "deterministic" in completed.stderr


def test_demo_rejects_deterministic(tmp_path: Path) -> None:
    completed = _run_cli(
        "search",
        "--scenario",
        "demo",
        "--runtime",
        "deterministic",
        "--state",
        str(tmp_path / "state"),
    )
    assert completed.returncode == 1
    assert "allowed_runtimes" in completed.stderr
    assert "fixture" in completed.stderr


def test_legal_combinations_pass(tmp_path: Path) -> None:
    svc = _run_cli(
        "search",
        "--scenario",
        "svc-config",
        "--runtime",
        "deterministic",
        "--state",
        str(tmp_path / "svc"),
    )
    assert svc.returncode == 0, svc.stderr
    demo = _run_cli(
        "search",
        "--scenario",
        "demo",
        "--runtime",
        "fixture",
        "--state",
        str(tmp_path / "demo"),
    )
    assert demo.returncode == 0, demo.stderr
    for state in (tmp_path / "svc", tmp_path / "demo"):
        runs = StateStore(state).list_runs()
        assert runs and all(run.outcome != "error" for run in runs)


def test_calibrate_forced_fixture_is_not_blocked(tmp_path: Path) -> None:
    completed = _run_cli(
        "calibrate",
        "--scenario",
        "svc-config",
        "--min-pass",
        "0",
        "--state",
        str(tmp_path / "state"),
    )
    assert completed.returncode == 0, completed.stderr
    assert "calibrate solved=" in completed.stdout


def test_build_engine_rejects_cross_runtime(tmp_path: Path) -> None:
    scenario = _scenario("scenarios/svc-config/scenario.json")
    with pytest.raises(ValueError, match="allowed_runtimes"):
        build_engine(
            ROOT,
            scenario,
            StateStore(tmp_path / "state"),
            tmp_path / "work",
            runtime="fixture",
        )
