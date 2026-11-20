"""通用 kernel CLI 与只读 v1 兼容入口；不加载或执行 Agent。"""

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
from molt.core.types import Scenario
from molt.kernel_client import KernelClient, KernelError
from molt.version_cli import add_version_parser, cmd_version


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="molt", description="状态事务、证据审计、迁移与恢复")
    parser.add_argument("--root", type=Path, default=None, help="schema / 协作检查的仓库根")
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
    audit = sub.add_parser("audit", help="kernel 完整性审计；v1 模式只读重算历史语义")
    audit.add_argument("--state", type=Path, help="显式磁盘 state；不指定时通过 socket 审计")
    audit.add_argument("--checkpoint", type=Path, help="离线 kernel 审计所用外部可信检查点")
    audit.add_argument("--scenario", "--shell", dest="scenario", help="v1 审计的 scenario JSON 路径或旧 id")
    recover = sub.add_parser("recover", help="离线 kernel 事务恢复；先停止该 state 的服务")
    recover.add_argument("--state", type=Path, required=True)
    checkpoint = sub.add_parser("checkpoint", help="导出 checkpoint；可信主体须独立保管")
    checkpoint_sub = checkpoint.add_subparsers(dest="checkpoint_command", required=True)
    export = checkpoint_sub.add_parser("export")
    export.add_argument("--state", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True, help="新文件，拒绝覆盖")
    export.add_argument("--created-at", required=True)
    migrate = sub.add_parser("migrate", help="只读预览或显式迁移 v1 state 到 kernel")
    migrate.add_argument("--state", type=Path, required=True)
    migrate.add_argument("--apply", action="store_true")
    migrate.add_argument("--migration-id", help="执行迁移所需的稳定幂等标识")
    schema = sub.add_parser("schema", help="冻结 v1 schema 校验与文档生成")
    schema_sub = schema.add_subparsers(dest="schema_command")
    validate = schema_sub.add_parser("validate")
    validate.add_argument("--all", action="store_true")
    docs = schema_sub.add_parser("docs")
    docs.add_argument("--output", type=Path)
    doctor = sub.add_parser("doctor", help="只读依赖边界 / 协作检查")
    group = doctor.add_mutually_exclusive_group()
    group.add_argument("--deps", action="store_true")
    group.add_argument("--repo-policy", action="store_true")
    add_version_parser(sub)
    return parser



def _repo_root(args: argparse.Namespace) -> Path:
    if args.root:
        return args.root.resolve()
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "schemas" / "v1").is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent



def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"期望 object: {path}")
    return data



def _scenario_path(root: Path, name: str) -> Path | None:
    """先按 scenario 目录名找，再按 ``shell_id`` 反查（``--shell`` 别名）。"""
    direct = root / "scenarios" / name / "scenario.json"
    if direct.is_file():
        return direct
    matches: list[Path] = []
    for path in sorted(root.glob("scenarios/*/scenario.json")):
        if _load_json(path).get("shell_id") == name:
            matches.append(path)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"shell_id={name!r} 对应多个 scenario: {[str(p) for p in matches]}")
    return None



def _load_scenario(root: Path, name: str) -> Scenario:
    explicit = Path(name)
    path = explicit if explicit.is_file() else _scenario_path(root, name)
    if path is None:
        raise FileNotFoundError(f"scenario 不存在: {name}")
    return Scenario.from_dict(_load_json(path))


def cmd_schema(args: argparse.Namespace) -> int:
    from molt import schema

    root = _repo_root(args)
    command = args.schema_command
    if command == "validate":
        if not args.all:
            print("schema validate 需要 --all")
            return 2
        validated = schema.validate_all(root)
        for path, kind in validated:
            print(f"ok {kind} {path.relative_to(root)}")
        print(f"schema validate --all: {len(validated)} 条记录通过")
        return 0
    if command == "docs":
        if args.output:
            # --output 只写目标文件，不碰仓库内 docs/protocol.md。
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(schema.render_protocol_docs(root), encoding="utf-8")
            path = args.output
        else:
            path = schema.write_protocol_docs(root)
        print(f"wrote {path}")
        return 0
    print("用法: molt schema {validate --all|docs}")
    return 2



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
    "molt/kernel_mirror.py",
)
REPO_POLICY_FROZEN: tuple[str, ...] = (
    "schemas/v1/*.schema.json",
)
REPO_POLICY_CONTRACT: tuple[str, ...] = (
    "schemas",
    "molt/core/ports.py",
    "docs/protocol.md",
    "docs/decisions",
)


