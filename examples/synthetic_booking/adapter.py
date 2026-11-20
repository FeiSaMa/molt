"""Bounded synthetic adapter; business state, caller journal and kernel are separate."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from opaque_client import submit

TS = "2026-11-19T00:00:00Z"
RESULT_KEYS = {"status", "operation_id", "business_ref", "provenance"}
STATUSES = {"confirmed", "no_capacity", "cancelled", "cancel_failed"}


class ServiceRejected(ValueError):
    pass


class ServiceUnknown(RuntimeError):
    pass


def canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Transport:
    def __init__(self, db: Path, *, drop_response: bool = False):
        self.db = db
        self.drop_response = drop_response

    def request(self, method: str, session: str, params: dict) -> dict:
        command = [sys.executable, "-I", str(Path(__file__).with_name("service.py")),
                   "--db", str(self.db)]
        if self.drop_response and method == "execute":
            command.append("--drop-response")
        try:
            result = subprocess.run(command, input=canonical({
                "method": method, "session": session, "params": params,
            }), text=True, capture_output=True, check=False, timeout=5,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ServiceUnknown("synthetic service outcome unknown") from error
        if result.returncode == 2:
            raise ServiceRejected("synthetic service rejected request")
        if result.returncode:
            raise ServiceUnknown("synthetic response unavailable")
        try:
            value = json.loads(result.stdout)
            if set(value) != {"result"} or not isinstance(value["result"], dict):
                raise ValueError("unexpected envelope")
            return value["result"]
        except (ValueError, TypeError) as error:
            raise ServiceUnknown("untrusted response rejected") from error

    def confirm(self, session: str, intent: dict) -> str:
        result = self.request("confirm", session, intent)
        if set(result) != {"confirmation", "provenance"} or result["provenance"] != "synthetic":
            raise ServiceUnknown("untrusted confirmation response")
        if not isinstance(result["confirmation"], str):
            raise ServiceUnknown("untrusted confirmation response")
        return result["confirmation"]


class BookingAdapter:
    def __init__(self, transport: Transport, journal: Path, socket: str):
        self.transport = transport
        self.journal = journal
        self.socket = socket
        journal.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with sqlite3.connect(journal) as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, binding TEXT, "
                       "state TEXT, evidence TEXT)")
        journal.chmod(0o600)

    def _save(self, operation_id: str, state: str, evidence: dict | None = None) -> None:
        with sqlite3.connect(self.journal) as db:
            db.execute("UPDATE jobs SET state=?,evidence=? WHERE id=?",
                       (state, canonical(evidence) if evidence else None, operation_id))

    @staticmethod
    def _unknown(intent: dict) -> dict:
        return {"status": "unknown", "operation_id": intent["operation_id"],
                "action": intent["action"], "provenance": "synthetic",
                "manual_review": "query original operation in synthetic service; do not resubmit"}

    def _query(self, session: str, intent: dict) -> dict:
        result = self.transport.request("query", session, {"operation_id": intent["operation_id"]})
        if not isinstance(result, dict):
            raise ServiceUnknown("untrusted query response")
        if result.get("status") == "not_found":
            if set(result) != {"status", "retry_same_id", "provenance"}:
                raise ServiceUnknown("untrusted query response")
            if result["provenance"] != "synthetic" or type(result["retry_same_id"]) is not bool:
                raise ServiceUnknown("untrusted query response")
            return result
        if (set(result) != RESULT_KEYS or not all(isinstance(v, str) for v in result.values())
                or result["provenance"] != "synthetic"
                or result["status"] not in STATUSES
                or result["operation_id"] != intent["operation_id"]):
            raise ServiceUnknown("untrusted query response")
        allowed = {"reserve": {"confirmed", "no_capacity"}, "cancel": {"cancelled", "cancel_failed"}}
        if result["status"] not in allowed[intent["action"]]:
            raise ServiceUnknown("response action mismatch")
        if intent["action"] == "cancel" and result["business_ref"] != intent["business_ref"]:
            raise ServiceUnknown("response booking mismatch")
        return result

    def _publish(self, intent: dict, evidence: dict) -> dict:
        # Only allowlisted service-query fields cross into immutable kernel storage.
        summary = {**evidence, "source": "synthetic_service_query", "action": intent["action"]}
        payload = canonical(summary).encode()
        try:
            commit = submit(self.socket, "synthetic-evidence:" + intent["operation_id"], payload, TS)
        except (OSError, RuntimeError, ValueError):
            self._save(intent["operation_id"], "evidence_pending", evidence)
            return {"status": "evidence_pending", "business_status": evidence["status"],
                    "operation_id": intent["operation_id"], "provenance": "synthetic"}
        self._save(intent["operation_id"], "recorded", evidence)
        return {**evidence, "kernel_generation": commit["generation"],
                "source": "synthetic_service_query"}

    def run(self, session: str, intent: dict, confirmation: str) -> dict:
        # Validate exact keys before touching either service or journal.
        required = {"operation_id", "action", "date", "seat", "business_ref"}
        if (set(intent) != required or not all(isinstance(v, str) for v in intent.values())
                or intent["action"] not in {"reserve", "cancel"}):
            raise ServiceRejected("invalid intent")
        identity = self.transport.request("identity", session, {})
        if (not isinstance(identity, dict) or set(identity) != {"principal", "provenance"}
                or not all(isinstance(v, str) for v in identity.values())
                or identity["provenance"] != "synthetic"
                or identity["principal"] not in {"alice", "bob"}):
            raise ServiceUnknown("untrusted identity response")
        # Binding includes confirmation to prevent replacement within a recovery operation.
        binding = hashlib.sha256(canonical({"principal": identity["principal"],
                                           "intent": intent, "confirmation": confirmation}).encode()).hexdigest()
        with sqlite3.connect(self.journal) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT binding,state,evidence FROM jobs WHERE id=?",
                             (intent["operation_id"],)).fetchone()
            if row is not None and row[0] != binding:
                raise ServiceRejected("operation context conflict")
            if row is None:
                # Durable attempted marker precedes the possible external side effect.
                db.execute("INSERT INTO jobs VALUES(?,?,?,NULL)",
                           (intent["operation_id"], binding, "attempted"))
        if row is not None and row[2]:
            return self._publish(intent, json.loads(row[2]))
        if row is not None and row[1] == "rejected":
            return {"status": "rejected", "operation_id": intent["operation_id"],
                    "provenance": "synthetic"}
        if row is not None:
            try:
                recovered = self._query(session, intent)
                if recovered["status"] != "not_found":
                    return self._publish(intent, recovered)
                if not recovered["retry_same_id"]:
                    return self._unknown(intent)
            except (ServiceRejected, ServiceUnknown):
                return self._unknown(intent)
        try:
            self.transport.request("execute", session, {**intent, "confirmation": confirmation})
        except ServiceRejected:
            self._save(intent["operation_id"], "rejected")
            return {"status": "rejected", "operation_id": intent["operation_id"],
                    "provenance": "synthetic"}
        except ServiceUnknown:
            pass  # Query, never infer failure or issue another business operation.
        try:
            evidence = self._query(session, intent)
            if evidence["status"] == "not_found":
                return self._unknown(intent)
        except (ServiceRejected, ServiceUnknown):
            return self._unknown(intent)
        return self._publish(intent, evidence)
