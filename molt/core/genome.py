"""Genome 规范化、id 计算与 artifact 校验。

``genome_id`` 由规范化清单（去掉自身字段）计算；清单内嵌被引用 artifact 的内容哈希。
任一字节变化 ⇒ 磁盘哈希变 ⇒ ``genome verify`` 失败；同步更新清单 ⇒ id 变。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from molt.core.errors import HashMismatch, ValidationError
from molt.core.hash import content_hash, sha256_file
from molt.core.types import Genome, Switches


def canonical_manifest(genome: Mapping[str, Any]) -> dict[str, Any]:
    """去掉 ``genome_id`` 后的规范化清单。"""
    payload = dict(genome)
    payload.pop("genome_id", None)
    return payload


def compute_genome_id(genome: Mapping[str, Any]) -> str:
    return "sha256:" + content_hash(canonical_manifest(genome))


def artifact_map(repo_root: Path, refs: Mapping[str, str]) -> dict[str, str]:
    """对给定路径清单重新计算磁盘哈希。"""
    result: dict[str, str] = {}
    for rel in refs:
        path = Path(repo_root) / rel
        if not path.is_file():
            raise HashMismatch(f"artifact 不存在或不是文件: {rel}")
        result[rel] = sha256_file(path)
    return result


def verify_genome(genome: Genome, repo_root: Path) -> list[str]:
    """返回失败原因列表；空列表即通过。"""
    errors: list[str] = []
    on_disk = artifact_map(repo_root, genome.artifacts)
    for rel, declared in genome.artifacts.items():
        actual = on_disk[rel]
        if actual != declared:
            errors.append(f"artifact 哈希不一致: {rel} declared={declared} actual={actual}")
    expected_id = compute_genome_id(genome.to_dict())
    if expected_id != genome.genome_id:
        errors.append(f"genome_id 不一致: declared={genome.genome_id} recomputed={expected_id}")
    return errors


def child_genome(
    parent: Genome,
    switches: Switches,
    *,
    repo_root: Path,
    genome_id: str | None = None,
    parent_id: str | None = None,
    artifacts: dict[str, str] | None = None,
) -> Genome:
    """由父代克隆出候选；默认复用父代 artifact 清单（磁盘未变）。"""
    payload = parent.to_dict()
    payload["parent_id"] = parent_id if parent_id is not None else parent.genome_id
    payload["switches"] = switches.to_dict()
    if artifacts is None:
        artifacts = artifact_map(repo_root, parent.artifacts)
    payload["artifacts"] = artifacts
    payload["genome_id"] = genome_id or compute_genome_id(payload)
    return Genome.from_dict(payload)


def require_v1_domains(switches: Switches) -> None:
    """提案越域检查；``Switches`` 已保证，这里给出统一错误类型。"""
    try:
        Switches.from_dict(switches.to_dict())
    except ValidationError as exc:
        raise ValidationError(f"提案开关越域: {exc}") from exc
