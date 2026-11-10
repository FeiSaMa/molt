"""D7：audit 绿/失败路径、search/report 分流断言、secret scan。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from molt.core.audit import audit_state
from molt.core.errors import AuditError
from molt.core.events import EventLog, make_event
from molt.core.types import GateDecision, Result, Run

ROOT = Path(__file__).resolve().parents[1]


def test_demo_audit_is_green(demo_artifacts) -> None:
    store, scenario, summary = demo_artifacts
    report = audit_state(store, scenario)
    assert report["runs"] == 6
    assert report["results"] == 4
    assert report["decisions"] == 1
    assert report["report_events"] >= 2


def test_audit_is_read_only(demo_artifacts) -> None:
    store, scenario, _ = demo_artifacts
    before_runs = len(store.list_runs())
    before_pointer = store.current()
    audit_state(store, scenario)
    assert len(store.list_runs()) == before_runs
    assert store.current() == before_pointer


def test_tampered_run_cost_fails_audit(demo_copy) -> None:
    store, scenario, _ = demo_copy
    run = store.list_runs()[0]
    data = run.to_dict()
    data["usage"]["cost_usd"] = data["usage"]["cost_usd"] + 1.0
    store.write_run(Run.from_dict(data))
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "重算不一致" in str(excinfo.value) or "账本" in str(excinfo.value)


def test_tampered_result_fails_audit(demo_copy) -> None:
    store, scenario, _ = demo_copy
    result = store.list_results()[0]
    data = result.to_dict()
    block = data["scores"]["search"]
    block["cost_total_usd"] = block["cost_total_usd"] + 2.0
    block["cost_per_solved_usd"] = (
        block["cost_total_usd"] / block["solved"] if block["solved"] else None
    )
    store.write_result(Result.from_dict(data))
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "result" in str(excinfo.value)


def test_tampered_decision_fails_audit(demo_copy) -> None:
    store, scenario, _ = demo_copy
    decision = store.list_decisions()[0]
    data = decision.to_dict()
    data["verdict"] = "reject"
    data["reason"] = "budget_exceeded"
    store.write_decision(GateDecision.from_dict(data))
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "decision" in str(excinfo.value)


def test_search_event_leaking_holdout_id_fails(demo_copy) -> None:
    store, scenario, _ = demo_copy
    log = EventLog(store.root)
    log.append(
        make_event(
            scope="search",
            event_type="custom.leak",
            payload={"task_id": "holdout-even"},
        )
    )
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "holdout" in str(excinfo.value)


def test_report_stream_requires_holdout_accessed(demo_copy) -> None:
    store, scenario, _ = demo_copy
    for path in (store.root / "events" / "report").glob("*.jsonl"):
        path.unlink()
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "holdout.accessed" in str(excinfo.value)


def test_secret_scan_blocks_audit(demo_copy) -> None:
    store, scenario, _ = demo_copy
    (store.root / "leak.json").write_text(json.dumps({"token": "sk-" + "z" * 32}), encoding="utf-8")
    with pytest.raises(AuditError) as excinfo:
        audit_state(store, scenario)
    assert "secret scan" in str(excinfo.value)


def test_audit_cli_demo() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-m", "molt", "audit", "--scenario", "demo"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    assert "audit ok" in completed.stdout
