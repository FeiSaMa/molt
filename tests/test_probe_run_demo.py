"""probes/run_demo.py 的纯函数与 schema 契约测试（不触网、不跑 pi）。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from probes.run_demo import (
    HEADROOM_TASKS,
    PROFILES,
    ROOT,
    RUN_SCHEMA,
    Demo,
    DemoStop,
    _call_pi,
    _cases,
    _check_paths,
    _child_pids,
    _diff_lines,
    _execute,
    _parse_args,
    _signature,
    _state_schema,
    _validate,
    _verdict_for,
)


def test_diff_lines_orders_replace_as_del_then_add() -> None:
    lines = _diff_lines("a\nb\n", "a\nc\n")
    assert lines == [
        {"type": "same", "text": "a"},
        {"type": "del", "text": "b"},
        {"type": "add", "text": "c"},
    ]


def test_signature_reports_missing_and_syntax_error() -> None:
    assert _signature("def other() -> None:\n    pass\n", "target") == "missing"
    assert _signature("def broken(:\n", "broken") == "missing"
    assert _signature("def f(x: int) -> str:\n    return 'a'\n", "f") == "(x) -> str"


def test_cases_keep_declared_input_shape() -> None:
    single = _cases("probe-order-2", (ROOT / "bench/probe/probe-order-2/workspace/solution.py").read_text())
    assert single[0]["input"] == ["Alpha", "bb", "ALPHA", "alpha"]
    double = _cases(
        "probe-order-x3", (ROOT / "bench/probe/probe-order-x3/workspace/solution.py").read_text()
    )
    assert double[0]["input"] == [["Gamma", "dd", "GAMMA", "gamma"], 2]
    assert all(case["passed"] == (case["actual"] == case["expected"]) for case in double)


def test_state_document_matches_schema(tmp_path: Path) -> None:
    demo = Demo(
        state_dir=tmp_path / "state",
        out=tmp_path / "web/state.json",
        run_out=tmp_path / "web/run.json",
        max_wall=420,
        max_cost=1.2,
        model="deepseek/deepseek-v4-pro",
    )
    document = demo.state_document()
    _validate(document, _state_schema(), "state.json")
    assert document["live"] is True
    assert document["phase"] == "run1"
    assert len(document["revision"]) == 64
    assert document["lineage"] == []
    assert document["verdict"] == ""


def test_run_document_matches_inline_schema(tmp_path: Path) -> None:
    demo = Demo(
        state_dir=tmp_path / "state",
        out=tmp_path / "web/state.json",
        run_out=tmp_path / "web/run.json",
        max_wall=420,
        max_cost=1.2,
        model="deepseek/deepseek-v4-pro",
    )
    _validate(demo.run_document("done", ""), RUN_SCHEMA, "run.json")


def test_verdict_three_way() -> None:
    passing = {task_id: True for task_id in HEADROOM_TASKS}
    assert _verdict_for({**passing, "probe-order-x8": True}, "full") == "v3_stable_all"
    assert _verdict_for({**passing, "probe-order-x8": False}, "full") == (
        "v3_stable_headroom"
    )
    assert _verdict_for({"probe-order-x5": True, "probe-order-x6": False}) == (
        "partial_transfer"
    )


def test_verdict_fast_uses_actual_task_set() -> None:
    assert _verdict_for(
        {"probe-order-x5": True, "probe-order-x6": True}, "fast"
    ) == "v3_stable_headroom"
    # x7/x8 不在 fast 题集里，不能借它们翻成 all。
    assert _verdict_for(
        {
            "probe-order-x5": True,
            "probe-order-x6": True,
            "probe-order-x7": True,
            "probe-order-x8": True,
        },
        "fast",
    ) == "v3_stable_headroom"
    assert _verdict_for(
        {"probe-order-x5": True, "probe-order-x6": False}, "fast"
    ) == "partial_transfer"


def test_profile_task_sets_and_parse_defaults(tmp_path: Path) -> None:
    fast = Demo(
        state_dir=tmp_path / "state",
        out=tmp_path / "web/state.json",
        run_out=tmp_path / "web/run.json",
        max_wall=360,
        max_cost=1.2,
        model="deepseek/deepseek-v4-pro",
        profile="fast",
    )
    assert fast.run1_scope == ("probe-order-2",)
    assert fast.run2_scope == ("probe-order-x5", "probe-order-x6")
    assert fast.headroom_tasks == ("probe-order-x5", "probe-order-x6")
    full = Demo(
        state_dir=tmp_path / "state",
        out=tmp_path / "web/state.json",
        run_out=tmp_path / "web/run.json",
        max_wall=420,
        max_cost=1.2,
        model="deepseek/deepseek-v4-pro",
        profile="full",
    )
    assert full.run2_scope == PROFILES["full"]["run2"]

    args = _parse_args([])
    assert args.profile == "fast"
    assert args.max_wall is None
    assert _parse_args(["--profile", "full"]).profile == "full"
    assert _parse_args(["--profile", "full", "--max-wall", "500"]).max_wall == 500


def test_run_blocks_and_lineage_only_cover_actual_tasks(tmp_path: Path) -> None:
    demo = Demo(
        state_dir=tmp_path / "state",
        out=tmp_path / "web/state.json",
        run_out=tmp_path / "web/run.json",
        max_wall=360,
        max_cost=1.2,
        model="deepseek/deepseek-v4-pro",
        profile="fast",
    )
    demo.run1_tasks = [
        {
            "id": "probe-order-2",
            "function": "dedupe",
            "signature": "(names) -> list[str]",
            "passed": True,
            "cases": [],
            "code_before": "",
            "code_after": "",
            "diff_lines": [],
            "_cost_usd": 0.0,
            "_wall_ms": 1,
        }
    ]
    demo.run2_tasks = [
        {
            "id": "probe-order-x5",
            "function": "unique",
            "signature": "(names) -> list[str]",
            "passed": True,
            "cases": [],
            "code_before": "",
            "code_after": "",
            "diff_lines": [],
            "_cost_usd": 0.0,
            "_wall_ms": 1,
        },
        {
            "id": "probe-order-x6",
            "function": "dedupe_merge",
            "signature": "(left, right) -> list[str]",
            "passed": False,
            "cases": [],
            "code_before": "",
            "code_after": "",
            "diff_lines": [],
            "_cost_usd": 0.0,
            "_wall_ms": 1,
        },
    ]
    document = demo.state_document()
    assert document["run1"]["total"] == 1
    assert [task["id"] for task in document["run1"]["tasks"]] == ["probe-order-2"]
    assert document["run2"]["total"] == 2
    assert document["run2"]["solved"] == 1
    assert document["run2"]["headroom_solved"] == 1
    assert [node["scope"] for node in document["lineage"]] == [
        ["probe-order-2"],
        ["probe-order-x5", "probe-order-x6"],
    ]
    assert "x8 未跑" in document["lineage"][1]["note"]
    _validate(document, _state_schema(), "state.json")


def test_path_guard_keeps_tier1_and_repo_state_untouched(tmp_path: Path) -> None:
    allowed = ROOT / "web/data/probe/state.json"
    assert _check_paths(Path("/tmp/molt-probe/demo"), allowed, allowed.with_name("run.json")) is None
    tier1 = ROOT / "web/data/runs.json"
    assert _check_paths(Path("/tmp/molt-probe/demo"), tier1, allowed) is not None
    inside = tmp_path  # 仅验证仓库内 state 被拒
    assert _check_paths(ROOT / "state", allowed, allowed) is not None
    assert inside.exists()


def test_single_task_timeout_is_partial(tmp_path: Path, monkeypatch) -> None:
    demo = Demo(
        state_dir=tmp_path / "state",
        out=tmp_path / "web/state.json",
        run_out=tmp_path / "web/run.json",
        max_wall=360,
        max_cost=0.6,
        model="deepseek/deepseek-v4-pro",
        profile="fast",
    )

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="molt search", timeout=1)

    monkeypatch.setattr("probes.run_demo._run_capture", boom)
    with pytest.raises(DemoStop) as excinfo:
        _execute(
            run_dir=demo.state_dir / "run1" / "probe-order-2",
            genome_path=tmp_path / "genome.json",
            task_id="probe-order-2",
            timeout_s=1,
        )
    assert excinfo.value.phase == "partial"


def test_total_budget_exhaustion_is_timeout(tmp_path: Path) -> None:
    demo = Demo(
        state_dir=tmp_path / "state",
        out=tmp_path / "web/state.json",
        run_out=tmp_path / "web/run.json",
        max_wall=360,
        max_cost=0.6,
        model="deepseek/deepseek-v4-pro",
        profile="fast",
    )
    demo.started -= 400
    with pytest.raises(DemoStop) as excinfo:
        demo.check_limits("run2")
    assert excinfo.value.phase == "timeout"


def test_reflection_timeout_phase_depends_on_budget(tmp_path: Path, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="pi", timeout=1)

    monkeypatch.setattr("probes.run_demo._run_capture", boom)
    kwargs = {
        "state_dir": tmp_path,
        "model": "deepseek/deepseek-v4-pro",
        "attempt": 1,
        "timeout_s": 240,
    }
    with pytest.raises(DemoStop) as excinfo:
        _call_pi("prompt", timeout_phase="partial", **kwargs)
    assert excinfo.value.phase == "partial"
    with pytest.raises(DemoStop) as excinfo:
        _call_pi("prompt", timeout_phase="timeout", **kwargs)
    assert excinfo.value.phase == "timeout"


def test_child_pids_finds_direct_child() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if process.pid in _child_pids(os.getpid()):
                break
            time.sleep(0.01)
        else:
            pytest.fail(f"child {process.pid} not found under /proc")
    finally:
        process.kill()
        process.wait(timeout=5)
