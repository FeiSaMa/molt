"""Probe 投影契约测试：冻结值、闭集、哈希、生成器确定性与映射文档。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from molt.agent.probe import (
    CAVEATS,
    build_index,
    compute_revision,
    load_index,
    validate_index,
)

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "probes" / "build_probe_experiments.py"
MANIFEST = ROOT / "probes" / "probe_experiments_manifest.json"
MAPPING = ROOT / "contracts" / "examples" / "agent-sample-mapping.md"

EXPECTED_IDS = [
    "matrix-core-v3",
    "validity-green",
    "2c-main",
    "2c-memory-v2",
    "2c-memory-v3",
    "rehearsal-run1",
    "rehearsal-run2",
    "batch1",
]

# 冻结值（与冻结方案表格逐列对应）：task_scope=(count,reps,cells)
FROZEN = {
    "matrix-core-v3": dict(
        title="模型与推理档位矩阵",
        study_kind="configuration_matrix",
        stage="scan",
        status="completed",
        kind="live",
        basis="runtime_record",
        coverage=(36, 36),
        version_fields=(None, None, None),
        scope=(2, 2, 4),
        metrics=(36, 0, 16, 0.0, "run"),
        cost=(0.99972634, "all_records", True, False, False, True),
        wall=(2240986, "sum_rows", False),
        comparison=dict(
            baseline=None,
            control=(16, 16, 8, 8),
            oracle=(4, 4, 2, 2),
        ),
        rows_count=36,
        verdict_runs_count=None,
        completeness={"rows": "present", "verdict": "present", "reflection": "absent", "traces": "absent"},
        caveats=[
            "missing_scenario_version",
            "missing_shell_version",
            "missing_verifier_hash",
            "cost_estimated",
            "function_check_violations",
        ],
    ),
    "validity-green": dict(
        title="初始题族有效性检查",
        study_kind="validity_check",
        stage="validation",
        status="completed",
        kind="live",
        basis="runtime_record",
        coverage=(16, 16),
        version_fields=("probe-1", None, None),
        scope=(2, 4, 1),
        metrics=(16, 0, 8, 0.0, "run"),
        cost=(0.79813552, "all_records", True, False, False, True),
        wall=(1694232, "sum_rows", False),
        comparison=dict(
            baseline=None,
            control=(4, 4, 2, 2),
            oracle=(4, 4, 2, 2),
        ),
        rows_count=16,
        verdict_runs_count=None,
        completeness={"rows": "present", "verdict": "present", "reflection": "absent", "traces": "absent"},
        caveats=["missing_shell_version", "missing_verifier_hash"],
    ),
    "2c-main": dict(
        title="2c 主实验",
        study_kind="memory_transfer",
        stage="gate",
        status="completed",
        kind="live",
        basis="runtime_record",
        coverage=(20, 20),
        version_fields=(
            "probe-4",
            "0.2.0",
            "b194ce321b416674ada40f5b00980cc489b7deeff8a59c0ff6e1d3573e809521",
        ),
        scope=(4, 2, 1),
        metrics=(20, 3, 4, 0.75, "task"),
        cost=(2.30024424, "main_plus_control", True, False, False, True),
        wall=(4693178, "sum_main_plus_control_rows", False),
        comparison=dict(
            baseline=(8, 2, 4, 1),
            control=(4, 4, 2, 2),
            oracle=None,
        ),
        rows_count=20,
        verdict_runs_count=None,
        completeness={"rows": "present", "verdict": "present", "reflection": "absent", "traces": "absent"},
        caveats=["cost_includes_control", "budget_stopped", "function_check_violations"],
    ),
    "2c-memory-v2": dict(
        title="Memory v2 反思迁移",
        study_kind="memory_transfer",
        stage="reflection",
        status="completed",
        kind="live",
        basis="runtime_record",
        coverage=(6, 12),
        version_fields=(None, None, None),
        scope=(4, None, 1),
        metrics=(12, 3, 4, 0.75, "task"),
        cost=(0.53273915, "main", False, True, False, False),
        wall=(678622, "sum_rows", False),
        comparison=dict(baseline=None, control=None, oracle=None),
        rows_count=12,
        verdict_runs_count=6,
        completeness={"rows": "partial", "verdict": "present", "reflection": "present", "traces": "absent"},
        caveats=[
            "missing_scenario_version",
            "missing_shell_version",
            "missing_verifier_hash",
            "partial_provenance",
            "cost_includes_reflection",
            "wall_excludes_reflection",
            "rows_verdict_count_differs",
        ],
    ),
    "2c-memory-v3": dict(
        title="Memory v3 约束反思迁移",
        study_kind="memory_transfer",
        stage="reflection",
        status="completed",
        kind="live",
        basis="runtime_record",
        coverage=(10, 18),
        version_fields=(None, None, None),
        scope=(4, 4, 1),
        metrics=(18, 4, 4, 1.0, "task"),
        cost=(0.4589434, "main", False, True, False, False),
        wall=(560946, "sum_rows", False),
        comparison=dict(baseline=None, control=None, oracle=None),
        rows_count=18,
        verdict_runs_count=10,
        completeness={"rows": "partial", "verdict": "present", "reflection": "present", "traces": "absent"},
        caveats=[
            "missing_scenario_version",
            "missing_shell_version",
            "missing_verifier_hash",
            "partial_provenance",
            "cost_includes_reflection",
            "wall_excludes_reflection",
            "rows_verdict_count_differs",
        ],
    ),
    "rehearsal-run1": dict(
        title="现场彩排 run1",
        study_kind="rehearsal",
        stage="rehearsal",
        status="completed",
        kind="live",
        basis="explicit_flag",
        coverage=(1, 1),
        version_fields=(None, None, None),
        scope=(3, 1, 1),
        metrics=(3, 2, 3, 0.6667, "run"),
        cost=(0.050723, "run_json_total", False, True, False, False),
        wall=(98219, "run_json_total", True),
        comparison=dict(baseline=None, control=None, oracle=None),
        rows_count=3,
        verdict_runs_count=None,
        completeness={"rows": "present", "verdict": "present", "reflection": "present", "traces": "absent"},
        caveats=[
            "missing_scenario_version",
            "missing_shell_version",
            "missing_verifier_hash",
            "cost_includes_reflection",
            "wall_includes_reflection",
        ],
    ),
    "rehearsal-run2": dict(
        title="现场彩排 run2",
        study_kind="rehearsal",
        stage="rehearsal",
        status="completed",
        kind="live",
        basis="explicit_flag",
        coverage=(1, 1),
        version_fields=(None, None, None),
        scope=(3, 1, 1),
        metrics=(3, 2, 3, 0.6667, "run"),
        cost=(0.072286, "run_json_total", False, True, False, False),
        wall=(88773, "run_json_total", True),
        comparison=dict(baseline=None, control=None, oracle=None),
        rows_count=3,
        verdict_runs_count=None,
        completeness={"rows": "present", "verdict": "present", "reflection": "present", "traces": "absent"},
        caveats=[
            "missing_scenario_version",
            "missing_shell_version",
            "missing_verifier_hash",
            "cost_includes_reflection",
            "wall_includes_reflection",
        ],
    ),
    "batch1": dict(
        title="判分器 cell 判别验证",
        study_kind="verifier_validation",
        stage="validation",
        status="completed",
        kind="mixed",
        basis="runtime_record",
        coverage=(3, 3),
        version_fields=(None, None, None),
        scope=(1, 1, 3),
        metrics=(3, 1, 3, 0.3333, "cell"),
        cost=(0.43275734, "all_attempts", False, False, True, True),
        wall=(547678, "final_rows_only", False),
        comparison=dict(baseline=None, control=None, oracle=None),
        rows_count=3,
        verdict_runs_count=None,
        completeness={"rows": "present", "verdict": "present", "reflection": "absent", "traces": "present"},
        caveats=[
            "missing_scenario_version",
            "missing_shell_version",
            "missing_verifier_hash",
            "mixed_provenance",
            "validation_not_capability",
            "cost_includes_retries",
            "wall_final_rows_only",
        ],
    ),
}

FORBIDDEN_KEYS = {
    "prompt",
    "response",
    "thinking",
    "memory",
    "cases",
    "stdout",
    "stderr",
    "solution_snapshot",
    "ext",
    "task_id",
    "task_ids",
    "run_id",
    "run_ids",
}


@pytest.fixture(scope="module")
def index() -> dict:
    return build_index(ROOT)


def _walk_keys(value, path="root"):
    if isinstance(value, dict):
        for key, item in value.items():
            yield path, key
            yield from _walk_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for position, item in enumerate(value):
            yield from _walk_keys(item, f"{path}[{position}]")


def test_index_has_eight_canonical_experiments(index: dict) -> None:
    assert index["schema_version"] == "1.0.0"
    assert index["generated_at"] == "2026-11-13T00:00:00Z"
    assert [item["experiment_id"] for item in index["experiments"]] == EXPECTED_IDS
    assert len(index["experiments"]) == 8


@pytest.mark.parametrize("experiment_id", EXPECTED_IDS)
def test_frozen_values(index: dict, experiment_id: str) -> None:
    expected = FROZEN[experiment_id]
    experiment = next(item for item in index["experiments"] if item["experiment_id"] == experiment_id)
    assert experiment["title"] == expected["title"]
    assert experiment["study_kind"] == expected["study_kind"]
    assert experiment["stage"] == expected["stage"]
    assert experiment["status"] == expected["status"]
    assert experiment["provenance"]["kind"] == expected["kind"]
    assert experiment["provenance"]["basis"] == expected["basis"]
    coverage = experiment["provenance"]["coverage"]
    assert (coverage["records_with_evidence"], coverage["records_total"]) == expected["coverage"]
    assert (
        experiment["scenario_version"],
        experiment["shell_version"],
        experiment["verifier_hash"],
    ) == expected["version_fields"]
    scope = experiment["task_scope"]
    assert (scope["count"], scope["reps"], scope["cells"]) == expected["scope"]

    metrics = experiment["metrics"]
    assert (
        metrics["recorded_runs"],
        metrics["passed"],
        metrics["total"],
        metrics["pass_rate"],
        metrics["score_scope"],
    ) == expected["metrics"]
    cost = metrics["cost"]
    assert (
        cost["usd"],
        cost["scope"],
        cost["includes_control"],
        cost["includes_reflection"],
        cost["includes_retries"],
        cost["estimated"],
    ) == expected["cost"]
    wall = metrics["wall_ms"]
    assert (wall["value"], wall["scope"], wall["includes_reflection"]) == expected["wall"]

    evidence = experiment["evidence"]
    assert evidence["rows_count"] == expected["rows_count"]
    assert evidence["verdict_runs_count"] == expected["verdict_runs_count"]
    assert evidence["completeness"] == expected["completeness"]
    assert experiment["caveats"] == expected["caveats"]
    assert experiment["comparability_key"] is None


@pytest.mark.parametrize("experiment_id", EXPECTED_IDS)
def test_comparison_quadruples(index: dict, experiment_id: str) -> None:
    expected = FROZEN[experiment_id]["comparison"]
    experiment = next(item for item in index["experiments"] if item["experiment_id"] == experiment_id)
    comparison = experiment["comparison"]
    assert set(comparison) == {"baseline", "control", "oracle"}
    for group, quadruple in expected.items():
        value = comparison[group]
        if quadruple is None:
            assert value is None
            continue
        assert value is not None
        assert (
            value["runs"],
            value["runs_solved"],
            value["tasks"],
            value["tasks_solved"],
        ) == quadruple


def test_caveats_are_closed_set_with_frozen_lists(index: dict) -> None:
    assert len(CAVEATS) == 16
    for experiment in index["experiments"]:
        caveats = experiment["caveats"]
        assert len(caveats) == len(set(caveats))
        assert set(caveats) <= set(CAVEATS)
        assert caveats == FROZEN[experiment["experiment_id"]]["caveats"]


def test_no_sensitive_keys_or_raw_text(index: dict) -> None:
    forbidden_paths = [
        (path, key) for path, key in _walk_keys(index) if key in FORBIDDEN_KEYS
    ]
    assert forbidden_paths == []
    serialized = json.dumps(index, ensure_ascii=False)
    for marker in ("solution_snapshot", "stdout", "stderr", "task_ids", "run_id"):
        assert marker not in serialized


def test_revision_is_self_verifying(index: dict) -> None:
    assert compute_revision(index) == index["revision"]
    tampered = json.loads(json.dumps(index))
    tampered["experiments"][0]["metrics"]["passed"] = 1
    with pytest.raises(ValueError):
        validate_index(tampered)


def test_manifest_hashes_match_sources() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert len(manifest["sources"]) >= 20
    for entry in manifest["sources"]:
        payload = (ROOT / entry["path"]).read_bytes()
        import hashlib

        assert len(payload) == entry["bytes"], entry["path"]
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"], entry["path"]
    assert set(manifest["caveat_catalog"]) == set(CAVEATS)


def test_builder_is_byte_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "one.json"
    second = tmp_path / "two.json"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for out in (first, second):
        completed = subprocess.run(
            [sys.executable, str(BUILDER), "--out", str(out)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
    assert first.read_bytes() == second.read_bytes()
    loaded = load_index(first)
    assert [item["experiment_id"] for item in loaded["experiments"]] == EXPECTED_IDS


def test_builder_rejects_hash_mismatch_and_writes_nothing(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["sources"][0]["sha256"] = "0" * 64
    broken = tmp_path / "manifest.json"
    broken.write_text(json.dumps(manifest), encoding="utf-8")
    out = tmp_path / "index.json"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    completed = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--manifest",
            str(broken),
            "--out",
            str(out),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "probe_experiments_invalid" in completed.stderr
    assert not out.exists()


def test_builder_rejects_out_inside_repo(tmp_path: Path) -> None:
    out = ROOT / ".probe-index-inside-repo.json"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        completed = subprocess.run(
            [sys.executable, str(BUILDER), "--out", str(out)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 2
        assert "仓库外" in completed.stderr
        assert not out.exists()
    finally:
        out.unlink(missing_ok=True)


def test_builder_requires_out_argument() -> None:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    completed = subprocess.run(
        [sys.executable, str(BUILDER)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2


def test_mapping_document_covers_required_fields() -> None:
    text = MAPPING.read_text(encoding="utf-8")
    required = [
        "session.session_id",
        "`status`",
        "`last_event_id`",
        "messages[].message_id",
        "created_at",
        "turn_id",
        "experiment_refs",
        "turn_events[].event_id",
        "tool_call",
        "tool.call",
        "tool.result",
        "message.completed",
        "turn.completed",
        "tool_call_id",
        "call_id",
        "experiments.get",
        "experiments_get",
        'ok: true → status: "ok"',
        'ok: false → status: "error"',
        "result_ref",
        "title",
        "study_kind",
        "stage",
        "recorded_runs",
        "total_runs",
        "records_with_evidence",
        "records_total",
        "16/16",
        "20/20",
        "cost_total_usd",
        "cost.usd",
        "verdict",
        "evidence.completeness",
        "rows_count",
        "verdict_runs_count",
        "score_scope",
        "task_scope",
        "source_ref",
        "evidence.files",
        "caveats",
        "rehearsal-run1",
        "rehearsal-run2",
        "batch1",
        "probe-4-main-2c",
        "2c-main",
        "probe-4-memory-v2",
        "2c-memory-v2",
        "probe-4-memory-v3",
        "2c-memory-v3",
        "probe-matrix-c1",
        "matrix-core-v3",
        "probe-1-validity",
        "validity-green",
    ]
    missing = [token for token in required if token not in text]
    assert missing == []
    assert ("pass" + "_scope") not in text


def test_sample_is_illustrative_and_mapped() -> None:
    sample = json.loads((ROOT / "web" / "agent.sample.json").read_text(encoding="utf-8"))
    assert sample["sample_only"] is True
    assert sample["contract_status"] == "illustrative"
    sample_ids = {item["experiment_id"] for item in sample["experiments"]["items"]}
    assert sample_ids == {
        "probe-4-main-2c",
        "probe-4-memory-v2",
        "probe-4-memory-v3",
        "probe-matrix-c1",
        "probe-1-validity",
    }
    mapping = MAPPING.read_text(encoding="utf-8")
    for sample_id in sample_ids:
        assert sample_id in mapping
