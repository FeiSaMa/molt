"""D1：schema 与数据类双校验器、兼容矩阵、跨 scope 事件。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from molt import schema
from molt.core.errors import ValidationError
from molt.core.types import RECORD_TYPES

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "records"


def load_golden(kind: str) -> dict:
    return json.loads((FIXTURES / f"{kind}.json").read_text(encoding="utf-8"))


def test_all_golden_records_pass_both_validators() -> None:
    assert set(RECORD_TYPES) == {path.stem for path in FIXTURES.glob("*.json")}
    for kind in RECORD_TYPES:
        data = load_golden(kind)
        schema.validate_schema(kind, data, ROOT)
        record = schema.validate_record(kind, data, ROOT)
        assert record.to_dict() == data


@pytest.mark.parametrize("kind", RECORD_TYPES)
def test_required_field_removal_rejected_by_both(kind: str) -> None:
    data = load_golden(kind)
    document = schema.load_schema(kind, ROOT)
    for field in document["required"]:
        mutated = copy.deepcopy(data)
        mutated.pop(field)
        with pytest.raises(ValidationError):
            schema.validate_schema(kind, mutated, ROOT)
        with pytest.raises(ValidationError):
            schema.validate_record(kind, mutated, ROOT)


@pytest.mark.parametrize("kind", RECORD_TYPES)
def test_unknown_top_level_field_rejected_by_both(kind: str) -> None:
    data = load_golden(kind)
    data["new_top_level_field"] = 1
    with pytest.raises(ValidationError):
        schema.validate_schema(kind, data, ROOT)
    with pytest.raises(ValidationError):
        schema.validate_record(kind, data, ROOT)


@pytest.mark.parametrize("kind", RECORD_TYPES)
def test_ext_only_addition_is_minor_compatible(kind: str) -> None:
    data = load_golden(kind)
    data["ext"] = {"future": {"nested": [1, 2, 3]}}
    record = schema.validate_record(kind, data, ROOT)
    assert record.to_dict()["ext"] == {"future": {"nested": [1, 2, 3]}}


@pytest.mark.parametrize("kind", RECORD_TYPES)
def test_wrong_major_schema_version_rejected(kind: str) -> None:
    data = load_golden(kind)
    data["schema_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        schema.validate_record(kind, data, ROOT)


def test_result_scope_score_matrix() -> None:
    data = load_golden("result")
    search_only = copy.deepcopy(data)
    search_only["scope"] = "search"
    search_only["scores"] = {"search": data["scores"]["search"]}
    schema.validate_record("result", search_only, ROOT)

    report_missing_holdout = copy.deepcopy(data)
    report_missing_holdout["scores"] = {"search": data["scores"]["search"]}
    with pytest.raises(ValidationError):
        schema.validate_record("result", report_missing_holdout, ROOT)

    search_with_holdout = copy.deepcopy(search_only)
    search_with_holdout["scores"]["holdout"] = data["scores"]["holdout"]
    with pytest.raises(ValidationError):
        schema.validate_record("result", search_with_holdout, ROOT)


def test_cross_scope_event_rejected_by_both() -> None:
    leaked = {
        "schema_version": "1.0.0",
        "event_id": "ev-leak",
        "scope": "search",
        "type": "holdout.accessed",
        "ts": "2026-01-01T00:00:00Z",
        "run_id": None,
        "payload": {"task_ids": ["holdout-even"]},
        "ext": {},
    }
    with pytest.raises(ValidationError):
        schema.validate_schema("event", leaked, ROOT)
    with pytest.raises(ValidationError):
        schema.validate_record("event", leaked, ROOT)

    legit = dict(leaked, scope="report", event_id="ev-legit")
    schema.validate_record("event", legit, ROOT)


def test_unknown_event_type_is_open() -> None:
    event = {
        "schema_version": "1.0.0",
        "event_id": "ev-open",
        "scope": "search",
        "type": "custom.future_event",
        "ts": "2026-01-01T00:00:00Z",
        "run_id": None,
        "payload": {"anything": [1, {"deep": True}]},
        "ext": {},
    }
    record = schema.validate_record("event", event, ROOT)
    assert record.payload == {"anything": [1, {"deep": True}]}


def test_known_event_payload_is_tagged_union() -> None:
    event = load_golden("event")
    event["payload"] = {"task_id": "smoke-add", "genome_id": "g", "surprise": 1}
    with pytest.raises(ValidationError):
        schema.validate_record("event", event, ROOT)


def test_docs_are_generated_from_schema() -> None:
    generated = schema.render_protocol_docs(ROOT)
    checked_in = (ROOT / "docs" / "protocol.md").read_text(encoding="utf-8")
    assert generated == checked_in
    assert "| `schema_version` | string | 是 |" in generated
    for kind in RECORD_TYPES:
        assert f"### `{kind}`" in generated


def test_validate_all_covers_repository_artifacts() -> None:
    validated = schema.validate_all(ROOT)
    kinds = {kind for _, kind in validated}
    assert kinds == set(RECORD_TYPES)
    expected = {(ROOT / "tests" / "fixtures" / "records" / f"{kind}.json", kind)
                for kind in RECORD_TYPES}
    assert set(validated) == expected


def test_shell_manifest_cannot_declare_gate_policy() -> None:
    manifest = load_golden("shell_manifest")
    for key in ("tolerance", "cost_threshold", "invalid_ratio", "repeat_policy"):
        mutated = copy.deepcopy(manifest)
        mutated[key] = 1
        with pytest.raises(ValidationError):
            schema.validate_schema("shell_manifest", mutated, ROOT)
        with pytest.raises(ValidationError):
            schema.validate_record("shell_manifest", mutated, ROOT)

    capabilities = copy.deepcopy(manifest)
    capabilities["capabilities"]["repeat_policy"] = "retry-3"
    with pytest.raises(ValidationError):
        schema.validate_record("shell_manifest", capabilities, ROOT)


def test_proposal_out_of_domain_rejected() -> None:
    proposal = load_golden("proposal")
    fourth = copy.deepcopy(proposal)
    fourth["switches"]["compaction"] = "auto"
    with pytest.raises(ValidationError):
        schema.validate_record("proposal", fourth, ROOT)

    out_of_range = copy.deepcopy(proposal)
    out_of_range["switches"]["thinking"] = "max"
    with pytest.raises(ValidationError):
        schema.validate_record("proposal", out_of_range, ROOT)


def test_gate_decision_requires_paired_table() -> None:
    decision = load_golden("gate_decision")
    missing = copy.deepcopy(decision)
    missing.pop("paired")
    with pytest.raises(ValidationError):
        schema.validate_record("gate_decision", missing, ROOT)
    empty = copy.deepcopy(decision)
    empty["paired"] = []
    with pytest.raises(ValidationError):
        schema.validate_record("gate_decision", empty, ROOT)
