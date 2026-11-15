"""Slice-A skill runtime tests: manifest, loader, selector, binding, evidence."""

from __future__ import annotations

import dataclasses
import json
import shutil
import threading
from pathlib import Path

import pytest

from molt.agent.chat import MockAdapter
from molt.skills import (
    BindingConflict,
    BindingError,
    BindingStore,
    CaptureAdapter,
    SkillIntegrityError,
    SkillManifestError,
    SkillPackage,
    SkillPackageError,
    SkillPathError,
    context_loaded_evidence,
    load_package,
    package_hash,
    parse_manifest,
    run_context_check,
    select,
    snapshot_sha256,
    validate_package,
)
from molt.skills import binding as binding_module

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"
SKILL_ID = "repo-evidence"


@pytest.fixture()
def skills_copy(tmp_path: Path) -> Path:
    dest = tmp_path / "skills"
    shutil.copytree(SKILLS_ROOT, dest)
    return dest


def _manifest_payload() -> dict:
    path = SKILLS_ROOT / SKILL_ID / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _binding_entry(pkg: SkillPackage, **overrides: str) -> dict:
    entry = {
        "skill_id": pkg.skill_id,
        "version": pkg.version,
        "asset_id": f"skill-{pkg.skill_id}-{pkg.version}",
        "package_hash": pkg.content_hash,
    }
    entry.update(overrides)
    return entry


# --- manifest ---------------------------------------------------------------


def test_parse_manifest_accepts_repository_skill() -> None:
    manifest = parse_manifest(SKILLS_ROOT / SKILL_ID / "manifest.json")
    assert manifest.skill_id == SKILL_ID
    assert manifest.version == "1.0.0"
    assert manifest.permissions.network is False
    assert manifest.source_refs[0].source_id == "AGENTS.md"
    assert manifest.source_refs[0].available_content == "repository-policy"


def test_parse_manifest_rejects_unknown_top_level_field(tmp_path: Path) -> None:
    payload = _manifest_payload()
    payload["extra_field"] = "nope"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SkillManifestError, match="unknown fields: extra_field"):
        parse_manifest(path)


def test_parse_manifest_rejects_unknown_nested_field(tmp_path: Path) -> None:
    payload = _manifest_payload()
    payload["permissions"]["shell"] = True
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SkillManifestError, match="permissions has unknown fields: shell"):
        parse_manifest(path)


def test_parse_manifest_rejects_self_hash(tmp_path: Path) -> None:
    payload = _manifest_payload()
    payload["package_hash"] = "sha256:" + "0" * 64
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SkillManifestError, match="must not store its own hash"):
        parse_manifest(path)


def test_manifest_tool_tokens_match_skill_body() -> None:
    pkg = load_package(SKILLS_ROOT, SKILL_ID)
    assert pkg.manifest.permissions.tools == (
        "experiments_list",
        "experiments_get",
        "mcp:project_kb:search_docs",
        "mcp:project_kb:get_doc",
    )
    assert pkg.manifest.permissions.network is False
    assert pkg.manifest.permissions.filesystem == "none"
    for token in pkg.manifest.permissions.tools:
        assert token in pkg.body, f"tool token {token} is not declared in SKILL.md"


# --- hashing / loader -------------------------------------------------------


def test_package_hash_is_stable_and_body_sensitive() -> None:
    first = load_package(SKILLS_ROOT, SKILL_ID)
    second = load_package(SKILLS_ROOT, SKILL_ID)
    assert first.content_hash == second.content_hash
    assert first.content_hash.startswith("sha256:")
    assert package_hash(first.manifest, first.body.encode("utf-8")) == first.content_hash
    assert package_hash(first.manifest, b"different body") != first.content_hash


def test_load_package_pins_declared_version() -> None:
    current = load_package(SKILLS_ROOT, SKILL_ID)
    pinned = load_package(SKILLS_ROOT, SKILL_ID, "1.0.0")
    assert pinned.content_hash == current.content_hash
    with pytest.raises(SkillPackageError):
        load_package(SKILLS_ROOT, SKILL_ID, "9.9.9")


