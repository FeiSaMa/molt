"""Skill evolution (service layer for D5/D6): propose candidates, mirror the gate.

Scope rules:

* A candidate is **data only**. :func:`propose_candidate` writes a package on
  disk and returns a :class:`SkillCandidate`; it never registers, approves or
  activates anything.
* ``origin`` is decided by how the body was obtained, never by the caller:
  operator-supplied body → ``manual``; a configured real-model adapter →
  ``provider``. With neither, the function raises
  :class:`ProviderUnavailable` — a fake body is never invented.
* The gate is not re-implemented here. :func:`mirror_gate` calls the existing
  ``UpperEvaluator._decide`` (``molt/upper/evaluator.py:891-917``) so the
  thresholds keep a single owner.

No HTTP, no network, stdlib only.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .loader import SkillPackage
from .manifest import (
    BODY_FILENAME,
    MANIFEST_FILENAME,
    VERSION_RE,
    SkillManifest,
    canonical_json,
    package_hash,
    parse_manifest,
)

PROVIDER_NOT_CONFIGURED = "provider_not_configured"
CANDIDATE_SCHEMA_VERSION = "1.0.0"

#: Hard cap on a generated body; anything larger is treated as a parse failure.
MAX_GENERATED_BODY_CHARS = 64 * 1024
MIN_GENERATED_BODY_CHARS = 20
MAX_FEEDBACK_CHARS = 8 * 1024
MAX_BASE_BODY_IN_PROMPT = 4 * 1024

#: ``generation_meta`` keys that may be recorded. Credentials, base URLs and raw
#: request payloads are never copied into evidence.
SAFE_META_KEYS = ("authorship", "calls_model", "driver", "model")

PROPOSER_SYSTEM_PROMPT = (
    "你是技能演化提议器。用户给出一个已有技能包与评审反馈。"
    "只输出新的 SKILL.md 正文（Markdown，中文），不要输出 JSON、不要解释过程、"
    "不要编造未在反馈中出现的权限或来源。"
)

_FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)\n```\s*$", re.DOTALL)
_JSON_PREFIXES = ("{", "[")
_PHASES = ("transfer", "regression")


class SkillEvolutionError(RuntimeError):
    """Raised for invalid evolution inputs or unparseable provider output."""


class ProviderUnavailable(SkillEvolutionError):
    """Raised when no real-model provider is configured (no fake fallback)."""


@dataclass(frozen=True)
class SkillCandidate:
    """One proposed skill package; nothing about it is approved or bound."""

    skill_id: str
    version: str
    body: str
    manifest: SkillManifest
    origin: str  # "manual" | "provider"
    generation_meta: dict[str, Any]
    package_dir: Path

    @property
    def content_hash(self) -> str:
        """``sha256(canonical_json(manifest) + body)`` via the slice-A helper."""
        return package_hash(self.manifest, self.body.encode("utf-8"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "origin": self.origin,
            "generation_meta": copy.deepcopy(self.generation_meta),
            "content_hash": self.content_hash,
            "package_dir": str(self.package_dir),
        }


def _next_version(version: str) -> str:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version)
    if match is None:
        raise SkillEvolutionError(f"base version is not semver: {version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def candidate_asset_id(skill_id: str, version: str) -> str:
    """Deterministic asset id for a skill package version (single source)."""
    return f"skill-{skill_id}-{version.replace('.', '-')}"


def evidence_allows_approval(evidence: Any) -> bool:
    """Only a ``keep`` verdict with both phase sections may be approved.

    ``reject``/``rollback`` evidence records an evaluation that must not be
    promoted; the approval path enforces this instead of trusting the caller.
    """
    if not isinstance(evidence, dict):
        return False
    return (
        evidence.get("final") == "keep"
        and isinstance(evidence.get("transfer"), dict)
        and isinstance(evidence.get("regression"), dict)
    )


def _require_version(version: str) -> str:
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise SkillEvolutionError(f"candidate version must be 'X.Y.Z', got {version!r}")
    return version


def _reply_text(reply: Any) -> str:
    if isinstance(reply, str):
        return reply
    if isinstance(reply, dict):
        content = reply.get("content", "")
        if isinstance(content, str):
            return content
        raise SkillEvolutionError("provider reply content is not a string")
    content = getattr(reply, "content", None)
    if isinstance(content, str):
        return content
    raise SkillEvolutionError(f"cannot read provider reply content from {type(reply).__name__}")


def _call_adapter(adapter: Any, messages: list[dict[str, Any]]) -> Any:
    """Call ``complete`` with the runtime shape, falling back to the 1-arg form."""
    try:
        return adapter.complete(messages, [], "none")
    except TypeError:
        return adapter.complete(messages)


def parse_provider_body(raw: Any) -> str:
    """Normalise a provider reply into a skill body, or raise.

    Accepted: a fenced Markdown block (language line optional) or plain Markdown.
    Rejected (parse failure, not silently patched): empty/too short output,
    oversized output, and JSON objects/arrays.
    """
    text = _reply_text(raw).strip()
    fenced = _FENCE_RE.match(text)
    if fenced is not None:
        text = fenced.group(1).strip()
    if not text:
        raise SkillEvolutionError("provider_output_invalid: empty body")
    if text.startswith(_JSON_PREFIXES):
        raise SkillEvolutionError("provider_output_invalid: expected Markdown body, got JSON")
    if len(text) < MIN_GENERATED_BODY_CHARS:
        raise SkillEvolutionError(
            f"provider_output_invalid: body shorter than {MIN_GENERATED_BODY_CHARS} chars"
        )
    if len(text) > MAX_GENERATED_BODY_CHARS:
        raise SkillEvolutionError(
            f"provider_output_invalid: body exceeds {MAX_GENERATED_BODY_CHARS} chars"
        )
    return text


def _provider_body(base_pkg: SkillPackage, feedback_text: str, adapter: Any) -> str:
    base_body = base_pkg.body
    if len(base_body) > MAX_BASE_BODY_IN_PROMPT:
        base_body = base_body[:MAX_BASE_BODY_IN_PROMPT] + "\n…（已截断）"
    messages = [
        {"role": "system", "content": PROPOSER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"技能：{base_pkg.skill_id}@{base_pkg.version}\n"
                f"当前正文：\n{base_body}\n\n"
                f"评审反馈：\n{feedback_text}\n\n"
                "请给出修订后的 SKILL.md 正文。"
            ),
        },
    ]
    return parse_provider_body(_call_adapter(adapter, messages))


def _write_candidate_package(
    base_pkg: SkillPackage, version: str, body: str, candidate_root: Path
) -> tuple[SkillManifest, Path, str]:
    """Write ``<candidate_root>/<skill_id>/{manifest.json,SKILL.md}``; return manifest/dir/hash."""
    manifest = replace(base_pkg.manifest, version=version)
    package_dir = Path(candidate_root).expanduser() / base_pkg.skill_id
    body_bytes = body.encode("utf-8")
    digest = package_hash(manifest, body_bytes)

    manifest_path = package_dir / MANIFEST_FILENAME
    if manifest_path.is_file():
        existing = parse_manifest(manifest_path)
        if existing.version != version:
            raise SkillEvolutionError(
                f"candidate_version_conflict: {package_dir} already holds "
                f"{manifest.skill_id}@{existing.version}"
            )

    package_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(canonical_json(manifest.to_dict()) + "\n", encoding="utf-8")
    (package_dir / BODY_FILENAME).write_bytes(body_bytes)

    # Re-read from disk: the returned hash must describe the bytes actually stored.
    stored = parse_manifest(manifest_path)
    stored_bytes = (package_dir / BODY_FILENAME).read_bytes()
    stored_hash = package_hash(stored, stored_bytes)
    if stored_hash != digest:
        raise SkillEvolutionError("candidate_package_hash_mismatch: package changed while writing")
    return stored, package_dir, digest


def propose_candidate(
    base_pkg: SkillPackage,
    feedback_text: str,
    *,
    adapter: Any | None = None,
    manual_body: str | None = None,
    version: str | None = None,
    candidate_root: Path | str,
) -> SkillCandidate:
    """Propose one candidate package without registering or approving it.

    ``manual_body`` wins when given (``origin="manual"``, operator authorship).
    Otherwise a ``real_model`` adapter is required (``origin="provider"``); if it
    is missing the call raises :class:`ProviderUnavailable` and writes nothing.
    """
    if not isinstance(base_pkg, SkillPackage):
        raise SkillEvolutionError("base_pkg must be a SkillPackage")
    if not isinstance(feedback_text, str) or not feedback_text.strip():
        raise SkillEvolutionError("feedback_text must be a non-empty string")
    if len(feedback_text) > MAX_FEEDBACK_CHARS:
        raise SkillEvolutionError(
            f"feedback_text exceeds {MAX_FEEDBACK_CHARS} chars"
        )
    target_version = _require_version(version) if version is not None else _next_version(
        base_pkg.version
    )

    if manual_body is not None:
        if not isinstance(manual_body, str) or not manual_body.strip():
            raise SkillEvolutionError("manual_body must be a non-empty string")
        body = manual_body
        origin = "manual"
        generation_meta: dict[str, Any] = {"authorship": "operator", "calls_model": False}
    else:
        if adapter is None or not getattr(adapter, "real_model", False):
            raise ProviderUnavailable(PROVIDER_NOT_CONFIGURED)
        body = _provider_body(base_pkg, feedback_text, adapter)
        origin = "provider"
        generation_meta = {
            "authorship": "model",
            "calls_model": True,
            "driver": str(getattr(adapter, "name", "unknown")),
            "model": str(getattr(adapter, "model", "unknown")),
        }

    if not body.strip():
        raise SkillEvolutionError("candidate body must be a non-empty string")
    unsafe = sorted(set(generation_meta) - set(SAFE_META_KEYS))
    if unsafe:  # pragma: no cover - guarded by construction
        raise SkillEvolutionError(f"generation_meta carries unsafe keys: {', '.join(unsafe)}")

    manifest, package_dir, digest = _write_candidate_package(
        base_pkg, target_version, body, Path(candidate_root)
    )
    candidate = SkillCandidate(
        skill_id=manifest.skill_id,
        version=manifest.version,
        body=body,
        manifest=manifest,
        origin=origin,
        generation_meta=generation_meta,
        package_dir=package_dir,
    )
    if candidate.content_hash != digest:  # pragma: no cover - defensive
        raise SkillEvolutionError("candidate_package_hash_mismatch: in-memory body differs")
    return candidate


def mirror_gate(phase: str, metrics: dict[str, Any]) -> tuple[str, str]:
    """Return ``(verdict, reason)`` from the existing upper gate.

    Reuse, not re-implementation: this delegates to
    ``molt.upper.evaluator.UpperEvaluator._decide`` (``evaluator.py:891-917``),
    which only reads ``metrics``. No threshold, budget or cost rule is added
    here; a second copy of the decision logic would let the two drift.
    """
    if phase not in _PHASES:
        raise SkillEvolutionError(f"unknown phase: {phase!r}")
    if not isinstance(metrics, dict):
        raise SkillEvolutionError("metrics must be a dict")
    missing = [
        name
        for name in ("candidate", "baseline", "tasks_regressed")
        if name not in metrics
    ]
    if missing:
        raise SkillEvolutionError(f"metrics missing keys: {', '.join(missing)}")

    from molt.upper.evaluator import UpperEvaluator  # local import: keep this layer light

    return UpperEvaluator._decide(None, phase, metrics)


__all__ = [
    "CANDIDATE_SCHEMA_VERSION",
    "PROVIDER_NOT_CONFIGURED",
    "SAFE_META_KEYS",
    "ProviderUnavailable",
    "SkillCandidate",
    "SkillEvolutionError",
    "candidate_asset_id",
    "evidence_allows_approval",
    "mirror_gate",
    "parse_provider_body",
    "propose_candidate",
]
