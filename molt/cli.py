"""通用 kernel CLI。"""

from __future__ import annotations

import argparse
import ast
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from molt.core.errors import MoltError
from molt.kernel_client import KernelClient, KernelError
from molt.project_cli import add_project_parser
from molt.version_cli import add_version_parser, cmd_version


def _tui_disabled() -> bool:
    """``MOLT_NO_TUI=1`` is the only hard off switch for the explicit subcommand."""
    return os.environ.get("MOLT_NO_TUI") == "1"


def _tui_wanted() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty() and not _tui_disabled()


def _tui_platform_ok() -> bool:
    return sys.platform == "linux" and platform.machine() == "x86_64"


def _tui_bin() -> str | None:
    explicit = os.environ.get("MOLT_TUI_BIN")
    if explicit is not None:
        path = os.path.abspath(os.path.expanduser(explicit))
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
        return None
    found = shutil.which("molt-tui")
    if found and os.path.isfile(found):
        return found
    return None


def _run_tui(binary: str | None, tui_args: Sequence[str]) -> int:
    if binary is None:
        explicit = os.environ.get("MOLT_TUI_BIN")
        if explicit is None:
            print("molt-tui not found; install it or set MOLT_TUI_BIN", file=sys.stderr)
        else:
            print(f"MOLT_TUI_BIN does not name an executable file: {explicit!r}", file=sys.stderr)
        return 2
    try:
        os.execv(binary, [binary, *tui_args])
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="molt", description="状态事务、证据审计与恢复")
    parser.add_argument("--root", type=Path, default=None, help="doctor 协作检查的仓库根")
    parser.add_argument("--socket", default=None, help="kernel UDS；缺省 MOLT_KERNEL_SOCKET")
    sub = parser.add_subparsers(dest="command")
    for name, help_text in (("current", "查询当前 generation"), ("inspect", "查询 state 身份与状态")):
        sub.add_parser(name, help=help_text)
    status = sub.add_parser("request-status", help="以原 request ID 查询；unknown 不表示失败")
    status.add_argument("request_id")
    objects = sub.add_parser("object", help="不透明 CAS 对象上传 / 读取")
    object_sub = objects.add_subparsers(dest="object_command", required=True)
    put = object_sub.add_parser("put", help="有界分块上传文件")
    put.add_argument("path", type=Path)
    get = object_sub.add_parser("get", help="读取到新文件；拒绝覆盖")
    get.add_argument("hash")
    get.add_argument("--out", type=Path, required=True)
    manifests = sub.add_parser("manifest", help="创建 / 查询不透明对象树")
    manifest_sub = manifests.add_subparsers(dest="manifest_command", required=True)
    create = manifest_sub.add_parser("create", help="从 JSON roots 对象创建 manifest")
    create.add_argument("path", type=Path)
    get_manifest = manifest_sub.add_parser("get")
    get_manifest.add_argument("hash")
    commit = sub.add_parser("commit", help="提交 generation；未知结果先查原 request ID，禁止盲重试")
    commit.add_argument("--manifest", required=True)
    commit.add_argument("--request-id", required=True, help="稳定幂等标识，重试时保留全部参数")
    commit.add_argument("--at", required=True, help="稳定 RFC3339 时间；重试时不得改变")
    audit = sub.add_parser("audit", help="kernel 完整性审计")
    audit.add_argument("--state", type=Path, help="显式磁盘 state；不指定时通过 socket 审计")
    audit.add_argument("--checkpoint", type=Path, help="离线 kernel 审计所用外部可信检查点")
    recover = sub.add_parser("recover", help="离线 kernel 事务恢复；先停止该 state 的服务")
    recover.add_argument("--state", type=Path, required=True)
    checkpoint = sub.add_parser("checkpoint", help="导出 checkpoint；可信主体须独立保管")
    checkpoint_sub = checkpoint.add_subparsers(dest="checkpoint_command", required=True)
    export = checkpoint_sub.add_parser("export")
    export.add_argument("--state", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True, help="新文件，拒绝覆盖")
    export.add_argument("--created-at", required=True)
    doctor = sub.add_parser("doctor", help="只读依赖边界 / 协作检查")
    group = doctor.add_mutually_exclusive_group()
    group.add_argument("--deps", action="store_true")
    group.add_argument("--repo-policy", action="store_true")
    add_version_parser(sub)
    add_project_parser(sub)
    tui = sub.add_parser("tui", help="启动 molt-tui（终端界面）", add_help=False)
    tui.add_argument("tui_args", nargs=argparse.REMAINDER, help="透传给 molt-tui 的参数")
    return parser



