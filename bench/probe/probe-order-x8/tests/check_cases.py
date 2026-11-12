"""Run the package cases and emit one JSON object."""

from __future__ import annotations

import json


def main() -> int:
    try:
        from solution import distinct_count
    except Exception:
        print(json.dumps({"cases": [], "error": "import_error"}))
        return 0

    cases = (
        ("case_01", ["Hotel", "ii", "HOTEL"], 2),
        ("case_02", ["cc", "bb"], 2),
        ("case_03", ["dd", "dd"], 1),
    )
    results = []
    for case_id, names, expected in cases:
        try:
            actual = distinct_count(list(names))
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
