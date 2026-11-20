"""Acceptance through the isolated worker and existing real kernel UDS."""

from __future__ import annotations

import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from test_kernel_transactions import _binary, _server

from examples.synthetic_versions import ROOT, VersionSandbox
from examples.version_workers import ScriptWorker
from molt.kernel_client import KernelClient, KernelError

pytestmark = pytest.mark.integration


def _worker(tmp_path, body, name="worker"):
    script = tmp_path / f"{name}.py"
    script.write_text("import json, sys\nrequest = json.load(sys.stdin)\n" + body)
    return ScriptWorker(script)


def _staged(config=None, tool=None):
    return {
        "config": config or {"reply_format": "plain", "prefix": "synthetic: "},
        "tool": tool or {"tool": "echo", "output_format": "plain"},
        "judgment": {"reason": "make changes visible", "evidence": ["task"], "assumptions": []},
    }


def _propose(sandbox, worker, base, branch="main", **kwargs):
    return sandbox.propose(
        branch, worker, expected=base["current"], expected_head=base["snapshot"],
        task=kwargs.pop("task", "Change the way the synthetic echo is displayed."),
        sources={"task": "Make this synthetic change visible in the echo."},
        approval="Synthetic isolated acceptance case", **kwargs,
    )


def test_proposal_boundary_and_pinned_runtime(tmp_path, monkeypatch):
    sandbox = VersionSandbox.initialize(tmp_path / "v", _binary())
    with _server(_binary(), sandbox.state):
        first = sandbox.activate("v1", expected=None)
        pin = sandbox.pin()
        bad = _worker(tmp_path, f"print(json.dumps({_staged({'reply_format': 'json'})!r}))", "bad")
        with pytest.raises(ValueError, match="incompatible"):
            _propose(sandbox, bad, first, activate=True)
        external = _staged(tool={"tool": "echo", "output_format": "plain", "path": "/tmp/script"})
        bad = _worker(tmp_path, f"print(json.dumps({external!r}))", "external")
        with pytest.raises(ValueError, match="unsupported"):
            _propose(sandbox, bad, first)
        forged = {**_staged(), "approval": {"uid": 0}}
        bad = _worker(tmp_path, f"print(json.dumps({forged!r}))", "forged")
        with pytest.raises(ValueError, match="expected config, tool and judgment"):
            _propose(sandbox, bad, first)
        good = _worker(tmp_path, f"""
import os, socket
assert 'HOME' not in os.environ and os.getcwd() == '/tmp'
assert not {{'home', 'run', 'sys', 'etc'}} & set(os.listdir('/'))
try:
    socket.create_connection(('192.0.2.1', 9), timeout=1)
except OSError as exc:
    assert exc.errno == 101, exc
else:
    raise AssertionError('unexpected network route')
for target in {[str(sandbox.state / 'kernel.sock'), str(sandbox.state / 'objects'), str(sandbox.journal)]!r}:
    assert not os.path.exists(target)
    try:
        if target.endswith('.sock'):
            socket.socket(socket.AF_UNIX).connect(target)
        else:
            open(target, 'wb').close()
    except FileNotFoundError:
        pass
    else:
        raise AssertionError(target)
print(json.dumps({_staged({'reply_format': 'json'}, {'tool': 'echo', 'output_format': 'json'})!r}))
""")
        with monkeypatch.context() as patch:
            patch.setattr("molt.version_workers._BWRAP", tmp_path / "missing")
            with pytest.raises(RuntimeError, match="isolated worker"):
                _propose(sandbox, good, first, activate=True)
        assert sandbox.pin() == pin
        committed = _propose(sandbox, good, first, activate=True)
        with pytest.raises(ValueError, match="stale generation"):
            _propose(sandbox, good, first, activate=True)
        evidence = sandbox.audit(committed["event"])["evidence"]
        assert evidence["approval"]["uid"] == os.getuid()
        assert evidence["subject"]["worker"] == "isolated-python"
        assert evidence["judgment"]["evidence"] == ["task"]
        new_pin = sandbox.pin()
        assert sandbox.run(pin, "hi") == "hi"
        assert json.loads(sandbox.run(new_pin, "hi")) == {"echo": "hi"}
        with KernelClient(str(sandbox.state / "kernel.sock")) as client:
            roots = client.get_manifest(new_pin["current"]["manifest_hash"])["roots"][ROOT]
            dependency = roots[f"snapshots/{committed['snapshot']}/dependency.json"]
            assert json.loads(client.get_object(dependency["sha256"]))["version"] == 1
        with monkeypatch.context() as patch:
            patch.setattr("molt.versioning.ECHO_DEPENDENCY", {"version": 2})
            with pytest.raises(ValueError, match="dependency is unavailable"):
                sandbox.run(new_pin, "hi")
    with _server(_binary(), sandbox.state):
        restarted = VersionSandbox(sandbox.directory)
        assert restarted.run(pin, "hi") == "hi"
        assert json.loads(restarted.run(new_pin, "hi")) == {"echo": "hi"}