def _repo_root(args: argparse.Namespace) -> Path:
    if args.root:
        return args.root.resolve()
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "molt" / "cli.py").is_file():
            return candidate
    return Path(__file__).resolve().parent.parent



def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"期望 object: {path}")
    return data



def core_import_violations(root: Path) -> list[str]:
    """``molt/core/**`` 只允许 stdlib 与 ``molt.core.*``。"""
    allowed = set(sys.stdlib_module_names) | {"__future__"}
    violations: list[str] = []
    for path in sorted((root / "molt" / "core").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module not in allowed and module != "molt":
                        violations.append(f"{path}: import {alias.name}")
                    elif alias.name == "molt":
                        violations.append(f"{path}: import molt（只允许 molt.core.*）")
                    elif alias.name.startswith("molt.") and not alias.name.startswith("molt.core"):
                        violations.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                module = node.module or ""
                top = module.split(".")[0]
                if module == "molt" or module.startswith("molt."):
                    if module != "molt.core" and not module.startswith("molt.core."):
                        violations.append(f"{path}: from {module} import ...")
                elif top not in allowed:
                    violations.append(f"{path}: from {module} import ...")
    return violations



REPO_POLICY_FILES: tuple[str, ...] = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "docs/style.md",
    ".github/CODEOWNERS",
    ".github/pull_request_template.md",
    ".github/workflows/ci.yml",
    "docs/architecture.md",
    "docs/operations/kernel-cli.md",
    "crates/molt-kernel/Cargo.toml",
    "crates/molt-kernel/src/main.rs",
    "molt/kernel_client.py",
    "molt/project_cli.py",
    "molt/version_cli.py",
)
REPO_POLICY_CONTRACT: tuple[str, ...] = (
    "docs/decisions",
)


def repo_policy_missing(root: Path) -> list[str]:
    """只读检查仓库协作文件与契约域；返回缺失项（空即通过）。"""
    missing: list[str] = []
    for rel in REPO_POLICY_FILES:
        if not (root / rel).is_file():
            missing.append(rel)
    for rel in REPO_POLICY_CONTRACT:
        if not (root / rel).exists():
            missing.append(rel)
    return missing



def cmd_doctor(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    if args.repo_policy:
        # 纯只读：不访问网络 / GitHub API，不写文件。
        print("repo-policy contract: " + ", ".join(REPO_POLICY_CONTRACT))
        missing = repo_policy_missing(root)
        if missing:
            for item in missing:
                print(f"repo-policy missing: {item}", file=sys.stderr)
            return 1
        print("repo-policy ok")
        return 0
    violations = core_import_violations(root)
    if violations:
        for violation in violations:
            print(f"FAIL {violation}", file=sys.stderr)
        return 1
    print("doctor --deps: core 只依赖 stdlib 与 molt.core.*")
    return 0



def _print_json(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, ensure_ascii=False))


def _kernel_local(args: argparse.Namespace) -> int:
    """Delegate offline operations to the kernel."""
    executable = os.environ.get("MOLT_KERNEL_BIN") or shutil.which("molt-kernel")
    if not executable:
        print("requires molt-kernel on PATH or MOLT_KERNEL_BIN", file=sys.stderr)
        return 2
    command = [executable, args.command]
    if args.command == "checkpoint":
        command.append("export")
    command.extend(["--state", str(args.state.resolve())])
    if args.command == "audit" and args.checkpoint:
        command.extend(["--checkpoint", str(args.checkpoint)])
    if args.command == "checkpoint":
        command.extend(["--out", str(args.out), "--created-at", args.created_at])
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode if result.returncode >= 0 else 1


