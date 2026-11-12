"""Run the package cases and emit one JSON object."""

from __future__ import annotations

import json


def main() -> int:
    try:
        from solution import min_max
    except Exception:
        print(json.dumps({"cases": [], "error": "import_error"}))
        return 0

    cases = (
        ("case_01", ["eeee", "d", "cc"], ("d", "eeee")),
        ("case_02", ["cc", "bb"], ("bb", "cc")),
        ("case_03", ["c", "b"], ("b", "c")),
    )
    results = []
    for case_id, names, expected in cases:
        try:
            actual = min_max(list(names))
        except Exception:
            actual = None
        results.append(
            {
                "case_id": case_id,
                "expected": list(expected),
                "actual": list(actual) if actual is not None else None,
                "passed": actual == expected,
            }
        )
    print(json.dumps({"cases": results, "error": None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
