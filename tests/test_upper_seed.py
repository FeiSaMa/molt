from __future__ import annotations

import json
import subprocess
import os
from pathlib import Path

import pytest

from molt.upper.fixture import build_synthetic_fixture
from molt.upper.seed import (
    IMPORTER_VERSION,
    asset_record_hash,
    denied_name,
    import_seeds,
    scan_sensitive,
    seed_identity_hash,
)
from molt.upper.types import UpperAsset

REPO = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get("MOLT_SEED_BASE", "~/feisama-base")).expanduser()


def _git_status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _fake_seed_base(tmp_path: Path, *, text: str) -> Path:
    base = tmp_path / "base"
    lesson = base / "docs" / "lessons" / "2026-08-23-chengdu-public-ip-ipv6.md"
    lesson.parent.mkdir(parents=True)
    lesson.write_text(text, encoding="utf-8")
    return base


@pytest.mark.skipif(not BASE.is_dir(), reason="feisama-base not present")
def test_seed_import_hashes_summary_and_public_projection() -> None:
    result = import_seeds()
    assert len(result.assets) >= 3
    assert result.skipped == []
    for asset in result.assets:
        assert asset.kind in {"policy_memory", "skill"}
        assert asset.origin == "seed_import"
        assert asset.eval_state == "not_evaluated"
        assert asset.status == "draft"
        assert asset.provenance == "seed_import/reconstructed"
        assert asset.verified_lineage is False
        assert asset.importer_version == IMPORTER_VERSION
        assert asset.source_path_hash and len(asset.source_path_hash) == 64
        assert asset.content_hash and len(asset.content_hash) == 64
        assert asset.seed_identity_hash and len(asset.seed_identity_hash) == 64
        assert asset.record_hash and len(asset.record_hash) == 64
        assert asset.extracted_summary
        assert len(asset.extracted_summary) <= 60
        assert "\n" not in asset.extracted_summary
        assert asset.date_unknown in {True, False}

    public = json.dumps(result.to_public_dict(), ensure_ascii=False)
    assert "/home/" not in public
    assert "feisama-base" not in public
    assert '"source_path"' not in public
    assert "source_path_hash" in public
    assert "token=" not in public
    assert "password =" not in public.lower()


@pytest.mark.skipif(not BASE.is_dir(), reason="feisama-base not present")
def test_repeated_seed_import_has_stable_identity_and_record_hash() -> None:
    first = import_seeds()
    second = import_seeds()
    first_pairs = [(a.asset_id, a.seed_identity_hash, a.record_hash) for a in first.assets]
    second_pairs = [(a.asset_id, a.seed_identity_hash, a.record_hash) for a in second.assets]
    assert first_pairs == second_pairs
    assert len({pair[1] for pair in first_pairs}) == len(first_pairs)


def test_seed_identity_and_asset_record_hash_are_distinct() -> None:
    base = {
        "source_path_hash": "a" * 64,
        "content_hash": "b" * 64,
        "summary": "summary",
        "classification": "lesson:test",
    }
    identity = seed_identity_hash(**base)
    assert identity == seed_identity_hash(**base)
    assert identity != seed_identity_hash(**{**base, "summary": "changed"})
    assert identity != seed_identity_hash(**{**base, "classification": "lesson:other"})

    payload = {"a": 1, "b": 2}
    assert asset_record_hash(payload) == asset_record_hash({"b": 2, "a": 1})
    assert asset_record_hash(payload) != asset_record_hash({"a": 1, "b": 3})


def test_asset_record_hash_is_renamed_from_legacy_context_hash() -> None:
    payload = {
        "asset_id": "mem-legacy",
        "kind": "policy_memory",
        "name": "legacy",
        "version": "v1",
        "source": "tests/test_upper_seed.py",
        "family": "family-a",
        "created_by": "tests",
        "content": {"note": "legacy"},
        "record_hash": "a" * 64,
    }
    asset = UpperAsset.from_dict(payload)
    assert asset.record_hash == "a" * 64
    assert asset.to_dict()["record_hash"] == "a" * 64
    assert "context_hash" not in asset.to_dict()
    assert "context_hash" not in asset.to_public_dict()

    legacy_payload = {key: value for key, value in payload.items() if key != "record_hash"}
    legacy_payload["context_hash"] = "b" * 64
    legacy = UpperAsset.from_dict(legacy_payload)
    assert legacy.record_hash == "b" * 64
    assert "context_hash" not in legacy.to_dict()


