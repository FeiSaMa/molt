"""Synthetic booking service, one private SQLite transaction per subprocess call.

The fixed session handles below are fake test fixtures, never production credentials.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

SESSIONS = {"synthetic-alice": "alice", "synthetic-bob": "bob"}
INTENT_KEYS = {"operation_id", "action", "date", "seat", "business_ref"}


class Rejected(ValueError):
    pass


def canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def initialize(path: Path, *, query: bool, dedup: bool, cancel: bool) -> None:
    if path.exists():
        raise Rejected("state_exists")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE config (query_ok INTEGER, dedup INTEGER, cancel_ok INTEGER);
            CREATE TABLE confirmations (token TEXT PRIMARY KEY, owner TEXT, binding TEXT,
                                        expires REAL, used INTEGER);
            CREATE TABLE bookings (id TEXT PRIMARY KEY, owner TEXT, day TEXT, seat TEXT,
                                   active INTEGER);
            CREATE UNIQUE INDEX occupied ON bookings(day, seat) WHERE active=1;
            CREATE TABLE operations (seq INTEGER PRIMARY KEY, id TEXT, owner TEXT,
                                     binding TEXT, result TEXT);
        """)
        db.execute("INSERT INTO config VALUES(?,?,?)", (query, dedup, cancel))
    path.chmod(0o600)


def intent_checked(params: dict) -> dict:
    if set(params) != INTENT_KEYS or not all(isinstance(v, str) for v in params.values()):
        raise Rejected("invalid_intent")
    if not params["operation_id"] or len(params["operation_id"]) > 80:
        raise Rejected("invalid_operation_id")
    if params["action"] not in {"reserve", "cancel"}:
        raise Rejected("invalid_action")
    try:
        datetime.date.fromisoformat(params["date"])
    except ValueError as error:
        raise Rejected("invalid_date") from error
    if params["seat"] not in {"seat-A", "seat-B"}:
        raise Rejected("invalid_seat")
    if (params["action"] == "reserve") != (params["business_ref"] == ""):
        raise Rejected("invalid_business_ref")
    return params


def handle(db: sqlite3.Connection, request: dict) -> dict:
    if set(request) != {"method", "session", "params"} or not isinstance(request["params"], dict):
        raise Rejected("invalid_request")
    session = request["session"]
    if session == "synthetic-expired":
        raise Rejected("session_expired")
    if session == "synthetic-denied":
        raise Rejected("permission_denied")
    if not isinstance(session, str) or session not in SESSIONS:
        raise Rejected("unauthenticated")
    owner = SESSIONS[session]
    method, params = request["method"], request["params"]
    query_ok, dedup, cancel_ok = db.execute("SELECT * FROM config").fetchone()
    if method == "identity" and not params:
        return {"principal": owner, "provenance": "synthetic"}
    if method == "query" and set(params) == {"operation_id"}:
        if not query_ok:
            raise Rejected("query_unavailable")
        row = db.execute("SELECT owner,result FROM operations WHERE id=? ORDER BY seq LIMIT 1",
                         (params["operation_id"],)).fetchone()
        if row is None:
            return {"status": "not_found", "retry_same_id": bool(dedup), "provenance": "synthetic"}
        if row[0] != owner:
            raise Rejected("permission_denied")
        return json.loads(row[1])
    if method not in {"confirm", "execute"}:
        raise Rejected("invalid_request")
    if method == "execute":
        if set(params) != INTENT_KEYS | {"confirmation"}:
            raise Rejected("invalid_intent")
        params = params.copy()
        token = params.pop("confirmation")
    intent = intent_checked(params)
    binding = canonical(intent)
    if method == "confirm":
        token = uuid.uuid4().hex
        db.execute("INSERT INTO confirmations VALUES(?,?,?,?,0)",
                   (token, owner, binding, time.time() + 60))
        return {"confirmation": token, "provenance": "synthetic"}
    previous = db.execute("SELECT owner,binding,result FROM operations WHERE id=? ORDER BY seq LIMIT 1",
                          (intent["operation_id"],)).fetchone()
    if previous and dedup:
        if previous[:2] != (owner, binding):
            raise Rejected("operation_conflict")
        return json.loads(previous[2])
    row = db.execute("SELECT owner,binding,expires,used FROM confirmations WHERE token=?",
                     (token,)).fetchone()
    if row is None or row[:2] != (owner, binding) or row[2] < time.time() or row[3]:
        raise Rejected("confirmation_invalid")
    ref = intent["business_ref"]
    if intent["action"] == "reserve":
        occupied = db.execute("SELECT 1 FROM bookings WHERE day=? AND seat=? AND active=1",
                              (intent["date"], intent["seat"])).fetchone()
        status = "no_capacity" if occupied else "confirmed"
        if not occupied:
            ref = "synthetic-" + uuid.uuid4().hex
            db.execute("INSERT INTO bookings VALUES(?,?,?,?,1)",
                       (ref, owner, intent["date"], intent["seat"]))
    else:
        booking = db.execute("SELECT owner,day,seat,active FROM bookings WHERE id=?", (ref,)).fetchone()
        if booking is None or booking[:3] != (owner, intent["date"], intent["seat"]):
            raise Rejected("permission_denied")
        status = "cancelled" if cancel_ok else "cancel_failed"
        if cancel_ok:
            db.execute("UPDATE bookings SET active=0 WHERE id=?", (ref,))
    result = {"status": status, "operation_id": intent["operation_id"],
              "business_ref": ref, "provenance": "synthetic"}
    db.execute("UPDATE confirmations SET used=1 WHERE token=?", (token,))
    db.execute("INSERT INTO operations(id,owner,binding,result) VALUES(?,?,?,?)",
               (intent["operation_id"], owner, binding, canonical(result)))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--no-query", action="store_true")
    parser.add_argument("--no-dedup", action="store_true")
    parser.add_argument("--deny-cancel", action="store_true")
    parser.add_argument("--drop-response", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.init:
            initialize(args.db, query=not args.no_query, dedup=not args.no_dedup,
                       cancel=not args.deny_cancel)
            print('{"provenance":"synthetic","initialized":true}')
            return
        if not args.db.is_file():
            raise Rejected("uninitialized")
        request = json.loads(sys.stdin.read(8193))
        if not isinstance(request, dict):
            raise Rejected("invalid_request")
        with sqlite3.connect(args.db, timeout=2) as db:
            db.execute("BEGIN IMMEDIATE")
            result = handle(db, request)
        if args.drop_response and request["method"] == "execute":
            sys.exit(73)  # Business transaction is durable; caller receives no result.
        print(canonical({"result": result}))
    except (Rejected, ValueError, TypeError, sqlite3.Error):
        # Do not echo raw requests, handles, confirmations or database paths.
        print('{"error":"rejected"}')
        sys.exit(2)


if __name__ == "__main__":
    main()
