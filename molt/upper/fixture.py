"""Deterministic synthetic graph fixture (ops/dev) over seed assets.

This fixture is explicitly *not* a real evaluation. It creates:
- a synthetic root and two branches (ops/dev) with committed empty context tips;
- context nodes referencing seed-import assets;
- one candidate under dev that never moves a tip.

Every node is ``origin="synthetic"`` or ``seed_import`` and
``eval_state="not_evaluated"``. No ``UpperEvaluation`` / ``UpperDecision`` is
produced, and no core verdict is implied. Node ids are stable across rebuilds
because they hash semantic fields only (no created_at/author).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .graph import UpperGraphStore, build_context_manifest
from .registry import UpperRegistry
from .types import UpperAsset

SYNTHETIC_SCENARIO_VERSION = "upper-synthetic-1"


def _asset_ref(asset: UpperAsset) -> dict[str, str]:
    return {"asset_id": asset.asset_id, "version": asset.version, "hash": asset.hash}


def _manifest(
    parent: str,
    *,
    assets: list[UpperAsset] | tuple[UpperAsset, ...],
    tasks: tuple[str, ...],
) -> dict[str, Any]:
    policy = [
        _asset_ref(asset) for asset in assets if asset.kind == "policy_memory"
    ]
    skills = [_asset_ref(asset) for asset in assets if asset.kind == "skill"]
    return build_context_manifest(
        parents=[parent],
        policy_memory_refs=policy,
        skill_refs=skills,
        scenario_version=SYNTHETIC_SCENARIO_VERSION,
        task_refs=tasks,
        project_requirements={"ref": "synthetic:fixture", "hash": None},
        budget={"limits": {"mode": "synthetic", "max_model_calls": 0}},
    )


def build_synthetic_fixture(
    *,
    state_dir: Path | str,
    repo_root: Path | str,
    seed_assets: list[UpperAsset] | tuple[UpperAsset, ...],
    registry: UpperRegistry | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    registry = registry or UpperRegistry(state_dir, repo_root=repo_root)
    for asset in seed_assets:
        try:
            registry.get(asset.asset_id)
        except Exception:
            registry.register(asset)
    graph = UpperGraphStore(state_dir, repo_root=repo_root, registry=registry)
    root = graph.create_root(
        author="synthetic-fixture",
        message="synthetic root",
        scenario_version=SYNTHETIC_SCENARIO_VERSION,
        scenario_refs=["synthetic:fixture"],
        project_requirements={"ref": "synthetic:fixture", "hash": None},
        budget={"limits": {"mode": "synthetic", "max_model_calls": 0}},
    )
    ops = graph.fork(
        "ops",
        root.node_id,
        author="synthetic-fixture",
        permission_scope="read:seed/ops",
        approval_level="standard",
    )
    dev = graph.fork(
        "dev",
        root.node_id,
        author="synthetic-fixture",
        permission_scope="read:seed/dev",
        approval_level="standard",
    )
    ops_base = graph.append_node(
        [root.node_id],
        kind="context",
        manifest=_manifest(root.node_id, assets=(), tasks=("synthetic:ops",)),
        message="ops base",
        origin="synthetic",
        eval_state="not_evaluated",
        source_category="synthetic_fixture",
        author="synthetic-fixture",
    )
    dev_base = graph.append_node(
        [root.node_id],
        kind="context",
        manifest=_manifest(root.node_id, assets=(), tasks=("synthetic:dev",)),
        message="dev base",
        origin="synthetic",
        eval_state="not_evaluated",
        source_category="synthetic_fixture",
        author="synthetic-fixture",
    )
    graph.switch(ops.branch_id, ops_base.node_id, expected_tip=root.node_id, author="synthetic-fixture")
    graph.switch(dev.branch_id, dev_base.node_id, expected_tip=root.node_id, author="synthetic-fixture")
    # Keep ops as the active branch and leave dev as an independent tip.
    graph.switch(
        ops.branch_id,
        ops_base.node_id,
        expected_tip=ops_base.node_id,
        expected_active_branch=dev.branch_id,
        author="synthetic-fixture",
    )

    ops_assets = [asset for asset in seed_assets if asset.seed_branch == "ops"]
    dev_assets = [asset for asset in seed_assets if asset.seed_branch == "dev"]
    ops_seed = graph.append_node(
        [ops_base.node_id],
        kind="context",
        manifest=_manifest(ops_base.node_id, assets=ops_assets, tasks=("synthetic:ops",)),
        message="ops seed context",
        origin="seed_import",
        eval_state="not_evaluated",
        source_category="seed_import",
        author="synthetic-fixture",
    )
    dev_candidate = graph.create_candidate(
        dev_base.node_id,
        _manifest(dev_base.node_id, assets=dev_assets, tasks=("synthetic:dev",)),
        "synthetic:dev-candidate",
        author="synthetic-fixture",
        message="dev seed candidate (not evaluated)",
        evidence_refs=["synthetic:fixture"],
        origin="synthetic",
        eval_state="not_evaluated",
        source_category="synthetic_fixture",
    )
    return {
        "graph": graph,
        "registry": registry,
        "branches": {"ops": ops, "dev": dev},
        "nodes": {
            "root": root,
            "ops_base": ops_base,
            "dev_base": dev_base,
            "ops_seed": ops_seed,
            "dev_candidate": dev_candidate,
        },
        "seed_assets": list(seed_assets),
    }
