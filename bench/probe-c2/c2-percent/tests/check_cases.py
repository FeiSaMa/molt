"""Run the package cases and emit one JSON object."""

from __future__ import annotations

import json
from decimal import Decimal


def main() -> int:
    try:
        from solution import format_percent
    except Exception:
        print(json.dumps({"cases": [], "error": "import_error"}))
        return 0

    cases = (
        ("case_01", "12.121", "12.12"),
        ("case_02", "12.129", "12.13"),
        ("case_03", "12.125", "12.13"),
        ("case_04", "-12.125", "-12.13"),
    )
    results = []
    for case_id, raw, expected in cases:
        try:
            actual = format_percent(Decimal(raw))
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
