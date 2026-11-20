"""Installed CLI for the local version workspace."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from molt.version_workers import LlamaWorker
from molt.versioning import RecoveryRequired, VersionConflict, VersionSandbox


def _generation(value: str) -> int:
    try:
        generation = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("需要非负整数") from exc
    if generation < 0:
        raise argparse.ArgumentTypeError("需要非负整数")
    return generation


def add_version_parser(commands) -> None:
    version = commands.add_parser("version", help="个人工作区的配置、受管工具、版本与审计")
    version.add_argument("--workspace", required=True, type=Path, help="独立私有版本工作区")
    actions = version.add_subparsers(dest="version_command", required=True)
    init = actions.add_parser("init", help="初始化新工作区，拒绝覆盖目录")
    init.add_argument("--synthetic", action="store_true", help="将工作区记录标为合成")
    serve = actions.add_parser("serve", help="在前台启动该工作区的 kernel")
    for command in (init, serve):
        command.add_argument("--kernel", type=Path, help="kernel 可执行文件，默认从 PATH 查找")
    actions.add_parser("status", help="查看 generation、分支头、活动版本与待恢复 ID")
    actions.add_parser("history", help="按 generation 列出原始版本事件")
    actions.add_parser("show", help="查看快照、父版本、配置及工具").add_argument("snapshot")
    actions.add_parser("pin", help="输出活动快照的运行凭据")
    export = actions.add_parser("export", help="导出指定快照到新的只读目录")
    export.add_argument("snapshot")
    reader = actions.add_parser("read-tree", help="固定活动快照并输出其新只读目录")
    for command in (export, reader):
        command.add_argument("--into", type=Path, required=True, help="专用 0700 父目录")
    run = actions.add_parser("run", help="执行内置文本工具，可使用旧 pin")
    run.add_argument("message")
    run.add_argument("--pin", type=Path, help="由 pin 命令输出的文件，缺省使用活动版本")
    actions.add_parser("audit", help="读取原始事件、证据字段及摘要历史").add_argument("event")
    actions.add_parser("request-status", help="查询原 request ID").add_argument("request_id")
    actions.add_parser("recover", help="从 journal 查询或恢复原 request ID")

    commit = actions.add_parser("commit", help="验证并提交配置和受管工具")
    commit.add_argument("--config", type=Path, required=True)
    commit.add_argument("--tool", type=Path, required=True)
    commit.add_argument("--branch", default="main")
    commit.add_argument("--expect-head", help="预期分支头，首次提交省略")
    commit.add_argument("--activate", action="store_true", help="提交并激活，首次提交自动激活")

    commit_files = actions.add_parser("commit-files", help="从独立编辑目录提交清单内的文件")
    commit_files.add_argument("--root", required=True, type=Path)
    commit_files.add_argument("--manifest", required=True, type=Path, help="相对文件路径的 JSON 数组")
    commit_files.add_argument("--branch", default="main")
    commit_files.add_argument("--expect-head")
    commit_files.add_argument("--activate", action="store_true")
    commit_files.add_argument("--reason")
    commit_files.add_argument("--reason-source")

    branch = actions.add_parser("branch", help="从已有快照建立分支")
    branch.add_argument("branch")
    branch.add_argument("snapshot")
    switch = actions.add_parser("switch", help="激活指定分支头")
    switch.add_argument("branch")
    switch.add_argument("--expect-head", required=True)
    merge = actions.add_parser("merge", help="三方合并配置和工具")
    merge.add_argument("target")
    merge.add_argument("source")
    merge.add_argument("--expect-target", required=True)
    merge.add_argument("--expect-source", required=True)
    merge.add_argument("--activate", action="store_true")
    rollback = actions.add_parser("rollback", help="把活动版本回退到已提交快照")
    rollback.add_argument("snapshot")

    propose = actions.add_parser("propose", help="用隔离 GGUF worker 提案并校验后提交")
    propose.add_argument("branch")
    propose.add_argument("--expect-head", required=True)
    propose.add_argument("--task", required=True)
    propose.add_argument("--approval", required=True, help="可信本地操作者批准的范围")
    propose.add_argument("--sources", type=Path, help="明确允许投影给模型的短文本 JSON 对象")
    propose.add_argument("--activate", action="store_true")
    digest = actions.add_parser("digest", help="追加可回链的审计摘要")
    digest.add_argument("event")
    for command in (propose, digest):
        required = command is propose
        command.add_argument("--model", required=required, type=Path, help="已安装的本地 GGUF")
        command.add_argument("--model-id", required=required, help="操作者提供的模型版本标识")
    for command in (commit, commit_files, branch, switch, merge, rollback, propose):
        command.add_argument("--expect-generation", type=_generation, required=True)


def _json_file(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            data = handle.read(65537)
        if len(data) > 65536:
            raise ValueError("JSON 输入超过 64 KiB")
        result = json.loads(data)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"{path}: 需要 JSON 对象")
    return result


def _kernel_path(path: Path | None) -> Path:
    if path is None:
        command = shutil.which("molt-kernel")
        if command is None:
            raise ValueError("PATH 找不到 molt-kernel，请指定 --kernel")
        path = Path(command)
    return path.resolve(strict=True)


def _dispatch(args: argparse.Namespace):
    command = args.version_command
    if command == "init":
        provenance = "synthetic" if args.synthetic else "local"
        box = VersionSandbox.initialize(args.workspace, _kernel_path(args.kernel), provenance=provenance)
        return {"workspace": str(box.directory), "state": str(box.state), "provenance": provenance}

    box = VersionSandbox(args.workspace)
    expected = getattr(args, "expect_generation", None)
    match command:
        case "serve":
            kernel = _kernel_path(args.kernel)
            box.check_state_identity()
            os.execv(kernel, [str(kernel), "serve", "--state", str(box.state)])
        case "status":
            return box.workspace_status()
        case "history":
            return box.history()
        case "show":
            return box.show(args.snapshot)
        case "audit":
            return box.audit(args.event)
        case "pin":
            return box.pin()
        case "export" | "read-tree":
            return box.export_tree(args.into, snapshot=getattr(args, "snapshot", None))
        case "run":
            pin = _json_file(args.pin) if args.pin else box.pin()
            return {"snapshot": pin["snapshot"], "output": box.run(pin, args.message)}
        case "request-status":
            return box.request_status(args.request_id)
        case "recover":
            return box.recover()
        case "commit":
            return box.commit(
                args.branch, _json_file(args.config), _json_file(args.tool),
                expected=expected, expected_head=args.expect_head, activate=args.activate,
            )
        case "commit-files":
            return box.commit_files(
                args.branch, args.root, args.manifest, expected=expected,
                expected_head=args.expect_head, activate=args.activate,
                reason=args.reason, source=args.reason_source,
            )
        case "branch":
            return box.create_branch(args.branch, args.snapshot, expected=expected)
        case "switch":
            return box.switch(args.branch, expected=expected, expected_head=args.expect_head)
        case "merge":
            return box.merge(
                args.target, args.source, expected=expected, expected_target=args.expect_target,
                expected_source=args.expect_source, activate=args.activate,
            )
        case "rollback":
            return box.rollback(args.snapshot, expected=expected)
        case "propose" | "digest":
            if bool(args.model) != bool(args.model_id):
                raise ValueError("--model 与 --model-id 必须一起指定")
            worker = LlamaWorker(args.model, model_id=args.model_id) if args.model else None
            if command == "digest":
                return box.digest(args.event, worker=worker)
            return box.propose(
                args.branch, worker, task=args.task, approval=args.approval,
                sources=_json_file(args.sources) if args.sources else {}, activate=args.activate,
                expected=expected, expected_head=args.expect_head,
            )
    raise AssertionError(f"unexpected version command {command}")


def cmd_version(args: argparse.Namespace) -> int:
    if args.socket is not None:
        print("error: version 使用工作区自己的 socket", file=sys.stderr)
        return 2
    try:
        result = _dispatch(args)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except RecoveryRequired as exc:
        code, message = 4, str(exc)
    except (VersionConflict, BlockingIOError) as exc:
        code, message = 3, str(exc)
    except (ValueError, KeyError, FileExistsError) as exc:
        code, message = 2, str(exc)
    except subprocess.CalledProcessError as exc:
        error = exc.stderr
        code, message = 1, error.decode(errors="replace").strip() if error else str(exc)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        code, message = 1, str(exc)
    print(f"error: {message}", file=sys.stderr)
    return code
