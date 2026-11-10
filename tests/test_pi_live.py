"""真实 Pi 1.5.0 冒烟测试（默认跳过；``MOLT_RUN_PI_LIVE=1`` 才执行）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from molt.core.store import StateStore
from molt.core.types import Genome
from molt.demo import build_engine, load_demo_scenario

ROOT = Path(__file__).resolve().parents[1]

pytestmark = [
    pytest.mark.pi,
    pytest.mark.skipif(
        os.environ.get("MOLT_RUN_PI_LIVE") != "1",
        reason="真实 Pi 调用默认跳过；设置 MOLT_RUN_PI_LIVE=1 启用",
    ),
]


def test_live_pi_smoke_single_task(tmp_path: Path) -> None:
    scenario = load_demo_scenario(ROOT)
    store = StateStore(tmp_path / "state")
    engine = build_engine(ROOT, scenario, store, tmp_path / "work", runtime="pi")
    genome = Genome.from_dict(
        json.loads((ROOT / "shells/code-repair/genomes/v0-pi.json").read_text(encoding="utf-8"))
    )
    task = engine.task_provider.load_task("smoke-add")
    run = engine.run_task(genome, task, scope="search")
    runtime = run.ext.get("runtime", {})
    assert run.outcome == "passed", runtime
    assert runtime["project_trust"] == "cli_approve"
    assert runtime["provenance"] == "live"
    assert runtime["ledger"]["matches"] is True
    assert run.usage.cost_usd <= 1.0
