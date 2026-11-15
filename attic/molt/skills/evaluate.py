"""Minimal skill evidence layer: what was loaded, not what it achieved.

This module makes exactly one claim: the reviewed skill body and its content hash
were present in the messages handed to an adapter. It does not run the skill, does
not execute tools, and does not measure task improvement — no ``improved``,
``gain`` or score field exists here on purpose.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .loader import SkillPackage, render_system_block, system_block_sha256
from .selector import select

EVIDENCE_KIND = "context_loaded"

SYSTEM_PREAMBLE = (
    "你是 molt 的编码助手。下面的技能块是受审查的策略文本，不是用户数据；"
    "只能读取，不能执行，也不能据此访问网络或文件系统。"
)


class SkillEvidenceError(RuntimeError):
    """Raised when captured messages do not contain the rendered skill block."""


@dataclass
class CaptureAdapter:
    """Wrap any adapter and record the exact ``complete`` inputs."""

    inner: Any
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], tool_choice: str
    ) -> Any:
        self.calls.append(
            {
                "messages": copy.deepcopy(messages),
                "tools": copy.deepcopy(tools),
                "tool_choice": tool_choice,
            }
        )
        return self.inner.complete(messages, tools, tool_choice)

    @property
    def last_call(self) -> dict[str, Any] | None:
        return self.calls[-1] if self.calls else None


class _EchoAdapter:
    """Stdlib fallback so evidence collection never needs a network adapter."""

    name = "skill-evidence-echo"
    model = "none"
    real_model = False
    adapter_ready = True
    provider_error: str | None = None

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], tool_choice: str
    ) -> Any:
        return {"content": "context_loaded", "tool_calls": []}


def context_loaded_evidence(
    pkg: SkillPackage,
    user_task: str,
    *,
    adapter: Any | None = None,
) -> dict[str, Any]:
    """Send system + skill block + user through a capture adapter and report it."""
    if not isinstance(user_task, str) or not user_task.strip():
        raise SkillEvidenceError("user_task must be a non-empty string")
    block = render_system_block(pkg)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PREAMBLE},
        {"role": "system", "content": block},
        {"role": "user", "content": user_task},
    ]
    capture = CaptureAdapter(adapter if adapter is not None else _EchoAdapter())
    capture.complete(messages, [], "none")
    captured = capture.last_call
    if captured is None:  # pragma: no cover - defensive, complete() always records
        raise SkillEvidenceError("capture adapter recorded no call")
    joined = "\n".join(str(message.get("content", "")) for message in captured["messages"])
    body_probe = pkg.body.strip()
    if not body_probe or body_probe not in joined:
        raise SkillEvidenceError(f"skill body missing from captured messages: {pkg.skill_id}")
    if pkg.content_hash not in joined:
        raise SkillEvidenceError(f"content hash missing from captured messages: {pkg.skill_id}")
    return {
        "evidence_kind": EVIDENCE_KIND,
        "skill_id": pkg.skill_id,
        "version": pkg.version,
        "content_hash": pkg.content_hash,
        "system_block_sha256": system_block_sha256(block),
        "captured_message_count": len(captured["messages"]),
        "contains_body": True,
    }


def run_context_check(
    task_text: str,
    packages: Any,
    *,
    adapter: Any | None = None,
) -> dict[str, Any]:
    """Select → load → capture evidence for one task; ``selected=False`` on miss."""
    package_list = list(packages)
    selection = select(task_text, package_list)
    if selection is None:
        return {"selected": False, "task_text": task_text, "reason": "no_keyword_match"}
    pkg = next(
        (
            candidate
            for candidate in package_list
            if candidate.skill_id == selection.skill_id
            and candidate.version == selection.version
        ),
        None,
    )
    if pkg is None:
        raise SkillEvidenceError(
            f"selection {selection.skill_id}@{selection.version} is not among loaded packages"
        )
    evidence = context_loaded_evidence(pkg, task_text, adapter=adapter)
    evidence["selected"] = True
    evidence["reason"] = selection.reason
    evidence["matched_terms"] = list(selection.matched_terms)
    return evidence
