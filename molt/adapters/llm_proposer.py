"""DeepSeek OpenAI-compatible LLM proposer adapter。

只依赖 stdlib。Transport 通过构造注入，测试不触网；默认 transport 用
``urllib.request``。任何缺 key / 网络 / 解析失败都收敛成可识别的 fallback，
由 core 生成网格 Proposal，不向调用方抛裸异常。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from molt.core.errors import ValidationError
from molt.core.events import utc_now
from molt.core.hash import canonical_json_bytes, sha256_bytes
from molt.core.ledger import Ledger
from molt.core.proposer import (
    ProposalDraft,
    build_proposal,
    filter_history,
    normalize_domains,
    normalize_usage,
)
from molt.core.types import Genome, Proposal, SearchHistory, Switches

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TIMEOUT_S = 30.0
API_KEY_ENVS: tuple[str, ...] = ("MOLT_PROPOSER_API_KEY", "DEEPSEEK_API_KEY")

# USD / 1M tokens；provider 未实报成本时按此估算，并标 cost_source=estimate。
DEFAULT_PRICES: dict[str, float] = {
    "input": 0.28,
    "output": 0.42,
    "cache_read": 0.028,
    "cache_write": 0.28,
}

Transport = Callable[[dict[str, Any]], dict[str, Any]]

SYSTEM_PROMPT = """你是 molt 的候选提案器。只输出一个 JSON 对象，形如
{"diagnosis": "...", "switches": {"thinking": "...", "context": "...", "tools": "..."}, "reason": "..."}。
只能依据给出的 search history、当前 switches 与允许的三维枚举；不得请求留出集或报告数据，
不得引入第四维，不得输出 JSON 之外的文字。"""


def resolve_api_key(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    for name in API_KEY_ENVS:
        value = os.environ.get(name)
        if value:
            return value
    return None


def default_transport(
    *, base_url: str, api_key: str, timeout_s: float = DEFAULT_TIMEOUT_S
) -> Transport:
    """DeepSeek ``/chat/completions`` 的最小 stdlib transport。"""

    def transport(payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            url=f"{base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("provider 响应不是 JSON object")
        return parsed

    return transport


class LLMProposer:
    """SearchHistory → Proposal；构造时注入 transport / ledger / 预算。"""

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        domains: Mapping[str, Sequence[str]] | None = None,
        ledger: Ledger | None = None,
        max_cost_usd: float | None = None,
        max_retries: int = 1,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        prices: Mapping[str, float] | None = None,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.domains = normalize_domains(domains)
        self.ledger = ledger
        self.max_cost_usd = max_cost_usd
        self.max_retries = max(0, min(1, int(max_retries)))
        self.prices = dict(DEFAULT_PRICES)
        if prices:
            self.prices.update({key: float(value) for key, value in prices.items()})
        self.clock = clock
        self.api_key = resolve_api_key(api_key)
        if transport is None and self.api_key:
            transport = default_transport(
                base_url=base_url, api_key=self.api_key, timeout_s=timeout_s
            )
        self.transport = transport

    @property
    def available(self) -> bool:
        return self.transport is not None

    # -- 对外 -------------------------------------------------------------
    def propose(self, history: SearchHistory, parent: Genome) -> Proposal:
        if not isinstance(history, SearchHistory):
            raise TypeError("LLMProposer 只接受 SearchHistory；ReportArchive 不得进入搜索回路")
        filtered, _ = filter_history(history, parent.scenario_version)
        payload = self._request_payload(filtered, parent)
        prompt_sha256 = sha256_bytes(canonical_json_bytes(payload))

        response: dict[str, Any] | None = None
        response_sha256 = sha256_bytes(canonical_json_bytes({"error": "no_response"}))
        usage = dict.fromkeys(
            ("input", "output", "cache_read", "cache_write", "reasoning", "total"), 0
        )
        cost_usd = 0.0
        cost_source = "none"
        fallback_reason: str | None = None

        for _attempt in range(self.max_retries + 1):
            if self.transport is None:
                fallback_reason = "missing_api_key"
                break
            try:
                response = self.transport(payload)
            except Exception:  # noqa: BLE001 - 网络错误统一转 fallback
                response = None
                fallback_reason = "transport_error"
                continue
            if not isinstance(response, dict):
                response = None
                fallback_reason = "invalid_response"
                continue
            response_sha256 = sha256_bytes(canonical_json_bytes(response))
            usage = self._usage(response)
            cost_usd, cost_source = self._cost(response, usage)
            try:
                parsed = json.loads(self._content(response))
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                fallback_reason = "invalid_json"
                continue
            if not isinstance(parsed, dict):
                fallback_reason = "invalid_response"
                break
            switches_payload = parsed.get("switches")
            if not isinstance(switches_payload, dict) or not switches_payload:
                fallback_reason = "empty_candidate"
                break
            try:
                switches = Switches.from_dict(switches_payload)
                self._validate_switches(switches)
            except (ValidationError, ValueError):
                fallback_reason = "switch_out_of_domain"
                break
            diagnosis = parsed.get("diagnosis")
            reason = parsed.get("reason")
            if not isinstance(diagnosis, str) or not isinstance(reason, str):
                fallback_reason = "invalid_response"
                break
            if self.max_cost_usd is not None and cost_usd > self.max_cost_usd + 1e-12:
                fallback_reason = "proposal_over_budget"
                break
            draft = ProposalDraft(
                switches=switches,
                diagnosis=diagnosis,
                reason=reason,
                model=self.model,
                prompt_sha256=prompt_sha256,
                response_sha256=response_sha256,
                usage=usage,
                cost_usd=cost_usd,
                cost_source=cost_source,
                source="llm",
            )
            proposal = build_proposal(
                history,
                parent,
                self.domains,
                draft=draft,
                created_at=self.clock(),
                max_cost_usd=self.max_cost_usd,
            )
            self._record(proposal)
            return proposal

        evidence = ProposalDraft(
            switches=parent.switches,
            diagnosis=f"llm fallback: {fallback_reason}",
            reason=f"llm fallback: {fallback_reason}",
            model=self.model,
            prompt_sha256=prompt_sha256,
            response_sha256=response_sha256,
            usage=normalize_usage(usage),
            cost_usd=cost_usd,
            cost_source=cost_source,
            source="grid",
            fallback_used=True,
            fallback_reason=fallback_reason,
        )
        proposal = build_proposal(
            history,
            parent,
            self.domains,
            draft=None,
            evidence=evidence,
            fallback_reason=fallback_reason,
            created_at=self.clock(),
            max_cost_usd=self.max_cost_usd,
        )
        self._record(proposal)
        return proposal

    # -- 内部 -------------------------------------------------------------
    def _request_payload(self, history: SearchHistory, parent: Genome) -> dict[str, Any]:
        history_payload = history.to_dict()
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": canonical_json_bytes(
                        {
                            "history": history_payload,
                            "current_switches": parent.switches.to_dict(),
                            "allowed_switches": {
                                dim: list(values) for dim, values in self.domains.items()
                            },
                        }
                    ).decode("utf-8"),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }

    def _validate_switches(self, switches: Switches) -> None:
        for dim, values in self.domains.items():
            if getattr(switches, dim) not in values:
                raise ValidationError(f"switch {dim}={getattr(switches, dim)!r} 越域")

    def _content(self, response: Mapping[str, Any]) -> str:
        if "choices" in response:
            choices = response["choices"]
            if not isinstance(choices, list) or not choices:
                raise ValueError("choices 为空")
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if not isinstance(message, dict):
                raise ValueError("choices[0].message 缺失")
            content = message.get("content")
        else:
            content = response.get("content")
        if not isinstance(content, str):
            raise ValueError("响应缺少 content")
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        return text

    def _usage(self, response: Mapping[str, Any]) -> dict[str, int]:
        usage = response.get("usage")
        return normalize_usage(usage if isinstance(usage, Mapping) else None)

    def _cost(self, response: Mapping[str, Any], usage: Mapping[str, int]) -> tuple[float, str]:
        usage_payload = response.get("usage")
        for container in (response, usage_payload):
            if isinstance(container, Mapping):
                for key in ("cost_usd", "cost"):
                    value = container.get(key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        return max(0.0, float(value)), "provider"
        tokens = normalize_usage(usage)
        if tokens["total"] == 0 and tokens["input"] == 0 and tokens["output"] == 0:
            return 0.0, "none"
        billable_input = max(0, tokens["input"] - tokens["cache_read"])
        value = (
            billable_input * self.prices["input"]
            + tokens["cache_read"] * self.prices["cache_read"]
            + tokens["cache_write"] * self.prices["cache_write"]
            + tokens["output"] * self.prices["output"]
        ) / 1_000_000
        return round(value, 6), "estimate"

    def _record(self, proposal: Proposal) -> None:
        if self.ledger is None:
            return
        existing = self.ledger.read(proposal.proposal_id)
        if any(entry.get("kind") == "overhead" for entry in existing):
            return
        self.ledger.append(
            proposal.proposal_id,
            {
                "kind": "overhead",
                "cost_usd": proposal.cost_usd,
                "source": proposal.ext.get("source"),
                "model": proposal.ext.get("model"),
                "counted_as": proposal.ext.get("counted_as"),
            },
        )
