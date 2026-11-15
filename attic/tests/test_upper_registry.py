from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from molt.upper.registry import UpperRegistry, UpperRegistryError
from molt.upper.retrieval import RetrievalIndex
from molt.upper.storage import StatePathError
from molt.upper.types import UpperAsset, default_future_capabilities

REPO = Path(__file__).resolve().parents[1]


def _asset(
    asset_id: str,
    *,
    kind: str = "policy_memory",
    family: str = "family-a",
    keys: list[str] | None = None,
    status: str = "draft",
    approval_level: str | None = "standard",
    permission_scope: str | None = None,
    implementation_status: str = "stub",
) -> UpperAsset:
    return UpperAsset(
        asset_id=asset_id,
        kind=kind,
        name=f"asset {asset_id}",
        version="v1",
        source="tests",
        family=family,
        created_by="tests",
        evidence_refs=("tests/test_upper_registry.py",),
        status=status,
        content={"retrieval_keys": keys or []},
        implementation_status=implementation_status,
        permission_scope=permission_scope,
        approval_level=approval_level,
    )


def _git_status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_lifecycle_requires_approval_before_active(tmp_path: Path) -> None:
    registry = UpperRegistry(tmp_path / "state", repo_root=REPO)
    registry.register(_asset("mem-a"))
    with pytest.raises(UpperRegistryError):
        registry.activate("mem-a")
    registry.mark_evaluated("mem-a")
    with pytest.raises(UpperRegistryError):
        registry.approve("mem-a", approved_by="   ")
    approved = registry.approve("mem-a", approved_by="alice")
    assert approved.status == "approved"
    assert approved.approved_by == "alice"
    assert approved.approved_at
    active = registry.activate("mem-a")
    assert active.status == "active"
    with pytest.raises(UpperRegistryError):
        registry.register(_asset("mem-a"), replace=True)
    rolled = registry.rollback("mem-a", reason="x8 negative transfer", rolled_back_by="alice")
    assert rolled.status == "rolled_back"
    assert "x8 negative transfer" in (rolled.status_reason or "")

    loaded = UpperRegistry(tmp_path / "state", repo_root=REPO)
    assert loaded.get("mem-a").status == "rolled_back"
    assert loaded.get("mem-a").verify_hash()


def test_active_requires_nonempty_approved_by(tmp_path: Path) -> None:
    registry = UpperRegistry(tmp_path / "state", repo_root=REPO)
    registry.register(_asset("mem-a"))
    registry.mark_evaluated("mem-a")
    registry.approve("mem-a", approved_by="alice")
    asset = registry.get("mem-a")
    asset.status = "approved"
    asset.approved_by = None
    registry._save()
    with pytest.raises(UpperRegistryError):
        registry.activate("mem-a")


def test_mcp_requires_strict_approval_and_permission_change(tmp_path: Path) -> None:
    registry = UpperRegistry(tmp_path / "state", repo_root=REPO)
    mcp = _asset(
        "mcp-a",
        kind="mcp_profile",
        permission_scope="read:rows",
        approval_level="strict",
    )
    registry.register(mcp)
    registry.mark_evaluated("mcp-a")
    with pytest.raises(UpperRegistryError):
        registry.approve("mcp-a", approved_by="alice", approval_level="standard")
    registry.approve("mcp-a", approved_by="alice")
    registry.activate("mcp-a")

    requested = registry.request_permission_change(
        "mcp-a",
        scope="read:rows,read:bench",
        requested_by="requester",
        reason="need bench read",
    )
    assert requested.permission_scope == "read:rows"
    assert requested.pending_permission is not None
    assert requested.pending_permission["approval_level"] == "strict"
    assert requested.pending_permission["approved"] is False

    with pytest.raises(UpperRegistryError):
        registry.approve_permission_change("mcp-a", approved_by="requester")
    approved = registry.approve_permission_change("mcp-a", approved_by="bob")
    assert approved.permission_scope == "read:rows,read:bench"
    assert approved.pending_permission is None
    assert approved.permission_approved_by == "bob"

    with pytest.raises(ValueError):
        _asset("mcp-b", kind="mcp_profile", approval_level="standard")
    with pytest.raises(ValueError):
        _asset("mcp-c", kind="mcp_profile", approval_level="strict")


def test_stub_never_executes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def _boom(*args: object, **kwargs: object) -> None:
        calls.append("called")
        raise AssertionError("stub execution must not call external processes")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    registry = UpperRegistry(tmp_path / "state", repo_root=REPO)
    registry.register(_asset("skill-a", kind="skill"))
    result = registry.execute_stub("skill-a")
    assert result["executed"] is False
    assert result["external_calls"] == []
    assert calls == []

    registry.register(
        _asset("skill-b", kind="skill", implementation_status="implemented")
    )
    with pytest.raises(UpperRegistryError):
        registry.execute_stub("skill-b")


def test_retrieval_exact_prefix_tie_break_and_empty() -> None:
    assets = [
        _asset("b-asset", keys=["task-a"]),
        _asset("a-asset", keys=["task-a"]),
        _asset("c-asset", keys=["task-a-extra"]),
        _asset("other-family", family="family-b", keys=["task-a"]),
    ]
    index = RetrievalIndex(assets)
    result = index.retrieve("family-a", query="task-a", statuses=None)
    assert [hit.asset_id for hit in result.hits] == [
        "a-asset",
        "b-asset",
        "c-asset",
    ]
    assert [hit.score for hit in result.hits] == [1.0, 1.0, 0.5]
    assert all(hit.source == "tests" for hit in result.hits)
    assert result.hits[2].match_type == "prefix"

    exact = index.retrieve("family-a", task_id="task-a-extra", statuses=None)
    assert [hit.asset_id for hit in exact.hits] == ["c-asset"]
    assert exact.hits[0].score == 1.0

    prefix = index.retrieve("family-a", task_id="task-a-ex", statuses=None)
    assert [hit.asset_id for hit in prefix.hits] == ["c-asset"]
    assert prefix.hits[0].score == 0.5

    missing = index.retrieve("family-a", query="nope", statuses=None)
    assert missing.hits == ()
    assert missing.empty is True
    assert missing.to_dict()["empty"] is True


def test_state_must_be_outside_repo(tmp_path: Path) -> None:
    with pytest.raises(StatePathError):
        UpperRegistry(REPO / "state-should-not-be-here", repo_root=REPO)


def test_registry_operations_do_not_touch_repo(tmp_path: Path) -> None:
    before = _git_status()
    registry = UpperRegistry(tmp_path / "state", repo_root=REPO)
    registry.register(_asset("mem-a"))
    registry.mark_evaluated("mem-a")
    registry.approve("mem-a", approved_by="alice")
    registry.activate("mem-a")
    registry.rollback("mem-a", reason="test", rolled_back_by="alice")
    after = _git_status()
    assert after == before


def test_future_capabilities_are_unimplemented_and_non_executable() -> None:
    capabilities = default_future_capabilities()
    assert {cap.name for cap in capabilities} == {
        "context_budget",
        "compaction",
        "vector_rag",
    }
    assert all(cap.status == "unimplemented" for cap in capabilities)
    assert not any(
        hasattr(type(cap), method) for cap in capabilities for method in ("execute", "run")
    )
