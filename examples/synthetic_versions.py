"""Exercise versioned synthetic configuration and tools in a private kernel state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from molt.version_workers import LlamaWorker, ScriptWorker
from molt.versioning import RELEASES, VersionSandbox
from molt.versioning import ROOT as ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init").add_argument("kernel", type=Path)
    activate = commands.add_parser("activate")
    activate.add_argument("release", choices=RELEASES)
    activate.add_argument("expected", type=json.loads, help="complete prior current JSON or null")
    rollback = commands.add_parser("rollback")
    rollback.add_argument("snapshot")
    rollback.add_argument("expected", type=json.loads)
    commands.add_parser("status")
    branch = commands.add_parser("create-branch")
    branch.add_argument("branch")
    branch.add_argument("snapshot")
    branch.add_argument("expected", type=json.loads)
    switch = commands.add_parser("switch")
    switch.add_argument("branch")
    switch.add_argument("expected", type=json.loads)
    switch.add_argument("expected_head")
    advance = commands.add_parser("advance")
    advance.add_argument("branch")
    advance.add_argument("release", choices=RELEASES)
    advance.add_argument("expected", type=json.loads)
    advance.add_argument("expected_head")
    proposal = commands.add_parser("propose")
    proposal.add_argument("branch")
    proposal.add_argument("expected", type=json.loads)
    proposal.add_argument("expected_head")
    proposal.add_argument("--task", required=True)
    proposal.add_argument("--approval", required=True)
    proposal.add_argument("--sources", type=json.loads, default={})
    proposal.add_argument("--activate", action="store_true")
    agent = proposal.add_mutually_exclusive_group(required=True)
    agent.add_argument("--script", type=Path)
    agent.add_argument("--model", type=Path)
    proposal.add_argument("--model-id")
    merge = commands.add_parser("merge")
    merge.add_argument("target")
    merge.add_argument("source")
    merge.add_argument("expected", type=json.loads)
    merge.add_argument("expected_target")
    merge.add_argument("expected_source")
    merge.add_argument("--activate", action="store_true")
    commands.add_parser("pin")
    commands.add_parser("read").add_argument("pin", type=json.loads)
    run = commands.add_parser("run")
    run.add_argument("pin", type=json.loads)
    run.add_argument("message")
    digest = commands.add_parser("digest")
    digest.add_argument("event")
    generator = digest.add_mutually_exclusive_group()
    generator.add_argument("--script", type=Path)
    generator.add_argument("--model", type=Path)
    digest.add_argument("--model-id")
    commands.add_parser("audit").add_argument("event")
    commands.add_parser("recover")
    commands.add_parser("gc-plan")
    args = parser.parse_args()
    worker = None
    if args.command in ("propose", "digest"):
        if bool(args.model) != bool(args.model_id):
            parser.error("--model and --model-id must be supplied together")
        if args.model:
            worker = LlamaWorker(args.model, model_id=args.model_id)
        elif args.script:
            worker = ScriptWorker(args.script)
    if args.command == "init":
        sandbox = VersionSandbox.initialize(args.directory, args.kernel.resolve(strict=True))
        result = {"state": str(sandbox.state)}
    else:
        sandbox = VersionSandbox(args.directory)
        match args.command:
            case "activate":
                result = sandbox.activate(args.release, expected=args.expected)
            case "rollback":
                result = sandbox.rollback(args.snapshot, expected=args.expected)
            case "status":
                result = sandbox.status()
            case "create-branch":
                result = sandbox.create_branch(args.branch, args.snapshot, expected=args.expected)
            case "switch":
                result = sandbox.switch(
                    args.branch, expected=args.expected, expected_head=args.expected_head,
                )
            case "advance":
                result = sandbox.advance(
                    args.branch, args.release, expected=args.expected, expected_head=args.expected_head,
                )
            case "propose":
                result = sandbox.propose(
                    args.branch, worker, task=args.task, expected=args.expected,
                    expected_head=args.expected_head, approval=args.approval, sources=args.sources,
                    activate=args.activate,
                )
            case "merge":
                result = sandbox.merge(
                    args.target, args.source, expected=args.expected,
                    expected_target=args.expected_target, expected_source=args.expected_source,
                    activate=args.activate,
                )
            case "pin":
                result = sandbox.pin()
            case "read":
                result = sandbox.read(args.pin)
            case "run":
                result = sandbox.run(args.pin, args.message)
            case "digest":
                result = sandbox.digest(args.event, worker=worker)
            case "audit":
                result = sandbox.audit(args.event)
            case "recover":
                result = sandbox.recover()
            case "gc-plan":
                result = sandbox.gc_plan()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