def test_digest_projection_failure_history_and_concurrent_commit(tmp_path, monkeypatch):
    sandbox = VersionSandbox.initialize(tmp_path / "v", _binary())
    with _server(_binary(), sandbox.state):
        first = sandbox.activate("v1", expected=None)
        staged = _staged()
        staged["judgment"]["assumptions"] = ["throughput is unmeasured"]
        proposer = _worker(tmp_path, f"print(json.dumps({staged!r}))", "propose")
        change = _propose(sandbox, proposer, first, activate=True)
        event = change["event"]
        hidden = _worker(tmp_path, """
print(json.dumps({"claims": [{"kind": "inference", "text": "hidden", "evidence": ["base"]}]}))
""", "hidden")
        with pytest.raises(ValueError, match="outside the approved projection"):
            sandbox.digest(event, worker=hidden)
        failure = _worker(tmp_path, "raise SystemExit(5)", "failure")
        with pytest.raises(RuntimeError, match="worker failed"):
            sandbox.digest(event, worker=failure)
        assert sandbox.status()["current"] == change["current"]
        assert sandbox.audit(event)["digest_status"] == "pending"

        summary = _worker(tmp_path, """
assert set(request) == {"purpose", "evidence"}
assert "base" not in request["evidence"] and "subject" not in request["evidence"]
print(json.dumps({"claims": [
    {"kind": "inference", "text": "Echo output gains a prefix", "evidence": ["before", "after"]},
    {"kind": "missing", "text": "Throughput is unmeasured", "evidence": ["judgment"]},
    {"kind": "conflict", "text": "The source and judgment address different concerns",
     "evidence": ["source.task", "judgment"]},
]}))
""", "summary")
        original = ScriptWorker.generate
        entered, resume = Event(), Event()

        def concurrent(worker, purpose, request):
            entered.set()
            assert resume.wait(10)
            return original(worker, purpose, request)

        with monkeypatch.context() as patch:
            patch.setattr(ScriptWorker, "generate", concurrent)
            with ThreadPoolExecutor(1) as pool:
                pending = pool.submit(sandbox.digest, event, worker=summary)
                try:
                    assert entered.wait(5)
                    sandbox.create_branch("other", first["snapshot"], expected=change["current"])
                finally:
                    resume.set()
                first_digest = pending.result(timeout=25)
        second_digest = sandbox.digest(event, worker=summary)
        audit = sandbox.audit(event)
        assert audit["digests"] == [first_digest["digest"], second_digest["digest"]]
        assert second_digest["digest"]["previous"].startswith("digests/")
        assert sandbox.status()["branches"]["other"] == first["snapshot"]
        assert sandbox.status()["active"] == change["snapshot"]
        for claim in audit["digests"][-1]["claims"]:
            refs = claim["references"] if claim["kind"].endswith("_observation") else claim["evidence"]
            assert all(ref["event"] == event and ref["field"] in audit["projection"] for ref in refs)


