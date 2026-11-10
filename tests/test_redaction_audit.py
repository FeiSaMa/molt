"""D7：canonical evidence 脱敏、指标不变断言、secret scan。"""

from __future__ import annotations

from pathlib import Path

import pytest

from molt.core.errors import RedactionError
from molt.core.hash import canonical_json_bytes, content_hash, sha256_bytes
from molt.core.redact import (
    DEFAULT_RULES,
    redact_evidence,
    redact_file_bytes,
    scan_secrets,
)

ROOT = Path(__file__).resolve().parents[1]


def evidence() -> dict:
    return {
        "run_id": "run-1",
        "cost_usd": 1.84,
        "tokens": [1000, 500],
        "passed": True,
        "note": "key sk-abcdefghijklmnopqrstuvwx and ghp_012345678901234567890123456789",
        "path": "/home/alice/dev/molt/runs/run-1.log",
        "auth": "Authorization: Bearer abcdefghijklmnop",
    }


def test_redaction_removes_secrets_and_keeps_metrics() -> None:
    redacted, manifest = redact_evidence(evidence())
    canonical = canonical_json_bytes(redacted).decode("utf-8")
    assert "sk-abcdefghijklmnopqrstuvwx" not in canonical
    assert "ghp_012345678901234567890123456789" not in canonical
    assert "/home/alice" not in canonical
    assert "Bearer abcdefghijklmnop" not in canonical
    assert manifest["replacements"] >= 4
    assert manifest["redaction_version"] == "1"
    assert manifest["pre_metrics"] == manifest["post_metrics"]
    assert manifest["metrics_hash"] == content_hash(manifest["pre_metrics"])


def test_manifest_hashes_are_canonical() -> None:
    redacted, manifest = redact_evidence(evidence())
    assert manifest["source_hash"] == sha256_bytes(canonical_json_bytes(evidence()))
    assert manifest["canonical_hash"] == sha256_bytes(canonical_json_bytes(redacted))
    assert [rule.name for rule in DEFAULT_RULES] == manifest["rules"]


def test_secret_scan_hits_before_and_clean_after() -> None:
    raw = canonical_json_bytes(evidence()).decode("utf-8")
    assert set(scan_secrets(raw)) >= {"api_key_sk", "github_token", "bearer_token"}
    redacted, _ = redact_evidence(evidence())
    assert scan_secrets(canonical_json_bytes(redacted).decode("utf-8")) == []


def test_redaction_is_idempotent() -> None:
    once, _ = redact_evidence(evidence())
    twice, manifest = redact_evidence(once)
    assert canonical_json_bytes(once) == canonical_json_bytes(twice)
    assert manifest["replacements"] == 0


def test_metrics_mutation_is_rejected() -> None:
    def drop_cost(value: dict) -> dict:
        value.pop("cost_usd", None)
        return value

    with pytest.raises(RedactionError):
        redact_evidence(evidence(), mutator=drop_cost)


def test_non_json_evidence_rejected() -> None:
    with pytest.raises(RedactionError):
        redact_file_bytes(b"not json")


def test_redact_file_bytes_returns_canonical() -> None:
    import json

    payload = json.dumps(evidence()).encode("utf-8")
    canonical, manifest = redact_file_bytes(payload)
    assert canonical.startswith(b'{"')
    assert manifest["canonical_hash"]


def test_secret_scan_detects_constructor_fixture_paths() -> None:
    assert scan_secrets("/home/bob/.config/molt/token=sk-" + "x" * 24)
    assert scan_secrets("nothing sensitive here") == []