def repo_policy_missing(root: Path) -> list[str]:
    """只读检查仓库协作文件、冻结面与契约域；返回缺失项（空即通过）。"""
    missing: list[str] = []
    for rel in REPO_POLICY_FILES:
        if not (root / rel).is_file():
            missing.append(rel)
    for pattern in REPO_POLICY_FROZEN:
        if not any(root.glob(pattern)):
            missing.append(pattern)
    for rel in REPO_POLICY_CONTRACT:
        if not (root / rel).exists():
            missing.append(rel)
    return missing



def cmd_doctor(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    if args.repo_policy:
        # 纯只读：不访问网络 / GitHub API，不写文件。
        print("repo-policy frozen: " + ", ".join(REPO_POLICY_FROZEN))
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



def cmd_migrate(args: argparse.Namespace) -> int:
    socket_path = args.socket or os.environ.get("MOLT_KERNEL_SOCKET")
    if not socket_path:
        print("migrate requires MOLT_KERNEL_SOCKET", file=sys.stderr)
        return 2
    if not args.state.is_dir():
        print(f"v1 state directory does not exist: {args.state}", file=sys.stderr)
        return 2
    try:
        from molt.kernel_mirror import mirror_state, preview_state

        if args.apply:
            if not args.migration_id:
                print("--apply requires --migration-id", file=sys.stderr)
                return 2
            report = mirror_state(args.state, socket_path, migration_id=args.migration_id)
            print(
                "migration applied "
                f"state_id={preview_state(args.state, socket_path)['state_id']} "
                f"generation={report['generation']} files={report['files']} "
                f"unchanged={str(report['unchanged']).lower()}"
            )
        else:
            report = preview_state(args.state, socket_path)
            print("migration dry-run " + json.dumps(report, sort_keys=True, ensure_ascii=False))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        return 1



def _print_json(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, ensure_ascii=False))


def _kernel_local(args: argparse.Namespace) -> int:
    """Delegate offline operations to the existing kernel; never select a v1 writer."""
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
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        if args.command == "schema":
            return cmd_schema(args)
        if args.command == "doctor":
            return cmd_doctor(args)
        from molt.core import write_policy

        backend = os.environ.get("MOLT_KERNEL_BACKEND", write_policy.DEFAULT_BACKEND)
        if backend not in {"v1", "kernel"}:
            print(f"unsupported MOLT_KERNEL_BACKEND: {backend}", file=sys.stderr)
            return 2
        if backend == "v1" and args.command != "migrate":
            if args.command != "audit":
                print("v1 state is read-only; use audit or explicit migrate", file=sys.stderr)
                return 2
            if args.state is None or not args.state.is_dir() or not args.scenario:
                print("v1 read-only audit requires existing --state and --scenario JSON/id", file=sys.stderr)
                return 2
            if args.checkpoint:
                print("--checkpoint is only supported for kernel audit", file=sys.stderr)
                return 2
            from molt.core.audit import audit_state
            from molt.core.store import StateStore

            _print_json(audit_state(StateStore(args.state), _load_scenario(_repo_root(args), args.scenario)))
            return 0
        if sys.platform != "linux" or platform.machine() != "x86_64":
            print("kernel backend is supported only on Linux x86_64", file=sys.stderr)
            return 2
        if args.command == "migrate":
            return cmd_migrate(args)
        if args.command == "version":
            return cmd_version(args)
        if args.command == "audit":
            if args.scenario:
                print("--scenario is only supported for v1 read-only audit", file=sys.stderr)
                return 2
            if args.checkpoint and not args.state:
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
