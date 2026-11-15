"""Run the package cases and emit one JSON object."""

from __future__ import annotations

import json


def main() -> int:
    try:
        from solution import unique
    except Exception:
        print(json.dumps({"cases": [], "error": "import_error"}))
        return 0

    cases = (
        ("case_01", ["Delta", "ee", "DELTA", "delta"], ["ee", "DELTA"]),
        ("case_02", ["cc", "bb"], ["bb", "cc"]),
        ("case_03", ["c", "b"], ["b", "c"]),
    )
    results = []
    for case_id, names, expected in cases:
        try:
            actual = unique(list(names))
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