def test_validate_package_detects_tampered_body(skills_copy: Path) -> None:
    pkg = load_package(skills_copy, SKILL_ID)
    body_path = pkg.package_dir / "SKILL.md"
    body_path.write_text(pkg.body + "\n未审查追加行。\n", encoding="utf-8")
    with pytest.raises(SkillIntegrityError, match="package hash mismatch"):
        validate_package(pkg)


def test_validate_package_detects_tampered_manifest(skills_copy: Path) -> None:
    pkg = load_package(skills_copy, SKILL_ID)
    manifest_path = pkg.package_dir / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["description"] = "被篡改的描述"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SkillIntegrityError, match="package hash mismatch"):
        validate_package(pkg)


@pytest.mark.parametrize("bad_id", ["../repo-evidence", "a/b", "/etc", "..", ""])
def test_load_package_rejects_path_escape(bad_id: str) -> None:
    with pytest.raises(SkillPathError):
        load_package(SKILLS_ROOT, bad_id)


def test_load_package_rejects_symlinked_package(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    try:
        (root / "linked").symlink_to(SKILLS_ROOT / SKILL_ID, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not available on this filesystem")
    with pytest.raises(SkillPathError):
        load_package(root, "linked")


def test_load_package_rejects_extra_files(skills_copy: Path) -> None:
    extra = skills_copy / SKILL_ID / "run.sh"
    extra.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    with pytest.raises(SkillPathError, match="may only contain"):
        load_package(skills_copy, SKILL_ID)


# --- selector ---------------------------------------------------------------


def test_select_hits_with_keyword_reason() -> None:
    pkg = load_package(SKILLS_ROOT, SKILL_ID)
    selection = select("如何验证这次改动零写入？", [pkg])
    assert selection is not None
    assert selection.skill_id == SKILL_ID
    assert selection.reason == "matched keywords: 零写入"
    assert selection.matched_terms == ("零写入",)
    assert selection.content_hash == pkg.content_hash


def test_select_hits_second_task_with_different_reason() -> None:
    pkg = load_package(SKILLS_ROOT, SKILL_ID)
    first = select("如何验证这次改动零写入？", [pkg])
    second = select("跨 scenario_version 能不能比较结果？", [pkg])
    assert first is not None and second is not None
    assert second.reason == "matched keywords: scenario_version"
    assert first.reason != second.reason


def test_select_returns_none_without_keyword() -> None:
    pkg = load_package(SKILLS_ROOT, SKILL_ID)
    assert select("今天午饭吃什么", [pkg]) is None


def test_select_ties_break_on_skill_id() -> None:
    base = load_package(SKILLS_ROOT, SKILL_ID)
    alpha_manifest = dataclasses.replace(base.manifest, skill_id="aaa-skill")
    zed_manifest = dataclasses.replace(base.manifest, skill_id="zzz-skill")
    alpha = dataclasses.replace(base, skill_id="aaa-skill", manifest=alpha_manifest)
    zed = dataclasses.replace(base, skill_id="zzz-skill", manifest=zed_manifest)
    selection = select("如何验证零写入？", [zed, alpha])
    assert selection is not None
    assert selection.skill_id == "aaa-skill"


# --- evidence ---------------------------------------------------------------


def test_capture_adapter_records_complete_inputs() -> None:
    capture = CaptureAdapter(MockAdapter())
    reply = capture.complete([{"role": "user", "content": "你好"}], [], "none")
    assert reply.content
    assert len(capture.calls) == 1
    assert capture.last_call is not None
    assert capture.last_call["messages"] == [{"role": "user", "content": "你好"}]
    assert capture.last_call["tool_choice"] == "none"


def test_context_loaded_evidence_contains_body_and_hash() -> None:
    pkg = load_package(SKILLS_ROOT, SKILL_ID)
    evidence = context_loaded_evidence(pkg, "如何验证零写入？", adapter=MockAdapter())
    assert evidence["evidence_kind"] == "context_loaded"
    assert evidence["skill_id"] == SKILL_ID
    assert evidence["version"] == pkg.version
    assert evidence["content_hash"] == pkg.content_hash
    assert evidence["captured_message_count"] == 3
    assert evidence["contains_body"] is True
    assert evidence["system_block_sha256"].startswith("sha256:")
    assert "improved" not in evidence and "gain" not in evidence


def test_run_context_check_selects_and_misses() -> None:
    pkg = load_package(SKILLS_ROOT, SKILL_ID)
    hit = run_context_check("如何验证这次改动零写入？", [pkg], adapter=MockAdapter())
    assert hit["selected"] is True
    assert hit["reason"] == "matched keywords: 零写入"
    miss = run_context_check("今天午饭吃什么", [pkg])
    assert miss["selected"] is False


# --- binding ----------------------------------------------------------------


def test_binding_cas_conflict_and_history(tmp_path: Path) -> None:
    pkg = load_package(SKILLS_ROOT, SKILL_ID)
    store = BindingStore(tmp_path / "state")
    empty = store.load()
    assert empty["revision"] == 0
    assert empty["snapshot_sha256"] is None
    assert empty["skills"] == {} and empty["mcp"] == {}

    entry = _binding_entry(pkg)
    first = store.commit("skills", SKILL_ID, entry, expected_revision=0)
    assert first["revision"] == 1
    assert first["skills"][SKILL_ID] == entry
    assert first["snapshot_sha256"] == snapshot_sha256(first)
    assert len(first["snapshot_sha256"]) == 64
    assert store.history_path(1, first["snapshot_sha256"]).is_file()

    with pytest.raises(BindingConflict) as conflict:
        store.commit("skills", SKILL_ID, entry, expected_revision=0)
    assert conflict.value.actual_revision == 1

    mcp_entry = {
        "server_id": "filesystem",
        "version": "1.0.0",
        "asset_id": "mcp-filesystem-1.0.0",
        "package_hash": pkg.content_hash,
    }
    second = store.commit("mcp", "filesystem", mcp_entry, expected_revision=1)
    assert second["revision"] == 2
    assert set(second) == {
        "schema_version",
        "revision",
        "snapshot_sha256",
        "skills",
        "mcp",
        "updated_at",
    }
    assert second["skills"][SKILL_ID] == entry
    assert store.get_section("mcp", "filesystem") == mcp_entry

    for revision in (1, 2):
        files = store.history_files(revision)
        assert len(files) == 1
        record = json.loads(files[0].read_text(encoding="utf-8"))
        assert record["revision"] == revision
        assert files[0].name == f"{revision}-{record['snapshot_sha256'][:12]}.json"


def _mcp_entry(pkg: SkillPackage) -> dict:
    return {
        "server_id": "filesystem",
        "version": "1.0.0",
        "asset_id": "mcp-filesystem-1.0.0",
        "package_hash": pkg.content_hash,
    }


def test_history_file_is_immutable(tmp_path: Path) -> None:
    pkg = load_package(SKILLS_ROOT, SKILL_ID)
    store = BindingStore(tmp_path / "state")
    committed = store.commit("skills", SKILL_ID, _binding_entry(pkg), expected_revision=0)
    history = store.history_path(1, committed["snapshot_sha256"])
    payload = json.loads(history.read_text(encoding="utf-8"))
    payload["updated_at"] = "1999-01-01T00:00:00Z"
    history.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BindingError, match="immutable"):
        store._write_history(committed)
    with pytest.raises(BindingError, match="history_missing"):
        store.load()


def test_load_rejects_missing_history(tmp_path: Path) -> None:
    pkg = load_package(SKILLS_ROOT, SKILL_ID)
    store = BindingStore(tmp_path / "state")
    committed = store.commit("skills", SKILL_ID, _binding_entry(pkg), expected_revision=0)
    store.history_path(1, committed["snapshot_sha256"]).unlink()
    with pytest.raises(BindingError, match="history_missing"):
        store.load()


def test_load_rejects_hash_mismatched_history(tmp_path: Path) -> None:
    pkg = load_package(SKILLS_ROOT, SKILL_ID)
    store = BindingStore(tmp_path / "state")
    committed = store.commit("skills", SKILL_ID, _binding_entry(pkg), expected_revision=0)
    history = store.history_path(1, committed["snapshot_sha256"])
    payload = json.loads(history.read_text(encoding="utf-8"))
    payload["updated_at"] = "1999-01-01T00:00:00Z"
    history.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BindingError, match="history_missing"):
        store.load()


