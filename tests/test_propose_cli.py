"""B2-D1 CLI：``molt propose`` 端到端（grid / offline / 无 key fallback）。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from molt.cli import main
from molt.core.ledger import Ledger
from molt.core.store import StateStore

ROOT = Path(__file__).resolve().parents[1]


def _proposal_id(output: str) -> str:
    match = re.search(r"proposal=(prop-[0-9a-f]+)", output)
    assert match is not None, output
    return match.group(1)


def _search_events(state: Path) -> list[dict]:
    events = []
    for path in sorted((state / "events" / "search").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def test_offline_propose_end_to_end(tmp_path: Path, capsys) -> None:
    state = tmp_path / "state"
    code = main(["propose", "--scenario", "demo", "--state", str(state), "--offline"])
    output = capsys.readouterr().out
    assert code == 0, output
    assert "source=grid" in output
    assert "fallback=False" in output
    assert "cost_usd=0.0" in output

    proposal_id = _proposal_id(output)
    store = StateStore(state)
    proposal = store.read_proposal(proposal_id)
    assert proposal.parent_id is not None
    assert proposal.ext["source"] == "grid"
    assert proposal.ext["fallback"] == {"used": False, "reason": None}
    assert len(list((state / "proposals").glob("*.json"))) == 1

    ledger = Ledger(state)
    entries = ledger.read(proposal_id)
    assert len(entries) == 1
    assert entries[0]["kind"] == "overhead"
    assert entries[0]["cost_usd"] == 0.0
    assert entries[0]["counted_as"] == "search_overhead"

    events = _search_events(state)
    assert any(
        event["type"] == "proposal.generated" and event["payload"]["proposal_id"] == proposal_id
        for event in events
    )


def test_repeat_offline_does_not_duplicate_overhead(tmp_path: Path, capsys) -> None:
    state = tmp_path / "state"
    args = ["propose", "--scenario", "demo", "--state", str(state), "--offline"]
    assert main(args) == 0
    first_output = capsys.readouterr().out
    first_id = _proposal_id(first_output)
    assert main(args) == 0
    second_output = capsys.readouterr().out
    assert _proposal_id(second_output) == first_id

    ledger = Ledger(state)
    assert len(ledger.read(first_id)) == 1
    assert len(list((state / "proposals").glob("*.json"))) == 1


def test_llm_without_api_key_falls_back(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("MOLT_PROPOSER_API_KEY", raising=False)
    state = tmp_path / "state"
    code = main(
        [
            "propose",
            "--scenario",
            "demo",
            "--state",
            str(state),
            "--proposer",
            "llm",
        ]
    )
    output = capsys.readouterr().out
    assert code == 0, output
    assert "source=grid" in output
    assert "fallback=True" in output
    assert "reason=missing_api_key" in output

    proposal_id = _proposal_id(output)
    proposal = StateStore(state).read_proposal(proposal_id)
    assert proposal.ext["fallback"] == {"used": True, "reason": "missing_api_key"}
    assert proposal.cost_usd == 0.0


def test_unknown_scenario_returns_two(tmp_path: Path, capsys) -> None:
    code = main(["propose", "--scenario", "nope", "--state", str(tmp_path / "state"), "--offline"])
    assert code == 2
    assert "scenario 不存在" in capsys.readouterr().err
