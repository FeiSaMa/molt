from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from molt.upper.graph import (
    ApprovalError,
    ConflictError,
    ContextBinding,
    ContextChangedError,
    GraphIntegrityError,
    GraphValidationError,
    UpperGraphStore,
    build_context_manifest,
)
from molt.upper.registry import UpperRegistry
from molt.upper.storage import StatePathError
from molt.upper.types import UpperAsset, canonical_json

REPO = Path(__file__).resolve().parents[1]


def _git_status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _asset(
    asset_id: str,
    *,
    kind: str = "policy_memory",
    approval_level: str = "standard",
    permission_scope: str | None = None,
) -> UpperAsset:
    return UpperAsset(
        asset_id=asset_id,
        kind=kind,
        name=asset_id,
        version="v1",
        source="tests/test_upper_graph.py",
        family="family-a",
        created_by="tests",
        content={"note": asset_id},
        approval_level=approval_level,
        permission_scope=permission_scope,
        upper_scenario_id="upper-test",
        upper_scenario_version="upper-test-1",
    )


def _approved_asset(
    registry: UpperRegistry,
    asset_id: str,
    *,
    kind: str = "policy_memory",
    approval_level: str = "standard",
    permission_scope: str | None = None,
) -> UpperAsset:
    registry.register(
        _asset(
            asset_id,
            kind=kind,
            approval_level=approval_level,
            permission_scope=permission_scope,
        )
    )
    registry.mark_evaluated(asset_id)
    registry.approve(asset_id, approved_by="alice")
    registry.activate(asset_id)
    return registry.get(asset_id)


def _ref(asset: UpperAsset) -> dict[str, str]:
    return {"asset_id": asset.asset_id, "version": asset.version, "hash": asset.hash}


def _manifest(
    parent: str,
    *,
    refs: list[dict[str, str]] | None = None,
    scenario_version: str = "upper-test-1",
    tasks: tuple[str, ...] = ("task-a",),
    mcp: list[dict[str, str]] | None = None,
) -> dict:
    return build_context_manifest(
        parents=[parent],
        policy_memory_refs=refs or [],
        mcp_profiles=mcp or [],
        scenario_version=scenario_version,
        task_refs=tasks,
    )


def _setup(tmp_path: Path):
    state = tmp_path / "state"
    registry = UpperRegistry(state, repo_root=REPO)
    graph = UpperGraphStore(state, repo_root=REPO, registry=registry)
    root = graph.create_root(author="tester", scenario_version="upper-test-1")
    branch = graph.fork("main", root.node_id, author="tester")
    return state, registry, graph, root, branch


def _append_committed(graph: UpperGraphStore, parent: str, message: str):
    return graph.append_node(
        [parent],
        kind="evaluation",
        manifest=_manifest(parent),
        message=message,
        author="tester",
    )


def _pointer_bytes(state: Path) -> bytes:
    return (state / "upper" / "current.json").read_bytes()


DEFAULT_APPROVAL = {"approved_by": "alice", "approved_at": "2026-01-01T00:00:00Z"}


