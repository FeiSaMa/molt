"""Exercise versioned synthetic configuration and tools in a private kernel state."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from examples.version_workers import LlamaWorker, ScriptWorker
from molt.core.fsutil import atomic_write_bytes, exclusive_lock
from molt.core.hash import canonical_json_bytes, sha256_bytes
from molt.kernel_client import KernelClient

ROOT = "synthetic-versions"
CONTRACT = "molt-synthetic-versions/1"
RELEASES = {
    "v1": ({"reply_format": "plain"}, {"tool": "echo", "output_format": "plain"}),
    "v2": ({"reply_format": "json"}, {"tool": "echo", "output_format": "json"}),
    "invalid": ({"reply_format": "json"}, {"tool": "echo", "output_format": "plain"}),
    "prefixed": (
        {"reply_format": "plain", "prefix": "synthetic: "},
        {"tool": "echo", "output_format": "plain"},
    ),
    "flexible": ({"reply_format": "plain"}, {"tool": "echo", "output_format": "config"}),
    "json-config": ({"reply_format": "json"}, {"tool": "echo", "output_format": "config"}),
}
VERSION_OBJECTS = ("config", "tool")
ECHO_DEPENDENCY = {"engine": "molt.synthetic-echo", "version": 1}


def _short_text(value, limit: int = 400) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _text_list(values, limit: int = 8) -> bool:
    return isinstance(values, list) and len(values) <= limit and all(_short_text(v) for v in values)


def _proposal(value: dict, sources: dict) -> tuple[dict, dict]:
    if set(value) != {"config", "tool", "judgment"}:
        raise ValueError("expected config, tool and judgment from the Agent")
    config, tool, judgment = (value[key] for key in ("config", "tool", "judgment"))
    if (
        not isinstance(config, dict) or set(config) - {"reply_format", "prefix"}
        or config.get("reply_format") not in ("plain", "json")
        or not isinstance(config.get("prefix", ""), str) or len(config.get("prefix", "")) > 120
        or not isinstance(tool, dict) or set(tool) != {"tool", "output_format"}
        or tool["tool"] != "echo" or tool["output_format"] not in ("plain", "json", "config")
    ):
        raise ValueError("unsupported staged configuration, tool or dependency")
    if (
        not isinstance(judgment, dict) or set(judgment) != {"reason", "evidence", "assumptions"}
        or not _short_text(judgment["reason"])
        or not _text_list(judgment["assumptions"]) or not _text_list(judgment["evidence"])
        or any(key not in sources for key in judgment["evidence"])
    ):
        raise ValueError("invalid judgment or evidence outside the proposal input")
    return {"config": config, "tool": tool}, judgment


def _put(client: KernelClient, value: dict) -> dict:
    payload = canonical_json_bytes(value)
    return {
        "type": "file",
        "mode": 0o600,
        "size": len(payload),
        "sha256": client.upload_object(payload),
    }


def _read(client: KernelClient, files: dict, name: str) -> dict:
    return json.loads(client.get_object(files[name]["sha256"]))


def _validate(config: dict, tool: dict) -> str:
    reply_format = config["reply_format"]
    if (
        reply_format not in {"plain", "json"}
        or not isinstance(config.get("prefix", ""), str)
        or tool["tool"] != "echo"
        or tool["output_format"] not in {reply_format, "config"}
    ):
        raise ValueError("synthetic config and tool formats are incompatible")
    if tool["output_format"] == "config":
        return f"echo tool supports the configured {reply_format} format"
    return "config and tool formats match"


class VersionSandbox:
    """A broker for cooperating processes using one freshly initialized synthetic state."""

    def __init__(self, directory: Path):
        self.directory = directory.resolve(strict=True)
        info = self.directory.stat()
        if not self.directory.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("sandbox directory must be private and owned by the caller")
        self.state = self.directory / "state"
        self.journal = self.directory / "broker.json"
        if not self.journal.is_file() or self.journal.is_symlink() or self.state.is_symlink():
            raise ValueError("expected an initialized synthetic sandbox")
        self._load_journal()

    @classmethod
    def initialize(cls, directory: Path, binary: Path) -> VersionSandbox:
        directory.mkdir(mode=0o700)
        directory = directory.resolve(strict=True)
        state = directory / "state"
        subprocess.run(
            [binary, "init", "--state", state],
            check=True,
            capture_output=True,
            timeout=15,
        )
        journal = {
            "contract": CONTRACT,
            "state_id": (state / "STATE_ID").read_text(),
            "current": None,
        }
        atomic_write_bytes(directory / "broker.json", canonical_json_bytes(journal))
        return cls(directory)

    def _load_journal(self) -> dict:
        journal = json.loads(self.journal.read_bytes())
        if journal["contract"] != CONTRACT:
            raise ValueError("unsupported sandbox contract")
        return journal

    def _save(self, journal: dict) -> None:
        atomic_write_bytes(self.journal, canonical_json_bytes(journal))

    @contextmanager
    def _session(self):
        with exclusive_lock(self.directory / "broker.lock"):
            journal = self._load_journal()
            with KernelClient(str(self.state / "kernel.sock"), timeout=5) as client:
                if client.handshake["state_id"] != journal["state_id"]:
                    raise ValueError("kernel state identity mismatch")
                yield client, journal

    def _idle_current(self, client: KernelClient, journal: dict) -> dict | None:
        if journal.get("pending"):
            request_id = journal["pending"]["params"]["request_id"]
            raise RuntimeError(f"pending request {request_id}; run recover before writing")
        current = client.current()
        if current != journal["current"]:
            raise RuntimeError("kernel current changed outside the broker")
        return current

    def _index(self, client: KernelClient, current: dict | None) -> tuple[dict, dict]:
        if current is None:
            return {}, {
                "format": CONTRACT, "provenance": "synthetic", "active": None,
                "branches": {}, "snapshots": {}, "events": {}, "digests": {},
            }
        files = client.get_manifest(current["manifest_hash"])["roots"][ROOT]
        index = _read(client, files, "index.json")
        if index["format"] != CONTRACT or index["provenance"] != "synthetic":
            raise ValueError("unsupported application index")
        return files, index

    def _complete(self, journal: dict, result: dict) -> dict:
        intent = journal["pending"]
        params = intent["params"]
        expected = intent["expected"]
        generation = expected["generation"] if expected else 0
        if (
            result["manifest_hash"] != params["manifest_hash"]
            or result["committed_at"] != params["ts"]
            or result["generation"] != generation + 1
        ):
            raise RuntimeError("original request result does not match the durable intent")
        settled = {key: value for key, value in journal.items() if key != "pending"}
        self._save({**settled, "current": result})
        return result

    def _commit(self, client: KernelClient, journal: dict) -> dict:
        params = journal["pending"]["params"]
        try:
            result = client.call("generation.commit", params)
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(f"request {params['request_id']} needs recovery") from exc
        return self._complete(journal, result)

    def _publish(self, client: KernelClient, journal: dict, files: dict, index: dict) -> dict:
        request_id = f"synthetic-versions-{uuid4()}"
        index["request_id"] = request_id
        files["index.json"] = _put(client, index)
        manifest = client.create_manifest({ROOT: files})
        params = {
            "request_id": request_id,
            "manifest_hash": manifest,
            "ts": datetime.now(UTC).isoformat(),
        }
        pending = {**journal, "pending": {"expected": journal["current"], "params": params}}
        self._save(pending)
        return self._commit(client, pending)

    def recover(self) -> dict | None:
        """Query the original ID before deciding whether exact replay is possible."""
        with self._session() as (client, journal):
            if not (intent := journal.get("pending")):
                return self._idle_current(client, journal)
            params = intent["params"]
            status = client.request_status(params["request_id"])
            current = client.current()
            if status["status"] == "committed":
                if current != status["result"]:
                    raise RuntimeError("kernel current changed outside the broker")
                return self._complete(journal, status["result"])
            if status != {"status": "not_found", "retry_with_same_request_id": True}:
                raise RuntimeError("request status is unresolved")
            if current != intent["expected"]:
                raise RuntimeError("original request has a stale base; intent retained")
            client.get_manifest(params["manifest_hash"])
            return self._commit(client, journal)

    def activate(self, release: str, *, expected: dict | None) -> dict:
        version = dict(zip(VERSION_OBJECTS, RELEASES[release], strict=True))
        validation = _validate(**version)
        with self._session() as (client, journal):
            current = self._idle_current(client, journal)
            if current != expected:
                raise ValueError("stale generation; proposal was not applied")
            files, index = self._index(client, current)
            parent = index["branches"].get("main")
            parents = [parent] if parent else []
            snapshot = self._store_version(client, files, index, version, parents)
            index["branches"]["main"] = snapshot
            return self._record(
                client, journal, files, index, snapshot,
                action="activate", validation=validation, activate=True,
                details={"branch": "main", "previous_head": parent},
            )

    def status(self) -> dict:
        with self._session() as (client, _):
            current = client.current()
            _, index = self._index(client, current)
            return {
                "current": current, "active": index["active"], "branches": index["branches"],
                "last_event": index.get("last_event"),
            }

    def create_branch(self, branch: str, snapshot: str, *, expected: dict) -> dict:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", branch):
            raise ValueError("invalid branch name")
        with self._session() as (client, journal):
            files, index = self._checked_index(client, journal, expected)
            if branch in index["branches"]:
                raise ValueError("branch already exists")
            if snapshot not in index["snapshots"]:
                raise ValueError("unknown snapshot")
            self._version(client, files, index, snapshot)
            index["branches"][branch] = snapshot
            return self._record(
                client, journal, files, index, snapshot,
                action="create-branch", validation="committed snapshot and both objects verified",
                details={"branch": branch},
            )

    def switch(self, branch: str, *, expected: dict, expected_head: str) -> dict:
        with self._session() as (client, journal):
            files, index = self._checked_index(client, journal, expected)
            snapshot = self._branch_head(index, branch)
            if snapshot != expected_head:
                raise ValueError("stale branch head")
            self._version(client, files, index, snapshot)
            return self._record(
                client, journal, files, index, snapshot, action="switch",
                validation="committed snapshot and both objects verified",
                activate=True, details={"branch": branch},
            )

    def advance(self, branch: str, release: str, *, expected: dict, expected_head: str) -> dict:
        version = dict(zip(VERSION_OBJECTS, RELEASES[release], strict=True))
        with self._session() as (client, journal):
            current = self._idle_current(client, journal)
            files, index = self._index(client, current)
            head = self._branch_head(index, branch)
            if current != expected or head != expected_head:
                candidate = {
                    "provenance": "synthetic", "branch": branch, "release": release,
                    "base": expected, "parent": expected_head, **version,
                }
                payload = canonical_json_bytes(candidate)
                directory = self.directory / "candidates"
                directory.mkdir(mode=0o700, exist_ok=True)
                path = directory / f"{sha256_bytes(payload)}.json"
                atomic_write_bytes(path, payload)
                raise ValueError(f"stale generation or branch head; candidate retained at {path}")
            validation = _validate(**version)
            snapshot = self._store_version(client, files, index, version, [head])
            index["branches"][branch] = snapshot
            return self._record(
                client, journal, files, index, snapshot, action="advance", validation=validation,
                details={"branch": branch, "previous_head": head},
            )

    def propose(
        self, branch: str, worker: ScriptWorker | LlamaWorker, *, task: str,
        expected: dict, expected_head: str, approval: str, sources: dict, activate: bool = False,
    ) -> dict:
        """Run an isolated Agent, then validate and publish its staged version."""
        if type(worker) not in (ScriptWorker, LlamaWorker):
            raise ValueError("an isolated Agent worker is required")
        if not _short_text(task) or not _short_text(approval):
            raise ValueError("the broker operator must supply a task and approval")
        if not isinstance(sources, dict) or len(sources) > 8 or not all(
            _short_text(key, 40) and _short_text(value) for key, value in sources.items()
        ):
            raise ValueError("invalid synthetic evidence projection")
        sources = dict(sources)
        with self._session() as (client, journal):
            files, index = self._checked_index(client, journal, expected)
            if self._branch_head(index, branch) != expected_head:
                raise ValueError("stale branch head")
            before = self._version(client, files, index, expected_head)
        staged = worker.generate("propose", {"task": task, **before, "evidence": sources})
        version, judgment = _proposal(staged, sources)
        if version == before:
            raise ValueError("proposal did not change the staged version")
        validation = _validate(**version)
        with self._session() as (client, journal):
            files, index = self._checked_index(client, journal, expected)
            if self._branch_head(index, branch) != expected_head:
                raise ValueError("stale branch head")
            snapshot = self._store_version(client, files, index, version, [expected_head], managed=True)
            index["branches"][branch] = snapshot
            return self._record(
                client, journal, files, index, snapshot, action="propose", validation=validation,
                activate=activate, details={
                    "branch": branch, "previous_head": expected_head, "task": task,
                    "subject": {"worker": worker.identity, "model": worker.model_id},
                    "approval": {"source": "local sandbox operator", "uid": os.getuid(), "scope": approval},
                    "judgment": judgment, "sources": sources,
                },
            )

    def merge(
        self, target: str, source: str, *, expected: dict, expected_target: str,
        expected_source: str, activate: bool = False,
    ) -> dict:
        with self._session() as (client, journal):
            files, index = self._checked_index(client, journal, expected)
            ours = self._branch_head(index, target)
            theirs = self._branch_head(index, source)
            if (ours, theirs) != (expected_target, expected_source):
                raise ValueError("stale merge branch head")
            if ours == theirs:
                raise ValueError("merge requires two distinct parent snapshots")
            ancestor = self._merge_base(client, files, index, ours, theirs)
            base = self._version(client, files, index, ancestor)
            left = self._version(client, files, index, ours)
            right = self._version(client, files, index, theirs)
            version = {}
            for kind in VERSION_OBJECTS:
                if left[kind] == base[kind]:
                    version[kind] = right[kind]
                elif right[kind] in (base[kind], left[kind]):
                    version[kind] = left[kind]
                else:
                    raise ValueError(f"merge conflict in {kind}")
            validation = _validate(**version)
            parents = [ours, theirs]
            snapshot = self._store_version(client, files, index, version, parents)
            index["branches"][target] = snapshot
            return self._record(
                client, journal, files, index, snapshot, action="merge", validation=validation,
                activate=activate,
                details={"branch": target, "source_branch": source, "parents": parents,
                         "ancestor": ancestor},
            )

    def _checked_index(self, client, journal, expected) -> tuple[dict, dict]:
        current = self._idle_current(client, journal)
        if current != expected:
            raise ValueError("stale generation")
        return self._index(client, current)

    @staticmethod
    def _branch_head(index: dict, branch: str) -> str:
        if branch not in index["branches"]:
            raise ValueError(f"unknown branch: {branch}")
        return index["branches"][branch]

    def _store_version(self, client, files, index, version, parents, *, managed=False) -> str:
        entries = {kind: _put(client, version[kind]) for kind in VERSION_OBJECTS}
        if managed or any("dependency" in self._descriptor(client, files, index, p) for p in parents):
            entries["dependency"] = _put(client, ECHO_DEPENDENCY)
        descriptor = {"parents": parents, **{kind: entry["sha256"] for kind, entry in entries.items()}}
        snapshot = sha256_bytes(canonical_json_bytes(descriptor))
        directory = f"snapshots/{snapshot}"
        for kind, entry in entries.items():
            files[f"{directory}/{kind}.json"] = entry
        files[f"{directory}/snapshot.json"] = _put(client, descriptor)
        index["snapshots"][snapshot] = directory
        return snapshot

    def _merge_base(self, client, files, index, ours, theirs) -> str:
        parents = {}

        def ancestors(head):
            seen = set()
            pending = [head]
            while pending:
                snapshot = pending.pop()
                if snapshot in seen:
                    continue
                seen.add(snapshot)
                if snapshot not in parents:
                    parents[snapshot] = self._descriptor(client, files, index, snapshot)["parents"]
                pending.extend(parents[snapshot])
            return seen

        common = ancestors(ours) & ancestors(theirs)
        older = {parent for snapshot in common for parent in parents[snapshot]}
        best = common - older
        if len(best) != 1:
            raise ValueError("merge requires a unique nearest common ancestor")
        return best.pop()

    def rollback(self, snapshot: str, *, expected: dict) -> dict:
        with self._session() as (client, journal):
            current = self._idle_current(client, journal)
            if current != expected:
                raise ValueError("stale generation; rollback was not applied")
            files, index = self._index(client, current)
            if snapshot not in index["snapshots"]:
                raise ValueError("unknown snapshot")
            self._version(client, files, index, snapshot)
            return self._record(
                client, journal, files, index, snapshot,
                action="rollback", validation="stored snapshot and both objects verified", activate=True,
            )

    def _record(
        self, client, journal, files, index, snapshot, *, action, validation,
        activate=False, details=None,
    ) -> dict:
        previous = index["active"]
        evidence = {
            "provenance": "synthetic", "action": action,
            "from": previous, "to": snapshot, "base": journal["current"],
            "active_after": snapshot if activate else previous, "activate": activate,
            "judgment": {"goal": action, "choice": snapshot},
            "approval": {"source": "synthetic sandbox operator"},
            "validation": validation, **(details or {}),
        }
        event = _put(client, evidence)
        name = f"events/{event['sha256'].removeprefix('sha256:')}.json"
        files[name] = event
        index["events"][event["sha256"]] = name
        index["last_event"] = event["sha256"]
        index["active"] = evidence["active_after"]
        result = self._publish(client, journal, files, index)
        return {"current": result, "snapshot": snapshot, "event": event["sha256"]}

    @staticmethod
    def _descriptor(client, files, index, snapshot) -> dict:
        directory = index["snapshots"][snapshot]
        descriptor_name = f"{directory}/snapshot.json"
        descriptor = _read(client, files, descriptor_name)
        if files[descriptor_name]["sha256"] != f"sha256:{snapshot}":
            raise ValueError("snapshot identity mismatch")
        return descriptor

    def _version(self, client, files, index, snapshot) -> dict:
        directory = index["snapshots"][snapshot]
        descriptor = self._descriptor(client, files, index, snapshot)
        version = {}
        for kind in VERSION_OBJECTS:
            file = f"{directory}/{kind}.json"
            if files[file]["sha256"] != descriptor[kind]:
                raise ValueError("snapshot object identity mismatch")
            version[kind] = _read(client, files, file)
        if "dependency" in descriptor:
            file = f"{directory}/dependency.json"
            if files[file]["sha256"] != descriptor["dependency"] or _read(client, files, file) != ECHO_DEPENDENCY:
                raise ValueError("snapshot runtime dependency is unavailable")
        _validate(**version)
        return version

    def pin(self) -> dict:
        with self._session() as (client, journal):
            current = client.current()
            _, index = self._index(client, current)
            if current is None:
                raise ValueError("no activated snapshot")
            return {
                "state_id": journal["state_id"],
                "current": current,
                "snapshot": index["active"],
                "request_id": index["request_id"],
            }

    def read(self, pin: dict) -> dict:
        """Resolve a persisted run pin against its original, committed generation."""
        with self._session() as (client, journal):
            if pin["state_id"] != journal["state_id"]:
                raise ValueError("run pin belongs to a different state")
            status = client.request_status(pin["request_id"])
            if status != {"status": "committed", "result": pin["current"]}:
                raise ValueError("run pin does not identify a committed generation")
            files, index = self._index(client, pin["current"])
            if pin["snapshot"] != index["active"]:
                raise ValueError("run pin does not match the active snapshot of its generation")
            return self._version(client, files, index, pin["snapshot"])

    def run(self, pin: dict, message: str) -> str:
        """Run the fixed synthetic echo with the pinned config and tool pair."""
        version = self.read(pin)
        config, tool = version["config"], version["tool"]
        message = config.get("prefix", "") + message
        output_format = config["reply_format"] if tool["output_format"] == "config" else tool["output_format"]
        if output_format == "json":
            return canonical_json_bytes({"echo": message}).decode("utf-8")
        return message

    def _projection(self, client, files, index, event_id) -> tuple[dict, dict]:
        evidence = _read(client, files, index["events"][event_id])
        projection = {
            key: evidence[key]
            for key in ("action", "judgment", "approval", "validation", "active_after")
        }
        projection["approval"] = {key: value for key, value in evidence["approval"].items() if key != "uid"}
        before = evidence.get("previous_head", evidence.get("from"))
        if before is not None:
            projection["before"] = self._version(client, files, index, before)
        projection["after"] = self._version(client, files, index, evidence["to"])
        projection.update({f"source.{key}": value for key, value in evidence.get("sources", {}).items()})
        return evidence, projection

    @staticmethod
    def _inferences(response: dict, projection: dict, reference) -> list[dict]:
        claims = response.get("claims")
        if set(response) != {"claims"} or not isinstance(claims, list) or not 1 <= len(claims) <= 8:
            raise ValueError("invalid digest claims")
        result = []
        for claim in claims:
            if not isinstance(claim, dict) or set(claim) != {"kind", "text", "evidence"}:
                raise ValueError("invalid digest claim")
            kind, excerpt, refs = claim["kind"], claim["text"], claim["evidence"]
            if (
                kind not in ("inference", "missing", "conflict") or not _short_text(excerpt)
                or not _text_list(refs) or len(set(refs)) != len(refs)
                or any(key not in projection for key in refs)
                or len(refs) < (2 if kind == "conflict" else 1)
            ):
                raise ValueError("invalid digest claim or evidence outside the approved projection")
            result.append({"kind": kind, "text": excerpt, "evidence": reference(refs)})
        return result

    def digest(self, event_id: str, *, worker: ScriptWorker | LlamaWorker | None = None) -> dict:
        """Generate outside the broker lock and append against the latest application index."""
        if worker is not None and type(worker) not in (ScriptWorker, LlamaWorker):
            raise ValueError("an isolated digest worker is required")
        with self._session() as (client, journal):
            files, index = self._index(client, self._idle_current(client, journal))
            evidence, projection = self._projection(client, files, index, event_id)

        def reference(fields):
            return [{"event": event_id, "field": key} for key in fields]

        # Render recorded observations from the source, independent of the generator.
        claims = [{
            "kind": "recorded_observation", "evidence": [event_id],
            "text": f"Recorded {evidence['action']}: {evidence['validation']}",
            "references": reference(["action", "validation"]),
        }]
        claims.extend({
            "kind": "recorded_observation", "field": key,
            "text": canonical_json_bytes(value).decode(), "evidence": [event_id],
            "references": reference([key]),
        } for key, value in projection.items() if key not in {"action", "validation"})
        if worker is not None:
            response = worker.generate("digest", {"evidence": projection})
            claims.extend(self._inferences(response, projection, reference))

        with self._session() as (client, journal):
            files, index = self._index(client, self._idle_current(client, journal))
            if self._projection(client, files, index, event_id) != (evidence, projection):
                raise RuntimeError("committed digest inputs changed")
            history = index["digests"].setdefault(event_id, [])
            digest = {
                "provenance": "synthetic",
                "generation_mode": "local_model" if isinstance(worker, LlamaWorker) else "synthetic",
                "generator": worker.identity if worker else "deterministic",
                "generator_version": 1,
                "model": worker.model_id if worker else "none",
                "template_version": 2, "generated_at": datetime.now(UTC).isoformat(),
                "previous": history[-1] if history else None,
                "inputs": [event_id], "input_fields": list(projection),
                "input_sha256": sha256_bytes(canonical_json_bytes(projection)), "snapshot": evidence["to"],
                "claims": claims,
            }
            entry = _put(client, digest)
            name = f"digests/{entry['sha256'].removeprefix('sha256:')}.json"
            files[name] = entry
            history.append(name)
            result = self._publish(client, journal, files, index)
            return {"current": result, "digest": digest}

    def audit(self, event_id: str) -> dict:
        with self._session() as (client, _):
            files, index = self._index(client, client.current())
            evidence, projection = self._projection(client, files, index, event_id)
            history = index["digests"].get(event_id, [])
            return {
                "evidence": evidence,
                "projection": projection,
                "digest_status": "available" if history else "pending",
                "digests": [_read(client, files, name) for name in history],
            }

    def gc_plan(self) -> dict:
        with self._session() as (client, journal):
            self._idle_current(client, journal)
            return client.call("gc", {"dry_run": True})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init").add_argument("kernel", type=Path)
    activate = commands.add_parser("activate")
    activate.add_argument("release", choices=RELEASES)
    activate.add_argument("expected", type=json.loads, help="complete prior current JSON or null")
    rollback = commands.add_parser("rollback")
    rollback.add_argument("snapshot")
    rollback.add_argument("expected", type=json.loads)
    commands.add_parser("status")
    branch = commands.add_parser("create-branch")
    branch.add_argument("branch")
    branch.add_argument("snapshot")
    branch.add_argument("expected", type=json.loads)
    switch = commands.add_parser("switch")
    switch.add_argument("branch")
    switch.add_argument("expected", type=json.loads)
    switch.add_argument("expected_head")
    advance = commands.add_parser("advance")
    advance.add_argument("branch")
    advance.add_argument("release", choices=RELEASES)
    advance.add_argument("expected", type=json.loads)
    advance.add_argument("expected_head")
    proposal = commands.add_parser("propose")
    proposal.add_argument("branch")
    proposal.add_argument("expected", type=json.loads)
    proposal.add_argument("expected_head")
    proposal.add_argument("--task", required=True)
    proposal.add_argument("--approval", required=True)
    proposal.add_argument("--sources", type=json.loads, default={})
    proposal.add_argument("--activate", action="store_true")
    agent = proposal.add_mutually_exclusive_group(required=True)
    agent.add_argument("--script", type=Path)
    agent.add_argument("--model", type=Path)
    proposal.add_argument("--model-id")
    merge = commands.add_parser("merge")
    merge.add_argument("target")
    merge.add_argument("source")
    merge.add_argument("expected", type=json.loads)
    merge.add_argument("expected_target")
    merge.add_argument("expected_source")
    merge.add_argument("--activate", action="store_true")
    commands.add_parser("pin")
    commands.add_parser("read").add_argument("pin", type=json.loads)
    run = commands.add_parser("run")
    run.add_argument("pin", type=json.loads)
    run.add_argument("message")
    digest = commands.add_parser("digest")
    digest.add_argument("event")
    generator = digest.add_mutually_exclusive_group()
    generator.add_argument("--script", type=Path)
    generator.add_argument("--model", type=Path)
    digest.add_argument("--model-id")
    commands.add_parser("audit").add_argument("event")
    commands.add_parser("recover")
    commands.add_parser("gc-plan")
    args = parser.parse_args()
    worker = None
    if args.command in ("propose", "digest"):
        if bool(args.model) != bool(args.model_id):
            parser.error("--model and --model-id must be supplied together")
        if args.model:
            worker = LlamaWorker(args.model, model_id=args.model_id)
        elif args.script:
            worker = ScriptWorker(args.script)
    if args.command == "init":
        sandbox = VersionSandbox.initialize(args.directory, args.kernel.resolve(strict=True))
        result = {"state": str(sandbox.state)}
    else:
        sandbox = VersionSandbox(args.directory)
        match args.command:
            case "activate":
                result = sandbox.activate(args.release, expected=args.expected)
            case "rollback":
                result = sandbox.rollback(args.snapshot, expected=args.expected)
            case "status":
                result = sandbox.status()
            case "create-branch":
                result = sandbox.create_branch(args.branch, args.snapshot, expected=args.expected)
            case "switch":
                result = sandbox.switch(
                    args.branch, expected=args.expected, expected_head=args.expected_head,
                )
            case "advance":
                result = sandbox.advance(
                    args.branch, args.release, expected=args.expected, expected_head=args.expected_head,
                )
            case "propose":
                result = sandbox.propose(
                    args.branch, worker, task=args.task, expected=args.expected,
                    expected_head=args.expected_head, approval=args.approval, sources=args.sources,
                    activate=args.activate,
                )
            case "merge":
                result = sandbox.merge(
                    args.target, args.source, expected=args.expected,
                    expected_target=args.expected_target, expected_source=args.expected_source,
                    activate=args.activate,
                )
            case "pin":
                result = sandbox.pin()
            case "read":
                result = sandbox.read(args.pin)
            case "run":
                result = sandbox.run(args.pin, args.message)
            case "digest":
                result = sandbox.digest(args.event, worker=worker)
            case "audit":
                result = sandbox.audit(args.event)
            case "recover":
                result = sandbox.recover()
            case "gc-plan":
                result = sandbox.gc_plan()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
