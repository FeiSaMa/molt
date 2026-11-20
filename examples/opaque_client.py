"""Minimal synthetic integration; installed molt + caller-owned stable IDs."""

from __future__ import annotations

import argparse
import json

from molt.kernel_client import KernelClient, KernelError


class OutcomeUnknown(RuntimeError):
    """Query the original request ID after restoring connectivity."""


def submit(socket: str, request_id: str, payload: bytes, ts: str) -> dict:
    """Caller must durably retain ID, payload and timestamp before calling.

    No business action is performed here. Retrying with changed inputs conflicts.
    Upload/manifest errors precede commit; ambiguous commit errors stay unknown.
    """
    with KernelClient(socket, timeout=3) as client:
        digest = client.upload_object(payload)
        manifest = client.create_manifest({"evidence": {"summary.json": {
            "type": "file", "mode": 0o600, "size": len(payload), "sha256": digest,
        }}})
        try:
            return client.call("generation.commit", {
                "request_id": request_id, "manifest_hash": manifest, "ts": ts,
            })
        except KernelError as error:
            if error.code not in {"io_error", "tx_locked"}:
                raise
            raise OutcomeUnknown(f"query original request ID: {request_id}") from error
        except (OSError, ConnectionError, ValueError) as error:
            raise OutcomeUnknown(f"query original request ID: {request_id}") from error


def query(socket: str, request_id: str) -> dict:
    with KernelClient(socket, timeout=3) as client:
        return client.request_status(request_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--query", action="store_true")
    args = parser.parse_args()
    if args.query:
        result = query(args.socket, args.request_id)
    else:
        result = submit(args.socket, args.request_id,
                        b'{"provenance":"synthetic","task_ref":"example-1"}',
                        "2026-11-19T00:00:00Z")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
