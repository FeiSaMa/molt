"""JSON CLI for the upper-layer append-only graph.

Usage examples:
    python -m molt.upper.graph_cli graph-show   --state /tmp/opencode/molt-upper-state
    python -m molt.upper.graph_cli graph-log    --state /tmp/opencode/molt-upper-state
    python -m molt.upper.graph_cli graph-branch --state /tmp/opencode/molt-upper-state --label ops
    python -m molt.upper.graph_cli graph-switch --state /tmp/opencode/molt-upper-state \
        --branch-id br-... --target-node-id <sha256> --expected-tip <sha256>

Only ``--state`` is a filesystem path (checked outside the repo); all other
arguments are ids/labels. Output is always JSON. There is no merge/delete command.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .graph import GraphError, UpperGraphStore
from .registry import UpperRegistry
from .storage import StatePathError, ensure_outside_repo

REPO_ROOT = Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(name: str, help_text: str) -> argparse.ArgumentParser:
        child = sub.add_parser(name, help=help_text)
        child.add_argument("--state", required=True)
        child.add_argument("--author", default="graph-cli")
        return child

    branch = common("graph-branch", "create a root if needed and fork a branch")
    branch.add_argument("--label", required=True)
    branch.add_argument("--from-node")
    branch.add_argument("--permission-scope")
    branch.add_argument("--approval-level", choices=("standard", "strict"))
    branch.add_argument("--scenario-version", default="")

    fork = common("graph-fork", "fork a branch from an existing node")
    fork.add_argument("--label", required=True)
    fork.add_argument("--from-node", required=True)
    fork.add_argument("--permission-scope")
    fork.add_argument("--approval-level", choices=("standard", "strict"))

    switch = common("graph-switch", "CAS switch a branch tip and active pointer")
    switch.add_argument("--branch-id", required=True)
    switch.add_argument("--target-node-id", required=True)
    switch.add_argument("--expected-tip", required=True)
    switch.add_argument("--expected-active-branch")

    rollback = common("graph-rollback", "CAS move a branch tip/active pointer back")
    rollback.add_argument("--branch-id", required=True)
    rollback.add_argument("--target-node-id", required=True)
    rollback.add_argument("--expected-tip", required=True)
    rollback.add_argument("--expected-active-branch")
    rollback.add_argument("--reason", required=True)

    log = common("graph-log", "print graph events")
    log.add_argument("--limit", type=int, default=0)

    common("graph-show", "print branches, active pointer, and nodes")
    return parser


def _store(state_ref: str) -> UpperGraphStore:
    state = ensure_outside_repo(Path(state_ref), REPO_ROOT)
    registry = None
    if (state / "registry.json").is_file():
        registry = UpperRegistry(state, repo_root=REPO_ROOT)
    return UpperGraphStore(state, repo_root=REPO_ROOT, registry=registry)


def _run(args: argparse.Namespace) -> dict[str, Any]:
    graph = _store(args.state)
    if args.command == "graph-branch":
        if not graph.nodes():
            graph.create_root(
                author=args.author,
                scenario_version=args.scenario_version,
            )
        root = graph.root_node()
        from_node = args.from_node
        if from_node is None:
            active = graph.active_pointer()
            from_node = active["head_node_id"] if active else root.node_id
        branch = graph.fork(
            args.label,
            from_node,
            author=args.author,
            permission_scope=args.permission_scope,
            approval_level=args.approval_level,
        )
        return {
            "ok": True,
            "command": args.command,
            "branch": branch.to_public_dict(),
            "active": graph.active_pointer(),
        }
    if args.command == "graph-fork":
        branch = graph.fork(
            args.label,
            args.from_node,
            author=args.author,
            permission_scope=args.permission_scope,
            approval_level=args.approval_level,
        )
        return {
            "ok": True,
            "command": args.command,
            "branch": branch.to_public_dict(),
            "active": graph.active_pointer(),
        }
    if args.command == "graph-switch":
        target = graph.switch(
            args.branch_id,
            args.target_node_id,
            expected_head=args.expected_tip,
            expected_active_branch=args.expected_active_branch,
            author=args.author,
        )
        return {
            "ok": True,
            "command": args.command,
            "target": target.to_public_dict(),
            "branch": graph.branch(args.branch_id).to_public_dict(),
            "active": graph.active_pointer(),
        }
    if args.command == "graph-rollback":
        target = graph.rollback(
            args.branch_id,
            args.target_node_id,
            expected_head=args.expected_tip,
            expected_active_branch=args.expected_active_branch,
            author=args.author,
            reason=args.reason,
        )
        return {
            "ok": True,
            "command": args.command,
            "target": target.to_public_dict(),
            "branch": graph.branch(args.branch_id).to_public_dict(),
            "active": graph.active_pointer(),
        }
    if args.command == "graph-log":
        events = graph.events()
        if args.limit > 0:
            events = events[-args.limit :]
        return {"ok": True, "command": args.command, "events": events}
    if args.command == "graph-show":
        return {"ok": True, "command": args.command, "graph": graph.to_public_dict()}
    raise GraphError(f"unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = _run(args)
    except (GraphError, StatePathError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "command": args.command,
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=1,
            )
        )
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
