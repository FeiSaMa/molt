"""脱敏：canonical evidence = 脱敏后的字节。

脱敏只改字符串值；一次遍历后断言 ``pre_metrics == post_metrics``，
不一致直接失败，避免"脱敏把指标也改了"。
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from molt.core.errors import RedactionError
from molt.core.fsutil import atomic_write_text
from molt.core.hash import canonical_json_bytes, content_hash, sha256_bytes

REDACTION_VERSION = "1"


@dataclass(frozen=True, slots=True)
class RedactionRule:
    name: str
    pattern: re.Pattern[str]
    replacement: str


DEFAULT_RULES: tuple[RedactionRule, ...] = (
    RedactionRule(
        "api_key_sk",
        re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
        "<redacted:api_key>",
    ),
    RedactionRule(
        "github_token",
        re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
        "<redacted:github_token>",
    ),
    RedactionRule(
        "bearer_token",
        re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{10,}"),
        "<redacted:bearer>",
    ),
    RedactionRule(
        "key_value_secret",
        re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[\"']?[A-Za-z0-9._\-]{8,}"),
        r"\1=<redacted:secret>",
    ),
    RedactionRule(
        "home_path",
        re.compile(r"/home/[A-Za-z0-9._-]+"),
        "<home>",
    ),
)

SECRET_SCAN_RULES: tuple[RedactionRule, ...] = DEFAULT_RULES[:4]


def _metrics(value: Any, path: str = "$") -> dict[str, Any]:
    """只收集结构与数字；字符串内容不参与比较。"""
    numbers: list[tuple[str, float]] = []
    keys: list[str] = []
    shape: list[tuple[str, str]] = []

    def walk(item: Any, current: str) -> None:
        if isinstance(item, dict):
            shape.append((current, "object"))
            for key in sorted(item):
                keys.append(f"{current}.{key}")
                walk(item[key], f"{current}.{key}")
        elif isinstance(item, list):
            shape.append((current, f"array[{len(item)}]"))
            for index, child in enumerate(item):
                walk(child, f"{current}[{index}]")
        elif isinstance(item, bool):
            shape.append((current, f"bool:{item}"))
        elif isinstance(item, (int, float)):
            numbers.append((current, float(item)))
            shape.append((current, "number"))
        elif item is None:
            shape.append((current, "null"))
        elif isinstance(item, str):
            shape.append((current, "string"))
        else:
            shape.append((current, type(item).__name__))

    walk(value, path)
    return {"numbers": numbers, "keys": keys, "shape": shape}


def evidence_metrics(value: Any) -> dict[str, Any]:
    """公开的指标视图，供脱敏前后比较。"""
    return _metrics(value)


def evidence_bundle(
    *,
    scenario_id: str,
    scenario_version: str,
    runs: Iterable[Any],
    results: Iterable[Any],
    decisions: Iterable[Any],
) -> dict[str, Any]:
    """把一次证据链收成 canonical evidence；写 manifest 与审计共用同一形状。"""
    return {
        "scenario_id": scenario_id,
        "scenario_version": scenario_version,
        "runs": [record.to_dict() for record in sorted(runs, key=lambda item: item.run_id)],
        "results": [
            record.to_dict() for record in sorted(results, key=lambda item: item.result_id)
        ],
        "decisions": [
            record.to_dict() for record in sorted(decisions, key=lambda item: item.decision_id)
        ],
    }


def _redact_string(value: str, rules: Iterable[RedactionRule]) -> tuple[str, int]:
    total = 0
    result = value
    for rule in rules:
        result, count = rule.pattern.subn(rule.replacement, result)
        total += count
    return result, total


def redact_evidence(
    value: Any,
    *,
    rules: Iterable[RedactionRule] = DEFAULT_RULES,
    redaction_version: str = REDACTION_VERSION,
    mutator: Callable[[Any], Any] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """返回 ``(脱敏对象, manifest)``；指标变化即失败。"""
    pre = evidence_metrics(value)
    if mutator is not None:
        result = mutator(copy.deepcopy(value))
        replacements = 0
    else:
        rules = tuple(rules)
        replacements = 0

        def walk(item: Any) -> Any:
            nonlocal replacements
            if isinstance(item, dict):
                return {key: walk(child) for key, child in item.items()}
            if isinstance(item, list):
                return [walk(child) for child in item]
            if isinstance(item, str):
                redacted, count = _redact_string(item, rules)
                replacements += count
                return redacted
            return item

        result = walk(copy.deepcopy(value))

    post = evidence_metrics(result)
    if pre != post:
        raise RedactionError("脱敏前后指标不一致：拒绝把改变指标的操作计入 canonical evidence")
    manifest = {
        "redaction_version": redaction_version,
        "replacements": replacements,
        "rules": [rule.name for rule in (rules if mutator is None else ())],
        "metrics_hash": content_hash(pre),
        "source_hash": sha256_bytes(canonical_json_bytes(value)),
        "canonical_hash": sha256_bytes(canonical_json_bytes(result)),
        "pre_metrics": pre,
        "post_metrics": post,
    }
    return result, manifest


def _safe_owner(owner_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in owner_id)
    if not safe:
        raise RedactionError(f"非法 owner_id: {owner_id!r}")
    return safe


def redaction_manifest_path(state_root: Path, owner_id: str) -> Path:
    """manifest 的固定落点，写入与审计共用。"""
    return Path(state_root) / "redaction" / f"{_safe_owner(owner_id)}.json"


def write_redaction_manifest(state_root: Path, owner_id: str, manifest: dict[str, Any]) -> Path:
    """把脱敏 manifest 落到 ``state/redaction/<owner>.json``。"""
    target = redaction_manifest_path(state_root, owner_id)
    atomic_write_text(target, canonical_json_bytes(manifest).decode("utf-8"))
    return target


def scan_secrets(text: str) -> list[str]:
    """简单正则 secret scan；命中即返回规则名。"""
    hits = []
    for rule in SECRET_SCAN_RULES:
        if rule.pattern.search(text):
            hits.append(rule.name)
    return hits


def redact_file_bytes(data: bytes) -> tuple[bytes, dict[str, Any]]:
    """对 canonical JSON 字节脱敏，返回 canonical 字节与 manifest。"""
    import json

    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        raise RedactionError(f"evidence 不是合法 JSON: {exc}") from exc
    result, manifest = redact_evidence(value)
    return canonical_json_bytes(result), manifest
