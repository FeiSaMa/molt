"""Versioned configuration and managed tools in a private kernel state."""

from __future__ import annotations

import json
import os
import re
import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from molt.core.fsutil import atomic_write_bytes, exclusive_lock
from molt.core.hash import canonical_json_bytes, sha256_bytes
from molt.kernel_client import KernelClient
from molt.version_files import capture, publish_tree, validate_paths, validate_sizes
from molt.version_workers import LlamaWorker, ScriptWorker

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
Expected = dict | int | None


class VersionConflict(ValueError):
    """A version precondition or merge failed."""


class RecoveryRequired(RuntimeError):
    """A durable intent must be resolved using its original request ID."""


def _matches(current: dict | None, expected: Expected) -> bool:
    if isinstance(expected, int):
        return (current["generation"] if current else 0) == expected
    return current == expected


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
        or tool["output_format"] not in (reply_format, "config")
    ):
        raise ValueError("synthetic config and tool formats are incompatible")
    if tool["output_format"] == "config":
        return f"echo tool supports the configured {reply_format} format"
    return "config and tool formats match"


class VersionSandbox:
    """A broker for cooperating processes using one private version state."""

    def __init__(self, directory: Path):
        self.directory = directory.resolve(strict=True)
        info = self.directory.stat()
        if not self.directory.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("sandbox directory must be private and owned by the caller")
        self.state = self.directory / "state"
        self.journal = self.directory / "broker.json"
        if not self.journal.is_file() or self.journal.is_symlink():
            raise ValueError("expected a version workspace or initialized synthetic sandbox")
        if (
            not self.state.is_dir() or self.state.is_symlink()
            or self.state.stat().st_uid != os.getuid()
            or (self.directory / "broker.lock").is_symlink()
        ):
            raise ValueError("expected a private initialized version workspace")
        self.provenance = self._load_journal().get("provenance", "synthetic")
        if self.provenance not in {"synthetic", "local"}:
            raise ValueError("unsupported workspace provenance")

    @classmethod
    def initialize(
        cls, directory: Path, binary: Path, *, provenance: str = "synthetic",
    ) -> VersionSandbox:
        if provenance not in {"synthetic", "local"}:
            raise ValueError("unsupported workspace provenance")
        if len(os.fsencode(directory.resolve() / "state" / "kernel.sock")) >= 108:
            raise ValueError("workspace path is too long for the Linux Unix socket")
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
            "provenance": provenance,
            "state_id": (state / "STATE_ID").read_text(),
            "current": None,
        }
        atomic_write_bytes(directory / "broker.json", canonical_json_bytes(journal))
        (directory / "broker.lock").touch(mode=0o600, exist_ok=False)
        return cls(directory)

    def check_state_identity(self) -> None:
        if (self.state / "STATE_ID").read_text() != self._load_journal()["state_id"]:
            raise ValueError("kernel state identity mismatch")

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
            raise RecoveryRequired(f"pending request {request_id}; query request-status or run recover")
        current = client.current()
        if current != journal["current"]:
            raise RuntimeError("kernel current changed outside the broker")
        return current

    def _index(self, client: KernelClient, current: dict | None) -> tuple[dict, dict]:
        if current is None:
            return {}, {
                "format": CONTRACT, "provenance": self.provenance, "active": None,
                "branches": {}, "snapshots": {}, "events": {}, "digests": {},
            }
        files = client.get_manifest(current["manifest_hash"])["roots"][ROOT]
        index = _read(client, files, "index.json")
        if index["format"] != CONTRACT or index["provenance"] != self.provenance:
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
            return self._complete(journal, result)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            raise RecoveryRequired(f"request {params['request_id']} needs recovery") from exc

    def _publish(self, client: KernelClient, journal: dict, files: dict, index: dict) -> dict:
        prefix = "synthetic-versions" if self.provenance == "synthetic" else "local-versions"
        request_id = f"{prefix}-{uuid4()}"
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
                raise RecoveryRequired(f"request status is unresolved: {params['request_id']}")
            if current != intent["expected"]:
                raise RecoveryRequired(f"original request {params['request_id']} has a stale base; intent retained")
            client.get_manifest(params["manifest_hash"])
            return self._commit(client, journal)

    def activate(self, release: str, *, expected: Expected) -> dict:
        version = dict(zip(VERSION_OBJECTS, RELEASES[release], strict=True))
        validation = _validate(**version)
        with self._session() as (client, journal):
            current = self._idle_current(client, journal)
            if not _matches(current, expected):
                raise VersionConflict("stale generation; proposal was not applied")
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

    def workspace_status(self) -> dict:
        with self._session() as (client, journal):
            current = client.current()
            _, index = self._index(client, current)
            pending = journal.get("pending")
            if not pending and current != journal["current"]:
                raise RuntimeError("kernel current changed outside the broker")
            return {
                "state_id": journal["state_id"], "provenance": self.provenance,
                "generation": current["generation"] if current else 0,
                "current": current, "active": index["active"], "branches": index["branches"],
                "last_event": index.get("last_event"),
                "pending_request": pending["params"]["request_id"] if pending else None,
            }

    def request_status(self, request_id: str) -> dict:
        with self._session() as (client, _):
            return client.request_status(request_id)

    def commit(
        self, branch: str, config: dict, tool: dict, *, expected: Expected,
        expected_head: str | None, activate: bool = False,
    ) -> dict:
        """Commit a user-supplied pair and its runtime dependency through the broker."""
        if (
            not isinstance(config, dict) or not isinstance(tool, dict)
            or set(config) - {"reply_format", "prefix"} or "reply_format" not in config
            or set(tool) != {"tool", "output_format"}
            or config["reply_format"] not in ("plain", "json")
            or tool["output_format"] not in ("plain", "json", "config")
            or not isinstance(config.get("prefix", ""), str) or len(config.get("prefix", "")) > 120
        ):
            raise ValueError("invalid config or managed echo tool descriptor")
        validation = _validate(config, tool)
        with self._session() as (client, journal):
            files, index = self._checked_index(client, journal, expected)
            initializing = journal["current"] is None
            head = None if initializing and branch == "main" else self._branch_head(index, branch)
            if head != expected_head:
                raise VersionConflict("stale branch head")
            snapshot = self._store_version(
                client, files, index, {"config": config, "tool": tool}, [head] if head else [], managed=True,
            )
            index["branches"][branch] = snapshot
            return self._record(
                client, journal, files, index, snapshot, action="commit", validation=validation,
                activate=activate or initializing, details={"branch": branch, "previous_head": head},
            )

    def commit_files(
        self, branch: str, root: Path, manifest: Path, *, expected: Expected,
        expected_head: str | None, activate: bool = False,
        reason: str | None = None, source: str | None = None,
    ) -> dict:
        if (reason is None) != (source is None):
            raise ValueError("provide both reason and reason source")
        if reason is not None and (not _short_text(reason) or not _short_text(source)):
            raise ValueError("reason and reason source must be nonempty short text")
        if any(path.is_relative_to(self.directory) for path in (root.absolute(), manifest.absolute())):
            raise ValueError("managed edit inputs must be outside the version workspace")
        contents = capture(root, manifest)
        with self._session() as (client, journal):
            files, index = self._checked_index(client, journal, expected)
            initializing = journal["current"] is None
            head = None if initializing and branch == "main" else self._branch_head(index, branch)
            if head != expected_head:
                raise VersionConflict("stale branch head")
            tree = {
                path: {"sha256": f"sha256:{sha256_bytes(data)}", "size": len(data)}
                for path, data in contents.items()
            }
            if head:
                parent = self._version(client, files, index, head)
                if "files" not in parent:
                    raise ValueError("this branch contains echo snapshots")
                if tree == parent["files"]:
                    return {"current": journal["current"], "snapshot": head, "event": None, "changed": False}
            for path, data in contents.items():
                entry = tree[path]
                name = self._file_object_name(entry["sha256"])
                if name not in files:
                    files[name] = {"type": "file", "mode": 0o600, "size": len(data),
                                   "sha256": client.upload_object(data)}
            snapshot = self._store_file_version(client, files, index, tree, [head] if head else [])
            index["branches"][branch] = snapshot
            return self._record(
                client, journal, files, index, snapshot, action="commit",
                validation="managed file paths and contents verified", activate=activate or initializing,
                details={"branch": branch, "previous_head": head,
                         "reason": {"text": reason, "source": source} if reason is not None else None},
            ) | {"changed": True}

    def history(self) -> list[dict]:
        with self._session() as (client, _):
            files, index = self._index(client, client.current())
            events = []
            for event, name in index["events"].items():
                record = _read(client, files, name)
                base = record["base"]
                events.append({"event": event, "generation": base["generation"] + 1 if base else 1, **record})
            return sorted(events, key=lambda record: record["generation"])

    def show(self, snapshot: str) -> dict:
        with self._session() as (client, _):
            files, index = self._index(client, client.current())
            return {
                "snapshot": snapshot, "descriptor": self._descriptor(client, files, index, snapshot),
                **self._version(client, files, index, snapshot),
            }

    def export_tree(self, parent: Path, *, snapshot: str | None = None) -> dict:
        """Read one committed selector, then materialize only that immutable snapshot."""
        if parent.absolute().is_relative_to(self.directory):
            raise ValueError("export parent must be outside the version workspace")
        with self._session() as (client, _):
            current = client.current()
            files, index = self._index(client, current)
            snapshot = index["active"] if snapshot is None else snapshot
            if snapshot not in index["snapshots"]:
                raise ValueError("no such committed snapshot")
            version = self._version(client, files, index, snapshot)
            if "files" not in version:
                raise ValueError("export requires a managed file snapshot")
            contents = {}
            for path, entry in version["files"].items():
                data = client.get_object(entry["sha256"])
                if len(data) != entry["size"] or f"sha256:{sha256_bytes(data)}" != entry["sha256"]:
                    raise ValueError(f"managed content identity mismatch: {path}")
                contents[path] = data
        return {"snapshot": snapshot, "tree": str(publish_tree(parent, contents))}

    def create_branch(self, branch: str, snapshot: str, *, expected: Expected) -> dict:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", branch):
            raise ValueError("invalid branch name")
        with self._session() as (client, journal):
            files, index = self._checked_index(client, journal, expected)
            if branch in index["branches"]:
                raise VersionConflict("branch already exists")
            if snapshot not in index["snapshots"]:
                raise ValueError("unknown snapshot")
            self._version(client, files, index, snapshot)
            index["branches"][branch] = snapshot
            return self._record(
                client, journal, files, index, snapshot,
                action="create-branch", validation="committed snapshot references verified",
                details={"branch": branch},
            )

    def switch(self, branch: str, *, expected: Expected, expected_head: str) -> dict:
        with self._session() as (client, journal):
            files, index = self._checked_index(client, journal, expected)
            snapshot = self._branch_head(index, branch)
            if snapshot != expected_head:
                raise VersionConflict("stale branch head")
            self._version(client, files, index, snapshot)
            return self._record(
                client, journal, files, index, snapshot, action="switch",
                validation="committed snapshot references verified",
                activate=True, details={"branch": branch},
            )

    def advance(self, branch: str, release: str, *, expected: Expected, expected_head: str) -> dict:
        version = dict(zip(VERSION_OBJECTS, RELEASES[release], strict=True))
        with self._session() as (client, journal):
            current = self._idle_current(client, journal)
            files, index = self._index(client, current)
            head = self._branch_head(index, branch)
            if not _matches(current, expected) or head != expected_head:
                candidate = {
                    "provenance": self.provenance, "branch": branch, "release": release,
                    "base": expected, "parent": expected_head, **version,
                }
                payload = canonical_json_bytes(candidate)
                directory = self.directory / "candidates"
                directory.mkdir(mode=0o700, exist_ok=True)
                path = directory / f"{sha256_bytes(payload)}.json"
                atomic_write_bytes(path, payload)
                raise VersionConflict(f"stale generation or branch head; candidate retained at {path}")
            validation = _validate(**version)
            snapshot = self._store_version(client, files, index, version, [head])
            index["branches"][branch] = snapshot
            return self._record(
                client, journal, files, index, snapshot, action="advance", validation=validation,
                details={"branch": branch, "previous_head": head},
            )

    def propose(
        self, branch: str, worker: ScriptWorker | LlamaWorker, *, task: str,
        expected: Expected, expected_head: str, approval: str, sources: dict, activate: bool = False,
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
                raise VersionConflict("stale branch head")
            before = self._version(client, files, index, expected_head)
            if "files" in before:
                raise ValueError("managed file snapshots do not support echo proposals")
        staged = worker.generate("propose", {"task": task, **before, "evidence": sources})
        version, judgment = _proposal(staged, sources)
        if version == before:
            raise ValueError("proposal did not change the staged version")
        validation = _validate(**version)
        with self._session() as (client, journal):
            files, index = self._checked_index(client, journal, expected)
            if self._branch_head(index, branch) != expected_head:
                raise VersionConflict("stale branch head")
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
        self, target: str, source: str, *, expected: Expected, expected_target: str,
        expected_source: str, activate: bool = False,
    ) -> dict:
        with self._session() as (client, journal):
            files, index = self._checked_index(client, journal, expected)
            ours = self._branch_head(index, target)
            theirs = self._branch_head(index, source)
            if (ours, theirs) != (expected_target, expected_source):
                raise VersionConflict("stale merge branch head")
            if ours == theirs:
                raise VersionConflict("merge requires two distinct parent snapshots")
            ancestor = self._merge_base(client, files, index, ours, theirs)
            base = self._version(client, files, index, ancestor)
            left = self._version(client, files, index, ours)
            right = self._version(client, files, index, theirs)
            parents = [ours, theirs]
            if "files" in base:
                tree = self._merge_entries(base["files"], left["files"], right["files"])
                try:
                    validate_paths(list(tree))
                    validate_sizes([entry["size"] for entry in tree.values()])
                except ValueError as exc:
                    raise VersionConflict(str(exc)) from exc
                snapshot = self._store_file_version(client, files, index, tree, parents)
                validation = "managed file paths and contents verified"
            else:
                version = self._merge_entries(base, left, right)
                validation = _validate(**version)
                snapshot = self._store_version(client, files, index, version, parents)
            index["branches"][target] = snapshot
            return self._record(
                client, journal, files, index, snapshot, action="merge", validation=validation,
                activate=activate,
                details={"branch": target, "source_branch": source, "parents": parents,
                         "ancestor": ancestor},
            )

    @staticmethod
    def _merge_entries(base: dict, left: dict, right: dict) -> dict:
        result = {}
        for path in sorted(base.keys() | left.keys() | right.keys()):
            previous, ours, theirs = base.get(path), left.get(path), right.get(path)
            if ours == previous:
                merged = theirs
            elif theirs in (previous, ours):
                merged = ours
            else:
                raise VersionConflict(f"merge conflict in {path}")
            if merged is not None:
                result[path] = merged
        return result

    def _checked_index(self, client, journal, expected) -> tuple[dict, dict]:
        current = self._idle_current(client, journal)
        if not _matches(current, expected):
            raise VersionConflict("stale generation")
        return self._index(client, current)

    @staticmethod
    def _branch_head(index: dict, branch: str) -> str:
        if branch not in index["branches"]:
            raise ValueError(f"unknown branch: {branch}")
        return index["branches"][branch]

    def _store_version(self, client, files, index, version, parents, *, managed=False) -> str:
        descriptors = [self._descriptor(client, files, index, parent) for parent in parents]
        if any("files" in descriptor for descriptor in descriptors):
            raise ValueError("this branch contains managed file snapshots")
        entries = {kind: _put(client, version[kind]) for kind in VERSION_OBJECTS}
        if managed or any("dependency" in descriptor for descriptor in descriptors):
            entries["dependency"] = _put(client, ECHO_DEPENDENCY)
        descriptor = {"parents": parents, **{kind: entry["sha256"] for kind, entry in entries.items()}}
        snapshot = sha256_bytes(canonical_json_bytes(descriptor))
        directory = f"snapshots/{snapshot}"
        for kind, entry in entries.items():
            files[f"{directory}/{kind}.json"] = entry
        files[f"{directory}/snapshot.json"] = _put(client, descriptor)
        index["snapshots"][snapshot] = directory
        return snapshot

    @staticmethod
    def _file_object_name(digest: str) -> str:
        return f"managed-objects/{digest.removeprefix('sha256:')}"

    def _store_file_version(self, client, files, index, tree, parents) -> str:
        descriptor = _put(client, {"parents": parents, "files": tree})
        snapshot = descriptor["sha256"].removeprefix("sha256:")
        directory = f"snapshots/{snapshot}"
        files[f"{directory}/snapshot.json"] = descriptor
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
            raise VersionConflict("merge requires a unique nearest common ancestor")
        return best.pop()

    def rollback(self, snapshot: str, *, expected: Expected) -> dict:
        with self._session() as (client, journal):
            current = self._idle_current(client, journal)
            if not _matches(current, expected):
                raise VersionConflict("stale generation; rollback was not applied")
            files, index = self._index(client, current)
            if snapshot not in index["snapshots"]:
                raise ValueError("unknown snapshot")
            self._version(client, files, index, snapshot)
            return self._record(
                client, journal, files, index, snapshot,
                action="rollback", validation="committed snapshot references verified", activate=True,
            )

    def _record(
        self, client, journal, files, index, snapshot, *, action, validation,
        activate=False, details=None,
    ) -> dict:
        previous = index["active"]
        evidence = {
            "provenance": self.provenance, "action": action,
            "from": previous, "to": snapshot, "base": journal["current"],
            "active_after": snapshot if activate else previous, "activate": activate,
            "judgment": {"goal": action, "choice": snapshot},
            "approval": {"source": "synthetic sandbox operator" if self.provenance == "synthetic" else "local workspace operator"},
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
        if "files" in descriptor:
            tree = descriptor["files"]
            validate_paths(list(tree))
            validate_sizes([entry["size"] for entry in tree.values()])
            for entry in tree.values():
                stored = files[self._file_object_name(entry["sha256"])]
                if any(stored[key] != entry[key] for key in ("sha256", "size")):
                    raise ValueError("managed file identity mismatch")
            return {"files": tree}
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
            if index["active"] is None:
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
        if "files" in version:
            raise ValueError("managed file snapshots do not have an echo runtime")
        config, tool = version["config"], version["tool"]
        message = config.get("prefix", "") + message
        output_format = config["reply_format"] if tool["output_format"] == "config" else tool["output_format"]
        if output_format == "json":
            return canonical_json_bytes({"echo": message}).decode("utf-8")
        return message

    def _projection(self, client, files, index, event_id, *, template_version=3) -> tuple[dict, dict]:
        evidence = _read(client, files, index["events"][event_id])
        projection = {
            key: evidence[key]
            for key in ("action", "judgment", "approval", "validation", "active_after")
        }
        projection["approval"] = {key: value for key, value in evidence["approval"].items() if key != "uid"}
        before = evidence.get("previous_head", evidence.get("from"))
        action = evidence["action"]
        if template_version == 3:
            if action == "merge":
                before = evidence["parents"][0]
            elif action == "create-branch":
                before = evidence["to"]
        if before is not None:
            projection["before"] = self._version(client, files, index, before)
        projection["after"] = self._version(client, files, index, evidence["to"])
        projection.update({f"source.{key}": value for key, value in evidence.get("sources", {}).items()})
        if template_version == 3:
            projection.update(
                target_snapshot=evidence["to"], before_snapshot=before,
                active_before=evidence.get("from"),
                branch_head_changed=(
                    action == "create-branch" or (
                        (action == "merge" or "previous_head" in evidence) and before != evidence["to"]
                    )
                ),
                reason_recorded=bool(evidence.get("reason")),
                changes=self._changes(projection.get("before", {}), projection["after"]),
            )
        return evidence, projection

    @staticmethod
    def _changes(before: dict, after: dict) -> list[dict]:
        changes = []
        for group in sorted(before.keys() | after.keys()):
            old, new = before.get(group, {}), after.get(group, {})
            for name in sorted(old.keys() | new.keys()):
                if old.get(name) != new.get(name):
                    changes.append({
                        "group": group, "name": name,
                        "operation": "add" if name not in old else "remove" if name not in new else "modify",
                        "before": old.get(name), "after": new.get(name),
                    })
        return changes

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
            result.append({"kind": kind, "text": excerpt, "evidence": reference(refs), "verified": False})
        return result

    def digest(self, event_id: str, *, worker: ScriptWorker | LlamaWorker | None = None) -> dict:
        """Generate outside the broker lock and append against the latest application index."""
        if worker is not None and type(worker) not in (ScriptWorker, LlamaWorker):
            raise ValueError("an isolated digest worker is required")
        with self._session() as (client, journal):
            files, index = self._index(client, self._idle_current(client, journal))
            evidence, projection = self._projection(client, files, index, event_id)

        def reference(fields):
            references = []
            raw_fields = {"action", "judgment", "validation", "active_after"}
            for key in fields:
                if key in raw_fields:
                    references.append({"event": event_id, "field": key, "source": "event"})
                else:
                    references.append({"event": event_id, "field": key, "source": "projection",
                                       "template_version": 3})
            return references

        # Render recorded observations from the source, independent of the generator.
        claims = [{
            "kind": "recorded_observation", "evidence": [event_id],
            "text": f"Recorded {evidence['action']}: {evidence['validation']}",
            "references": reference(["action", "validation"]),
        }]
        claims.extend({
            "kind": "recorded_observation" if key in {"judgment", "active_after"} else "derived_observation",
            "field": key,
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
                "provenance": self.provenance,
                "generation_mode": (
                    "local_model" if isinstance(worker, LlamaWorker)
                    else "synthetic" if worker or self.provenance == "synthetic" else "deterministic"
                ),
                "generator": worker.identity if worker else "deterministic",
                "generator_version": 1,
                "model": worker.model_id if worker else "none",
                "template_version": 3, "generated_at": datetime.now(UTC).isoformat(),
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
            digests = [_read(client, files, name) for name in history]
            legacy = {}
            if any(digest.get("template_version") == 2 for digest in digests):
                _, legacy["legacy_projection_v2"] = self._projection(client, files, index, event_id, template_version=2)
            return {
                "evidence": evidence,
                "projection": projection,
                "digest_status": "available" if history else "pending",
                "digests": digests, **legacy,
            }

    def gc_plan(self) -> dict:
        with self._session() as (client, journal):
            self._idle_current(client, journal)
            return client.call("gc", {"dry_run": True})