def test_orphan_history_does_not_block_revision_or_cas(tmp_path: Path) -> None:
    pkg = load_package(SKILLS_ROOT, SKILL_ID)
    store = BindingStore(tmp_path / "state")
    store.commit("skills", SKILL_ID, _binding_entry(pkg), expected_revision=0)
    (store.history_dir / f"1-{'0' * 12}.json").write_text(
        json.dumps({"orphan": True}), encoding="utf-8"
    )
    (store.history_dir / f"2-{'1' * 12}.json").write_text(
        json.dumps({"orphan": True}), encoding="utf-8"
    )
    assert store.load()["revision"] == 1
    assert len(store.history_files(2)) == 1
    nxt = store.commit("mcp", "filesystem", _mcp_entry(pkg), expected_revision=1)
    assert nxt["revision"] == 2
    assert store.load()["revision"] == 2
    assert len(store.history_files(2)) == 2


def test_commit_retries_after_crash_between_history_and_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = load_package(SKILLS_ROOT, SKILL_ID)
    store = BindingStore(tmp_path / "state")
    entry = _binding_entry(pkg)
    real_write = binding_module.atomic_write_json

    def crashing_write(path: Path, payload: dict, **kwargs) -> None:
        if Path(path) == store.path:
            raise RuntimeError("simulated crash before current swap")
        real_write(path, payload, **kwargs)

    monkeypatch.setattr(binding_module, "atomic_write_json", crashing_write)
    with pytest.raises(RuntimeError, match="simulated crash"):
        store.commit("skills", SKILL_ID, entry, expected_revision=0)
    monkeypatch.setattr(binding_module, "atomic_write_json", real_write)

    assert store.load()["revision"] == 0
    orphans = store.history_files(1)
    assert orphans, "history must be written before the current swap"
    retried = store.commit("skills", SKILL_ID, entry, expected_revision=0)
    assert retried["revision"] == 1
    assert store.load()["revision"] == 1


