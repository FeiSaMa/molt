"""One-shot replay worker: re-score a recorded solution with ProbeVerifier.

This module is launched by ``molt.upper.evaluator.ReplayRunner`` in its own
process group. The evaluator supervises the worker and kills the whole group on
timeout; the verifier's internal ``subprocess.run(timeout=...)`` only kills its
direct checker child, which is why the outer worker exists.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _payload(outcome: Any) -> dict[str, Any]:
    cases = outcome.diagnostic.get("cases") or []
    return {
        "passed": bool(outcome.passed),
        "outcome": outcome.outcome,
        "matches": bool(outcome.matches),
        "timed_out": bool(outcome.timed_out),
        "error": outcome.diagnostic.get("error"),
        "cases_passed": sum(1 for case in cases if case.get("passed") is True),
        "cases_total": len(cases),
        "tests_hash_before": outcome.tests_hash_before,
        "tests_hash_after": outcome.tests_hash_after,
    }


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--solution", required=True)
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args(argv)
    try:
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from shells.probe.task_provider import ProbeTaskProvider
        from shells.probe.verifier import ProbeVerifier

        task = ProbeTaskProvider(repo_root).load_task(args.task_id)
        verifier = ProbeVerifier(repo_root, python=sys.executable)
        outcome = verifier.verify(task, Path(args.workdir))
        print(json.dumps(_payload(outcome), ensure_ascii=False))
        return 0
    except Exception as exc:  # fail closed with a parseable marker
        print(
            json.dumps(
                {
                    "passed": False,
                    "outcome": "worker_error",
                    "matches": False,
                    "timed_out": False,
                    "error": "worker_exception",
                    "cases_passed": 0,
                    "cases_total": 0,
                    "exception": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