def _kernel_socket(args: argparse.Namespace) -> int:
    socket_path = args.socket or os.environ.get("MOLT_KERNEL_SOCKET")
    if not socket_path:
        print("requires MOLT_KERNEL_SOCKET or --socket", file=sys.stderr)
        return 2
    with KernelClient(socket_path, timeout=30) as client:
        if args.command in {"current", "inspect", "audit"}:
            result = client.call(args.command)
        elif args.command == "request-status":
            result = client.request_status(args.request_id)
        elif args.command == "object":
            if args.object_command == "put":
                result = {"hash": client.upload_object(args.path.read_bytes())}
            else:
                data = client.get_object(args.hash)
                with args.out.open("xb") as output:
                    output.write(data)
                result = {"hash": args.hash, "size": len(data)}
        elif args.command == "manifest":
            if args.manifest_command == "create":
                result = {"hash": client.create_manifest(_load_json(args.path))}
            else:
                result = client.get_manifest(args.hash)
        else:
            result = client.call("generation.commit", {
                "request_id": args.request_id, "manifest_hash": args.manifest, "ts": args.at,
            })
    _print_json(result)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    # argparse 的 REMAINDER 不会吞下紧随子命令的选项（如 ``tui --mock``），
    # 先用 parse_known_args 收走这些透传参数，再按原语义校验其余子命令。
    args, forwarded = parser.parse_known_args(argv)
    if forwarded and args.command != "tui":
        parser.error("unrecognized arguments: " + " ".join(forwarded))
    if not args.command:
        if _tui_wanted() and _tui_platform_ok():
            binary = _tui_bin()
            if binary is not None or "MOLT_TUI_BIN" in os.environ:
                return _run_tui(binary, [])
        parser.print_help()
        return 0
    try:
        if args.command == "tui":
            # 显式子命令不检查 TTY：CI 里也能跑 ``--selftest``；只服从
            # MOLT_NO_TUI=1 这个显式关闭开关。
            if _tui_disabled():
                print("molt tui is disabled by MOLT_NO_TUI=1", file=sys.stderr)
                return 2
            if not _tui_platform_ok():
                print("molt-tui is supported only on Linux x86_64", file=sys.stderr)
                return 2
            return _run_tui(_tui_bin(), [*forwarded, *args.tui_args])
        backend = os.environ.get("MOLT_KERNEL_BACKEND", "kernel")
        if backend == "v1":
            print("MOLT_KERNEL_BACKEND=v1 is retired; use an archived release on a separate copy of the old state", file=sys.stderr)
            return 2
        if backend != "kernel":
            print(f"unsupported MOLT_KERNEL_BACKEND: {backend}", file=sys.stderr)
            return 2
        if args.command == "doctor":
            return cmd_doctor(args)
        if sys.platform != "linux" or platform.machine() != "x86_64":
            print("kernel backend is supported only on Linux x86_64", file=sys.stderr)
            return 2
        if args.command in {"version", "project"}:
            return cmd_version(args)
        if args.command == "audit" and args.checkpoint and not args.state:
            print("--checkpoint requires --state for offline kernel audit", file=sys.stderr)
            return 2
        if args.command in {"recover", "checkpoint"} or (args.command == "audit" and args.state):
            return _kernel_local(args)
        return _kernel_socket(args)
    except (MoltError, OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        if args.command == "commit" and not (
            isinstance(exc, KernelError) and exc.code in {"invalid_request", "request_conflict", "permission_denied", "tx_locked"}
        ):
            print(f"commit outcome unknown; query request-status {args.request_id!r}; "
                  "recover if necessary, then retry only with the same ID and parameters", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