def test_concurrent_commits_allow_exactly_one_winner(tmp_path: Path) -> None:
    pkg = load_package(SKILLS_ROOT, SKILL_ID)
    entry = _binding_entry(pkg)
    state = tmp_path / "state"
    stores = [BindingStore(state), BindingStore(state)]
    barrier = threading.Barrier(len(stores))
    results: dict[int, object] = {}

    def attempt(index: int, store: BindingStore) -> None:
        barrier.wait()
        try:
            results[index] = store.commit("skills", SKILL_ID, entry, expected_revision=0)[
                "revision"
            ]
        except BindingConflict:
            results[index] = "conflict"

    threads = [
        threading.Thread(target=attempt, args=(index, store))
        for index, store in enumerate(stores)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert sorted(str(value) for value in results.values()) == ["1", "conflict"]


def test_binding_rejects_state_inside_repo() -> None:
    with pytest.raises(BindingError):
        BindingStore(REPO_ROOT / "state")
    with pytest.raises(BindingError):
        BindingStore(REPO_ROOT)


def test_binding_rejects_bad_entry(tmp_path: Path) -> None:
    pkg = load_package(SKILLS_ROOT, SKILL_ID)
    store = BindingStore(tmp_path / "state")
    with pytest.raises(BindingError, match="unknown fields"):
        store.commit(
            "skills",
            SKILL_ID,
            {**_binding_entry(pkg), "shell": "bash"},
            expected_revision=0,
        )
    with pytest.raises(BindingError, match="package_hash"):
        store.commit(
            "skills",
            SKILL_ID,
            _binding_entry(pkg, package_hash="sha256:nothex"),
            expected_revision=0,
        )
    with pytest.raises(BindingError, match="must match skill_id"):
        store.commit("skills", "other-skill", _binding_entry(pkg), expected_revision=0)
