"""Deterministic upper-layer retrieval over registered asset versions.

B1 intentionally supports only exact and prefix matching over explicit
``content.retrieval_keys``. There is no embedding, semantic search, or RAG
execution. A miss is returned explicitly as an empty hit list.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .registry import UpperRegistry
from .types import ASSET_STATUSES, UpperAsset

RETRIEVAL_MODE = "deterministic-exact-prefix"
EXACT_SCORE = 1.0
PREFIX_SCORE = 0.5


class RetrievalError(ValueError):
    """Raised for invalid retrieval input."""


@dataclass(frozen=True)
class RetrievalHit:
    asset_id: str
    asset_version: str
    status: str
    source: str
    family: str
    score: float
    match_type: str
    matched_key: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "asset_version": self.asset_version,
            "status": self.status,
            "source": self.source,
            "family": self.family,
            "score": self.score,
            "match_type": self.match_type,
            "matched_key": self.matched_key,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RetrievalResult:
    family: str
    query: str | None
    task_id: str | None
    statuses: tuple[str, ...]
    hits: tuple[RetrievalHit, ...]

    @property
    def empty(self) -> bool:
        return not self.hits

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "query": self.query,
            "task_id": self.task_id,
            "mode": RETRIEVAL_MODE,
            "statuses": list(self.statuses),
            "hits": [hit.to_dict() for hit in self.hits],
            "empty": self.empty,
        }


class RetrievalIndex:
    def __init__(self, assets: Iterable[UpperAsset]) -> None:
        self._assets = tuple(sorted(assets, key=lambda item: item.asset_id))

    @classmethod
    def from_registry(
        cls,
        registry: UpperRegistry,
        *,
        statuses: tuple[str, ...] | None = ("active",),
    ) -> RetrievalIndex:
        if statuses is None:
            return cls(registry.list_assets())
        return cls(
            [
                asset
                for status in statuses
                for asset in registry.list_assets(status=status)
            ]
        )

    def retrieve(
        self,
        family: str,
        *,
        query: str | None = None,
        task_id: str | None = None,
        statuses: tuple[str, ...] | None = ("active",),
    ) -> RetrievalResult:
        if not family.strip():
            raise RetrievalError("family is required")
        needle = query if query is not None else task_id
        if needle is None or not needle.strip():
            raise RetrievalError("query or task_id is required")
        needle = needle.strip().lower()
        family_key = family.strip().lower()
        if statuses is not None:
            unknown = set(statuses) - set(ASSET_STATUSES)
            if unknown:
                raise RetrievalError(f"unknown asset statuses: {sorted(unknown)}")

        hits: list[RetrievalHit] = []
        for asset in self._assets:
            if asset.family.strip().lower() != family_key:
                continue
            if statuses is not None and asset.status not in statuses:
                continue
            best: RetrievalHit | None = None
            for key in self._retrieval_keys(asset):
                key_lower = key.strip().lower()
                if not key_lower:
                    continue
                if key_lower == needle:
                    candidate = self._hit(asset, EXACT_SCORE, "exact", key)
                elif key_lower.startswith(needle):
                    candidate = self._hit(asset, PREFIX_SCORE, "prefix", key)
                else:
                    continue
                if best is None or candidate.score > best.score:
                    best = candidate
            if best is not None:
                hits.append(best)

        hits.sort(key=lambda hit: (-hit.score, hit.asset_id))
        return RetrievalResult(
            family=family.strip(),
            query=query,
            task_id=task_id,
            statuses=tuple(statuses) if statuses is not None else tuple(ASSET_STATUSES),
            hits=tuple(hits),
        )

    @staticmethod
    def _retrieval_keys(asset: UpperAsset) -> tuple[str, ...]:
        raw = asset.content.get("retrieval_keys", [])
        if isinstance(raw, str):
            return (raw,)
        if isinstance(raw, list):
            return tuple(str(item) for item in raw)
        return ()

    @staticmethod
    def _hit(asset: UpperAsset, score: float, match_type: str, key: str) -> RetrievalHit:
        expected_score = EXACT_SCORE if match_type == "exact" else PREFIX_SCORE
        if score != expected_score:
            raise AssertionError("retrieval score table drifted")
        return RetrievalHit(
            asset_id=asset.asset_id,
            asset_version=asset.version,
            status=asset.status,
            source=asset.source,
            family=asset.family,
            score=score,
            match_type=match_type,
            matched_key=key,
            reason=f"{match_type} match on {key!r}",
        )
