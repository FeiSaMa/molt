"""Pi session JSONL v3 账本：compaction/usage/未知类型、去重、成本链、交叉校验。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from molt.adapters.pi.ledger import (
    cross_check,
    parse_session_file,
    parse_session_text,
    parse_stream_text,
)

pytestmark = pytest.mark.pi


def usage(
    *,
    input_tokens: int = 100,
    output: int = 20,
    cache_read: int = 5,
    cache_write: int = 0,
    cost: float | None = 0.001,
    provider: bool | float | None = False,
) -> dict:
    payload = {
        "input": input_tokens,
        "output": output,
        "cacheRead": cache_read,
        "cacheWrite": cache_write,
        "reasoning": 3,
        "totalTokens": input_tokens + output + cache_read + cache_write,
    }
    cost_payload: dict = {}
    if cost is not None:
        cost_payload.update(
            {
                "input": 0.0,
                "output": 0.0,
                "cacheRead": 0.0,
                "cacheWrite": 0.0,
                "total": cost,
            }
        )
    if provider is not None:
        cost_payload["totalFromProvider"] = provider
    if cost_payload:
        payload["cost"] = cost_payload
    return payload


def message(
    entry_id: str, entry_usage: dict, *, role: str = "assistant", extra: dict | None = None
) -> dict:
    entry = {
        "type": "message",
        "id": entry_id,
        "parentId": None,
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {
            "role": role,
            "content": [{"type": "text", "text": "ok"}],
            "usage": entry_usage,
        },
    }
    if extra:
        entry.update(extra)
    return entry


def session(*entries: dict, total_tokens: int | None = None) -> str:
    header: dict = {
        "type": "session",
        "version": 3,
        "id": "session-1",
        "timestamp": "2026-01-01T00:00:00Z",
        "cwd": "/tmp/work",
    }
    if total_tokens is not None:
        header["totalTokens"] = total_tokens
    lines = [json.dumps(header)]
    lines.extend(json.dumps(entry) for entry in entries)
    return "\n".join(lines) + "\n"


def test_compaction_and_unknown_usage_are_included() -> None:
    text = session(
        message("m1", usage(input_tokens=100, output=20, cache_read=5, cost=0.001)),
        {
            "type": "compaction",
            "id": "c1",
            "parentId": "m1",
            "timestamp": "2026-01-01T00:00:01Z",
            "firstKeptEntryId": "c1",
            "tokensBefore": 500,
            "usage": usage(input_tokens=30, output=4, cache_read=0, cost=0.0004),
        },
        {
            "type": "usage",
            "id": "u1",
            "parentId": "m1",
            "kind": "cache_warm",
            "timestamp": "2026-01-01T00:00:02Z",
            "usage": usage(input_tokens=0, output=0, cache_read=50, cost=0.0001),
        },
        {
            "type": "future_entry",
            "id": "f1",
            "parentId": "m1",
            "timestamp": "2026-01-01T00:00:03Z",
            "usage": usage(input_tokens=1, output=2, cache_read=0, cost=0.00001),
        },
    )
    ledger = parse_session_text(text)
    assert ledger.entries == 4
    assert ledger.turns == 1
    assert ledger.total_tokens == 100 + 20 + 5 + 30 + 4 + 0 + 0 + 50 + 1 + 2
    assert ledger.cost_usd == pytest.approx(0.001 + 0.0004 + 0.0001 + 0.00001)
    assert ledger.cost_source == "estimate"
    assert ledger.assistant_tokens == 125
    assert ledger.matches is True


def test_cost_chain_provider_and_estimate() -> None:
    provider = parse_session_text(session(message("m1", usage(cost=0.25, provider=True))))
    assert provider.cost_usd == 0.25
    assert provider.cost_source == "provider"

    numeric = parse_session_text(session(message("m1", usage(cost=0.25, provider=0.2))))
    assert numeric.cost_usd == 0.2
    assert numeric.cost_source == "provider"

    no_total = parse_session_text(session(message("m1", usage(cost=None, provider=None))))
    assert no_total.cost_usd > 0
    assert no_total.cost_source == "estimate"


def test_duplicate_entry_id_is_mismatch() -> None:
    entry = message("m1", usage(input_tokens=10, output=1, cache_read=0, cost=0.001))
    ledger = parse_session_text(session(entry, entry))
    assert ledger.entries == 1
    assert ledger.matches is False
    assert ledger.mismatch_reason == "duplicate_entry_id"
    assert ledger.raw_total_tokens == 2 * ledger.total_tokens


def test_header_total_mismatch() -> None:
    ledger = parse_session_text(
        session(
            message("m1", usage(input_tokens=10, output=1, cache_read=0)),
            total_tokens=9999,
        )
    )
    assert ledger.matches is False
    assert ledger.mismatch_reason == "session_total_mismatch"


def test_turns_count_assistant_messages_even_without_usage() -> None:
    text = session(
        message("m1", usage()),
        message("m2", usage(), role="user"),
        {"type": "message", "id": "m3", "message": {"role": "assistant", "content": []}},
    )
    ledger = parse_session_text(text)
    assert ledger.turns == 2


def test_stream_cross_check() -> None:
    assistant_usage = usage(input_tokens=100, output=20, cache_read=5, cost=0.001)
    text = session(message("m1", assistant_usage))
    ledger = parse_session_text(text)
    stream_text = (
        json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "usage": assistant_usage,
                    "responseId": "resp-1",
                },
            }
        )
        + "\n"
    )
    stream = parse_stream_text(stream_text)
    assert cross_check(ledger, stream) == (True, None)

    bad_payload = json.loads(stream_text)
    bad_payload["message"]["usage"]["output"] = 99
    bad_stream = parse_stream_text(json.dumps(bad_payload) + "\n")
    ok, reason = cross_check(ledger, bad_stream)
    assert ok is False
    assert reason == "stream_token_mismatch"


def test_session_file_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        session(message("m1", usage(input_tokens=1, output=2, cache_read=0))),
        encoding="utf-8",
    )
    ledger = parse_session_file(path)
    assert ledger.total_tokens == 3
    with pytest.raises(FileNotFoundError):
        parse_session_file(tmp_path / "missing.jsonl")