def _cas(sandbox, object_id):
    digest = object_id.removeprefix("sha256:")
    return sandbox.state / "objects" / "sha256" / digest[:2] / digest[2:4] / digest


@pytest.mark.parametrize(("point", "durable"), [("log.fsynced", False), ("head.durable", True)])
def test_controlled_end_to_end_crash_and_gc(tmp_path, point, durable):
    sandbox = VersionSandbox.initialize(tmp_path / "v", _binary())
    worker = _worker(tmp_path, f"print(json.dumps({_staged()!r}))", "main")
    topic = _worker(tmp_path, f"""
print(json.dumps({_staged({'reply_format': 'plain'}, {'tool': 'echo', 'output_format': 'config'})!r}))
""", "topic")
    with _server(_binary(), sandbox.state):
        first = sandbox.activate("v1", expected=None)
        pin = sandbox.pin()
        side = sandbox.create_branch("topic", first["snapshot"], expected=first["current"])
        ours = _propose(
            sandbox, worker, {"current": side["current"], "snapshot": first["snapshot"]}, activate=True,
        )
    marker = tmp_path / "pause"
    env = {**os.environ, "MOLT_KERNEL_PAUSE_AT": point, "MOLT_KERNEL_PAUSE_MARKER": str(marker)}
    with subprocess.Popen([_binary(), "serve", "--state", sandbox.state], env=env) as kernel:
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    with KernelClient(str(sandbox.state / "kernel.sock"), timeout=0.2):
                        break
                except OSError:
                    assert kernel.poll() is None and time.monotonic() < deadline
                    time.sleep(0.01)
            with ThreadPoolExecutor(1) as pool:
                job = pool.submit(
                    _propose, sandbox, topic,
                    {"current": ours["current"], "snapshot": first["snapshot"]}, "topic", activate=True,
                )
                try:
                    while not marker.exists():
                        assert not job.done() and time.monotonic() < deadline
                        time.sleep(0.01)
                    intent = json.loads(sandbox.journal.read_text())["pending"]["params"]
                finally:
                    kernel.kill()
                    kernel.wait(timeout=5)
                with pytest.raises(RuntimeError, match="needs recovery"):
                    job.result(timeout=6)
        finally:
            if kernel.poll() is None:
                kernel.kill()
    with _server(_binary(), sandbox.state):
        restarted = VersionSandbox(sandbox.directory)
        interrupted = restarted.pin()
        assert restarted.read(interrupted)["tool"]["output_format"] == ("config" if durable else "plain")
        assert restarted.read(interrupted)["config"] == (
            {"reply_format": "plain"} if durable else {"reply_format": "plain", "prefix": "synthetic: "}
        )
        recovered = restarted.recover()
        with KernelClient(str(sandbox.state / "kernel.sock")) as client:
            assert client.request_status(intent["request_id"]) == {"status": "committed", "result": recovered}
        assert recovered["generation"] == ours["current"]["generation"] + 1
        assert restarted.recover() == recovered
        topic_head = restarted.status()["branches"]["topic"]
        old_pin = restarted.pin()
        merged = restarted.merge(
            "main", "topic", expected=recovered, expected_target=ours["snapshot"], expected_source=topic_head,
        )
        activated = restarted.switch("main", expected=merged["current"], expected_head=merged["snapshot"])
        merged_pin = restarted.pin()
        assert restarted.read(merged_pin) == {
            "config": {"reply_format": "plain", "prefix": "synthetic: "},
            "tool": {"tool": "echo", "output_format": "config"},
        }
        assert restarted.run(old_pin, "hi") == "hi"
        assert restarted.run(merged_pin, "hi") == "synthetic: hi"
        summary = _worker(tmp_path, """
print(json.dumps({"claims": [{"kind": "inference", "text": "Activation enabled the new echo format",
                            "evidence": ["action", "after", "active_after"]}]}))
""", "summary")
        digested = restarted.digest(activated["event"], worker=summary)
        assert restarted.audit(activated["event"])["digests"][0] == digested["digest"]
        rolled = restarted.rollback(first["snapshot"], expected=digested["current"])
        assert restarted.pin()["snapshot"] == first["snapshot"]
        assert restarted.status()["branches"] == {"main": merged["snapshot"], "topic": topic_head}
        # Sweep exactly one disposable CAS object created by this test.
        with restarted._session() as (client, journal):
            assert restarted._idle_current(client, journal) == rolled["current"]
            orphan = client.upload_object(b"disposable GC probe")
            assert _cas(restarted, orphan).is_file()
            assert client.call("gc", {"dry_run": True})["delete"] == [orphan]
            assert client.call("gc", {"dry_run": False})["delete"] == [orphan]
            assert not _cas(restarted, orphan).exists()
            with pytest.raises(KernelError):
                client.get_object(orphan)
        assert restarted.run(pin, "hi") == "hi"
        assert restarted.run(merged_pin, "hi") == "synthetic: hi"
        audited = restarted.audit(activated["event"])
        assert audited["projection"]["after"] == restarted.read(merged_pin)
        assert audited["projection"]["branch_head_changed"] is False
        assert audited["evidence"]["validation"]
        assert audited["digests"][0]["claims"][-1]["evidence"][-1] == {
            "event": activated["event"], "field": "active_after", "source": "event",
        }


