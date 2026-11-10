"""提案核心：SearchHistory → Proposal。

B2a 的边界：
- 只接受 ``SearchHistory``；构造时按 ``parent.scenario_version`` 过滤，旧版本 history
  不进入当前 proposal。
- LLM 策略通过构造注入的 callable 提供，core 不新增 Protocol（七端口白名单不变）。
- 越域 / 不可解析 / 超预算一律回落通用网格；回落也留下完整 Proposal。
- ``ext`` 承载 B2-D1 的扩展字段，proposal 顶层字段保持 B1 冻结的 10 项。

本模块只依赖 stdlib 与 ``molt.core.*``。
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from molt.core.errors import ProposalError, ValidationError
from molt.core.events import utc_now
from molt.core.hash import canonical_json_bytes, content_hash, sha256_bytes
from molt.core.types import (
    DIMENSIONS,
    SWITCH_KEYS,
    Genome,
    Proposal,
    SearchHistory,
    Switches,
)

PROPOSAL_SCHEMA_VERSION = "1.0.0"
COUNTED_AS = "search_overhead"
COST_SOURCES: tuple[str, ...] = ("provider", "estimate", "none")
SOURCES: tuple[str, ...] = ("llm", "grid")
USAGE_KEYS: tuple[str, ...] = (
    "input",
    "output",
    "cache_read",
    "cache_write",
    "reasoning",
    "total",
)

ZERO_USAGE: dict[str, int] = {key: 0 for key in USAGE_KEYS}


@dataclass(frozen=True, slots=True)
class ProposalDraft:
    """一次提案尝试的完整草稿；失败时也保留成本与哈希证据。"""

    switches: Switches
    diagnosis: str
    reason: str
    model: str
    prompt_sha256: str
    response_sha256: str
    usage: dict[str, int]
    cost_usd: float
    cost_source: str
    source: str = "llm"
    fallback_used: bool = False
    fallback_reason: str | None = None


def normalize_domains(
    domains: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, tuple[str, ...]]:
    """把 shell 传入的候选域规范化为 ``{dim: tuple(values)}``。"""
    raw = dict(DIMENSIONS if domains is None else domains)
    if set(raw) != set(SWITCH_KEYS):
        raise ProposalError(
            "invalid_domains",
            f"候选域必须且只能包含 {list(SWITCH_KEYS)}，实际 {sorted(raw)}",
        )
    normalized: dict[str, tuple[str, ...]] = {}
    for dim in SWITCH_KEYS:
        values = tuple(str(value) for value in raw[dim])
        if not values:
            raise ProposalError("invalid_domains", f"候选域 {dim} 为空")
        unknown = sorted(set(values) - set(DIMENSIONS[dim]))
        if unknown:
            raise ProposalError("invalid_domains", f"候选域 {dim} 含 v1 未定义取值 {unknown}")
        normalized[dim] = values
    return normalized


def _int_token(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _first(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return None


def normalize_usage(usage: Mapping[str, Any] | None) -> dict[str, int]:
    """统一 provider / fake transport 的 token 口径。"""
    if not isinstance(usage, Mapping):
        return dict(ZERO_USAGE)
    tokens_input = _int_token(_first(usage, "input", "prompt_tokens", "input_tokens"))
    tokens_output = _int_token(_first(usage, "output", "completion_tokens", "output_tokens"))
    cache_read = _int_token(
        _first(usage, "cache_read", "prompt_cache_hit_tokens", "cache_read_tokens")
    )
    cache_write = _int_token(
        _first(
            usage,
            "cache_write",
            "prompt_cache_miss_tokens",
            "cache_write_tokens",
        )
    )
    reasoning = _int_token(_first(usage, "reasoning", "reasoning_tokens"))
    details = usage.get("completion_tokens_details")
    if isinstance(details, Mapping):
        reasoning = max(reasoning, _int_token(details.get("reasoning_tokens")))
    total = _int_token(_first(usage, "total", "total_tokens"))
    if total == 0:
        total = tokens_input + tokens_output
    return {
        "input": tokens_input,
        "output": tokens_output,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "reasoning": reasoning,
        "total": total,
    }


def filter_history(history: SearchHistory, scenario_version: str) -> tuple[SearchHistory, int]:
    """按当前版本过滤；旧版本 history 整体丢弃，返回 ``(history, excluded_entries)``。"""
    if not isinstance(history, SearchHistory):
        raise TypeError("proposer 只接受 SearchHistory；ReportArchive 不得进入搜索回路")
    if history.scenario_version == scenario_version:
        return (
            SearchHistory(
                scenario_version=history.scenario_version,
                genome_id=history.genome_id,
                entries=list(history.entries),
            ),
            0,
        )
    return (
        SearchHistory(
            scenario_version=scenario_version,
            genome_id=history.genome_id,
            entries=[],
        ),
        len(history.entries),
    )


def grid_switches(
    current: Switches,
    domains: Mapping[str, Sequence[str]],
    *,
    rank: Callable[[Switches], tuple[Any, ...]] | None = None,
) -> list[Switches]:
    """通用三维枚举：返回除当前组合外的全部候选，按 ``rank`` 排序。"""
    normalized = normalize_domains(domains)
    candidates: list[Switches] = []
    for combo in itertools.product(*(normalized[dim] for dim in SWITCH_KEYS)):
        switches = Switches.from_dict(dict(zip(SWITCH_KEYS, combo, strict=True)))
        if switches != current:
            candidates.append(switches)

    def default_rank(switches: Switches) -> tuple[Any, ...]:
        diff = sum(1 for dim in SWITCH_KEYS if getattr(switches, dim) != getattr(current, dim))
        distance = sum(
            abs(
                normalized[dim].index(getattr(switches, dim))
                - normalized[dim].index(getattr(current, dim))
            )
            for dim in SWITCH_KEYS
            if getattr(current, dim) in normalized[dim]
        )
        positions = tuple(normalized[dim].index(getattr(switches, dim)) for dim in SWITCH_KEYS)
        return (diff, distance, positions)

    candidates.sort(key=rank or default_rank)
    return candidates


def grid_draft(
    history: SearchHistory,
    parent: Genome,
    domains: Mapping[str, Sequence[str]],
    *,
    created_at: str,
    rank: Callable[[Switches], tuple[Any, ...]] | None = None,
    fallback_reason: str | None = None,
) -> ProposalDraft:
    """不依赖任何 adapter 的确定性网格回退。"""
    normalized = normalize_domains(domains)
    candidates = grid_switches(parent.switches, normalized, rank=rank)
    chosen = candidates[0] if candidates else parent.switches
    if fallback_reason:
        diagnosis = f"grid fallback: {fallback_reason}"
    else:
        diagnosis = "grid step: 三维枚举"
    reason = diagnosis
    prompt = {
        "source": "grid",
        "parent": parent.genome_id,
        "history": history.to_dict(),
        "domains": {dim: list(normalized[dim]) for dim in SWITCH_KEYS},
    }
    response = {
        "diagnosis": diagnosis,
        "switches": chosen.to_dict(),
        "reason": reason,
    }
    return ProposalDraft(
        switches=chosen,
        diagnosis=diagnosis,
        reason=reason,
        model="grid",
        prompt_sha256=sha256_bytes(canonical_json_bytes(prompt)),
        response_sha256=sha256_bytes(canonical_json_bytes(response)),
        usage=dict(ZERO_USAGE),
        cost_usd=0.0,
        cost_source="none",
        source="grid",
        fallback_used=fallback_reason is not None,
        fallback_reason=fallback_reason,
    )


def _validate_sha256(value: str, field: str) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise ProposalError("invalid_evidence", f"{field} 必须是 64 位 sha256")
    if any(ch not in "0123456789abcdef" for ch in value):
        raise ProposalError("invalid_evidence", f"{field} 必须是 64 位 sha256")


def _validate_switches(switches: Switches, domains: Mapping[str, Sequence[str]]) -> None:
    if not isinstance(switches, Switches):
        raise ProposalError("switch_out_of_domain", "switches 必须是三维开关")
    for dim in SWITCH_KEYS:
        value = getattr(switches, dim)
        if value not in domains[dim]:
            raise ProposalError(
                "switch_out_of_domain",
                f"{dim}={value!r} 不在 shell 候选域 {list(domains[dim])}",
            )


def validate_draft(draft: ProposalDraft, domains: Mapping[str, Sequence[str]]) -> None:
    normalized = normalize_domains(domains)
    _validate_switches(draft.switches, normalized)
    if draft.source not in SOURCES:
        raise ProposalError("invalid_evidence", f"未知 source: {draft.source!r}")
    if draft.source == "llm" and draft.fallback_used:
        raise ProposalError("invalid_evidence", "source=llm 的草稿不得标记 fallback")
    if draft.cost_source not in COST_SOURCES:
        raise ProposalError("invalid_evidence", f"未知 cost_source: {draft.cost_source!r}")
    if draft.cost_usd < 0:
        raise ProposalError("invalid_evidence", "cost_usd 不得为负")
    if not draft.model:
        raise ProposalError("invalid_evidence", "model 不得为空")
    _validate_sha256(draft.prompt_sha256, "prompt_sha256")
    _validate_sha256(draft.response_sha256, "response_sha256")
    usage = normalize_usage(draft.usage)
    if set(usage) != set(USAGE_KEYS):
        raise ProposalError("invalid_evidence", "usage 键不完整")


def _overlay_evidence(base: ProposalDraft, evidence: ProposalDraft) -> ProposalDraft:
    return replace(
        base,
        diagnosis=evidence.diagnosis or base.diagnosis,
        reason=evidence.reason or base.reason,
        model=evidence.model or base.model,
        prompt_sha256=evidence.prompt_sha256 or base.prompt_sha256,
        response_sha256=evidence.response_sha256 or base.response_sha256,
        usage=normalize_usage(evidence.usage),
        cost_usd=float(evidence.cost_usd),
        cost_source=evidence.cost_source,
        fallback_used=True,
        fallback_reason=evidence.fallback_reason,
    )


def build_proposal(
    history: SearchHistory,
    parent: Genome,
    domains: Mapping[str, Sequence[str]] | None = None,
    *,
    draft: ProposalDraft | None = None,
    evidence: ProposalDraft | None = None,
    fallback_reason: str | None = None,
    created_at: str | None = None,
    clock: Callable[[], str] = utc_now,
    rank: Callable[[Switches], tuple[Any, ...]] | None = None,
    max_cost_usd: float | None = None,
) -> Proposal:
    """把草稿 / 网格回退统一落成 v1 ``Proposal``（扩展字段全在 ext）。"""
    if not isinstance(history, SearchHistory):
        raise TypeError("proposer 只接受 SearchHistory；ReportArchive 不得进入搜索回路")
    normalized = normalize_domains(domains)
    filtered, excluded = filter_history(history, parent.scenario_version)
    timestamp = created_at or clock()

    chosen: ProposalDraft
    if draft is not None:
        try:
            validate_draft(draft, normalized)
            if max_cost_usd is not None and draft.cost_usd > max_cost_usd + 1e-12:
                raise ProposalError(
                    "proposal_over_budget",
                    f"提案成本 {draft.cost_usd} 超过预算 {max_cost_usd}",
                )
        except ProposalError as exc:
            evidence = evidence or draft
            fallback_reason = exc.reason
            chosen = grid_draft(
                filtered,
                parent,
                normalized,
                created_at=timestamp,
                rank=rank,
                fallback_reason=fallback_reason,
            )
            chosen = _overlay_evidence(chosen, evidence)
        else:
            chosen = draft
    else:
        chosen = grid_draft(
            filtered,
            parent,
            normalized,
            created_at=timestamp,
            rank=rank,
            fallback_reason=fallback_reason,
        )
        if evidence is not None:
            chosen = _overlay_evidence(chosen, evidence)

    if fallback_reason is None and chosen.fallback_used:
        fallback_reason = chosen.fallback_reason or "grid"
    fallback_used = bool(chosen.fallback_used or fallback_reason is not None)
    ext = {
        "diagnosis": chosen.diagnosis,
        "model": chosen.model,
        "prompt_sha256": chosen.prompt_sha256,
        "response_sha256": chosen.response_sha256,
        "usage": normalize_usage(chosen.usage),
        "cost_source": chosen.cost_source,
        "counted_as": COUNTED_AS,
        "fallback": {"used": fallback_used, "reason": fallback_reason},
        "source": chosen.source,
        "history": {
            "scenario_version": filtered.scenario_version,
            "entries": len(filtered.entries),
            "excluded": excluded,
        },
    }
    proposal_id = (
        "prop-"
        + content_hash(
            {
                "parent": parent.genome_id,
                "switches": chosen.switches.to_dict(),
                "history": [entry.task_id for entry in filtered.entries],
                "source": chosen.source,
                "diagnosis": chosen.diagnosis,
                "fallback_reason": fallback_reason,
            }
        )[:16]
    )
    proposer_id = f"llm:{chosen.model}" if chosen.source == "llm" else "grid"
    payload = {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "proposal_id": proposal_id,
        "scenario_version": parent.scenario_version,
        "proposer_id": proposer_id,
        "parent_id": parent.genome_id,
        "switches": chosen.switches.to_dict(),
        "rationale": chosen.reason or chosen.diagnosis or "proposal",
        "created_at": timestamp,
        "cost_usd": float(chosen.cost_usd),
        "ext": ext,
    }
    try:
        return Proposal.from_dict(payload)
    except ValidationError as exc:
        raise ProposalError("invalid_proposal", str(exc)) from exc


def proposal_ext_keys() -> tuple[str, ...]:
    """测试 / 文档用的 ext 契约键。"""
    return (
        "diagnosis",
        "model",
        "prompt_sha256",
        "response_sha256",
        "usage",
        "cost_source",
        "counted_as",
        "fallback",
        "source",
        "history",
    )
