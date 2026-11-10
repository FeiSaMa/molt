"""Pi 1.5.0 session JSONL v3 账本解析。

- 累加所有携带 ``usage`` 的 entry：assistant message、``usage`` entry、``compaction``、
  未知类型；按 entry id 去重。
- 成本链：``cost.totalFromProvider``（数值/true）→ ``cost.total``（目录估算）→
  按 deepseek 目录价重算。
- 与 JSON 流的 assistant message 总计交叉校验；偏差超阈 → ``ledger_mismatch``。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PRICES: dict[str, float] = {
    "input": 0.30,
    "output": 1.20,
    "cache_read": 0.006,
    "cache_write": 0.0,
}
TOKEN_TOLERANCE = 1
COST_TOLERANCE = 1e-5


@dataclass(frozen=True, slots=True)
class UsageEntry:
    entry_id: str
    kind: str
    input: int
    output: int
    cache_read: int
    cache_write: int
    reasoning: int
    total: int
    cost_usd: float
    cost_source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "kind": self.kind,
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "reasoning": self.reasoning,
            "total": self.total,
            "cost_usd": self.cost_usd,
            "cost_source": self.cost_source,
        }


@dataclass(frozen=True, slots=True)
class SessionUsage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    cost_source: str = "none"
    turns: int = 0
    entries: int = 0
    assistant_tokens: int = 0
    assistant_cost_usd: float = 0.0
    raw_total_tokens: int = 0
    session_total_tokens: int | None = None
    matches: bool = True
    mismatch_reason: str | None = None
    details: tuple[UsageEntry, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "reasoning": self.reasoning,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "cost_source": self.cost_source,
            "turns": self.turns,
            "entries": self.entries,
            "assistant_tokens": self.assistant_tokens,
            "assistant_cost_usd": self.assistant_cost_usd,
            "raw_total_tokens": self.raw_total_tokens,
            "session_total_tokens": self.session_total_tokens,
            "matches": self.matches,
            "mismatch_reason": self.mismatch_reason,
            "details": [entry.to_dict() for entry in self.details],
        }


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def usage_tokens(usage: Mapping[str, Any]) -> dict[str, int]:
    """统一 Pi camelCase usage 字段。``reasoning`` 已含在 output；不重复加。"""
    tokens_input = _int(usage.get("input"))
    tokens_output = _int(usage.get("output"))
    cache_read = _int(usage.get("cacheRead"))
    cache_write = _int(usage.get("cacheWrite"))
    reasoning = _int(usage.get("reasoning"))
    total = _int(usage.get("totalTokens")) or (tokens_input + tokens_output)
    return {
        "input": tokens_input,
        "output": tokens_output,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "reasoning": reasoning,
        "total": total,
    }


def usage_cost(
    usage: Mapping[str, Any],
    prices: Mapping[str, float] | None = None,
) -> tuple[float, str]:
    """成本链：totalFromProvider → cost.total → 目录估算。"""
    price = dict(DEFAULT_PRICES)
    if prices:
        price.update(prices)
    cost = usage.get("cost")
    if isinstance(cost, Mapping):
        total_from_provider = cost.get("totalFromProvider", cost.get("total_from_provider"))
        if isinstance(total_from_provider, (int, float)) and not isinstance(
            total_from_provider, bool
        ):
            return max(0.0, float(total_from_provider)), "provider"
        if total_from_provider is True and "total" in cost:
            return max(0.0, _number(cost["total"])), "provider"
        declared_source = cost.get("source")
        if declared_source in ("provider", "estimate"):
            return max(0.0, _number(cost.get("total"))), declared_source
        if "total" in cost:
            return max(0.0, _number(cost["total"])), "estimate"
    tokens = usage_tokens(usage)
    if tokens["total"] == 0:
        return 0.0, "none"
    billable_input = max(0, tokens["input"] - tokens["cache_read"])
    value = (
        billable_input * price["input"]
        + tokens["cache_read"] * price["cache_read"]
        + tokens["cache_write"] * price["cache_write"]
        + tokens["output"] * price["output"]
    ) / 1_000_000
    return round(value, 8), "estimate"


def _usage_from_entry(entry: Mapping[str, Any]) -> Mapping[str, Any] | None:
    usage = entry.get("usage")
    if isinstance(usage, Mapping):
        return usage
    message = entry.get("message")
    if isinstance(message, Mapping):
        usage = message.get("usage")
        if isinstance(usage, Mapping):
            return usage
    return None


def _aggregate_source(sources: list[str]) -> str:
    non_empty = {source for source in sources if source != "none"}
    if not non_empty:
        return "none"
    if len(non_empty) == 1:
        return next(iter(non_empty))
    return "mixed"


def _records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"session JSONL 第 {line_no} 行不可解析: {exc}") from exc
        if isinstance(value, dict):
            records.append(value)
    return records


def parse_session_text(
    text: str,
    *,
    expected_total: int | None = None,
    prices: Mapping[str, float] | None = None,
) -> SessionUsage:
    records = _records(text)
    header = next((record for record in records if record.get("type") == "session"), {})
    if expected_total is None:
        expected_total = _int(header.get("totalTokens"))
        if expected_total == 0 and isinstance(header.get("usage"), Mapping):
            expected_total = _int(header["usage"].get("totalTokens"))
        if expected_total == 0:
            expected_total = None

    seen: set[str] = set()
    details: list[UsageEntry] = []
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "reasoning": 0}
    total_cost = 0.0
    raw_total = 0
    assistant_tokens = 0
    assistant_cost = 0.0
    turns = 0
    sources: list[str] = []
    duplicate = False

    for index, record in enumerate(records):
        if record.get("type") == "session":
            continue
        usage = _usage_from_entry(record)
        entry_id = str(record.get("id") or f"__index_{index}")
        is_assistant = (
            record.get("type") == "message"
            and isinstance(record.get("message"), Mapping)
            and record["message"].get("role") == "assistant"
        )
        if is_assistant:
            turns += 1
        if usage is None:
            continue
        tokens = usage_tokens(usage)
        raw_total += tokens["total"]
        if entry_id in seen:
            duplicate = True
            continue
        seen.add(entry_id)
        cost, source = usage_cost(usage, prices)
        sources.append(source)
        for key in totals:
            totals[key] += tokens[key]
        total_cost += cost
        if is_assistant:
            assistant_tokens += tokens["total"]
            assistant_cost += cost
        details.append(
            UsageEntry(
                entry_id=entry_id,
                kind=str(record.get("type", "unknown")),
                input=tokens["input"],
                output=tokens["output"],
                cache_read=tokens["cache_read"],
                cache_write=tokens["cache_write"],
                reasoning=tokens["reasoning"],
                total=tokens["total"],
                cost_usd=cost,
                cost_source=source,
            )
        )

    total_tokens = totals["input"] + totals["output"] + totals["cache_read"] + totals["cache_write"]
    matches = True
    reason = None
    if duplicate:
        matches = False
        reason = "duplicate_entry_id"
    if expected_total is not None and abs(total_tokens - expected_total) > TOKEN_TOLERANCE:
        matches = False
        reason = "session_total_mismatch"
    return SessionUsage(
        input=totals["input"],
        output=totals["output"],
        cache_read=totals["cache_read"],
        cache_write=totals["cache_write"],
        reasoning=totals["reasoning"],
        total_tokens=total_tokens,
        cost_usd=round(total_cost, 8),
        cost_source=_aggregate_source(sources),
        turns=turns,
        entries=len(details),
        assistant_tokens=assistant_tokens,
        assistant_cost_usd=round(assistant_cost, 8),
        raw_total_tokens=raw_total,
        session_total_tokens=expected_total,
        matches=matches,
        mismatch_reason=reason,
        details=tuple(details),
    )


def parse_session_file(
    path: Path,
    *,
    expected_total: int | None = None,
    prices: Mapping[str, float] | None = None,
) -> SessionUsage:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"session 文件不存在: {path}")
    return parse_session_text(
        path.read_text(encoding="utf-8"),
        expected_total=expected_total,
        prices=prices,
    )


def parse_stream_text(text: str, *, prices: Mapping[str, float] | None = None) -> SessionUsage:
    """从 ``--mode json`` stdout 解析 assistant message_end 的累计 usage。"""
    records = _records(text)
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    cost_total = 0.0
    turns = 0
    seen: set[str] = set()
    sources: list[str] = []
    for record in records:
        if record.get("type") != "message_end":
            continue
        message = record.get("message")
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            continue
        key = str(message.get("responseId") or message.get("timestamp") or turns)
        if key in seen:
            continue
        seen.add(key)
        usage = message.get("usage")
        if not isinstance(usage, Mapping):
            continue
        turns += 1
        tokens = usage_tokens(usage)
        for name in totals:
            totals[name] += tokens[name]
        cost, source = usage_cost(usage, prices)
        cost_total += cost
        sources.append(source)
    total_tokens = totals["input"] + totals["output"] + totals["cache_read"] + totals["cache_write"]
    return SessionUsage(
        input=totals["input"],
        output=totals["output"],
        cache_read=totals["cache_read"],
        cache_write=totals["cache_write"],
        total_tokens=total_tokens,
        cost_usd=round(cost_total, 8),
        cost_source=_aggregate_source(sources),
        turns=turns,
    )


def cross_check(
    ledger: SessionUsage,
    stream: SessionUsage,
) -> tuple[bool, str | None]:
    """ledger 的 assistant 总计 vs JSON 流 message_end 总计。"""
    if stream.turns == 0:
        return True, None
    if abs(ledger.assistant_tokens - stream.total_tokens) > TOKEN_TOLERANCE:
        return False, "stream_token_mismatch"
    if abs(ledger.assistant_cost_usd - stream.cost_usd) > COST_TOLERANCE:
        return False, "stream_cost_mismatch"
    return True, None
