"""Run the package cases and emit one JSON object."""

from __future__ import annotations

import json


def main() -> int:
    try:
        from solution import dedupe
    except Exception:
        print(json.dumps({"cases": [], "error": "import_error"}))
        return 0

    cases = (
        ("case_01", ["Alpha", "bb", "ALPHA", "alpha"], ["bb", "ALPHA"]),
        ("case_02", ["B", "a", "A", "b"], ["A", "B"]),
        ("case_03", ["bb", "aa", "bb"], ["aa", "bb"]),
        ("case_04", ["aaa", "bb", "bb"], ["bb", "aaa"]),
    )
    results = []
    for case_id, names, expected in cases:
        try:
            actual = dedupe(list(names))
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
