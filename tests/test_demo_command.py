"""B2-D7 ``molt demo``：1 live 候选 + 1 replay 基线 + 10 题扫描回放。

CI 只跑 ``--mode replay``（无 Pi、无网络）；真实 Pi 用 ``pytest.mark.pi`` +
``MOLT_RUN_PI_LIVE=1`` 显式放行，避免默认吞掉费用。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from molt.cli import main
from molt.core.store import StateStore

ROOT = Path(__file__).resolve().parents[1]
SCAN = 10


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _search_runs(state: Path) -> list[dict]:
    return [run.ext for run in StateStore(state).list_runs() if run.scope == "search"]


def _run_demo(state: Path, *extra: str) -> int:
    return main(
        [
            "--root",
            str(ROOT),
            "demo",
            "--scenario",
            "demo",
            "--state",
            str(state),
            *extra,
        ]
    )


def _tree_snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_demo_replay_end_to_end(tmp_path: Path) -> None:
    state = tmp_path / "state"
    assert _run_demo(state, "--mode", "replay", "--max-wall", "180") == 0

    summary = _load(state / "demo-summary.json")
    assert summary["mode"] == "replay"
    assert summary["within_wall"] is True
    assert summary["elapsed_s"] < 180
    assert summary["provenance_counts"]["live"] == 0
    # closure 1 条 replay + scan10 10 条 replay（10+1），fixture 只出现在非 live 扫描。
    assert summary["provenance_counts"]["replay"] == SCAN + 1
    assert len(summary["scan10"]) == SCAN
    assert len(summary["candidate_scan"]) == SCAN - 1
    assert summary["action"]["kind"] in ("adopt", "revert")

    runs = _search_runs(state)
    closure = [row for row in runs if row.get("demo_group") == "closure"]
    scan = [row for row in runs if row.get("demo_group") == "scan10"]
    candidate_scan = [row for row in runs if row.get("demo_group") == "candidate-scan"]
    assert len(closure) == 2
    assert len(scan) == SCAN
    assert len(candidate_scan) == SCAN - 1
    baseline = [row for row in closure if row["provenance"]["kind"] == "replay"]
    assert len(baseline) == 1
    assert baseline[0]["provenance"]["replay_of_run_id"]
    # replay 模式不得出现 live，也不得把 fixture 标成 live。
    assert all(row["provenance"]["kind"] != "live" for row in runs)

    audit = summary["audit"]
    assert audit["ok"] is True
    assert audit["mismatches"] == []

    assert len(list((state / "archive").glob("*.json"))) >= 2
    assert len(StateStore(state).list_decisions()) == 1


def test_demo_hard_wall_fails_without_summary(tmp_path: Path) -> None:
    state = tmp_path / "state"
    assert _run_demo(state, "--mode", "replay", "--max-wall", "0.001") == 1
    assert not (state / "demo-summary.json").exists()


def test_demo_baseline_state_is_read_only(tmp_path: Path) -> None:
    first_state = tmp_path / "first"
    assert _run_demo(first_state, "--mode", "replay") == 0
    baseline = tmp_path / "first-baseline"
    assert (baseline / "runs").is_dir()
    before = _tree_snapshot(baseline)

    second_state = tmp_path / "second"
    second = subprocess.run(
        [
            sys.executable,
            "-m",
            "molt",
            "--root",
            str(ROOT),
            "demo",
            "--mode",
            "replay",
            "--baseline-state",
            str(baseline),
            "--candidate-state",
            str(first_state),
            "--state",
            str(second_state),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    assert second.returncode == 0, second.stderr
    assert _tree_snapshot(baseline) == before, "demo 不得改写 --baseline-state 的原始 run"
    summary = _load(second_state / "demo-summary.json")
    assert summary["baseline_state"] == str(baseline)
    assert summary["provenance_counts"]["live"] == 0
    # 25 replay = scan10 10 + closure 1 + candidate-scan 9 + 候选 holdout 5；
    # 6 fixture = closure 候选 1（replay 模式）+ baseline holdout 5（未录制，回落）。
    assert summary["provenance_counts"]["replay"] == SCAN + 1 + (SCAN - 1) + 5
    assert summary["provenance_counts"]["fixture"] == 6
    runs = _search_runs(second_state)
    fixture = [row for row in runs if row["provenance"]["kind"] == "fixture"]
    assert len(fixture) == 1 and fixture[0]["demo_group"] == "closure"
    for group in ("scan10", "candidate-scan"):
        rows = [row for row in runs if row.get("demo_group") == group]
        assert rows and all(row["provenance"]["kind"] == "replay" for row in rows)
        assert all(row["provenance"]["replay_of_run_id"] for row in rows)
    candidate_scan = [row for row in runs if row.get("demo_group") == "candidate-scan"]
    assert len(candidate_scan) == SCAN - 1


@pytest.mark.pi
@pytest.mark.skipif(
    os.environ.get("MOLT_RUN_PI_LIVE") != "1",
    reason="真实 Pi 调用默认跳过；设置 MOLT_RUN_PI_LIVE=1 启用",
)
def test_demo_live_has_exactly_one_live_and_measures_overhead(tmp_path: Path) -> None:
    state = tmp_path / "state"
    assert _run_demo(state, "--mode", "live", "--max-wall", "180") == 0

    runs = _search_runs(state)
    live = [row for row in runs if row["provenance"]["kind"] == "live"]
    assert len(live) == 1
    assert live[0]["demo_group"] == "closure"
    assert live[0]["provenance"]["model"]
    assert not live[0]["provenance"].get("replay_of_run_id")

    baseline = [
        row
        for row in runs
        if row.get("demo_group") == "closure" and row["provenance"]["kind"] == "replay"
    ]
    assert len(baseline) == 1
    assert baseline[0]["provenance"]["replay_of_run_id"]
    assert len([row for row in runs if row.get("demo_group") == "scan10"]) == SCAN

    summary = _load(state / "demo-summary.json")
    overhead = summary["overhead"]
    assert overhead["measured"] is True
    assert overhead["threshold"] == 0.20
    assert overhead["overhead_ratio"] >= 0
    assert isinstance(overhead["within_threshold"], bool)
    assert summary["elapsed_s"] <= 180
    assert summary["audit"]["ok"] is True


def _fixture_search(state: Path, genome: str) -> None:
    code = main(
        [
            "--root",
            str(ROOT),
            "search",
            "--scenario",
            "demo",
            "--genome",
            str(ROOT / "shells/code-repair/genomes" / genome),
            "--state",
            str(state),
        ]
    )
    assert code == 0


def test_demo_infers_baseline_genome_from_state(tmp_path: Path) -> None:
    """baseline state 引用 v0-pi 时，demo 必须用它做起点，而不是默认 v0。"""
    baseline = tmp_path / "high-full"
    candidate = tmp_path / "low-full"
    _fixture_search(baseline, "v0-pi.json")
    _fixture_search(candidate, "v0.json")

    state = tmp_path / "demo"
    code = main(
        [
            "--root",
            str(ROOT),
            "demo",
            "--mode",
            "replay",
            "--baseline-state",
            str(baseline),
            "--candidate-state",
            str(candidate),
            "--state",
            str(state),
        ]
    )
    assert code == 0
    summary = _load(state / "demo-summary.json")
    baseline_meta = summary["genomes"]["baseline"]
    assert baseline_meta["source"] == "baseline-state"
    assert baseline_meta["switches"] == {"thinking": "high", "context": "full", "tools": "bash"}
    assert (
        baseline_meta["genome_id"]
        == _load(ROOT / "shells/code-repair/genomes/v0-pi.json")["genome_id"]
    )
    assert summary["genomes"]["proposal_matches_candidate"] is True
    assert summary["genomes"]["candidate"]["switches"]["thinking"] == "low"
    assert summary["genomes"]["candidate"]["switches"]["context"] == "full"
