from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_kernel_transactions import _binary, _server

from examples.synthetic_versions import ROOT, VersionSandbox
from molt.core.fsutil import exclusive_lock
from molt.kernel_client import KernelClient

pytestmark = pytest.mark.integration


@pytest.fixture
def sandbox(tmp_path):
    return VersionSandbox.initialize(tmp_path / "v", _binary())


def test_activation_pin_rollback_digest_and_gc(sandbox):
    with _server(_binary(), sandbox.state):
        first = sandbox.activate("v1", expected=None)
        first_pin = sandbox.pin()
        with pytest.raises(ValueError, match="incompatible"):
            sandbox.activate("invalid", expected=first["current"])
        assert sandbox.pin() == first_pin

        second = sandbox.activate("v2", expected=first["current"])
        second_pin = sandbox.pin()
        with pytest.raises(ValueError, match="stale generation"):
            sandbox.activate("v2", expected=first["current"])
        assert sandbox.read(first_pin) == {
            "config": {"reply_format": "plain"},
            "tool": {"tool": "echo", "output_format": "plain"},
        }
        assert sandbox.read(second_pin)["tool"]["output_format"] == "json"
        assert sandbox.run(first_pin, "synthetic") == "synthetic"
        assert json.loads(sandbox.run(second_pin, "synthetic")) == {"echo": "synthetic"}
        with pytest.raises(ValueError, match="active snapshot"):
            sandbox.read({**second_pin, "snapshot": first_pin["snapshot"]})

        audit = sandbox.audit(second["event"])
        assert audit["digest_status"] == "pending"
        assert audit["evidence"]["validation"] == "config and tool formats match"
        first_digest = sandbox.digest(second["event"])
        second_digest = sandbox.digest(second["event"])
        audit = sandbox.audit(second["event"])
        assert audit["digest_status"] == "available"
        assert audit["digests"] == [first_digest["digest"], second_digest["digest"]]
        assert audit["digests"][0]["claims"][0]["evidence"] == [second["event"]]
        assert audit["evidence"]["to"] == second_pin["snapshot"]

        rolled_back = sandbox.rollback(first["snapshot"], expected=second_digest["current"])
        assert sandbox.pin()["snapshot"] == first_pin["snapshot"]
        assert sandbox.audit(rolled_back["event"])["evidence"]["from"] == second_pin["snapshot"]
        with KernelClient(str(sandbox.state / "kernel.sock")) as client:
            roots = client.get_manifest(rolled_back["current"]["manifest_hash"])["roots"][ROOT]
            index = json.loads(client.get_object(roots["index.json"]["sha256"]))
            assert index["branches"]["main"] == second_pin["snapshot"]
            assert set(index["events"]) == {first["event"], second["event"], rolled_back["event"]}
            old = client.get_manifest(second_pin["current"]["manifest_hash"])["roots"][ROOT]
        reachable = sandbox.gc_plan()["reachable"]
        assert second_pin["current"]["manifest_hash"] in reachable
        assert {entry["sha256"] for entry in old.values()} <= set(reachable)

    with _server(_binary(), sandbox.state):
        resumed = VersionSandbox(sandbox.directory)
        assert resumed.read(json.loads(json.dumps(second_pin)))["config"]["reply_format"] == "json"
        assert resumed.read(first_pin)["tool"]["output_format"] == "plain"
        assert json.loads(resumed.run(second_pin, "synthetic")) == {"echo": "synthetic"}


def _diverge(sandbox, releases=("v1", "prefixed", "flexible")):
    base, main, topic = releases
    first = sandbox.activate(base, expected=None)
    initial = first["snapshot"]
    branch = sandbox.create_branch("topic", initial, expected=first["current"])
    ours = sandbox.advance("main", main, expected=branch["current"], expected_head=initial)
    theirs = sandbox.advance("topic", topic, expected=ours["current"], expected_head=initial)
    return first, ours, theirs


