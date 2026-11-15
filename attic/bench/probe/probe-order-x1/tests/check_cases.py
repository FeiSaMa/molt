"""Run the package cases and emit one JSON object."""

from __future__ import annotations

import json


def main() -> int:
    try:
        from solution import rank
    except Exception:
        print(json.dumps({"cases": [], "error": "import_error"}))
        return 0

    cases = (
        ("case_01", ["dddd", "c", "bb"], [1, 2, 0]),
        ("case_02", ["cc", "bb"], [1, 0]),
        ("case_03", ["c", "b"], [1, 0]),
    )
    results = []
    for case_id, names, expected in cases:
        try:
            actual = rank(list(names))
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
