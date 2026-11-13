"""probes/run_demo.py 的纯函数与 schema 契约测试（不触网、不跑 pi）。"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import probes.run_demo as run_demo
from molt.core.genome import compute_genome_id
from molt.core.types import Genome
from probes.run_demo import (
    HEADROOM_TASKS,
    MEMORY_REF,
    MEMORY_SNAPSHOT_DIR,
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
    _memory_bytes,
    _parse_args,
    _prepare_run2_genome,
    _publish_memory,
    _publish_memory_snapshot,
    _resolve_paths,
    _run_phase,
    _signature,
    _state_schema,
    _tracked_probe_files,
    _validate,
    _validate_outputs,
    _verdict_for,
    main,
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


def test_tracked_probe_files_cover_current_repo() -> None:
    protected = _tracked_probe_files()
    assert protected is not None
    assert (ROOT / "web/data/probe/example.json").resolve() in protected


def test_guard_fails_closed_when_git_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "not-a-repo"
    (repo / "web/data/probe").mkdir(parents=True)
    monkeypatch.setattr("probes.run_demo.ROOT", repo)
    state = tmp_path / "outside-state"
    problem = _check_paths(
        state,
        repo / "web/data/probe/state.json",
        repo / "web/data/probe/run.json",
    )
    assert problem is not None
    assert "git ls-files" in problem
    pi_calls: list[int] = []
    monkeypatch.setattr(
        "probes.run_demo._pi_version", lambda: pi_calls.append(1) or "1.5.0"
    )
    assert main(["--state", str(state), "--out", str(repo / "web/data/probe/state.json")]) == 2
    assert not state.exists()
    assert pi_calls == []


@pytest.mark.parametrize("option", ["--out", "--run-out"])
@pytest.mark.parametrize("kind", ["relative", "absolute", "dotdot", "symlink"])
def test_output_guard_rejects_tracked_probe_file(
    option: str, kind: str, tmp_path: Path, monkeypatch
) -> None:
    protected = ROOT / "web/data/probe/example.json"
    before = protected.read_bytes()
    if kind == "relative":
        target = Path("web/data/probe/example.json")
    elif kind == "absolute":
        target = protected
    elif kind == "dotdot":
        target = Path("web/data/probe/missing/../example.json")
    else:
        link = tmp_path / "protect-alias.json"
        link.symlink_to(protected)
        target = link
    state = tmp_path / "run-state"
    legal_out = tmp_path / "legal-state.json"
    pi_calls: list[int] = []

    def _pi_stub() -> str:
        pi_calls.append(1)
        return "1.5.0"

    monkeypatch.setattr("probes.run_demo._pi_version", _pi_stub)
    argv = ["--state", str(state), "--out", str(legal_out)]
    if option == "--out":
        argv = ["--state", str(state), "--out", str(target)]
    else:
        argv += ["--run-out", str(target)]
    assert main(argv) == 2
    assert protected.read_bytes() == before
    assert not (protected.parent / f"{protected.name}.tmp").exists()
    assert not legal_out.exists()
    assert not state.exists()
    assert pi_calls == []


def test_guard_rejects_newly_tracked_probe_file(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    probe_dir = repo / "web/data/probe"
    probe_dir.mkdir(parents=True)
    tracked = probe_dir / "new-evidence.json"
    tracked.write_text("{}", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "add", "web/data/probe/new-evidence.json"], check=True
    )
    monkeypatch.setattr("probes.run_demo.ROOT", repo)
    monkeypatch.setattr(
        "probes.run_demo._pi_version",
        lambda: pytest.fail("守卫拒绝时不得启动 pi"),
    )
    state = tmp_path / "run-state"
    assert main(["--state", str(state), "--out", str(tracked)]) == 2
    assert tracked.read_text(encoding="utf-8") == "{}"
    assert not state.exists()


def test_legal_output_paths_resolve_and_pass(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = ROOT / "web/data/probe/state.json"
    run_out = ROOT / "web/data/probe/run.json"
    assert _check_paths(state, out, run_out) is None
    args = _parse_args(["--state", str(state), "--out", "web/data/probe/state.json"])
    resolved_state, resolved_out, resolved_run = _resolve_paths(args)
    assert resolved_state == state.resolve()
    assert resolved_out == out.resolve()
    assert resolved_run == run_out.resolve()


def test_replay_snapshot_revision_matches_content() -> None:
    import json

    from molt.core.hash import content_hash

    path = ROOT / "probes/report/rehearsal/run1/state-replay.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    payload = {key: value for key, value in snapshot.items() if key != "revision"}
    assert snapshot["revision"] == content_hash(payload)
    assert snapshot["live"] is False
    baseline = subprocess.run(
        ["git", "-C", str(ROOT), "show", "HEAD:probes/report/rehearsal/run1/state-replay.json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if baseline.returncode == 0:
        previous = json.loads(baseline.stdout)
        assert payload == {key: value for key, value in previous.items() if key != "revision"}


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


# ---------------------------------------------------------------------------
# 本轮 memory 快照 / 绑定 / 边界检查（batch 3）
# ---------------------------------------------------------------------------


def _memory_repo(tmp_path: Path, monkeypatch, data: bytes) -> Path:
    repo = tmp_path / "repo"
    (repo / "shells/probe/memory").mkdir(parents=True, exist_ok=True)
    (repo / MEMORY_REF).write_bytes(data)
    (repo / "fake-marker.txt").write_bytes(b"marker")
    monkeypatch.setattr("probes.run_demo.ROOT", repo)
    return repo


def _make_demo(tmp_path: Path, *, state_dir: Path | None = None) -> Demo:
    return Demo(
        state_dir=state_dir or (tmp_path / "state"),
        out=tmp_path / "web/state.json",
        run_out=tmp_path / "web/run.json",
        max_wall=360,
        max_cost=1.2,
        model="deepseek/deepseek-v4-pro",
        profile="fast",
    )


def _task_entry(task_id: str, *, passed: bool = True) -> dict:
    return {
        "id": task_id,
        "run_id": f"run-{task_id}",
        "function": "dedupe",
        "signature": "(names) -> list[str]",
        "passed": passed,
        "passes": 1 if passed else 0,
        "runs": 1,
        "cases": [],
        "code_before": "",
        "code_after": "",
        "diff_lines": [],
        "_cost_usd": 0.0,
        "_wall_ms": 1,
        "_outcome": "passed" if passed else "failed",
    }


def _real_schema_before_root_patch() -> dict:
    return _state_schema()


def _fake_run1_genome() -> Genome:
    payload = json.loads(
        (ROOT / "shells/probe/genomes/v0-pro-max.json").read_text(encoding="utf-8")
    )
    payload["artifacts"] = {
        "fake-marker.txt": hashlib.sha256(b"marker").hexdigest()
    }
    payload["genome_id"] = compute_genome_id(payload)
    return Genome.from_dict(payload)


@pytest.fixture()
def real_state_schema() -> dict:
    return _state_schema()


def _bind_memory_for_test(
    tmp_path: Path, monkeypatch, lines: list[str], schema: dict
) -> tuple[Demo, Path, str]:
    data = _memory_bytes(lines)
    repo = _memory_repo(tmp_path, monkeypatch, data)
    monkeypatch.setattr("probes.run_demo._state_schema", lambda: schema)
    demo = _make_demo(tmp_path)
    ref, digest = _publish_memory(demo, lines)
    return demo, repo, digest


# -- D1：内容寻址快照发布 --


def test_memory_snapshot_two_rounds_coexist(tmp_path: Path, monkeypatch) -> None:
    data_a = _memory_bytes(["规则 A"])
    repo = _memory_repo(tmp_path, monkeypatch, data_a)
    state = tmp_path / "state"
    demo_a = _make_demo(tmp_path, state_dir=state)
    ref_a, sha_a = _publish_memory(demo_a, ["规则 A"])
    demo_b = _make_demo(tmp_path, state_dir=state)
    ref_b, sha_b = _publish_memory(demo_b, ["规则 B"])

    assert sha_a != sha_b
    assert ref_a == f"{MEMORY_SNAPSHOT_DIR}/run2-memory-{sha_a}.md"
    assert ref_b == f"{MEMORY_SNAPSHOT_DIR}/run2-memory-{sha_b}.md"
    assert (state / ref_a).read_bytes() == data_a
    assert (state / ref_b).read_bytes() == _memory_bytes(["规则 B"])
    assert (repo / MEMORY_REF).read_bytes() == _memory_bytes(["规则 B"])
    assert sorted(path.name for path in (state / MEMORY_SNAPSHOT_DIR).iterdir()) == sorted(
        [(state / ref_a).name, (state / ref_b).name]
    )
    assert demo_a.memory_sha256 == sha_a
    assert demo_b.memory_sha256 == sha_b
    assert demo_a.memory["snapshot"] == ref_a


def test_memory_snapshot_same_content_reuses_path(
    tmp_path: Path, monkeypatch
) -> None:
    data = _memory_bytes(["same rule"])
    _memory_repo(tmp_path, monkeypatch, data)
    state = tmp_path / "state"
    demo_a = _make_demo(tmp_path, state_dir=state)
    ref_a, sha_a = _publish_memory(demo_a, ["same rule"])
    demo_b = _make_demo(tmp_path, state_dir=state)
    ref_b, sha_b = _publish_memory(demo_b, ["same rule"])

    assert (ref_a, sha_a) == (ref_b, sha_b)
    assert (state / ref_a).read_bytes() == data
    snapshots = list((state / MEMORY_SNAPSHOT_DIR).glob("run2-memory-*.md"))
    assert len(snapshots) == 1


def test_memory_snapshot_existing_different_bytes_refused(
    tmp_path: Path,
) -> None:
    data = _memory_bytes(["expected"])
    digest = hashlib.sha256(data).hexdigest()
    state = tmp_path / "state"
    target = state / MEMORY_SNAPSHOT_DIR / f"run2-memory-{digest}.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"collision or half write")

    with pytest.raises(DemoStop) as excinfo:
        _publish_memory_snapshot(data, state)
    assert "字节不同" in str(excinfo.value)
    assert target.read_bytes() == b"collision or half write"


def test_memory_snapshot_rejects_symlink_directory_and_hardlink(
    tmp_path: Path,
) -> None:
    data = _memory_bytes(["expected"])
    digest = hashlib.sha256(data).hexdigest()
    state = tmp_path / "state"
    directory = state / MEMORY_SNAPSHOT_DIR
    directory.mkdir(parents=True)
    target = directory / f"run2-memory-{digest}.md"

    target.symlink_to(tmp_path / "elsewhere.md")
    with pytest.raises(DemoStop) as excinfo:
        _publish_memory_snapshot(data, state)
    assert "symlink" in str(excinfo.value)
    target.unlink()

    target.mkdir()
    with pytest.raises(DemoStop) as excinfo:
        _publish_memory_snapshot(data, state)
    assert "不是常规文件" in str(excinfo.value)
    target.rmdir()

    real = tmp_path / "real.md"
    real.write_bytes(data)
    os.link(real, target)
    with pytest.raises(DemoStop) as excinfo:
        _publish_memory_snapshot(data, state)
    assert "硬链接" in str(excinfo.value)


def test_memory_snapshot_ignores_leftover_tmp(tmp_path: Path) -> None:
    data = _memory_bytes(["rule"])
    digest = hashlib.sha256(data).hexdigest()
    state = tmp_path / "state"
    directory = state / MEMORY_SNAPSHOT_DIR
    directory.mkdir(parents=True)
    leftover = directory / f".run2-memory-{digest}.md.dead.tmp"
    leftover.write_bytes(b"partial junk")

    ref, sha = _publish_memory_snapshot(data, state)
    demo = _make_demo(tmp_path)
    demo.bind_memory(ref, sha)
    assert demo.memory_snapshot == ref
    assert not demo.memory_snapshot.endswith(".tmp")
    assert leftover.read_bytes() == b"partial junk"
    assert (state / ref).read_bytes() == data


def test_memory_snapshot_replace_failure_has_no_binding(
    tmp_path: Path, monkeypatch
) -> None:
    data = _memory_bytes(["rule"])
    repo = _memory_repo(tmp_path, monkeypatch, data)
    demo = _make_demo(tmp_path)
    real_replace = os.replace

    def boom(*args, **kwargs):
        raise OSError("replace failed")

    os.replace = boom
    try:
        with pytest.raises(DemoStop) as excinfo:
            _publish_memory(demo, ["rule"])
    finally:
        os.replace = real_replace
    assert "原子发布失败" in str(excinfo.value)
    assert demo.memory_sha256 is None
    assert demo.memory["sha256"] is None
    assert demo.memory["snapshot"] is None
    assert not (demo.state_dir / MEMORY_SNAPSHOT_DIR).exists() or not list(
        (demo.state_dir / MEMORY_SNAPSHOT_DIR).glob("*.md")
    )
    assert (repo / MEMORY_REF).read_bytes() == data


def test_memory_snapshot_published_but_shared_failure_keeps_binding(
    tmp_path: Path, monkeypatch
) -> None:
    data = _memory_bytes(["rule"])
    _memory_repo(tmp_path, monkeypatch, data)
    demo = _make_demo(tmp_path)

    def boom(payload: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("probes.run_demo._write_shared_memory", boom)
    with pytest.raises(DemoStop) as excinfo:
        _publish_memory(demo, ["rule"])
    assert "快照已发布" in str(excinfo.value)
    digest = hashlib.sha256(data).hexdigest()
    assert demo.memory_sha256 == digest
    assert demo.state_document()["memory"]["sha256"] == digest
    assert demo.run_document("partial", "shared failure")["memory_sha256"] == digest
    assert demo.run_document("timeout", "wall limit")["memory_sha256"] == digest


# -- D2：绑定、state/run 字段与真实 schema --


def test_unbound_run_and_state_are_null_and_validate(
    tmp_path: Path, real_state_schema
) -> None:
    demo = _make_demo(tmp_path)
    demo.write_state()
    demo.write_run("partial", "early stop")

    state_doc = json.loads(demo.out.read_text(encoding="utf-8"))
    run_doc = json.loads(demo.run_out.read_text(encoding="utf-8"))
    assert state_doc["memory"]["snapshot"] is None
    assert state_doc["memory"]["sha256"] is None
    assert run_doc["memory_sha256"] is None
    assert set(run_doc) == set(RUN_SCHEMA["required"])
    assert run_doc["memory_ref"] == MEMORY_REF
    _validate(state_doc, real_state_schema, "state.json")
    _validate(run_doc, RUN_SCHEMA, "run.json")
    demo.write_run("timeout", "wall limit")
    assert json.loads(demo.run_out.read_text(encoding="utf-8"))["memory_sha256"] is None
    assert _validate_outputs(demo.state_dir, demo.out, demo.run_out) == 0
    assert (
        main(
            [
                "--validate",
                "--state",
                str(demo.state_dir),
                "--out",
                str(demo.out),
                "--run-out",
                str(demo.run_out),
            ]
        )
        == 0
    )


def test_bound_run_and_state_validate(tmp_path: Path, monkeypatch) -> None:
    schema = _real_schema_before_root_patch()
    data = _memory_bytes(["rule"])
    _memory_repo(tmp_path, monkeypatch, data)
    monkeypatch.setattr("probes.run_demo._state_schema", lambda: schema)
    assert run_demo._state_schema() is schema
    assert schema == json.loads(
        (ROOT / "probes/report/probe_state_schema.json").read_text(encoding="utf-8")
    )
    demo = _make_demo(tmp_path)
    ref, digest = _publish_memory(demo, ["rule"])
    demo.memory["lines"] = ["rule"]
    demo.write_state()
    demo.write_run("done", "")

    state_doc = json.loads(demo.out.read_text(encoding="utf-8"))
    run_doc = json.loads(demo.run_out.read_text(encoding="utf-8"))
    assert state_doc["memory"]["snapshot"] == ref
    assert state_doc["memory"]["sha256"] == digest
    assert run_doc["memory_sha256"] == digest
    assert run_doc["memory_ref"] == MEMORY_REF
    _validate(state_doc, schema, "state.json")
    _validate(run_doc, RUN_SCHEMA, "run.json")
    assert _validate_outputs(demo.state_dir, demo.out, demo.run_out) == 0


def test_binding_survives_shared_deletion_and_new_demo_is_unbound(
    tmp_path: Path, monkeypatch
) -> None:
    data = _memory_bytes(["rule"])
    repo = _memory_repo(tmp_path, monkeypatch, data)
    demo = _make_demo(tmp_path)
    _, digest = _publish_memory(demo, ["rule"])
    (repo / MEMORY_REF).unlink()

    assert demo.run_document("done", "")["memory_sha256"] == digest
    assert demo.state_document()["memory"]["sha256"] == digest

    fresh = _make_demo(tmp_path, state_dir=demo.state_dir)
    assert fresh.memory_snapshot is None and fresh.memory_sha256 is None
    assert fresh.run_document("partial", "fresh")["memory_sha256"] is None
    assert fresh.state_document()["memory"]["snapshot"] is None


def test_reused_state_overwrites_run_json_but_keeps_snapshots(
    tmp_path: Path, monkeypatch
) -> None:
    data_a = _memory_bytes(["round one"])
    _memory_repo(tmp_path, monkeypatch, data_a)
    state = tmp_path / "state"
    out = tmp_path / "web/state.json"
    run_out = tmp_path / "web/run.json"
    demo_a = Demo(
        state_dir=state,
        out=out,
        run_out=run_out,
        max_wall=360,
        max_cost=1.2,
        model="m",
        profile="fast",
    )
    _, sha_a = _publish_memory(demo_a, ["round one"])
    demo_a.write_run("done", "")
    first_run = run_out.read_bytes()

    demo_b = Demo(
        state_dir=state,
        out=out,
        run_out=run_out,
        max_wall=360,
        max_cost=1.2,
        model="m",
        profile="fast",
    )
    _, sha_b = _publish_memory(demo_b, ["round two"])
    demo_b.write_run("done", "")

    assert sha_a != sha_b
    assert first_run != run_out.read_bytes()
    assert json.loads(run_out.read_text(encoding="utf-8"))["memory_sha256"] == sha_b
    snapshots = {
        path.name for path in (state / MEMORY_SNAPSHOT_DIR).glob("run2-memory-*.md")
    }
    assert snapshots == {
        f"run2-memory-{sha_a}.md",
        f"run2-memory-{sha_b}.md",
    }
    assert "归档由调用方负责" in (run_demo.__doc__ or "")


# -- D3：run2 完整性检查与执行出口 --


def test_prepare_run2_genome_uses_bound_hash_and_verifies(
    tmp_path: Path, monkeypatch
) -> None:
    data = _memory_bytes(["rule"])
    repo = _memory_repo(tmp_path, monkeypatch, data)
    demo = _make_demo(tmp_path)
    ref, digest = _publish_memory(demo, ["rule"])
    genome_path = demo.state_dir / "genomes/run2.json"

    genome = _prepare_run2_genome(
        _fake_run1_genome(),
        "m",
        genome_path,
        memory_snapshot=demo.state_dir / ref,
        memory_sha256=digest,
    )
    payload = json.loads(genome_path.read_text(encoding="utf-8"))
    assert genome.artifacts[MEMORY_REF] == digest
    assert payload["ext"]["memory_sha256"] == digest
    assert payload["ext"]["memory_snapshot"] == ref
    from molt.core.genome import verify_genome

    assert verify_genome(genome, repo) == []


def test_prepare_run2_genome_rejects_unbound_without_building(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        "molt.core.genome.child_genome", lambda *args, **kwargs: calls.append(1)
    )
    with pytest.raises(DemoStop) as excinfo:
        _prepare_run2_genome(
            _fake_run1_genome(),
            "m",
            tmp_path / "state/genomes/run2.json",
            memory_snapshot=None,
            memory_sha256=None,
        )
    assert "未绑定" in str(excinfo.value)
    assert calls == []


def test_prepare_run2_genome_rejects_tampered_shared_or_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    data = _memory_bytes(["rule"])
    repo = _memory_repo(tmp_path, monkeypatch, data)
    demo = _make_demo(tmp_path)
    ref, digest = _publish_memory(demo, ["rule"])
    genome_path = demo.state_dir / "genomes/run2.json"
    snapshot = demo.state_dir / ref

    (repo / MEMORY_REF).write_bytes(b"drifted shared")
    with pytest.raises(DemoStop) as excinfo:
        _prepare_run2_genome(
            _fake_run1_genome(),
            "m",
            genome_path,
            memory_snapshot=snapshot,
            memory_sha256=digest,
        )
    assert "共享 memory hash 不匹配" in str(excinfo.value)

    (repo / MEMORY_REF).write_bytes(data)
    snapshot.write_bytes(b"drifted snapshot")
    with pytest.raises(DemoStop) as excinfo:
        _prepare_run2_genome(
            _fake_run1_genome(),
            "m",
            genome_path,
            memory_snapshot=snapshot,
            memory_sha256=digest,
        )
    assert "快照 hash 不匹配" in str(excinfo.value)

    snapshot.unlink()
    with pytest.raises(DemoStop) as excinfo:
        _prepare_run2_genome(
            _fake_run1_genome(),
            "m",
            genome_path,
            memory_snapshot=snapshot,
            memory_sha256=digest,
        )
    assert "快照不存在" in str(excinfo.value)


def test_prepare_run2_genome_keeps_verify_genome_check(
    tmp_path: Path, monkeypatch
) -> None:
    data = _memory_bytes(["rule"])
    _memory_repo(tmp_path, monkeypatch, data)
    demo = _make_demo(tmp_path)
    ref, digest = _publish_memory(demo, ["rule"])
    calls: list[int] = []

    def fake_verify(genome, repo_root):
        calls.append(1)
        return ["forced artifact mismatch"]

    monkeypatch.setattr("molt.core.genome.verify_genome", fake_verify)
    with pytest.raises(DemoStop) as excinfo:
        _prepare_run2_genome(
            _fake_run1_genome(),
            "m",
            demo.state_dir / "genomes/run2.json",
            memory_snapshot=demo.state_dir / ref,
            memory_sha256=digest,
        )
    assert "run2 genome 校验失败" in str(excinfo.value)
    assert "forced artifact mismatch" in str(excinfo.value)
    assert calls == [1]


def test_run2_phase_success_appends_after_checks(
    tmp_path: Path, monkeypatch, real_state_schema
) -> None:
    demo, _, _ = _bind_memory_for_test(tmp_path, monkeypatch, ["rule"], real_state_schema)
    monkeypatch.setattr(
        "probes.run_demo._execute",
        lambda **kwargs: (_task_entry(kwargs["task_id"]), None),
    )
    _run_phase(demo, "run2", ("probe-order-x5",), tmp_path / "genome.json")
    assert [task["passed"] for task in demo.run2_tasks] == [True]
    state = json.loads(demo.out.read_text(encoding="utf-8"))
    assert state["run2"]["solved"] == 1
    _validate(state, real_state_schema, "state.json")


def test_run2_phase_normal_execute_plus_drift_is_diagnostic_only(
    tmp_path: Path, monkeypatch, real_state_schema
) -> None:
    demo, repo, _ = _bind_memory_for_test(
        tmp_path, monkeypatch, ["rule"], real_state_schema
    )

    def execute(**kwargs):
        (repo / MEMORY_REF).write_bytes(b"drifted")
        return _task_entry(kwargs["task_id"]), None

    monkeypatch.setattr("probes.run_demo._execute", execute)
    with pytest.raises(DemoStop) as excinfo:
        _run_phase(demo, "run2", ("probe-order-x5",), tmp_path / "genome.json")
    message = str(excinfo.value)
    assert "after检查失败" in message and "hash 漂移" in message

    task = demo.run2_tasks[0]
    assert task["passed"] is False
    assert task["_outcome"] == "memory_drift"
    assert demo._run_block(demo.run2_tasks, headroom=True)["solved"] == 0
    state = json.loads(demo.out.read_text(encoding="utf-8"))
    assert state["run2"]["tasks"][0]["passed"] is False
    assert state["run2"]["tasks"][0]["outcome"] == "memory_drift"
    assert "memory_drift" in state["run2"]["tasks"][0]["note"]
    _validate(state, real_state_schema, "state.json")
    _validate(demo.run_document("partial", "drift"), RUN_SCHEMA, "run.json")


def test_run2_phase_execute_exception_without_drift_keeps_error(
    tmp_path: Path, monkeypatch, real_state_schema
) -> None:
    demo, _, _ = _bind_memory_for_test(tmp_path, monkeypatch, ["rule"], real_state_schema)

    def execute(**kwargs):
        raise DemoStop("partial", "boom")

    monkeypatch.setattr("probes.run_demo._execute", execute)
    with pytest.raises(DemoStop) as excinfo:
        _run_phase(demo, "run2", ("probe-order-x5",), tmp_path / "genome.json")
    assert str(excinfo.value) == "boom"
    assert demo.run2_tasks == []


def test_run2_phase_execute_exception_plus_drift_keeps_both(
    tmp_path: Path, monkeypatch, real_state_schema
) -> None:
    demo, repo, _ = _bind_memory_for_test(
        tmp_path, monkeypatch, ["rule"], real_state_schema
    )

    def execute(**kwargs):
        (repo / MEMORY_REF).write_bytes(b"drifted")
        raise DemoStop("partial", "boom")

    monkeypatch.setattr("probes.run_demo._execute", execute)
    with pytest.raises(DemoStop) as excinfo:
        _run_phase(demo, "run2", ("probe-order-x5",), tmp_path / "genome.json")
    message = str(excinfo.value)
    assert "boom" in message
    assert "after检查失败" in message and "hash 漂移" in message
    assert "boom" in demo.note and "hash 漂移" in demo.note
    assert demo.run2_tasks == []
    state = json.loads(demo.out.read_text(encoding="utf-8"))
    assert state["memory"]["sha256"] == demo.memory_sha256


def test_run2_phase_after_check_read_error_is_integrity_failure(
    tmp_path: Path, monkeypatch, real_state_schema
) -> None:
    demo, _, _ = _bind_memory_for_test(tmp_path, monkeypatch, ["rule"], real_state_schema)
    monkeypatch.setattr(
        "probes.run_demo._execute",
        lambda **kwargs: (_task_entry(kwargs["task_id"]), None),
    )

    from molt.core.hash import sha256_file as real_sha256_file

    calls = {"count": 0}

    def flaky(path):
        calls["count"] += 1
        if calls["count"] > 2:  # before-check 的共享 + 快照两次读取正常
            raise OSError("disk gone")
        return real_sha256_file(path)

    monkeypatch.setattr("molt.core.hash.sha256_file", flaky)
    with pytest.raises(DemoStop) as excinfo:
        _run_phase(demo, "run2", ("probe-order-x5",), tmp_path / "genome.json")
    assert "不可读" in str(excinfo.value)
    assert demo.run2_tasks[0]["passed"] is False
    assert demo.out.exists()


def test_run2_phase_unbound_or_drift_before_check_skips_execute(
    tmp_path: Path, monkeypatch, real_state_schema
) -> None:
    unbound = _make_demo(tmp_path / "unbound")
    calls: list[str] = []

    def execute(**kwargs):
        calls.append(kwargs["task_id"])
        return _task_entry(kwargs["task_id"]), None

    monkeypatch.setattr("probes.run_demo._execute", execute)
    with pytest.raises(DemoStop) as excinfo:
        _run_phase(unbound, "run2", ("probe-order-x5",), tmp_path / "genome.json")
    assert "未绑定" in str(excinfo.value)
    assert calls == []

    demo, repo, _ = _bind_memory_for_test(
        tmp_path / "drift", monkeypatch, ["rule"], real_state_schema
    )
    (repo / MEMORY_REF).write_bytes(b"drifted before")
    with pytest.raises(DemoStop) as excinfo:
        _run_phase(demo, "run2", ("probe-order-x5",), tmp_path / "genome.json")
    assert "before检查失败" in str(excinfo.value)
    assert calls == []


def test_run2_phase_after_check_failure_does_not_run_next_task(
    tmp_path: Path, monkeypatch, real_state_schema
) -> None:
    demo, repo, _ = _bind_memory_for_test(
        tmp_path, monkeypatch, ["rule"], real_state_schema
    )
    calls: list[str] = []

    def execute(**kwargs):
        calls.append(kwargs["task_id"])
        (repo / MEMORY_REF).write_bytes(b"drifted")
        return _task_entry(kwargs["task_id"]), None

    monkeypatch.setattr("probes.run_demo._execute", execute)
    with pytest.raises(DemoStop):
        _run_phase(
            demo,
            "run2",
            ("probe-order-x5", "probe-order-x6"),
            tmp_path / "genome.json",
        )
    assert calls == ["probe-order-x5"]


def test_run1_phase_has_no_memory_boundary_check(
    tmp_path: Path, monkeypatch, real_state_schema
) -> None:
    demo = _make_demo(tmp_path)
    monkeypatch.setattr("probes.run_demo._state_schema", lambda: real_state_schema)
    calls: list[str] = []

    def execute(**kwargs):
        calls.append(kwargs["task_id"])
        return _task_entry(kwargs["task_id"]), None

    monkeypatch.setattr("probes.run_demo._execute", execute)
    _run_phase(demo, "run1", ("probe-order-2",), tmp_path / "genome.json")
    assert calls == ["probe-order-2"]
    assert demo.run1_tasks[0]["passed"] is True


def test_memory_integrity_comments_state_limits(
    tmp_path: Path, monkeypatch, real_state_schema
) -> None:
    demo, _, _ = _bind_memory_for_test(tmp_path, monkeypatch, ["rule"], real_state_schema)
    assert "单写者" in demo.note
    assert "不证明实际读取字节" in demo.note
    source = inspect.getsource(run_demo._run_phase)
    assert "单写者" in source
    assert "不证明 harness 实际读取的字节" in source
    assert "不构成完整性证明" in source
    assert "不消除 TOCTOU" in source


def test_bind_memory_is_one_shot(tmp_path: Path) -> None:
    demo = _make_demo(tmp_path)
    demo.bind_memory("memory/run2-memory-a.md", "a" * 64)
    with pytest.raises(DemoStop) as excinfo:
        demo.bind_memory("memory/run2-memory-b.md", "b" * 64)
    assert "二次绑定" in str(excinfo.value)
    assert demo.memory_sha256 == "a" * 64
    assert demo.memory["snapshot"] == "memory/run2-memory-a.md"


def test_both_memory_branches_use_the_publish_entry() -> None:
    source = inspect.getsource(run_demo.main)
    assert source.count("_publish_memory(") == 2
    assert "_write_memory(" not in source


def test_run2_phase_check_order_is_before_execute_after(
    tmp_path: Path, monkeypatch, real_state_schema
) -> None:
    demo, _, _ = _bind_memory_for_test(tmp_path, monkeypatch, ["rule"], real_state_schema)
    events: list[str] = []
    real_check = run_demo._check_memory_boundary

    def check_spy(demo_arg, phase, task_id, boundary):
        events.append(boundary)
        real_check(demo_arg, phase, task_id, boundary)

    def execute(**kwargs):
        events.append("execute")
        return _task_entry(kwargs["task_id"]), None

    monkeypatch.setattr("probes.run_demo._check_memory_boundary", check_spy)
    monkeypatch.setattr("probes.run_demo._execute", execute)
    _run_phase(demo, "run2", ("probe-order-x5",), tmp_path / "genome.json")
    assert events == ["before", "execute", "after"]