def test_named_branch_uses_both_expected_pointers(sandbox):
    with _server(_binary(), sandbox.state):
        initial = sandbox.activate("v1", expected=None)
        first = initial["snapshot"]
        with pytest.raises(ValueError, match="unknown snapshot"):
            sandbox.create_branch("topic", "missing", expected=initial["current"])
        created = sandbox.create_branch("topic", first, expected=initial["current"])
        assert sandbox.status()["active"] == first
        assert sandbox.audit(created["event"])["evidence"]["branch"] == "topic"
        with pytest.raises(ValueError, match="already exists"):
            sandbox.create_branch("topic", first, expected=created["current"])
        winner = sandbox.advance(
            "topic", "flexible", expected=created["current"], expected_head=first,
        )
        after_winner = sandbox.status()
        winning_head = winner["snapshot"]
        assert after_winner["branches"] == {"main": first, "topic": winning_head}
        assert after_winner["active"] == first
        for expected, expected_head in (
            (created["current"], first),
            (created["current"], winning_head),
            (winner["current"], first),
        ):
            with pytest.raises(ValueError, match="candidate retained at ") as error:
                sandbox.advance("topic", "prefixed", expected=expected, expected_head=expected_head)
            path = Path(str(error.value).split("retained at ", 1)[1])
            assert path.parent == sandbox.directory / "candidates"
            assert json.loads(path.read_bytes()) == {
                "provenance": "synthetic", "branch": "topic", "release": "prefixed",
                "base": expected, "parent": expected_head,
                "config": {"reply_format": "plain", "prefix": "synthetic: "},
                "tool": {"tool": "echo", "output_format": "plain"},
            }
            assert sandbox.status() == after_winner
        with pytest.raises(ValueError, match="incompatible"):
            sandbox.advance("topic", "invalid", expected=winner["current"], expected_head=winning_head)
        with pytest.raises(ValueError, match="stale branch head"):
            sandbox.switch("topic", expected=winner["current"], expected_head=first)
        assert sandbox.status() == after_winner

        selected = sandbox.switch("topic", expected=winner["current"], expected_head=winning_head)
        event = sandbox.audit(selected["event"])["evidence"]
        assert (event["branch"], event["from"], event["to"]) == ("topic", first, winning_head)
        assert event["active_after"] == winning_head
        rolled_back = sandbox.rollback(first, expected=selected["current"])
        assert sandbox.status()["branches"] == after_winner["branches"]
        assert sandbox.status()["current"] == rolled_back["current"]
        assert sandbox.status()["active"] == first


@pytest.mark.parametrize("activate", [False, True])
def test_merge_publishes_two_parents_and_auditable_validation(sandbox, activate):
    with _server(_binary(), sandbox.state):
        first, ours, theirs = _diverge(sandbox)
        old_pin = sandbox.pin()
        merged = sandbox.merge(
            "main", "topic", expected=theirs["current"],
            expected_target=ours["snapshot"], expected_source=theirs["snapshot"], activate=activate,
        )
        assert merged["current"]["generation"] == theirs["current"]["generation"] + 1
        expected_active = merged["snapshot"] if activate else first["snapshot"]
        assert sandbox.status() == {
            "current": merged["current"], "active": expected_active,
            "branches": {"main": merged["snapshot"], "topic": theirs["snapshot"]},
            "last_event": merged["event"],
        }
        evidence = sandbox.audit(merged["event"])["evidence"]
        parents = [ours["snapshot"], theirs["snapshot"]]
        assert evidence == {
            "provenance": "synthetic", "action": "merge", "branch": "main", "source_branch": "topic",
            "parents": parents, "ancestor": first["snapshot"], "base": theirs["current"],
            "from": first["snapshot"], "to": merged["snapshot"], "active_after": expected_active,
            "activate": activate, "judgment": {"goal": "merge", "choice": merged["snapshot"]},
            "approval": {"source": "synthetic sandbox operator"},
            "validation": "echo tool supports the configured plain format",
        }
        with KernelClient(str(sandbox.state / "kernel.sock")) as client:
            files = client.get_manifest(merged["current"]["manifest_hash"])["roots"][ROOT]
            obj = files[f"snapshots/{merged['snapshot']}/snapshot.json"]["sha256"]
            assert json.loads(client.get_object(obj))["parents"] == parents
        if not activate:
            sandbox.switch("main", expected=merged["current"], expected_head=merged["snapshot"])
        assert sandbox.read(sandbox.pin()) == {
            "config": {"reply_format": "plain", "prefix": "synthetic: "},
            "tool": {"tool": "echo", "output_format": "config"},
        }
        assert sandbox.run(sandbox.pin(), "hello") == "synthetic: hello"
        assert sandbox.run(old_pin, "hello") == "hello"
        digest = sandbox.digest(merged["event"])["digest"]
        assert digest["claims"][0]["evidence"] == [merged["event"]]

    with _server(_binary(), sandbox.state):
        resumed = VersionSandbox(sandbox.directory)
        assert resumed.run(old_pin, "hello") == "hello"
        assert resumed.run(resumed.pin(), "hello") == "synthetic: hello"


@pytest.mark.parametrize(("releases", "error"), [
    (("v1", "v2", "prefixed"), "merge conflict in config"),
    (("flexible", "json-config", "v1"), "formats are incompatible"),
])
def test_failed_merge_preserves_heads_current_and_audit(sandbox, releases, error):
    with _server(_binary(), sandbox.state):
        first, ours, theirs = _diverge(sandbox, releases)
        before = sandbox.status()
        pin = sandbox.pin()
        with pytest.raises(ValueError, match="stale merge branch head"):
            sandbox.merge(
                "main", "topic", expected=theirs["current"],
                expected_target=ours["snapshot"], expected_source=first["snapshot"], activate=True,
            )
        with pytest.raises(ValueError, match=error):
            sandbox.merge(
                "main", "topic", expected=theirs["current"],
                expected_target=ours["snapshot"], expected_source=theirs["snapshot"], activate=True,
            )
        assert sandbox.status() == before
        assert sandbox.pin() == pin
        assert sandbox.recover() == theirs["current"]