def test_rollback_digest_identifies_removed_prefix_and_unverified_model_claim(tmp_path):
    from molt.core.hash import canonical_json_bytes, sha256_bytes

    kernel = _binary()
    sandbox = VersionSandbox.initialize(tmp_path / "v", kernel)
    with _server(kernel, sandbox.state):
        initial = sandbox.commit(
            "main", {"reply_format": "plain"}, {"tool": "echo", "output_format": "plain"},
            expected=0, expected_head=None,
        )
        change = sandbox.commit(
            "main", {"reply_format": "plain", "prefix": "cfg: "},
            {"tool": "echo", "output_format": "config"},
            expected=1, expected_head=initial["snapshot"], activate=True,
        )
        rolled = sandbox.rollback(initial["snapshot"], expected=2)
        branches = sandbox.workspace_status()["branches"]
        false_model = _worker(tmp_path, """
print(json.dumps({"claims": [
    {"kind": "inference", "text": "The edit was required by the operator.", "evidence": ["action", "reason_recorded"]}
]}))
""")
        digest = sandbox.digest(rolled["event"], worker=false_model)["digest"]
        audit = sandbox.audit(rolled["event"])
        projection = audit["projection"]
        assert digest["template_version"] == 3
        assert digest["input_sha256"] == sha256_bytes(canonical_json_bytes(projection))
        assert audit["evidence"]["action"] == projection["action"] == "rollback"
        assert projection["target_snapshot"] == initial["snapshot"]
        assert projection["before_snapshot"] == change["snapshot"]
        assert projection["branch_head_changed"] is projection["reason_recorded"] is False
        assert sandbox.workspace_status()["branches"] == branches == {"main": change["snapshot"]}
        assert projection["changes"] == [
            {"group": "config", "name": "prefix", "operation": "remove", "before": "cfg: ", "after": None},
            {"group": "tool", "name": "output_format", "operation": "modify", "before": "config", "after": "plain"},
        ]
        observations = {claim["field"]: claim for claim in digest["claims"] if "field" in claim}
        assert observations["changes"]["kind"] == "derived_observation"
        assert observations["changes"]["references"] == [{
            "event": rolled["event"], "field": "changes", "source": "projection", "template_version": 3,
        }]
        assert digest["claims"][0]["references"][0]["source"] == "event"
        assert digest["claims"][-1]["kind"] == "inference"
        assert digest["claims"][-1]["verified"] is False
