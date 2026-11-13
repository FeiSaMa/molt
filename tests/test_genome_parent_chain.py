"""genome lineage：除一条历史悬空引用外，parent_id 必须能在仓库内解析。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# `shells/probe/genomes/gen2.json` 的 parent 是 probe 早期未入库的 genome；
# 历史证据保留原样，仅此一条允许悬空。
DANGLING_PARENT = "sha256:397d3453bbfdca2092891e9da3406a544c5f7824ea116f4f389a1ce770369433"
DANGLING_GENOME = Path("shells/probe/genomes/gen2.json")

ROOT_GENOME = Path("shells/svc-config/genomes/v0.json")


def _genome_paths() -> list[Path]:
    return sorted(ROOT.glob("shells/*/genomes/*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _by_id() -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    for path in _genome_paths():
        genome_id = _load(path).get("genome_id")
        assert genome_id, f"{path.relative_to(ROOT)} 缺少 genome_id"
        assert genome_id not in mapping, f"重复 genome_id: {genome_id}"
        mapping[genome_id] = path.relative_to(ROOT)
    return mapping


def test_genome_ids_are_unique() -> None:
    assert len(_by_id()) == len(_genome_paths())


def test_active_genome_count_is_15() -> None:
    assert len(_genome_paths()) == 15


def test_non_root_parents_resolve_in_repo() -> None:
    mapping = _by_id()
    dangling: list[tuple[Path, str]] = []
    for path in _genome_paths():
        relative = path.relative_to(ROOT)
        parent_id = _load(path).get("parent_id")
        if not parent_id:
            continue
        if parent_id in mapping:
            assert mapping[parent_id] != relative, f"{relative} 的 parent 指向自身"
            continue
        dangling.append((relative, parent_id))

    assert dangling, "预期至少有一条历史悬空引用"
    assert sorted(dangling) == [(DANGLING_GENOME, DANGLING_PARENT)]


def test_dangling_parent_belongs_to_probe_gen2_only() -> None:
    parent_id = _load(ROOT / DANGLING_GENOME).get("parent_id")
    assert parent_id == DANGLING_PARENT


def test_svc_config_v0_is_root() -> None:
    genome = _load(ROOT / ROOT_GENOME)
    assert genome.get("parent_id") in (None, ""), "svc-config v0 必须是 root genome"