def _write_evaluation(
    state: Path,
    evaluation_id: str,
    *,
    asset_id: str = "mem-a",
    status: str = "passed",
    verdict: str | None = "keep",
    approval: dict | None = DEFAULT_APPROVAL,
    context_node_id: str | None = None,
    context_hash: str | None = None,
    subdir: str = "upper",
) -> None:
    """Persist the evaluation record a ``commit_candidate`` must be bound to."""
    directory = state / subdir / "evaluations"
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "evaluation_id": evaluation_id,
        "asset_id": asset_id,
        "status": status,
        "upper_decision": verdict,
        "approval": approval,
        "context_node_id": context_node_id,
        "context_hash": context_hash,
    }
    (directory / f"{evaluation_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_root_branch_fork_independent_heads(tmp_path: Path) -> None:
    _state, _registry, graph, root, main = _setup(tmp_path)
    dev = graph.fork("dev", root.node_id, author="tester")
    node = _append_committed(graph, root.node_id, "eval-a")
    graph.switch(main.branch_id, node.node_id, expected_head=root.node_id, author="tester")

    assert graph.branch(main.branch_id).tip_node_id == node.node_id
    assert graph.branch(dev.branch_id).tip_node_id == root.node_id
    assert graph.active_pointer()["branch_id"] == main.branch_id
    assert graph.active_pointer()["node_id"] == node.node_id


def test_switch_cas_failure_keeps_old_pointer(tmp_path: Path) -> None:
    state, _registry, graph, root, branch = _setup(tmp_path)
    node = _append_committed(graph, root.node_id, "eval-a")
    graph.switch(branch.branch_id, node.node_id, expected_head=root.node_id, author="tester")
    before = _pointer_bytes(state)

    with pytest.raises(ConflictError):
        graph.switch(
            branch.branch_id,
            root.node_id,
            expected_head=root.node_id,
            author="tester",
        )
    assert _pointer_bytes(state) == before
    assert graph.branch(branch.branch_id).tip_node_id == node.node_id
    assert graph.active_pointer()["node_id"] == node.node_id

    graph.switch(
        branch.branch_id,
        root.node_id,
        expected_head=node.node_id,
        expected_active_branch=branch.branch_id,
        author="tester",
    )
    assert graph.active_pointer()["node_id"] == root.node_id


def test_rollback_moves_head_without_deleting_history(tmp_path: Path) -> None:
    _state, _registry, graph, root, branch = _setup(tmp_path)
    first = _append_committed(graph, root.node_id, "eval-1")
    second = _append_committed(graph, first.node_id, "eval-2")
    graph.switch(branch.branch_id, first.node_id, expected_head=root.node_id, author="tester")
    graph.switch(branch.branch_id, second.node_id, expected_head=first.node_id, author="tester")
    node_count = len(graph.nodes())

    target = graph.rollback(
        branch.branch_id,
        first.node_id,
        expected_tip=second.node_id,
        expected_active_branch=branch.branch_id,
        author="tester",
        reason="negative transfer",
    )
    assert target.node_id == first.node_id
    assert graph.branch(branch.branch_id).tip_node_id == first.node_id
    assert graph.active_pointer()["node_id"] == first.node_id
    assert len(graph.nodes()) == node_count
    assert {node.node_id for node in graph.nodes()} >= {root.node_id, first.node_id, second.node_id}

    required_event_keys = (
        "event_id",
        "type",
        "branch_id",
        "old_node_id",
        "new_node_id",
        "old_active_branch",
        "new_active_branch",
        "revision",
        "created_at",
    )
    events = graph.events()
    intents = [event for event in events if event["type"] == "intent"]
    intent_keys = tuple(key for key in required_event_keys if key != "revision") + (
        "target_revision",
        "payload_hash",
    )
    assert all(key in intents[0] for key in intent_keys)
    commits = [event for event in events if event["type"] == "commit"]
    fork_event = [event for event in commits if event["op"] == "fork"][-1]
    assert fork_event["old_node_id"] == root.node_id
    assert fork_event["new_node_id"] == root.node_id
    assert fork_event["old_active_branch"] is None
    assert all(key in fork_event for key in required_event_keys)
    switches = [event for event in commits if event["op"] == "switch"]
    assert switches[0]["old_node_id"] == root.node_id
    assert switches[0]["new_node_id"] == first.node_id
    assert switches[1]["old_node_id"] == first.node_id
    assert switches[1]["new_node_id"] == second.node_id
    assert all(key in switches[0] for key in required_event_keys)
    rollback_event = [event for event in commits if event["op"] == "rollback"][-1]
    assert rollback_event["old_node_id"] == second.node_id
    assert rollback_event["new_node_id"] == first.node_id
    assert rollback_event["old_active_branch"] == branch.branch_id
    assert rollback_event["new_active_branch"] == branch.branch_id
    assert rollback_event["revision"] == graph.active_pointer()["revision"]
    assert all(key in rollback_event for key in required_event_keys)


def test_event_append_failure_leaves_pointer_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state, _registry, graph, root, branch = _setup(tmp_path)
    node = _append_committed(graph, root.node_id, "eval-a")
    before = _pointer_bytes(state)

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("event write failed")

    monkeypatch.setattr(graph, "_append_line", _boom)
    with pytest.raises(OSError):
        graph.switch(branch.branch_id, node.node_id, expected_head=root.node_id)
    assert _pointer_bytes(state) == before
    assert graph.branch(branch.branch_id).tip_node_id == root.node_id
    assert graph.active_pointer() is None


def _fault_setup(tmp_path: Path):
    state, registry, graph, root, branch = _setup(tmp_path)
    first = _append_committed(graph, root.node_id, "first")
    second = _append_committed(graph, first.node_id, "second")
    graph.switch(branch.branch_id, first.node_id, expected_head=root.node_id, author="tester")
    binding = ContextBinding.capture(graph, branch_id=branch.branch_id)
    return state, registry, graph, root, branch, first, second, binding


def test_crash_before_current_write_keeps_old_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import molt.upper.graph as graph_module

    state, registry, graph, root, branch, first, second, binding = _fault_setup(tmp_path)
    before = _pointer_bytes(state)
    before_nodes = len(graph.nodes())

    def _hook(point: str) -> None:
        if point == "before_current_write":
            raise RuntimeError("kill before current")

    monkeypatch.setattr(graph, "_fault", _hook)
    with pytest.raises(RuntimeError):
        graph.switch(branch.branch_id, second.node_id, expected_head=first.node_id)
    assert _pointer_bytes(state) == before
    assert graph.branch(branch.branch_id).tip_node_id == first.node_id

    reopened = graph_module.UpperGraphStore(state, repo_root=REPO, registry=registry)
    assert reopened.active_pointer()["node_id"] == first.node_id
    assert reopened.branch(branch.branch_id).tip_node_id == first.node_id
    assert len(reopened.nodes()) == before_nodes
    binding.assert_unchanged()
    types = [event["type"] for event in reopened.events() if event.get("op") == "switch"]
    assert types[-2:] == ["intent", "abort"]


@pytest.mark.parametrize(
    "point",
    ["after_replace_before_dir_fsync", "after_current_before_commit"],
)
def test_crash_around_current_write_recovers_new_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    point: str,
) -> None:
    import molt.upper.graph as graph_module

    state, registry, graph, root, branch, first, second, binding = _fault_setup(tmp_path)
    before_nodes = len(graph.nodes())
    if point == "after_replace_before_dir_fsync":
        def _hook(current_point: str) -> None:
            if current_point == point:
                raise RuntimeError("kill after replace before dir fsync")
        graph.fault_hook = _hook
        expected = RuntimeError
    else:
        def _hook(current_point: str) -> None:
            if current_point == point:
                raise RuntimeError("kill after current before commit")
        graph.fault_hook = _hook
        expected = graph_module.CommitEventPending
    with pytest.raises(expected):
        graph.switch(branch.branch_id, second.node_id, expected_head=first.node_id)

    reopened = graph_module.UpperGraphStore(state, repo_root=REPO, registry=registry)
    assert reopened.active_pointer()["node_id"] == second.node_id
    assert reopened.branch(branch.branch_id).tip_node_id == second.node_id
    assert len(reopened.nodes()) == before_nodes
    with pytest.raises(ContextChangedError):
        binding.assert_unchanged()
    switch_events = [event for event in reopened.events() if event.get("op") == "switch"]
    assert switch_events[-1]["type"] == "commit"
    assert switch_events[-1].get("recovered") is True


