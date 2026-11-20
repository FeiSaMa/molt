"""v1 synthetic manifest 与审计校验；不再声称验证已退出的 demo 生产路径。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from molt.core.audit import audit_state
from molt.core.errors import AuditError
from molt.core.redact import REDACTION_VERSION


def _manifest_path(store, scenario) -> Path:
    return store.root / "redaction" / f"{scenario.scenario_id}.json"


def test_synthetic_manifest_is_auditable(demo_artifacts) -> None:
    store, scenario, summary = demo_artifacts
    path = _manifest_path(store, scenario)
    assert path.is_file(), "synthetic evidence 必须附带脱敏 manifest"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["redaction_version"] == REDACTION_VERSION
    assert manifest["pre_metrics"] == manifest["post_metrics"]
    for key in ("source_hash", "metrics_hash", "pre_metrics", "post_metrics"):
        assert key in manifest
    assert summary["redaction_manifest"].endswith(f"redaction/{scenario.scenario_id}.json")
    assert audit_state(store, scenario)["decisions"] == 1


def test_tampered_pre_metrics_fails_audit(demo_copy) -> None:
    store, scenario, _ = demo_copy
    path = _manifest_path(store, scenario)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["pre_metrics"]["numbers"][0][1] = 999.0
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "脱敏" in str(excinfo.value)


def test_tampered_post_metrics_fails_audit(demo_copy) -> None:
    store, scenario, _ = demo_copy
    path = _manifest_path(store, scenario)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["post_metrics"]["numbers"][0][1] = 999.0
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "pre_metrics != post_metrics" in str(excinfo.value)


def test_missing_manifest_fails_audit(demo_copy) -> None:
    store, scenario, _ = demo_copy
    _manifest_path(store, scenario).unlink()
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "缺少脱敏 manifest" in str(excinfo.value)