def test_sensitive_filter_and_deny_priority(tmp_path: Path) -> None:
    assert denied_name("opencode/agents/token.md") == "token"
    assert denied_name("docs/lessons/2026-secret.md") == "secret"
    assert denied_name("docs/lessons/safe.md") is None
    assert scan_sensitive("Bearer abcdefghijklmnop") == ["bearer"]
    assert {
        "password_assignment",
        "password_mention",
    } <= set(scan_sensitive("password = hunter2"))
    assert scan_sensitive("safe text") == []

    base = _fake_seed_base(
        tmp_path,
        text="---\ntitle: unsafe\ndate: 2026-01-01\n---\npassword = hunter2\n",
    )
    result = import_seeds(base=base)
    assert result.assets == []
    assert result.skipped
    skipped = result.skipped[0]
    assert skipped["reason"] == "sensitive_pattern"
    assert "patterns" in skipped
    assert "hunter2" not in json.dumps(result.to_public_dict(), ensure_ascii=False)


def test_seed_import_does_not_execute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def _boom(*args: object, **kwargs: object) -> None:
        calls.append("called")
        raise AssertionError("seed import must not execute anything")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    base = _fake_seed_base(
        tmp_path,
        text="---\ntitle: safe lesson\ndate: 2026-01-01\n---\nA safe lesson body.\n",
    )
    result = import_seeds(base=base)
    assert len(result.assets) == 1
    assert calls == []


@pytest.mark.skipif(not BASE.is_dir(), reason="feisama-base not present")
def test_synthetic_fixture_is_not_evaluated_and_has_two_branch_tips(
    tmp_path: Path,
) -> None:
    before = _git_status()
    seed = import_seeds()
    state = tmp_path / "synthetic"
    fixture = build_synthetic_fixture(
        state_dir=state,
        repo_root=REPO,
        seed_assets=seed.assets,
    )
    graph = fixture["graph"]
    branches = {branch.label: branch for branch in graph.branches()}
    assert set(branches) == {"ops", "dev"}
    assert branches["ops"].tip_node_id != branches["dev"].tip_node_id
    assert graph.active_pointer()["branch_id"] == fixture["branches"]["ops"].branch_id
    assert graph.node(fixture["nodes"]["dev_candidate"].node_id).status == "candidate"
    assert branches["dev"].tip_node_id == fixture["nodes"]["dev_base"].node_id

    for node in graph.nodes():
        assert node.eval_state == "not_evaluated"
        assert node.provenance_class in {
            "seed_import",
            "synthetic_reconstructed",
            "real_replay",
            "real_evaluation",
        }
    assert {node.origin for node in graph.nodes()} <= {"seed_import", "synthetic"}
    assert not (state / "evaluations").exists()
    assert not (state / "decisions").exists()
    assert not (state / "upper" / "evaluations").exists()
    assert not (state / "upper" / "decisions").exists()
    public = json.dumps(graph.to_public_dict(), ensure_ascii=False)
    assert "/home/" not in public
    assert "feisama-base" not in public
    assert "upper_decision" not in public

    node_count = len(graph.nodes())
    root_id = fixture["nodes"]["root"].node_id
    ops_base_id = fixture["nodes"]["ops_base"].node_id
    graph.rollback(
        branches["ops"].branch_id,
        root_id,
        expected_tip=ops_base_id,
        expected_active_branch=branches["ops"].branch_id,
        reason="synthetic fixture check",
    )
    assert graph.active_pointer()["node_id"] == root_id
    assert len(graph.nodes()) == node_count
    graph.switch(
        branches["ops"].branch_id,
        ops_base_id,
        expected_tip=root_id,
        expected_active_branch=branches["ops"].branch_id,
    )
    assert graph.active_pointer()["node_id"] == ops_base_id
    assert _git_status() == before


@pytest.mark.skipif(not BASE.is_dir(), reason="feisama-base not present")
def test_synthetic_node_ids_are_deterministic(tmp_path: Path) -> None:
    seed = import_seeds()
    first = build_synthetic_fixture(
        state_dir=tmp_path / "one",
        repo_root=REPO,
        seed_assets=seed.assets,
    )
    second = build_synthetic_fixture(
        state_dir=tmp_path / "two",
        repo_root=REPO,
        seed_assets=seed.assets,
    )
    for key, node in first["nodes"].items():
        assert node.node_id == second["nodes"][key].node_id
        assert node.context_hash == second["nodes"][key].context_hash


@pytest.mark.skipif(not BASE.is_dir(), reason="feisama-base not present")
def test_seed_assets_are_in_distinct_branches() -> None:
    result = import_seeds()
    ops = [asset for asset in result.assets if asset.seed_branch == "ops"]
    dev = [asset for asset in result.assets if asset.seed_branch == "dev"]
    assert len(ops) >= 3
    assert len(dev) >= 3
    assert len([asset for asset in result.assets if asset.classification.startswith("lesson:")]) == 8
    assert len([asset for asset in result.assets if asset.kind == "skill"]) == 4
    assert all(isinstance(asset, UpperAsset) for asset in result.assets)
