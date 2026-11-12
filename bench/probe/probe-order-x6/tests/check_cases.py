"""Run the package cases and emit one JSON object."""

from __future__ import annotations

import json


def main() -> int:
    try:
        from solution import dedupe_merge
    except Exception:
        print(json.dumps({"cases": [], "error": "import_error"}))
        return 0

    cases = (
        ("case_01", (["Echo", "ff"], ["ECHO", "gg"]), ["ff", "gg", "ECHO"]),
        ("case_02", (["cc"], ["bb"]), ["bb", "cc"]),
        ("case_03", (["c"], ["b"]), ["b", "c"]),
    )
    results = []
    for case_id, args, expected in cases:
        try:
            actual = dedupe_merge(list(args[0]), list(args[1]))
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
