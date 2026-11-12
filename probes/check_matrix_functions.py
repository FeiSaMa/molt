"""Check whether matrix runs changed the function named by ext.probe.function."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "probes/report/matrix/runs.json"
OUT = ROOT / "probes/report/matrix/function-check.json"
FAMILY_TASKS = ("probe-order-2", "probe-order-x3")


def _function_source(text: str, name: str) -> str | None:
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node)
    return None


def _function_names(text: str) -> list[str]:
    return [
        node.name
        for node in ast.parse(text).body
        if isinstance(node, ast.FunctionDef)
    ]


def _workspace(state: str) -> Path | None:
    work = Path(state) / "work"
    matches = sorted(work.glob("*/workspace/solution.py"))
    return matches[-1] if matches else None


def main() -> int:
    runs = json.loads(RUNS.read_text(encoding="utf-8"))["runs"]
    checks: list[dict[str, Any]] = []
    for record in runs:
        task_id = record.get("task_id")
        task_path = ROOT / "bench/probe" / str(task_id) / "task.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        function = (task.get("ext", {}).get("probe") or {}).get("function")
        stub_path = ROOT / "bench/probe" / str(task_id) / "workspace/solution.py"
        workspace = _workspace(record["state"])
        item: dict[str, Any] = {
            "cell": record.get("cell"),
            "task_id": task_id,
            "rep": record.get("rep"),
            "solved": record.get("solved"),
            "outcome": record.get("outcome"),
            "function": function,
        }
        if workspace is None:
            item["workspace"] = None
            item["target_present"] = False
            item["target_changed"] = False
            item["added_functions"] = []
            checks.append(item)
            continue
        workspace_text = workspace.read_text(encoding="utf-8")
        stub_text = stub_path.read_text(encoding="utf-8")
        target_source = _function_source(workspace_text, str(function)) if function else None
        stub_source = _function_source(stub_text, str(function)) if function else None
        item["target_present"] = target_source is not None
        item["target_changed"] = bool(
            target_source is not None and stub_source is not None and target_source != stub_source
        )
        item["added_functions"] = sorted(
            set(_function_names(workspace_text)) - set(_function_names(stub_text))
        )
        checks.append(item)

    summary: dict[str, Any] = {}
    for check in checks:
        key = f"{check['cell']}/{check['task_id']}"
        bucket = summary.setdefault(
            key, {"runs": 0, "target_changed": 0, "target_unchanged": 0, "target_missing": 0}
        )
        bucket["runs"] += 1
        if not check["target_present"]:
            bucket["target_missing"] += 1
        elif check["target_changed"]:
            bucket["target_changed"] += 1
        else:
            bucket["target_unchanged"] += 1

    payload = {"summary": summary, "checks": checks}
    violations = [
        check
        for check in checks
        if not check["target_present"] or not check["target_changed"] or check["added_functions"]
    ]
    payload["all_ok"] = not violations
    payload["violations"] = [
        {
            "cell": check["cell"],
            "task_id": check["task_id"],
            "rep": check["rep"],
            "target_present": check["target_present"],
            "target_changed": check["target_changed"],
            "added_functions": check["added_functions"],
        }
        for check in violations
    ]
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: {len(checks)} runs all_ok={payload['all_ok']}")
    return 0 if payload["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