def test_crash_before_current_write_keeps_commit_node_and_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import molt.upper.graph as graph_module

    state, registry, graph, root, branch = _setup(tmp_path)
    asset = _approved_asset(registry, "mem-a")
    _write_evaluation(state, "eval-1", asset_id=asset.asset_id)
    candidate = graph.create_candidate(
        root.node_id,
        _manifest(root.node_id, refs=[_ref(asset)]),
        "eval-1",
        author="tester",
    )

    def _hook(point: str) -> None:
        if point == "before_current_write":
            raise RuntimeError("kill before current")

    monkeypatch.setattr(graph, "_fault", _hook)
    with pytest.raises(RuntimeError):
        graph.commit_candidate(
            candidate.node_id,
            root.node_id,
            {"approved_by": "alice", "approved_at": "2026-01-01T00:00:00Z"},
            evaluation_id="eval-1",
            branch_id=branch.branch_id,
        )
    reopened = graph_module.UpperGraphStore(state, repo_root=REPO, registry=registry)
    assert reopened.branch(branch.branch_id).tip_node_id == root.node_id
    assert reopened.active_pointer() is None
    assert len(reopened.nodes()) >= 3  # root, candidate, decision prepared before the fault
    assert reopened.node(candidate.node_id).status == "candidate"
    assert any(node.kind == "decision" for node in reopened.nodes())
    assert any(event.get("type") == "abort" for event in reopened.events())


