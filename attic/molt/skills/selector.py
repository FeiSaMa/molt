"""Deterministic skill selection: exact keyword match, no ranking model.

Selection is intentionally dumb and repeatable:

1. each manifest keyword that appears in the task text scores ``2.0`` (exact),
   or ``1.0`` when it only prefixes a word token (prefix);
2. every matched ``task_families`` entry adds ``0.5`` but can never select a
   skill on its own — at least one keyword must match;
3. the highest score wins, ties break on ``skill_id`` then ``version``;
4. no embedding, no learned ranker, no semantic similarity.

A miss returns ``None``. The ``reason`` string lists matched keywords in manifest
order, e.g. ``matched keywords: 零写入, 证据``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .loader import SkillPackage

EXACT_SCORE = 2.0
PREFIX_SCORE = 1.0
FAMILY_SCORE = 0.5
SELECTION_MODE = "deterministic-exact-prefix"

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.-]*")


@dataclass(frozen=True)
class SkillSelection:
    skill_id: str
    version: str
    content_hash: str
    reason: str
    matched_terms: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "content_hash": self.content_hash,
            "reason": self.reason,
            "matched_terms": list(self.matched_terms),
            "mode": SELECTION_MODE,
        }


def _keyword_score(keyword: str, normalized: str, tokens: tuple[str, ...]) -> float:
    folded = keyword.casefold().strip()
    if not folded:
        return 0.0
    if folded in normalized:
        return EXACT_SCORE
    if any(token.startswith(folded) for token in tokens):
        return PREFIX_SCORE
    return 0.0


def _score(pkg: SkillPackage, normalized: str, tokens: tuple[str, ...]) -> tuple[float, list[str]]:
    score = 0.0
    matched: list[str] = []
    for keyword in pkg.manifest.triggers.keywords:
        weight = _keyword_score(keyword, normalized, tokens)
        if weight:
            score += weight
            matched.append(keyword)
    if not matched:
        return 0.0, []
    for family in pkg.manifest.triggers.task_families:
        if family.casefold() in normalized:
            score += FAMILY_SCORE
    return score, matched


def select(task_text: str, packages: Any) -> SkillSelection | None:
    """Pick at most one skill for ``task_text``; ``None`` when nothing matches."""
    if not isinstance(task_text, str):
        raise TypeError("task_text must be a string")
    normalized = task_text.casefold()
    tokens = tuple(_TOKEN_RE.findall(normalized))
    ranked: list[tuple[float, str, str, list[str], SkillPackage]] = []
    for pkg in packages:
        score, matched = _score(pkg, normalized, tokens)
        if not matched:
            continue
        ranked.append((score, pkg.skill_id, pkg.version, matched, pkg))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    _, skill_id, version, matched, pkg = ranked[0]
    return SkillSelection(
        skill_id=skill_id,
        version=version,
        content_hash=pkg.content_hash,
        reason=f"matched keywords: {', '.join(matched)}",
        matched_terms=tuple(matched),
    )
