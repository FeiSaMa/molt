#!/usr/bin/env python3
"""Demo: drive the project_kb MCP server through the sandboxed client.

Default run spawns a real ``bwrap`` child, so the whole legacy stdio handshake
is exercised end to end: initialize → notifications/initialized → tools/list →
tools/call. ``--self-check`` adds negative checks (allowlist denials, unknown
tool -32602, read-only workspace, profile asset guard).

Exit codes: 0 success, 1 failure. Nothing is faked on failure — the concrete
MCP error code is printed and the process exits non-zero.

Run with: PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/demo_mcp.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from molt.mcp.allowlist import (  # noqa: E402
    McpAllowlist,
    McpAllowlistError,
    default_allowlist_path,
)
from molt.mcp.client import McpClient, workspace_digest  # noqa: E402
from molt.mcp.jsonrpc import (  # noqa: E402
    McpError,
    McpIntegrityError,
    McpPermissionError,
    McpProtocolError,
    McpRemoteError,
    read_message,
)
from molt.mcp.profile import (  # noqa: E402
    McpProfileError,
    assert_profile_executable,
    build_profile_asset,
    profile_sidecar,
    verify_artifact_hash,
)

SERVER_ID = "project_kb"
TRANSCRIPT = Path("/tmp/molt-mcp-transcript.jsonl")


def _preview(payload: Any, limit: int = 200) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def _load_allowlist() -> McpAllowlist:
    return McpAllowlist.load(default_allowlist_path(REPO_ROOT), repo_root=REPO_ROOT)


def _require_project_kb(allowlist: McpAllowlist):
    return allowlist.require_tool(SERVER_ID, "search_docs")


def run_demo() -> int:
    allowlist = _load_allowlist()
    spec = _require_project_kb(allowlist)
    if TRANSCRIPT.exists():
        TRANSCRIPT.unlink()

    client = McpClient(spec, REPO_ROOT, bwrap=True, transcript=TRANSCRIPT)
    client.start()
    workspace = client.workspace
    before = workspace_digest(workspace) if workspace else ""
    try:
        handshake = client.initialize()
        print(f"protocol_version={client.protocol_version}")
        print(f"server_info={_preview(client.server_info)}")
        tools = client.list_tools()
        print("tools=" + ",".join(tool["name"] for tool in tools))
        search = client.call_tool("search_docs", {"query": "零写入"})
        print("search_docs=" + _preview(search))
        document = client.call_tool("get_doc", {"doc_id": "zero-write"})
        print("get_doc=" + _preview(document))
        print(f"isolation={'bwrap' if client.use_bwrap else 'none'}")
        print("workspace_bind=ro")
        print(f"resource_limits={client.limits}")
        print(f"frames={client.frame_count} bytes={client.byte_count}")
        print(f"transcript={TRANSCRIPT}")
        after = workspace_digest(workspace) if workspace else ""
        if workspace is not None and before != after:
            print("workspace_digest_changed=true")
            return 1
        print("workspace_digest_unchanged=true")
        if handshake.get("protocolVersion") != "2025-06-18":
            print("handshake_version_mismatch=true")
            return 1
        if client.limits != "prlimit":
            print(f"limits_unavailable={client.limits}")
            return 1
        asset = build_profile_asset(spec, repo_root=REPO_ROOT)
        verify_artifact_hash(asset, REPO_ROOT)
        print(f"artifact_hash={asset.content['artifact_hash']}")
        print(f"spec_hash={asset.content['spec_hash']}")
        print("profile_verified=true")
        return 0
    finally:
        client.close()


def run_self_check() -> int:
    checks: list[tuple[str, bool, str]] = []
    raw = json.loads(default_allowlist_path(REPO_ROOT).read_text(encoding="utf-8"))
    entry = dict(raw["servers"][0])

    def check(name: str, fn) -> None:
        try:
            detail = fn()
        except BaseException as exc:  # noqa: BLE001 - report, do not abort the suite
            checks.append((name, False, f"{type(exc).__name__}: {exc}"))
            return
        checks.append((name, True, str(detail)))

    # 1. tools 只有 search_docs 时，get_doc 未被批准 → McpPermissionError
    def unapproved_tool() -> str:
        partial = McpAllowlist.from_mapping(
            {"servers": [{**entry, "tools": ["search_docs"]}]}, repo_root=REPO_ROOT
        )
        try:
            partial.require_tool(SERVER_ID, "get_doc")
        except McpPermissionError as exc:
            return f"McpPermissionError({exc.code})"
        raise AssertionError("require_tool did not deny get_doc")

    # 2. tools 为空 → 载入即拒绝
    def empty_tools() -> str:
        try:
            McpAllowlist.from_mapping(
                {"servers": [{**entry, "tools": []}]}, repo_root=REPO_ROOT
            )
        except McpAllowlistError as exc:
            return f"McpAllowlistError({exc.code})"
        raise AssertionError("empty tools were accepted")

    # 3. tools 含未知工具 → 载入即拒绝
    def unknown_tool_in_allowlist() -> str:
        try:
            McpAllowlist.from_mapping(
                {"servers": [{**entry, "tools": ["search_docs", "rm_rf"]}]}, repo_root=REPO_ROOT
            )
        except McpAllowlistError as exc:
            return f"McpAllowlistError({exc.code})"
        raise AssertionError("unknown tool was accepted")

    # 4. 任意 command（sh -c）→ 载入即拒绝
    def shell_command() -> str:
        try:
            McpAllowlist.from_mapping(
                {"servers": [{**entry, "command": ["sh", "-c", "id"]}]}, repo_root=REPO_ROOT
            )
        except McpAllowlistError as exc:
            return f"McpAllowlistError({exc.code})"
        raise AssertionError("shell command was accepted")

    # 5. 路径逃逸 → 载入即拒绝
    def path_escape() -> str:
        try:
            McpAllowlist.from_mapping(
                {"servers": [{**entry, "corpus": "../../etc"}]}, repo_root=REPO_ROOT
            )
        except McpAllowlistError as exc:
            return f"McpAllowlistError({exc.code})"
        raise AssertionError("path escape was accepted")

    # 6. 真实 server 对不存在 tool 返回 -32602
    def unknown_tool_rpc() -> str:
        spec = _require_project_kb(_load_allowlist())
        client = McpClient(spec, REPO_ROOT, bwrap=False)
        client.start()
        try:
            client.initialize()
            try:
                client._request("tools/call", {"name": "definitely_missing", "arguments": {}})
            except McpRemoteError as exc:
                if exc.rpc_code != -32602:
                    raise AssertionError(f"unexpected rpc code {exc.rpc_code}") from exc
                return "McpRemoteError(-32602)"
            raise AssertionError("unknown tool did not fail")
        finally:
            client.close()

    # 7. profile 资产：draft 不可执行；origin 不得冒充 real_run
    def profile_guard() -> str:
        spec = _require_project_kb(_load_allowlist())
        asset = build_profile_asset(spec, repo_root=REPO_ROOT)
        expected = {
            "server_id",
            "tools",
            "timeout_s",
            "max_output_bytes",
            "artifact_hash",
            "spec_hash",
        }
        if set(asset.content) != expected:
            raise AssertionError(f"unexpected profile content keys: {sorted(asset.content)}")
        if asset.origin != "synthetic":
            raise AssertionError(f"unexpected origin: {asset.origin}")
        if profile_sidecar(spec)["declared_origin"] != "manual":
            raise AssertionError("sidecar declared_origin must be manual")
        verify_artifact_hash(asset, REPO_ROOT)
        try:
            assert_profile_executable(asset)
        except McpProfileError as exc:
            return f"McpProfileError({exc.code}),origin=synthetic"
        raise AssertionError("draft profile was executable")

    # 7b. hash 绑定：spec 变、corpus 变、server.py 变都必须失败
    def integrity_bindings() -> str:
        spec = _require_project_kb(_load_allowlist())
        asset = build_profile_asset(spec, repo_root=REPO_ROOT)
        changed = replace(spec, timeout_s=spec.timeout_s + 1)
        try:
            verify_artifact_hash(asset, REPO_ROOT, spec=changed)
        except McpIntegrityError as exc:
            if exc.code != "spec_changed":
                raise AssertionError(f"unexpected code {exc.code}") from exc
        else:
            raise AssertionError("spec change was not detected")

        for target, payload in (
            ("mcp_servers/project_kb/corpus/zero-write.md", "\n篡改一行\n"),
            ("mcp_servers/project_kb/server.py", "\n# tampered\n"),
        ):
            with tempfile.TemporaryDirectory(prefix="mcp-tamper-") as tmp:
                root = Path(tmp) / "repo"
                shutil.copytree(REPO_ROOT / "mcp_servers", root / "mcp_servers")
                tampered_spec = McpAllowlist.from_mapping(
                    {"servers": [{**entry}]}, repo_root=root
                ).require_tool(SERVER_ID, "search_docs")
                tampered_asset = build_profile_asset(tampered_spec, repo_root=root)
                verify_artifact_hash(tampered_asset, root, spec=tampered_spec)
                with (root / target).open("a", encoding="utf-8") as handle:
                    handle.write(payload)
                try:
                    verify_artifact_hash(tampered_asset, root, spec=tampered_spec)
                except McpIntegrityError as exc:
                    if exc.code != "artifact_changed":
                        raise AssertionError(f"{target}: unexpected code {exc.code}") from exc
                else:
                    raise AssertionError(f"{target}: tampering was not detected")
        return "spec_changed,artifact_changed(corpus),artifact_changed(server.py)"

    # 7c. 握手状态机：未 ready 拒绝 list_tools；重复 initialize 拒绝
    def handshake_state_machine() -> str:
        spec = _require_project_kb(_load_allowlist())
        client = McpClient(spec, REPO_ROOT, bwrap=False)
        client.start()
        try:
            try:
                client.list_tools()
            except McpProtocolError as exc:
                if exc.code != "not_initialized":
                    raise AssertionError(f"unexpected code {exc.code}") from exc
            else:
                raise AssertionError("list_tools before initialize was allowed")
            client.initialize()
            try:
                client.initialize()
            except McpProtocolError as exc:
                if exc.code != "already_initialized":
                    raise AssertionError(f"unexpected code {exc.code}") from exc
            else:
                raise AssertionError("double initialize was allowed")
        finally:
            client.close()
        return "not_initialized,already_initialized"

    # 7d. 截断帧不得被当成合法消息
    def truncated_frame_rejected() -> str:
        import io

        try:
            read_message(io.BytesIO(b'{"jsonrpc":"2.0","id":1}'))
        except McpError as exc:
            if exc.code != "truncated_frame":
                raise AssertionError(f"unexpected code {exc.code}") from exc
            return "McpFrameError(truncated_frame)"
        raise AssertionError("partial frame was accepted")

    # 8. 真实 bwrap 会话：/workspace 只读输入，运行期哈希不变
    def workspace_read_only() -> str:
        spec = _require_project_kb(_load_allowlist())
        client = McpClient(spec, REPO_ROOT, bwrap=True)
        client.start()
        try:
            workspace = client.workspace
            assert workspace is not None
            before = workspace_digest(workspace)
            client.initialize()
            client.call_tool("search_docs", {"query": "零写入"})
            after = workspace_digest(workspace)
        finally:
            client.close()
        if before != after:
            raise AssertionError("workspace content changed during the session")
        return "digest_unchanged,workspace_bind=ro"

    check("allowlist.deny_unapproved_tool", unapproved_tool)
    check("allowlist.reject_empty_tools", empty_tools)
    check("allowlist.reject_unknown_tool", unknown_tool_in_allowlist)
    check("allowlist.reject_shell_command", shell_command)
    check("allowlist.reject_path_escape", path_escape)
    check("server.unknown_tool_-32602", unknown_tool_rpc)
    check("profile.draft_not_executable", profile_guard)
    check("profile.integrity_bindings", integrity_bindings)
    check("protocol.handshake_state_machine", handshake_state_machine)
    check("frames.truncated_rejected", truncated_frame_rejected)
    check("workspace.read_only_digest", workspace_read_only)

    failed = 0
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
        if not ok:
            failed += 1
    print(f"self_check={'ok' if failed == 0 else 'failed'} ({len(checks) - failed}/{len(checks)})")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="project_kb MCP demo（bwrap 真实子进程）")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="跑负向自检（allowlist 拒绝、-32602、profile 守卫），不启动长驻会话",
    )
    args = parser.parse_args(argv)
    try:
        return run_self_check() if args.self_check else run_demo()
    except McpError as exc:
        print(f"error={exc.code}", file=sys.stderr)
        if exc.detail:
            print(f"detail={exc.detail}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - demo must never pretend success
        print(f"error=unexpected_exception detail={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