def test_commit_event_append_failure_is_recoverable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import molt.upper.graph as graph_module

    state, registry, graph, root, branch, first, second, _binding = _fault_setup(tmp_path)
    original = graph._append_event

    def _flaky(event: dict) -> dict:
        if event.get("type") == "commit":
            raise OSError("commit event append failed")
        return original(event)

    monkeypatch.setattr(graph, "_append_event", _flaky)
    with pytest.raises(graph_module.CommitEventPending):
        graph.switch(branch.branch_id, second.node_id, expected_head=first.node_id)
    assert json.loads((state / "upper" / "current.json").read_text())["revision"] == 3
    monkeypatch.setattr(graph, "_append_event", original)

    reopened = graph_module.UpperGraphStore(state, repo_root=REPO, registry=registry)
    assert reopened.active_pointer()["node_id"] == second.node_id
    commits = [
        event
        for event in reopened.events()
        if event.get("type") == "commit" and event.get("op") == "switch"
    ]
    assert commits[-1]["recovered"] is True


def test_legacy_branches_active_files_are_migrated(tmp_path: Path) -> None:
    import molt.upper.graph as graph_module

    state, registry, graph, root, branch = _setup(tmp_path)
    current = json.loads((state / "upper" / "current.json").read_text())
    (state / "branches.json").write_text(
        json.dumps(
            {"schema_version": "1.0.0", "branches": current["branches"]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (state / "active.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "active": {
                    "branch_id": None,
                    "head_node_id": None,
                    "node_id": None,
                    "context_hash": None,
                    "revision": 1,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (state / "upper" / "current.json").unlink()
    reopened = graph_module.UpperGraphStore(state, repo_root=REPO, registry=registry)
    assert (state / "upper" / "current.json").is_file()
    assert reopened.branch(branch.branch_id).tip_node_id == root.node_id
    assert reopened.active_pointer() is None
    assert any(event.get("type") == "migrate_legacy" for event in reopened.events())


def test_candidate_does_not_move_tip_and_commit_requires_approval(tmp_path: Path) -> None:
    state, registry, graph, root, branch = _setup(tmp_path)
    asset = _approved_asset(registry, "mem-a")
    _write_evaluation(state, "eval-1", asset_id=asset.asset_id)
    manifest = _manifest(root.node_id, refs=[_ref(asset)])
    candidate = graph.create_candidate(
        root.node_id, manifest, "eval-1", author="tester"
    )
    assert candidate.status == "candidate"
    assert graph.branch(branch.branch_id).tip_node_id == root.node_id
    assert graph.active_pointer() is None
    before = _pointer_bytes(state)
    with pytest.raises(ApprovalError):
        graph.commit_candidate(
            candidate.node_id,
            root.node_id,
            {"approved_by": "", "approved_at": ""},
            evaluation_id="eval-1",
            branch_id=branch.branch_id,
        )
    assert _pointer_bytes(state) == before
    assert graph.branch(branch.branch_id).tip_node_id == root.node_id

    decision = graph.commit_candidate(
        candidate.node_id,
        root.node_id,
        {"approved_by": "alice", "approved_at": "2026-01-01T00:00:00Z"},
        evaluation_id="eval-1",
        branch_id=branch.branch_id,
    )
    assert decision.kind == "decision"
    assert decision.status == "committed"
    assert graph.branch(branch.branch_id).tip_node_id == decision.node_id
    assert graph.active_pointer()["node_id"] == decision.node_id
    assert graph.node(candidate.node_id).status == "candidate"
    commit_event = [
        event
        for event in graph.events()
        if event["type"] == "commit" and event["op"] == "commit"
    ][-1]
    assert commit_event["old_node_id"] == root.node_id
    assert commit_event["new_node_id"] == decision.node_id
    assert commit_event["old_active_branch"] is None
    assert commit_event["new_active_branch"] == branch.branch_id
    assert commit_event["revision"] == graph.active_pointer()["revision"]


def test_commit_rejects_unbound_or_unapproved_evaluations(tmp_path: Path) -> None:
    state, registry, graph, root, branch = _setup(tmp_path)
    asset = _approved_asset(registry, "mem-a")
    candidate = graph.create_candidate(
        root.node_id,
        _manifest(root.node_id, refs=[_ref(asset)]),
        "eval-1",
        author="tester",
    )
    before = _pointer_bytes(state)
    events_before = len(graph.events())

    def _commit(
        *,
        evaluation_id: str = "eval-1",
        approval: dict | None = None,
    ):
        return graph.commit_candidate(
            candidate.node_id,
            root.node_id,
            dict(DEFAULT_APPROVAL) if approval is None else approval,
            evaluation_id=evaluation_id,
            branch_id=branch.branch_id,
        )

    # No persisted evaluation record.
    with pytest.raises(ApprovalError):
        _commit()
    # Not passed.
    _write_evaluation(state, "eval-1", status="failed")
    with pytest.raises(ApprovalError):
        _commit()
    # Passed but not a keep decision.
    _write_evaluation(state, "eval-1", verdict="reject")
    with pytest.raises(ApprovalError):
        _commit()
    # Rejected evaluation record without any decision verdict.
    _write_evaluation(state, "eval-1", verdict=None)
    with pytest.raises(ApprovalError):
        _commit()
    # Keep but no recorded approval.
    _write_evaluation(state, "eval-1", approval=None)
    with pytest.raises(ApprovalError):
        _commit()
    # Recorded approved_at is not ISO8601 (and matches the caller).
    bad_time = {"approved_by": "alice", "approved_at": "not-a-timestamp"}
    _write_evaluation(state, "eval-1", approval=bad_time)
    with pytest.raises(ApprovalError):
        _commit(approval=dict(bad_time))
    # Fabricated approval: differs from the recorded one.
    _write_evaluation(state, "eval-1")
    with pytest.raises(ApprovalError):
        _commit(approval={"approved_by": "mallory", "approved_at": "2026-01-01T00:00:00Z"})
    with pytest.raises(ApprovalError):
        _commit(approval={"approved_by": "alice", "approved_at": "2026-02-02T00:00:00Z"})
    # Empty approved_by / invalid ISO8601.
    with pytest.raises(ApprovalError):
        _commit(approval={"approved_by": "", "approved_at": "2026-01-01T00:00:00Z"})
    with pytest.raises(ApprovalError):
        _commit(approval={"approved_by": "alice", "approved_at": "yesterday"})
    # Evaluation id not bound to the candidate / path traversal.
    with pytest.raises(ApprovalError):
        _commit(evaluation_id="eval-other")
    with pytest.raises(ApprovalError):
        _commit(evaluation_id="../eval-1")
    # Evaluated asset is not part of the candidate context.
    _write_evaluation(state, "eval-1", asset_id="mem-other")
    with pytest.raises(ApprovalError):
        _commit()
    # Unparsable evaluation record.
    path = state / "upper" / "evaluations" / "eval-1.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ApprovalError):
        _commit()
    # Context binding that is not part of the candidate history.
    _write_evaluation(state, "eval-1", context_node_id="f" * 64)
    with pytest.raises(ApprovalError):
        _commit()
    _write_evaluation(state, "eval-1", context_hash="c" * 64)
    with pytest.raises(ApprovalError):
        _commit()

    # No failure wrote the pointer, the tip, or an event.
    assert _pointer_bytes(state) == before
    assert graph.branch(branch.branch_id).tip_node_id == root.node_id
    assert len(graph.events()) == events_before
    assert graph.active_pointer() is None

    # A passed + keep evaluation with the matching approval commits.
    _write_evaluation(state, "eval-1")
    decision = _commit()
    assert decision.kind == "decision"
    assert graph.branch(branch.branch_id).tip_node_id == decision.node_id
    assert graph.active_pointer()["node_id"] == decision.node_id
    assert len(graph.events()) == events_before + 2  # intent + commit


def test_commit_reads_legacy_evaluation_directory(tmp_path: Path) -> None:
    state, registry, graph, root, branch = _setup(tmp_path)
    asset = _approved_asset(registry, "mem-a")
    candidate = graph.create_candidate(
        root.node_id,
        _manifest(root.node_id, refs=[_ref(asset)]),
        "eval-1",
        author="tester",
    )
    # Earlier demo states wrote evaluations next to registry.json.
    _write_evaluation(state, "eval-1", subdir="")
    path = state / "upper" / "evaluations" / "eval-1.json"
    assert not path.exists()

    decision = graph.commit_candidate(
        candidate.node_id,
        root.node_id,
        dict(DEFAULT_APPROVAL),
        evaluation_id="eval-1",
        branch_id=branch.branch_id,
    )
    assert graph.active_pointer()["node_id"] == decision.node_id


def test_switch_rejects_draft_and_mcp_non_strict(tmp_path: Path) -> None:
    state, registry, graph, root, branch = _setup(tmp_path)
    draft = _asset("draft-a")
    registry.register(draft)
    draft_node = UpperGraphStore(state, repo_root=REPO, registry=registry).append_node(
        [root.node_id],
        kind="context",
        manifest=_manifest(root.node_id, refs=[_ref(draft)]),
        message="draft",
        author="tester",
    )
    # Use the actual committed node returned by append_node (same parents/manifest).
    _ = draft_node
    before = _pointer_bytes(state)
    with pytest.raises(ApprovalError):
        graph.switch(branch.branch_id, draft_node.node_id, expected_head=root.node_id)
    assert _pointer_bytes(state) == before
    assert graph.branch(branch.branch_id).tip_node_id == root.node_id

    mcp_asset = SimpleNamespace(
        asset_id="mcp-a",
        kind="mcp_profile",
        version="v1",
        hash="a" * 64,
        status="active",
        approved_by="alice",
        approved_at="2026-01-01T00:00:00Z",
        approval_level="standard",
        permission_scope="read:rows",
    )

    class _Registry:
        def get(self, asset_id: str):
            assert asset_id == "mcp-a"
            return mcp_asset

    fake_registry = _Registry()
    fake_state = tmp_path / "mcp-state"
    fake = UpperGraphStore(fake_state, repo_root=REPO, registry=fake_registry)
    fake_root = fake.create_root(author="tester")
    strict = fake.fork(
        "strict", fake_root.node_id, author="tester", approval_level="strict"
    )
    profile = {
        "profile_id": "mcp-a",
        "version": "v1",
        "hash": "a" * 64,
        "permission_scope": "read:rows",
        "approval_level": "strict",
    }
    mcp_node = fake.append_node(
        [fake_root.node_id],
        kind="context",
        manifest=_manifest(fake_root.node_id, mcp=[profile]),
        message="mcp",
        author="tester",
    )
    fake_before = _pointer_bytes(fake_state)
    with pytest.raises(ApprovalError):
        fake.switch(strict.branch_id, mcp_node.node_id, expected_head=fake_root.node_id)
    assert _pointer_bytes(fake_state) == fake_before
    assert fake.branch(strict.branch_id).tip_node_id == fake_root.node_id

    standard = fake.fork("standard", fake_root.node_id, author="tester")
    with pytest.raises(ApprovalError):
        fake.switch(standard.branch_id, mcp_node.node_id, expected_head=fake_root.node_id)


def test_approved_node_can_switch(tmp_path: Path) -> None:
    _state, registry, graph, root, branch = _setup(tmp_path)
    asset = _approved_asset(registry, "mem-a")
    node = graph.append_node(
        [root.node_id],
        kind="context",
        manifest=_manifest(root.node_id, refs=[_ref(asset)]),
        message="approved-context",
        author="tester",
    )
    graph.switch(branch.branch_id, node.node_id, expected_head=root.node_id, author="tester")
    assert graph.active_pointer()["node_id"] == node.node_id
    graph.switch(
        branch.branch_id,
        root.node_id,
        expected_head=node.node_id,
        expected_active_branch=branch.branch_id,
        author="tester",
    )
    graph.switch(
        branch.branch_id,
        node.node_id,
        expected_head=root.node_id,
        expected_active_branch=branch.branch_id,
        author="tester",
    )
    assert graph.active_pointer()["node_id"] == node.node_id


def test_asset_hash_and_context_hash_mismatch_fail_closed(tmp_path: Path) -> None:
    state, registry, graph, root, branch = _setup(tmp_path)
    asset = _approved_asset(registry, "mem-a")
    wrong_ref = {"asset_id": asset.asset_id, "version": asset.version, "hash": "b" * 64}
    node = graph.append_node(
        [root.node_id],
        kind="context",
        manifest=_manifest(root.node_id, refs=[wrong_ref]),
        message="bad-asset-hash",
        author="tester",
    )
    before = _pointer_bytes(state)
    with pytest.raises(GraphValidationError):
        graph.switch(branch.branch_id, node.node_id, expected_head=root.node_id)
    assert _pointer_bytes(state) == before

    good = graph.append_node(
        [root.node_id],
        kind="context",
        manifest=_manifest(root.node_id, refs=[_ref(asset)]),
        message="good",
        author="tester",
    )
    nodes_path = state / "upper" / "nodes.jsonl"
    lines = nodes_path.read_text(encoding="utf-8").splitlines()
    target = json.loads(lines[good_index(lines, good.node_id)])
    target["context_hash"] = "c" * 64
    lines[good_index(lines, good.node_id)] = json.dumps(target, ensure_ascii=False)
    nodes_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stale_before = _pointer_bytes(state)
    with pytest.raises(GraphIntegrityError):
        UpperGraphStore(state, repo_root=REPO, registry=registry)
    assert _pointer_bytes(state) == stale_before


def good_index(lines: list[str], node_id: str) -> int:
    for index, line in enumerate(lines):
        if json.loads(line)["node_id"] == node_id:
            return index
    raise AssertionError(f"node not found: {node_id}")


def test_manifest_hash_stability_and_nan_rejection() -> None:
    refs = [
        {"asset_id": "b", "version": "1", "hash": "b" * 64},
        {"asset_id": "a", "version": "1", "hash": "a" * 64},
    ]
    first = build_context_manifest(
        parents=["p"],
        policy_memory_refs=refs,
        scenario_version="upper-test-1",
        task_refs=["t2", "t1"],
    )
    second = build_context_manifest(
        task_refs=["t1", "t2"],
        scenario_version="upper-test-1",
        policy_memory_refs=list(reversed(refs)),
        parents=["p"],
    )
    assert first["context_hash"] == second["context_hash"]
    assert canonical_json({"b": 1, "a": None}) == canonical_json({"a": None, "b": 1})
    assert canonical_json({"a": None}) == '{"a":null}'
    with pytest.raises(ValueError):
        canonical_json({"bad": float("nan")})

    mcp_a = build_context_manifest(
        parents=["p"],
        scenario_version="upper-test-1",
        mcp_profiles=[
            {
                "profile_id": "m",
                "version": "1",
                "hash": "a" * 64,
                "permission_scope": "read:a",
                "approval_level": "strict",
            }
        ],
    )
    mcp_b = build_context_manifest(
        parents=["p"],
        scenario_version="upper-test-1",
        mcp_profiles=[
            {
                "profile_id": "m",
                "version": "1",
                "hash": "a" * 64,
                "permission_scope": "read:b",
                "approval_level": "strict",
            }
        ],
    )
    assert mcp_a["context_hash"] != mcp_b["context_hash"]


def test_context_binding_detects_change(tmp_path: Path) -> None:
    _state, _registry, graph, root, branch = _setup(tmp_path)
    node = _append_committed(graph, root.node_id, "eval-a")
    graph.switch(branch.branch_id, node.node_id, expected_head=root.node_id, author="tester")
    binding = ContextBinding.capture(graph, branch_id=branch.branch_id)
    other = _append_committed(graph, node.node_id, "eval-b")
    graph.switch(branch.branch_id, other.node_id, expected_head=node.node_id, author="tester")
    with pytest.raises(ContextChangedError):
        binding.assert_unchanged()


def test_context_binding_detects_registry_approval_change(tmp_path: Path) -> None:
    _state, registry, graph, root, branch = _setup(tmp_path)
    asset = _approved_asset(registry, "mem-a")
    node = graph.append_node(
        [root.node_id],
        kind="context",
        manifest=_manifest(root.node_id, refs=[_ref(asset)]),
        message="approved-context",
        author="tester",
    )
    graph.switch(branch.branch_id, node.node_id, expected_head=root.node_id, author="tester")
    binding = ContextBinding.capture(graph, branch_id=branch.branch_id)
    binding.assert_unchanged()
    registry.rollback("mem-a", reason="negative transfer", rolled_back_by="alice")
    with pytest.raises(ContextChangedError):
        binding.assert_unchanged()


def test_no_merge_or_delete_api(tmp_path: Path) -> None:
    _state, _registry, graph, _root, _branch = _setup(tmp_path)
    for name in (
        "merge",
        "delete_node",
        "delete_branch",
        "remove_node",
        "remove_branch",
        "merge_branches",
    ):
        assert not hasattr(graph, name)
    public = graph.to_public_dict()
    assert public["merge_supported"] is False
    assert public["delete_supported"] is False
    assert {"active", "branches", "nodes"} <= set(public)


def test_state_must_be_outside_repo_and_writes_stay_outside(tmp_path: Path) -> None:
    with pytest.raises(StatePathError):
        UpperGraphStore(REPO / "graph-state", repo_root=REPO)
    before = _git_status()
    state, registry, graph, root, branch = _setup(tmp_path)
    asset = _approved_asset(registry, "mem-a")
    node = graph.append_node(
        [root.node_id],
        kind="context",
        manifest=_manifest(root.node_id, refs=[_ref(asset)]),
        message="ctx",
        author="tester",
    )
    graph.switch(branch.branch_id, node.node_id, expected_head=root.node_id, author="tester")
    assert _git_status() == before
    assert (state / "upper" / "lock").is_file()
    assert (state / "upper" / "current.json").is_file()


def test_concurrent_writers_do_not_interleave_jsonl(tmp_path: Path) -> None:
    state, _registry, graph, root, _branch = _setup(tmp_path)
    assert (state / "upper" / "lock").is_file()
    code = """
import sys
from pathlib import Path
from molt.upper.graph import UpperGraphStore, build_context_manifest
state = Path(sys.argv[1]); tag = sys.argv[2]; count = int(sys.argv[3]); repo = Path(sys.argv[4])
g = UpperGraphStore(state, repo_root=repo)
root = g.root_node()
manifest = build_context_manifest(parents=[root.node_id], scenario_version="upper-test-1")
for i in range(count):
    g.append_node([root.node_id], kind="evaluation", manifest=manifest,
                  message=f"{tag}-{i}", author=tag)
"""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(state), tag, "10", str(REPO)],
            cwd=str(REPO),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for tag in ("w1", "w2")
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, (stdout, stderr)
    lines = (state / "upper" / "nodes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 + 20
    parsed = [json.loads(line) for line in lines]
    assert len({item["node_id"] for item in parsed}) == 21
    assert graph.nodes()  # reloads and validates every node id


def test_active_pointer_waits_for_the_commit_lock(tmp_path: Path) -> None:
    state, registry, graph, _root, _branch = _setup(tmp_path)
    reader = UpperGraphStore(state, repo_root=REPO, registry=registry)
    entered = threading.Event()
    finished = threading.Event()

    def _read() -> None:
        entered.set()
        reader.active_pointer()
        finished.set()

    with graph._locked():
        thread = threading.Thread(target=_read, daemon=True)
        thread.start()
        assert entered.wait(2)
        # LOCK_SH must block while a writer holds LOCK_EX.
        assert not finished.wait(0.2)
    assert finished.wait(5)
    thread.join(5)
    assert not thread.is_alive()


def test_readers_do_not_fail_during_concurrent_commits(tmp_path: Path) -> None:
    state, registry, graph, root, branch = _setup(tmp_path)
    reader = UpperGraphStore(state, repo_root=REPO, registry=registry)
    errors: list[BaseException] = []
    stop = threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            try:
                reader.active_pointer()
                reader.nodes()
                reader.to_public_dict()
            except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
                errors.append(exc)
                return

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    tip = root.node_id
    try:
        for index in range(5):
            node = _append_committed(graph, tip, f"reader-race-{index}")
            graph.switch(branch.branch_id, node.node_id, expected_head=tip, author="tester")
            tip = node.node_id
    finally:
        stop.set()
        thread.join(5)
    assert errors == []
    assert reader.active_pointer()["node_id"] == tip


def test_graph_show_cli_outputs_json(tmp_path: Path) -> None:
    state, _registry, graph, root, branch = _setup(tmp_path)
    node = _append_committed(graph, root.node_id, "cli")
    graph.switch(branch.branch_id, node.node_id, expected_head=root.node_id, author="tester")
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-m", "molt.upper.graph_cli", "graph-show", "--state", str(state)],
        cwd=str(REPO),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    graph_payload = payload["graph"]
    assert graph_payload["active"]["branch_id"] == branch.branch_id
    assert graph_payload["merge_supported"] is False
    assert {item["node_id"] for item in graph_payload["nodes"]} >= {root.node_id, node.node_id}
