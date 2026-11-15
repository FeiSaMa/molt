"""B2d-0 ``molt export`` 契约：15 个文件、holdout 聚合、provenance 三档、零写入。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from molt.core.store import StateStore
from molt.core.types import Run
from molt.export import AGGREGATE_KEYS, DATA_FILES, ExportError, export_web_data

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = set(DATA_FILES)
EXCLUDED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
HOLDOUT_IDS = (
    "holdout-even",
    "holdout-palindrome",
    "holdout-gcd",
    "holdout-factorial",
    "holdout-dedupe",
)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _files(out: Path) -> set[str]:
    return {str(path.relative_to(out)) for path in out.rglob("*") if path.is_file()}


def _tree_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.is_dir():
            snapshot[str(relative)] = "dir"
        elif path.suffix != ".pyc":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot[str(relative)] = f"file:{path.stat().st_size}:{digest}"
    return snapshot


@pytest.fixture()
def exported(demo_artifacts, tmp_path: Path):
    store, scenario, _ = demo_artifacts
    out = tmp_path / "web"
    index = export_web_data(ROOT, scenario, store, out)
    return out, index, scenario


def test_file_set_is_exactly_the_contract(exported) -> None:
    out, index, scenario = exported
    assert _files(out) == EXPECTED
    assert index["scenario_id"] == scenario.scenario_id
    assert len(index["revision"]) == 64
    assert all(ch in "0123456789abcdef" for ch in index["revision"])
    assert set(index["files"]) == EXPECTED - {"index.json"}
    assert index["counts"]["runs"] > 0
    assert index["counts"]["report_runs"] > 0
    assert index["counts"]["events_search"] > 0
    scenario_data = _load(out / "scenario.json")
    assert scenario_data["claim"]["live_model_calls"] is False
    assert scenario_data["not_claimed"]
    counts = scenario_data["task_counts"]
    assert counts["total"] == counts["search"] + counts["report"]
    assert counts["search"] == 10
    assert counts["report"] == 5
    assert "report" not in scenario_data["task_sets"]
    assert scenario_data["holdout"] == {"task_count": counts["report"], "detail": "not-exported"}
    assert scenario_data["budget"]["execution_budget_usd"] is not None
    assert scenario_data["isolation"]["network"] == "provider_only"


def test_revision_is_stable_for_same_state(demo_artifacts, exported, tmp_path: Path) -> None:
    out, index, _ = exported
    store, scenario, _ = demo_artifacts
    again = export_web_data(ROOT, scenario, store, tmp_path / "web-again")
    assert again["revision"] == index["revision"]
    assert (tmp_path / "web-again" / "bundle.js").read_text(encoding="utf-8") == (
        out / "bundle.js"
    ).read_text(encoding="utf-8")


def test_bundle_js_is_valid_javascript(exported) -> None:
    out, _, _ = exported
    text = (out / "bundle.js").read_text(encoding="utf-8")
    assert "window.MOLT_VIEW =" in text
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 不可用，跳过语法检查")
    completed = subprocess.run(
        [node, "--check", str(out / "bundle.js")], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr


def test_search_results_have_no_holdout(exported) -> None:
    out, _, _ = exported
    data = _load(out / "search_results.json")
    assert data["scope"] == "search"
    assert data["results"]
    assert all(item["scope"] == "search" for item in data["results"])
    assert "holdout" not in (out / "search_results.json").read_text(encoding="utf-8")


def test_report_results_holdout_is_aggregate_only(exported) -> None:
    out, _, _ = exported
    data = _load(out / "report_results.json")
    assert data["results"]
    for item in data["results"]:
        assert set(item["scores"]) == {"search", "holdout"}
        assert set(item["scores"]["holdout"]) <= set(AGGREGATE_KEYS)
        for detail in ("tasks", "task_id", "run_refs"):
            assert detail not in item["scores"]["holdout"]
        report_runs = item["report_runs"]
        assert set(report_runs) == {
            "count",
            "solved",
            "by_status",
            "cost_usd",
            "wall_ms",
            "tokens",
        }
        assert report_runs["count"] > 0
        assert sum(report_runs["by_status"].values()) == report_runs["count"]
    assert data["report_runs_total"]["count"] == 10


def test_holdout_detail_never_appears_in_export(exported) -> None:
    out, _, _ = exported
    hits = [
        str(path.relative_to(out))
        for path in out.rglob("*")
        if path.is_file()
        and any(task_id in path.read_text(encoding="utf-8") for task_id in HOLDOUT_IDS)
    ]
    assert hits == [], f"holdout 逐题 id 泄漏: {hits}"
    report_events = [
        json.loads(line)
        for line in (out / "events" / "report.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    accessed = [event for event in report_events if event["type"] == "holdout.accessed"]
    assert accessed
    for event in accessed:
        assert event["run_id"] is None
        assert "task_ids" not in event["payload"]
        assert event["payload"]["count"] > 0


def test_runs_provenance_three_tiers(demo_copy, tmp_path: Path) -> None:
    store, scenario, _ = demo_copy
    runs = [run for run in store.list_runs() if run.scope == "search"]
    assert len(runs) >= 3
    for target, ext in zip(
        runs[:2],
        (
            {"provenance": {"kind": "live", "model": "pi"}},
            {"replay_of_run_id": runs[2].run_id},
        ),
        strict=False,
    ):
        data = target.to_dict()
        data["ext"] = ext
        store.write_run(Run.from_dict(data))

    out = tmp_path / "web"
    export_web_data(ROOT, scenario, store, out)
    rows = _load(out / "runs.json")["runs"]
    assert {row["scope"] for row in rows} == {"search"}
    kinds = {row["provenance"]["kind"] for row in rows}
    assert kinds == {"fixture", "live", "replay"}
    for row in rows:
        provenance = row["provenance"]
        assert provenance["live"] is (provenance["kind"] == "live")
        if provenance["kind"] == "fixture":
            assert "不是 live" in provenance["note"]
        if provenance["kind"] == "replay":
            assert provenance["replay_of_run_id"]


def _set_run_ext(store: StateStore, run: Run, ext: dict) -> None:
    data = run.to_dict()
    data["ext"] = ext
    store.write_run(Run.from_dict(data))


def test_live_with_replay_marker_fails_closed(demo_copy, tmp_path: Path) -> None:
    store, scenario, _ = demo_copy
    runs = [run for run in store.list_runs() if run.scope == "search"]
    _set_run_ext(
        store,
        runs[0],
        {"provenance": {"kind": "live", "model": "pi"}, "replay_of_run_id": runs[1].run_id},
    )
    with pytest.raises(ExportError, match="冲突"):
        export_web_data(ROOT, scenario, store, tmp_path / "web")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "molt",
            "export",
            "--scenario",
            "demo",
            "--state",
            str(store.root),
            "--out",
            str(tmp_path / "cli-web"),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    assert completed.returncode != 0
    assert "冲突" in completed.stderr


def test_replay_without_source_fails_closed(demo_copy, tmp_path: Path) -> None:
    store, scenario, _ = demo_copy
    run = next(item for item in store.list_runs() if item.scope == "search")
    _set_run_ext(store, run, {"provenance": {"kind": "replay"}})
    with pytest.raises(ExportError, match="replay_of_run_id"):
        export_web_data(ROOT, scenario, store, tmp_path / "web")


def test_live_without_evidence_fails_closed(demo_copy, tmp_path: Path) -> None:
    store, scenario, _ = demo_copy
    run = next(item for item in store.list_runs() if item.scope == "search")
    _set_run_ext(store, run, {"provenance": {"kind": "live"}})
    with pytest.raises(ExportError, match="缺少 model"):
        export_web_data(ROOT, scenario, store, tmp_path / "web")


def test_live_with_model_is_accepted(demo_copy, tmp_path: Path) -> None:
    store, scenario, _ = demo_copy
    run = next(item for item in store.list_runs() if item.scope == "search")
    _set_run_ext(store, run, {"provenance": {"kind": "live", "model": "pi-live"}})
    out = tmp_path / "web"
    export_web_data(ROOT, scenario, store, out)
    rows = _load(out / "runs.json")["runs"]
    live = [row for row in rows if row["provenance"]["kind"] == "live"]
    assert live and live[0]["provenance"]["live"] is True


def test_empty_state_requires_allow_empty(tmp_path: Path) -> None:
    state = tmp_path / "state"
    StateStore(state).ensure()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    base = [
        sys.executable,
        "-m",
        "molt",
        "export",
        "--scenario",
        "demo",
        "--state",
        str(state),
    ]
    completed = subprocess.run(
        [*base, "--out", str(tmp_path / "blocked")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 2
    assert "--allow-empty" in completed.stderr
    assert not (tmp_path / "blocked" / "index.json").is_file()

    allowed = subprocess.run(
        [*base, "--allow-empty", "--out", str(tmp_path / "empty-web")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert allowed.returncode == 0, allowed.stderr
    assert _files(tmp_path / "empty-web") == EXPECTED


def test_proposals_keep_top_level_and_ext(exported) -> None:
    out, _, _ = exported
    proposals = _load(out / "proposals.json")["proposals"]
    assert proposals
    proposal = proposals[0]
    for key in ("proposal_id", "scenario_version", "proposer_id", "parent_id", "switches"):
        assert key in proposal
    for key in ("source", "fallback", "cost_source", "usage", "prompt_sha256", "response_sha256"):
        assert key in proposal["ext"]
    assert proposal["ext"]["fallback"]["used"] is False


def test_gate_audit_and_redaction_are_present(exported) -> None:
    out, index, _ = exported
    gate = _load(out / "gate.json")
    assert gate["decisions"]
    decision = gate["decisions"][0]
    for key in ("verdict", "reason", "reasons", "pairs", "deltas", "scores", "budget_check"):
        assert key in decision
    assert decision["pairs"]
    assert len(gate["policy_sha256"]) == 64

    audit = _load(out / "audit.json")
    assert audit["ok"] is True
    assert audit["mismatches"] == []
    assert audit["checked"]["runs"] == (
        len(_load(out / "runs.json")["runs"]) + index["counts"]["report_runs"]
    )

    redaction = _load(out / "redaction.json")
    assert redaction is not None
    assert redaction["redaction_version"] == "1"
    assert "pre_metrics" in redaction

    assert _load(out / "archive.json")["archives"] == []
    assert _load(out / "snapshots.json")["snapshots"] == []


def test_events_are_sorted_and_scoped(exported) -> None:
    out, _, _ = exported
    for scope in ("search", "report"):
        lines = [
            line
            for line in (out / "events" / f"{scope}.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert lines
        events = [json.loads(line) for line in lines]
        assert all(event["scope"] == scope for event in events)
        assert [(event["ts"], event["event_id"]) for event in events] == sorted(
            (event["ts"], event["event_id"]) for event in events
        )


def test_cli_export_writes_nothing_inside_repository(tmp_path: Path) -> None:
    before = _tree_snapshot(ROOT)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    out = tmp_path / "web-data"
    completed = subprocess.run(
        [sys.executable, "-m", "molt", "export", "--scenario", "demo", "--out", str(out)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert out.joinpath("index.json").is_file()
    after = _tree_snapshot(ROOT)
    assert after == before, "molt export 不该改动仓库内任何文件"


def test_committed_sample_and_readme_are_usable() -> None:
    sample = ROOT / "web" / "data"
    # `web/data/probe/` 是独立的演示数据面（probes/report/VISUAL_SPEC.md），
    # 不属于 Tier 1 的 15 文件契约；导出层仍然只写这 15 个文件。
    committed = {
        path for path in _files(sample) if not path.startswith("probe/")
    }
    assert committed == EXPECTED
    index = _load(sample / "index.json")
    assert set(index["files"]) == EXPECTED - {"index.json"}
    for name, meta in index["files"].items():
        actual = hashlib.sha256((sample / name).read_bytes()).hexdigest()
        assert actual == meta["sha256"], f"{name} 与 index.json 记录不一致"
    assert "window.MOLT_VIEW =" in (sample / "bundle.js").read_text(encoding="utf-8")
    leaked = [
        str(path.relative_to(sample))
        for path in sample.rglob("*")
        if path.is_file()
        and any(task_id in path.read_text(encoding="utf-8") for task_id in HOLDOUT_IDS)
    ]
    assert leaked == []
    assert index["counts"]["report_runs"] == 10

    readme = (ROOT / "web" / "README.md").read_text(encoding="utf-8")
    for needle in ("molt export", "index.json", "aggregate", "holdout", "fixture"):
        assert needle in readme


def test_export_accepts_state_path(demo_artifacts, tmp_path: Path) -> None:
    store, scenario, _ = demo_artifacts
    out = tmp_path / "web"
    index = export_web_data(ROOT, scenario, Path(store.root), out)
    search_runs = [run for run in StateStore(store.root).list_runs() if run.scope == "search"]
    assert index["counts"]["runs"] == len(search_runs)
    assert index["counts"]["report_runs"] == 10
    assert (out / "variants.json").is_file()
