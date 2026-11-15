"""Run the package cases and emit one JSON object."""

from __future__ import annotations

import json


def main() -> int:
    try:
        from solution import dedupe_top_k
    except Exception:
        print(json.dumps({"cases": [], "error": "import_error"}))
        return 0

    cases = (
        ("case_01", ["Gamma", "dd", "GAMMA", "gamma"], 2, ["dd", "GAMMA"]),
        ("case_02", ["D", "c", "d"], 2, ["c", "D"]),
        ("case_03", ["eee", "dd", "eee"], 3, ["dd", "eee"]),
    )
    results = []
    for case_id, names, k, expected in cases:
        try:
            actual = dedupe_top_k(list(names), k)
        except Exception:
            actual = None
        results.append(
            {
                "case_id": case_id,
                "expected": expected,
                "actual": actual,
                "passed": actual == expected,
            }
        )
    print(json.dumps({"cases": results, "error": None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
