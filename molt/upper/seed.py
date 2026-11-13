"""Small, deterministic seed importer for feisama-base knowledge.

Read-only allowlist only:
- ``docs/lessons/*.md`` (explicit selected names, no archive)
- ``opencode/AGENTS.md``
- ``opencode/agents/*.md`` (explicit selected names)
- ``opencode/skills/*/SKILL.md`` (explicit selected names)

No home copying, no execution, no write to source files. Deny-by-name wins over
allowlist. Sensitive matches skip the source and record only the category, never
the matched text. Absolute source paths never enter the public projection;
``source_path_hash`` is the stable reference.

Two hashes are separate:
- ``seed_identity_hash`` = dedupe identity over source/content/summary/class/importer;
- ``record_hash`` = full semantic asset record hash (canonical JSON).

``record_hash`` names the asset-record fingerprint; ``context_hash`` is reserved
for graph context nodes/manifests (``graph.compute_context_hash``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .types import (
    UpperAsset,
    canonical_json,
    sha256_text,
)

IMPORTER_VERSION = "seed-1"
DEFAULT_BASE = Path("/home/feisama/feisama-base")

LESSON_SELECTION: dict[str, tuple[str, ...]] = {
    "ops": (
        "2026-08-23-chengdu-public-ip-ipv6.md",
        "2026-09-08-sakura-frp-vip-expiry.md",
        "2026-09-08-systemctl-disable-deletes-unit.md",
        "2026-09-17-ssh-quoting-expansion.md",
    ),
    "dev": (
        "2026-09-05-python-curses-tui.md",
        "2026-09-15-build-agent-role-mismatch.md",
        "2026-09-16-v2-explore-denial-gate-kills-codemode.md",
        "2026-09-18-agent-overinference-from-single-datapoint.md",
    ),
}
SKILL_SELECTION: dict[str, tuple[str, ...]] = {
    "ops": ("router-ops", "host-info"),
    "dev": ("memory", "verify-chain"),
}
AGENT_SELECTION = ("critic", "verify")

DENY_NAME_MARKERS = (
    ".zshenv",
    "auth",
    "token",
    "credential",
    "secret",
    ".env",
)

SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_sk", re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")),
    ("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{20,}")),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._-]{8,}", re.IGNORECASE)),
    ("token_assignment", re.compile(r"\btoken\s*=\s*[^\s]+", re.IGNORECASE)),
    ("password_assignment", re.compile(r"\bpassword\s*=\s*[^\s]+", re.IGNORECASE)),
    ("password_mention", re.compile(r"\bpassword\b", re.IGNORECASE)),
    ("credential_mention", re.compile(r"\bcredentials?\b", re.IGNORECASE)),
    ("private_key", re.compile(r"BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}")),
)


class SeedError(ValueError):
    """Invalid seed selection or allowlist escape."""


@dataclass(frozen=True)
class SeedSource:
    category: str
    branch: str
    relative_path: str
    kind: str
    absolute_path: Path


@dataclass
class SeedImport:
    assets: list[UpperAsset] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    importer_version: str = IMPORTER_VERSION

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "importer_version": self.importer_version,
            "assets": [asset.to_public_dict() for asset in self.assets],
            "skipped": list(self.skipped),
            "counts": {
                "assets": len(self.assets),
                "skipped": len(self.skipped),
            },
        }


def _resolve_allowlisted(base: Path, relative_path: str) -> Path:
    allowed_roots = (
        base / "docs" / "lessons",
        base / "opencode" / "agents",
        base / "opencode" / "skills",
    )
    specific = base / "opencode" / "AGENTS.md"
    path = (base / relative_path).resolve()
    if path != specific.resolve():
        if not any(root.resolve() in path.parents for root in allowed_roots):
            raise SeedError(f"seed path escapes allowlist: {relative_path}")
    if not path.is_file():
        raise SeedError(f"seed path is missing: {relative_path}")
    return path


def denied_name(relative_path: str) -> str | None:
    lowered = relative_path.lower()
    name = Path(relative_path).name.lower()
    for marker in DENY_NAME_MARKERS:
        if marker in name or marker in lowered:
            return marker
    return None


def scan_sensitive(text: str) -> list[str]:
    categories: list[str] = []
    for category, pattern in SENSITIVE_PATTERNS:
        if pattern.search(text):
            categories.append(category)
    return sorted(categories)


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta, parts[2]


def _clean_summary(text: str) -> str:
    text = re.sub(r"/home/[^\s)\]`]+", "<path>", text)
    text = re.sub(r"\s+", " ", text).strip(" #*`-")
    return text


def extract_summary(text: str, *, fallback: str, classification: str) -> str:
    meta, body = _frontmatter(text)
    title = meta.get("title", "").strip()
    if not title:
        for line in body.splitlines():
            if line.startswith("#"):
                title = line.lstrip("#").strip()
                if title:
                    break
    if not title:
        title = fallback
    paragraph = ""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "```", ">", "-", "*")):
            continue
        paragraph = stripped
        break
    summary = _clean_summary(f"{classification} · {title}")
    if paragraph:
        summary = _clean_summary(f"{summary}：{paragraph}")
    return summary[:60]


def _identity_payload(
    *, source_path_hash: str, content_hash: str, summary: str, classification: str
) -> dict[str, str]:
    return {
        "source_path_hash": source_path_hash,
        "content_hash": content_hash,
        "extracted_summary": summary,
        "classification": classification,
        "importer_version": IMPORTER_VERSION,
    }


def seed_identity_hash(
    *, source_path_hash: str, content_hash: str, summary: str, classification: str
) -> str:
    return sha256_text(
        canonical_json(
            _identity_payload(
                source_path_hash=source_path_hash,
                content_hash=content_hash,
                summary=summary,
                classification=classification,
            )
        )
    )


def asset_record_hash(payload: dict[str, Any]) -> str:
    return sha256_text(canonical_json(payload))


def _selection_sources(base: Path) -> list[SeedSource]:
    sources: list[SeedSource] = []
    for branch, names in LESSON_SELECTION.items():
        for name in names:
            sources.append(
                SeedSource(
                    category="lesson",
                    branch=branch,
                    relative_path=f"docs/lessons/{name}",
                    kind="policy_memory",
                    absolute_path=base / "docs" / "lessons" / name,
                )
            )
    for branch, names in SKILL_SELECTION.items():
        for name in names:
            sources.append(
                SeedSource(
                    category="skill",
                    branch=branch,
                    relative_path=f"opencode/skills/{name}/SKILL.md",
                    kind="skill",
                    absolute_path=base / "opencode" / "skills" / name / "SKILL.md",
                )
            )
    sources.append(
        SeedSource(
            category="global_rule",
            branch="global",
            relative_path="opencode/AGENTS.md",
            kind="policy_memory",
            absolute_path=base / "opencode/AGENTS.md",
        )
    )
    for name in AGENT_SELECTION:
        sources.append(
            SeedSource(
                category="agent_rule",
                branch="global",
                relative_path=f"opencode/agents/{name}.md",
                kind="policy_memory",
                absolute_path=base / "opencode" / "agents" / f"{name}.md",
            )
        )
    return sources


def _build_asset(source: SeedSource, text: str, *, base: Path) -> UpperAsset:
    relative = source.relative_path
    source_path_hash = sha256_text(relative)
    content_hash = sha256_text(text)
    meta, _body = _frontmatter(text)
    domain = meta.get("domain", "").strip()
    classification = f"{source.category}:{domain}" if domain else source.category
    summary = extract_summary(
        text,
        fallback=Path(relative).stem,
        classification=classification,
    )
    identity_hash = seed_identity_hash(
        source_path_hash=source_path_hash,
        content_hash=content_hash,
        summary=summary,
        classification=classification,
    )
    source_category = (
        "docs/lessons"
        if source.category == "lesson"
        else "opencode/AGENTS.md"
        if source.category == "global_rule"
        else "opencode/agents"
        if source.category == "agent_rule"
        else "opencode/skills"
    )
    content = {
        "extracted_summary": summary,
        "classification": classification,
        "importer_version": IMPORTER_VERSION,
        "provenance": "seed_import/reconstructed",
        "source_path_hash": source_path_hash,
        "content_hash": content_hash,
        "source_category": source_category,
        "seed_branch": source.branch,
        "verified_lineage": False,
        "date_unknown": "date" not in meta,
    }
    record_payload = {
        "seed_identity_hash": identity_hash,
        "kind": source.kind,
        "name": summary[:60],
        "version": IMPORTER_VERSION,
        "source": f"seed://{source.branch}/{source.category}",
        "family": "seed-import",
        "created_by": "molt.upper.seed",
        "evidence_refs": [f"seed:{source_path_hash[:16]}"],
        "content": content,
        "source_path_hash": source_path_hash,
        "content_hash": content_hash,
        "extracted_summary": summary,
        "classification": classification,
        "importer_version": IMPORTER_VERSION,
        "provenance": "seed_import/reconstructed",
        "verified_lineage": False,
        "date_unknown": "date" not in meta,
        "source_category": source_category,
        "seed_branch": source.branch,
        "origin": "seed_import",
        "eval_state": "not_evaluated",
    }
    return UpperAsset(
        asset_id=f"seed-{identity_hash[:16]}",
        kind=source.kind,
        name=summary[:60],
        version=IMPORTER_VERSION,
        source=f"seed://{source.branch}/{source.category}",
        family="seed-import",
        created_by="molt.upper.seed",
        evidence_refs=(f"seed:{source_path_hash[:16]}",),
        content=content,
        source_path_hash=source_path_hash,
        content_hash=content_hash,
        extracted_summary=summary,
        classification=classification,
        importer_version=IMPORTER_VERSION,
        provenance="seed_import/reconstructed",
        verified_lineage=False,
        date_unknown="date" not in meta,
        source_category=source_category,
        seed_branch=source.branch,
        seed_identity_hash=identity_hash,
        record_hash=asset_record_hash(record_payload),
        origin="seed_import",
        eval_state="not_evaluated",
        approval_level="standard",
    )


def import_seeds(base: Path | str | None = None) -> SeedImport:
    """Import the fixed small seed set; missing/denied/sensitive sources skip."""
    base_path = Path(base or DEFAULT_BASE).expanduser().resolve()
    result = SeedImport()
    for source in _selection_sources(base_path):
        denied = denied_name(source.relative_path)
        if denied:
            result.skipped.append(
                {
                    "category": source.category,
                    "reason": "denied_name",
                    "marker": denied,
                    "source_path_hash": sha256_text(source.relative_path),
                }
            )
            continue
        try:
            path = _resolve_allowlisted(base_path, source.relative_path)
        except SeedError:
            result.skipped.append(
                {
                    "category": source.category,
                    "reason": "missing_or_outside_allowlist",
                    "source_path_hash": sha256_text(source.relative_path),
                }
            )
            continue
        text = path.read_text(encoding="utf-8")
        sensitive = scan_sensitive(text)
        if sensitive:
            result.skipped.append(
                {
                    "category": source.category,
                    "reason": "sensitive_pattern",
                    "patterns": ",".join(sensitive),
                    "source_path_hash": sha256_text(source.relative_path),
                }
            )
            continue
        result.assets.append(_build_asset(source, text, base=base_path))
    # Deduplicate by seed identity; keep first deterministic source.
    seen: set[str] = set()
    unique: list[UpperAsset] = []
    for asset in result.assets:
        identity = asset.seed_identity_hash or ""
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(asset)
    result.assets = unique
    return result
