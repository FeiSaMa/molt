"""D6：per-run 事件与账本文件；并发扫描无共享写目标、无丢行、无半行。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from molt.core.events import EventLog, make_event
from molt.core.ledger import Ledger
from molt.core.store import StateStore

ROOT = Path(__file__).resolve().parents[1]
FIXED = "2026-01-01T00:00:00Z"
WRITERS = 4
LINES = 40


def test_concurrent_runs_write_isolated_files(tmp_path: Path) -> None:
    state = tmp_path / "state"
    roots = {"molt/core": tmp_path / "repo" / "molt" / "core"}
    roots["molt/core"].mkdir(parents=True)
    (roots["molt/core"] / "file.py").write_text("# core\n", encoding="utf-8")

    script = f"""
import sys, time
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})
from molt.core.events import EventLog, make_event
from molt.core.ledger import Ledger
run_id = sys.argv[1]
state = Path({str(state)!r})
ready = Path({str(tmp_path)!r}) / ('ready-' + run_id)
ready.write_text('1')
deadline = time.time() + 10
while len(list(Path({str(tmp_path)!r}).glob('ready-run-*'))) < {WRITERS}:
    if time.time() > deadline:
        sys.exit(2)
    time.sleep(0.005)
events = EventLog(state)
ledger = Ledger(state)
for index in range({LINES}):
    events.append(make_event(
        scope='search', event_type='run.finished', run_id=run_id,
        payload={{'task_id': 't' + str(index), 'outcome': 'passed'}},
        event_id=f'{{run_id}}-{{index}}', ts='{FIXED}',
    ))
    ledger.append(run_id, {{'kind': 'execution', 'cost_usd': 0.01, 'index': index}})
print('done')
"""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, f"run-{index}"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(WRITERS)
    ]

    # 与 4 个 writer 并发的一次 adoption
    store = StateStore(state)
    archive = store.adopt(_genome(), roots, result_refs=["r1"], note="concurrent", adopted_at=FIXED)
    assert archive.generation == 1

    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr
        assert "done" in stdout

    event_log = EventLog(state)
    files = sorted((state / "events" / "search").glob("*.jsonl"))
    assert len(files) == WRITERS
    seen = set()
    total = 0
    for path in files:
        run_id = path.stem
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == LINES
        for line in lines:
            assert line.strip()
            event = json.loads(line)
            assert event["run_id"] == run_id
            assert event["scope"] == "search"
            assert event["event_id"] not in seen
            seen.add(event["event_id"])
            total += 1
    assert total == WRITERS * LINES

    aggregate = event_log.materialize("search")
    aggregate_lines = aggregate.read_text(encoding="utf-8").splitlines()
    assert len(aggregate_lines) == WRITERS * LINES
    assert all(json.loads(line)["scope"] == "search" for line in aggregate_lines)

    ledger_files = sorted((state / "ledger").glob("*.jsonl"))
    assert len(ledger_files) == WRITERS
    for path in ledger_files:
        assert len(path.read_text(encoding="utf-8").splitlines()) == LINES


def test_report_events_never_enter_search_stream(tmp_path: Path) -> None:
    log = EventLog(tmp_path)
    log.append(
        make_event(
            scope="report",
            event_type="holdout.accessed",
            payload={"task_ids": ["holdout-even"]},
            event_id="ev-report",
            ts=FIXED,
        )
    )
    log.append(
        make_event(
            scope="search",
            event_type="run.started",
            payload={"task_id": "smoke-add", "genome_id": "g"},
            run_id="r1",
            event_id="ev-search",
            ts=FIXED,
        )
    )
    log.materialize("search")
    log.materialize("report")
    search_lines = (tmp_path / "events" / "search.jsonl").read_text(encoding="utf-8").splitlines()
    report_lines = (tmp_path / "events" / "report.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(search_lines) == 1 and "ev-search" in search_lines[0]
    assert len(report_lines) == 1 and "ev-report" in report_lines[0]
    assert "ev-report" not in "\n".join(search_lines)


def test_run_files_are_distinct_write_targets(tmp_path: Path) -> None:
    log = EventLog(tmp_path)
    paths = {log.run_path("search", f"run-{index}") for index in range(8)}
    assert len(paths) == 8
    global_paths = {log.run_path("search", None) for _ in range(3)}
    assert len(global_paths) == 1


def test_ledger_and_events_are_append_only(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    ledger.append("run-1", {"kind": "execution", "cost_usd": 1.0})
    ledger.append("run-1", {"kind": "overhead", "cost_usd": 2.0})
    assert ledger.total_cost("run-1") == 3.0
    assert ledger.total_cost("run-1", kind="overhead") == 2.0


def _genome():
    from molt.core.types import Genome

    data = json.loads((ROOT / "shells/code-repair/genomes/v0.json").read_text(encoding="utf-8"))
    return Genome.from_dict(data)