@pytest.mark.parametrize("action", ["switch", "merge"])
@pytest.mark.parametrize("durable", [False, True], ids=["before-commit", "lost-response"])
def test_branch_recovery_reuses_exact_request(sandbox, monkeypatch, action, durable):
    original_call = KernelClient.call
    attempts = []

    def disconnect(client, method, params=None):
        if method != "generation.commit":
            return original_call(client, method, params)
        attempts.append(params.copy())
        if durable:
            original_call(client, method, params)
        raise ConnectionError("synthetic disconnect")

    with _server(_binary(), sandbox.state), monkeypatch.context() as patch:
        first, ours, theirs = _diverge(sandbox)
        old_pin = sandbox.pin()
        before = sandbox.status()
        patch.setattr(KernelClient, "call", disconnect)
        with pytest.raises(RuntimeError, match="needs recovery"):
            if action == "switch":
                sandbox.switch("topic", expected=theirs["current"], expected_head=theirs["snapshot"])
            else:
                sandbox.merge(
                    "main", "topic", expected=theirs["current"], expected_target=ours["snapshot"],
                    expected_source=theirs["snapshot"], activate=True,
                )
        assert json.loads(sandbox.journal.read_bytes())["pending"]["params"] == attempts[0]
        with pytest.raises(RuntimeError, match="run recover"):
            sandbox.gc_plan()
        assert (sandbox.status() != before) is durable
        assert sandbox.pin()["snapshot"] == sandbox.status()["active"]

    calls = []

    def capture(client, method, params=None):
        if method in {"generation.commit", "request.status"}:
            calls.append((method, params.copy()))
        return original_call(client, method, params)

    with _server(_binary(), sandbox.state), monkeypatch.context() as patch:
        patch.setattr(KernelClient, "call", capture)
        resumed = VersionSandbox(sandbox.directory)
        result = resumed.recover()
        assert calls == [
            ("request.status", {"request_id": attempts[0]["request_id"]}),
            *([] if durable else [("generation.commit", attempts[0])]),
        ]
        assert result["generation"] == before["current"]["generation"] + 1
        assert resumed.recover() == result
        after = resumed.status()
        assert resumed.pin()["snapshot"] == after["active"]
        assert resumed.run(old_pin, "hello") == "hello"
        evidence = resumed.audit(after["last_event"])["evidence"]
        assert (evidence["action"], evidence["from"], evidence["to"]) == (
            action, first["snapshot"], after["active"],
        )
        if action == "merge":
            assert evidence["parents"] == [ours["snapshot"], theirs["snapshot"]]
            assert after["branches"]["main"] == after["active"]
        else:
            assert evidence["branch"] == "topic"
            assert after["branches"] == before["branches"]


@pytest.mark.parametrize("durable", [False, True], ids=["before-commit", "lost-response"])
def test_recovery_reuses_persisted_request_after_restart(sandbox, monkeypatch, durable):
    original_call = KernelClient.call
    attempts = []

    def lose_reply(client, method, params=None):
        if method != "generation.commit":
            return original_call(client, method, params)
        attempts.append(params.copy())
        if durable:
            original_call(client, method, params)
        raise ConnectionError("synthetic disconnect")

    with _server(_binary(), sandbox.state), monkeypatch.context() as patch:
        patch.setattr(KernelClient, "call", lose_reply)
        with pytest.raises(RuntimeError, match="needs recovery"):
            sandbox.activate("v1", expected=None)
        pending = json.loads(sandbox.journal.read_text())["pending"]
        assert pending["params"] == attempts[0]
        with pytest.raises(RuntimeError, match="run recover"):
            sandbox.gc_plan()
        with pytest.raises(RuntimeError, match="run recover"):
            sandbox.activate("v2", expected=None)

    calls = []

    def capture(client, method, params=None):
        if method in {"generation.commit", "request.status"}:
            calls.append((method, params.copy()))
        return original_call(client, method, params)

    with _server(_binary(), sandbox.state), monkeypatch.context() as patch:
        patch.setattr(KernelClient, "call", capture)
        resumed = VersionSandbox(sandbox.directory)
        result = resumed.recover()
        assert calls[0] == ("request.status", {"request_id": attempts[0]["request_id"]})
        assert calls[1:] == ([] if durable else [("generation.commit", attempts[0])])
        assert result["generation"] == 1
        assert resumed.recover() == result
        assert resumed.read(resumed.pin())["config"]["reply_format"] == "plain"


def test_existing_directory_and_concurrent_broker_are_rejected(sandbox, tmp_path):
    with pytest.raises(FileExistsError):
        VersionSandbox.initialize(Path(sandbox.directory), _binary())
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="initialized synthetic sandbox"):
        VersionSandbox(other)
    assert list(other.iterdir()) == []
    with _server(_binary(), sandbox.state):
        with exclusive_lock(sandbox.directory / "broker.lock"), pytest.raises(BlockingIOError):
            VersionSandbox(sandbox.directory).activate("v1", expected=None)
        assert sandbox.recover() is None
