"""Synthetic client process entrypoint; input from stdin, never argv credentials."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Load only this reviewed standalone example directory, including under python -I.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from synthetic_booking.adapter import BookingAdapter, ServiceRejected, ServiceUnknown, Transport


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--drop-response", action="store_true")
    args = parser.parse_args()
    try:
        request = json.loads(sys.stdin.read(8193))
        if set(request) != {"session", "intent", "confirmation"}:
            raise ValueError("unexpected fields")
        client = BookingAdapter(Transport(args.db, drop_response=args.drop_response),
                                args.journal, args.socket)
        result = client.run(request["session"], request["intent"], request["confirmation"])
    except (ValueError, TypeError, ServiceRejected, ServiceUnknown):
        print('{"status":"rejected_or_unavailable","provenance":"synthetic"}')
        sys.exit(2)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
