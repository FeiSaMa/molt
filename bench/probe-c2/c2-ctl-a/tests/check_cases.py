"""Run contract cases and emit one JSON object; failures stay opaque."""

from __future__ import annotations

import json


def main() -> int:
    try:
        from solution import sign_label
    except Exception:
        print(json.dumps({"cases": [], "error": "contract check failed: import"}))
        return 0

    cases = (("case_01", 7, "pos"), ("case_02", -3, "neg"), ("case_03", 0, "zero"))
    results = []
    error = None
    for case_id, n, want in cases:
        try:
            got = sign_label(n)
        except Exception:
            got = None
        ok = got == want
        results.append({"case_id": case_id, "expected": None, "actual": None, "passed": ok})
        if not ok and error is None:
            error = f"contract check failed: case={case_id}"
    print(json.dumps({"cases": results, "error": error}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
