"""Run the package cases and emit one JSON object."""

from __future__ import annotations

import json


def main() -> int:
    try:
        from solution import top_k
    except Exception:
        print(json.dumps({"cases": [], "error": "import_error"}))
        return 0

    cases = (
        ("case_01", ["ccc", "b", "aa"], 2, ["b", "aa"]),
        ("case_02", ["bb", "aa"], 2, ["aa", "bb"]),
        ("case_03", ["b", "a"], 1, ["a"]),
        ("case_04", [], 0, []),
    )
    results = []
    for case_id, names, k, expected in cases:
        try:
            actual = top_k(list(names), k)
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
