"""Tests for ``molt.mcp`` (written only; run by the integration owner).

Isolation tests: the protocol tests drive the real server module through a
plain subprocess (``bwrap=False``) so the handshake semantics are covered
without needing bubblewrap. The sandbox argv itself is asserted structurally.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

import molt.mcp.client as client_module
from molt.mcp.allowlist import McpAllowlist, McpAllowlistError, McpServerSpec
from molt.mcp.client import McpClient
from molt.mcp.jsonrpc import (
    McpBudgetError,
    McpFrameError,
    McpIntegrityError,
    McpPermissionError,
    McpProtocolError,
    McpRemoteError,
    McpTimeout,
    McpToolError,
    encode_message,
    read_message,
)
from molt.mcp.profile import (
    McpProfileError,
    assert_profile_executable,
    build_profile_asset,
    profile_sidecar,
    verify_artifact_hash,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = "mcp_servers.project_kb.server"
DEFAULT_ALLOWLIST = REPO_ROOT / "molt" / "mcp" / "allowlist.default.json"


def wait_for_failure(client: McpClient, timeout: float = 5.0) -> BaseException:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        failure = client._pending_failure()
        if failure is not None:
            return failure
        time.sleep(0.02)
    raise AssertionError("client recorded no failure within the timeout")


def base_entry(**overrides):
    entry = {
        "id": "project_kb",
        "command": ["python3", "-m", MODULE],
        "module": MODULE,
        "cwd": ".",
        "env": {},
        "tools": ["search_docs", "get_doc"],
        "corpus": "mcp_servers/project_kb/corpus",
        "timeout_s": 10,
        "max_output_bytes": 65536,
    }
    entry.update(overrides)
    return entry


def load_entry(**overrides) -> McpAllowlist:
    return McpAllowlist.from_mapping({"servers": [base_entry(**overrides)]}, repo_root=REPO_ROOT)


def direct_spec(command: list[str], module: str = MODULE) -> McpServerSpec:
    return McpServerSpec(
        server_id="test_server",
        command=tuple(command),
        module=module,
        cwd=".",
        tools=("search_docs", "get_doc"),
        corpus="mcp_servers/project_kb/corpus",
        timeout_s=5.0,
        max_output_bytes=65536,
    )


def write_server(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_server.py"
    script.write_text(body, encoding="utf-8")
    return script


FAKE_HANDSHAKE = """
import json, sys

def reply(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        reply({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "serverInfo": {"name": "fake", "version": "0"}}})
    elif method == "tools/list":
        reply({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": []}})
"""


# -- framing --------------------------------------------------------------


def test_encode_message_is_canonical_single_line():
    payload = encode_message({"b": 1, "a": "零写入"})
    assert payload.endswith(b"\n")
    assert payload.count(b"\n") == 1
    assert payload == b'{"a":"\xe9\x9b\xb6\xe5\x86\x99\xe5\x85\xa5","b":1}\n'


def test_encode_message_rejects_embedded_newline():
    with pytest.raises(McpFrameError) as excinfo:
        encode_message('{"a": "line1\nline2"}')
    assert excinfo.value.code == "embedded_newline"


def test_encode_message_rejects_non_utf8_and_unserializable():
    with pytest.raises(McpFrameError) as excinfo:
        encode_message({"a": "\ud800"})
    assert excinfo.value.code == "non_utf8_message"
    with pytest.raises(McpFrameError) as excinfo:
        encode_message({"a": object()})
    assert excinfo.value.code == "unserializable_message"


def test_read_message_skips_blank_lines_and_stops_at_eof():
    stream = io.BytesIO(b'\n\n{"jsonrpc":"2.0","id":1}\n')
    assert read_message(stream) == {"jsonrpc": "2.0", "id": 1}
    assert read_message(stream) is None


def test_read_message_rejects_invalid_json_and_oversize():
    with pytest.raises(McpFrameError) as excinfo:
        read_message(io.BytesIO(b"{not json}\n"))
    assert excinfo.value.code == "invalid_json"
    with pytest.raises(McpFrameError) as excinfo:
        read_message(io.BytesIO(b"[" + b"0" * 64 + b"]\n"), max_bytes=16)
    assert excinfo.value.code == "frame_too_large"


# -- allowlist ------------------------------------------------------------


def test_default_allowlist_loads_and_approves_known_tools():
    allowlist = McpAllowlist.load(DEFAULT_ALLOWLIST, repo_root=REPO_ROOT)
    spec = allowlist.require_tool("project_kb", "search_docs")
    assert spec.command == ("python3", "-m", MODULE)
    assert spec.tools == ("search_docs", "get_doc")
    assert spec.corpus == "mcp_servers/project_kb/corpus"


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"command": ["sh", "-c", "id"]}, "command_not_python_module"),
        (
            {"command": ["python3", "-m", "evil.server"], "module": "evil.server"},
            "module_not_allowed",
        ),
        ({"cwd": "../../etc"}, "path_outside_repo"),
        ({"corpus": "/etc"}, "absolute_host_path"),
        ({"corpus": "https://example.com/x"}, "url_not_allowed"),
        ({"env": {"LD_PRELOAD": "/tmp/x.so"}}, "env_key_not_allowed"),
        ({"tools": []}, "tools_empty"),
        ({"tools": ["rm_rf"]}, "tool_not_known"),
        (
            {"module": MODULE, "command": ["python3", "-m", MODULE, "extra"]},
            "command_not_python_module",
        ),
    ],
)
def test_allowlist_rejects_forbidden_entries(overrides, code):
    with pytest.raises(McpAllowlistError) as excinfo:
        load_entry(**overrides)
    assert excinfo.value.code == code


def test_require_tool_denies_unapproved_tool():
    allowlist = load_entry(tools=["search_docs"])
    with pytest.raises(McpPermissionError) as excinfo:
        allowlist.require_tool("project_kb", "get_doc")
    assert excinfo.value.code == "tool_not_allowed"
    with pytest.raises(McpPermissionError):
        allowlist.require_tool("nope", "search_docs")


# -- protocol against the real server module ------------------------------


def test_real_server_handshake_and_tools():
    spec = load_entry().require_tool("project_kb", "search_docs")
    client = McpClient(spec, REPO_ROOT, bwrap=False)
    client.start()
    try:
        result = client.initialize()
        assert result["protocolVersion"] == "2025-06-18"
        assert client.server_info["name"] == "project-kb"
        names = [tool["name"] for tool in client.list_tools()]
        assert names == ["search_docs", "get_doc"]
        found = client.call_tool("search_docs", {"query": "零写入"})
        payload = json.loads(found["content"][0]["text"])
        assert payload["matches"][0]["doc_id"] == "zero-write"
        document = client.call_tool("get_doc", {"doc_id": "zero-write"})
        assert json.loads(document["content"][0]["text"])["doc_id"] == "zero-write"
    finally:
        client.close()


def test_server_reports_unknown_tool_as_minus_32602():
    spec = load_entry().require_tool("project_kb", "search_docs")
    client = McpClient(spec, REPO_ROOT, bwrap=False)
    client.start()
    try:
        client.initialize()
        with pytest.raises(McpRemoteError) as excinfo:
            client._request("tools/call", {"name": "missing_tool", "arguments": {}})
        assert excinfo.value.rpc_code == -32602
    finally:
        client.close()


def test_server_returns_is_error_for_unknown_doc():
    spec = load_entry().require_tool("project_kb", "get_doc")
    client = McpClient(spec, REPO_ROOT, bwrap=False)
    client.start()
    try:
        client.initialize()
        with pytest.raises(McpToolError):
            client.call_tool("get_doc", {"doc_id": "no-such-doc"})
    finally:
        client.close()


def test_client_denies_tool_outside_spec(tmp_path):
    spec = load_entry(tools=["search_docs"]).require_tool("project_kb", "search_docs")
    client = McpClient(spec, REPO_ROOT, bwrap=False)
    client.start()
    try:
        client.initialize()
        with pytest.raises(McpPermissionError):
            client.call_tool("get_doc", {"doc_id": "zero-write"})
    finally:
        client.close()


# -- failure modes --------------------------------------------------------


def test_protocol_version_mismatch_fails(tmp_path):
    script = write_server(tmp_path, FAKE_HANDSHAKE)
    spec = direct_spec([sys.executable, "-B", str(script)])
    client = McpClient(spec, REPO_ROOT, bwrap=False)
    client.start()
    try:
        with pytest.raises(McpProtocolError) as excinfo:
            client.initialize()
        assert excinfo.value.code == "unsupported_protocol_version"
    finally:
        client.close()


def test_timeout_kills_process_group(tmp_path):
    script = write_server(
        tmp_path,
        "import sys, time\nfor line in sys.stdin:\n    time.sleep(30)\n",
    )
    spec = McpServerSpec(
        server_id="slow",
        command=(sys.executable, "-B", str(script)),
        module=MODULE,
        cwd=".",
        tools=("search_docs",),
        timeout_s=0.5,
    )
    client = McpClient(spec, REPO_ROOT, bwrap=False)
    client.start()
    try:
        proc = client._proc
        with pytest.raises(McpTimeout):
            client.initialize()
        assert proc is not None
        proc.wait(timeout=5)
        assert proc.returncode is not None
    finally:
        client.close()


def test_output_budget_is_enforced(tmp_path):
    script = write_server(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    if 'id' not in msg:\n"
        "        continue\n"
        "    method = msg.get('method')\n"
        "    if method == 'initialize':\n"
        "        result = {'protocolVersion': '2025-06-18', 'capabilities': {},"
        " 'serverInfo': {'name': 'big', 'version': '0'}}\n"
        "    elif method == 'tools/call':\n"
        "        result = {'content': [{'type': 'text', 'text': 'x' * 100}]}\n"
        "    else:\n"
        "        result = {}\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result})"
        " + '\\n')\n"
        "    sys.stdout.flush()\n",
    )
    spec = McpServerSpec(
        server_id="big",
        command=(sys.executable, "-B", str(script)),
        module=MODULE,
        cwd=".",
        tools=("search_docs",),
        timeout_s=5.0,
        max_output_bytes=32,
    )
    client = McpClient(spec, REPO_ROOT, bwrap=False)
    client.start()
    try:
        client.initialize()
        with pytest.raises(McpBudgetError):
            client.call_tool("search_docs", {"query": "x"})
    finally:
        client.close()


def test_response_id_mismatch_is_protocol_error(tmp_path):
    script = write_server(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    if 'id' not in msg:\n"
        "        continue\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': 999, 'result': {}}) + '\\n')\n"
        "    sys.stdout.flush()\n",
    )
    spec = direct_spec([sys.executable, "-B", str(script)])
    client = McpClient(spec, REPO_ROOT, bwrap=False)
    client.start()
    try:
        with pytest.raises(McpProtocolError) as excinfo:
            client.initialize()
        assert excinfo.value.code == "response_id_mismatch"
    finally:
        client.close()


# -- sandbox argv (structural; no bwrap needed) ---------------------------


def test_bwrap_argv_binds_workspace_read_only(tmp_path):
    spec = load_entry().require_tool("project_kb", "search_docs")
    client = McpClient(spec, REPO_ROOT, bwrap=True)
    argv = client._bwrap_argv(tmp_path, ["python3", "-m", MODULE])
    assert argv[0].endswith("bwrap")
    assert "--clearenv" in argv
    assert "--unshare-all" in argv and "--die-with-parent" in argv
    assert argv[argv.index("--ro-bind", argv.index("--tmpfs")) + 1] == str(tmp_path)
    assert argv[argv.index(str(tmp_path)) + 1] == "/workspace"
    assert argv[argv.index("--chdir") + 1] == "/tmp"
    assert argv[argv.index("--tmpfs") + 1] == "/tmp"
    assert str(REPO_ROOT) not in argv


def test_limit_prefix_matches_workspace_values():
    spec = load_entry().require_tool("project_kb", "search_docs")
    client = McpClient(spec, REPO_ROOT, bwrap=True)
    prefix = client._limit_prefix()
    prlimit = shutil.which("prlimit")
    if prlimit is None:
        assert prefix == []
        assert client.limits == "unavailable"
        assert "prlimit_unavailable" in client.warnings
        return
    assert prefix == [
        prlimit,
        "--as=536870912",
        "--cpu=10",
        "--nofile=128",
        "--nproc=4096",
        "--core=0",
        "--",
    ]
    assert client.limits == "prlimit"


def test_profile_asset_is_draft_and_not_executable():
    spec = load_entry().require_tool("project_kb", "search_docs")
    asset = build_profile_asset(spec, created_by="operator")
    assert asset.kind == "mcp_profile"
    assert asset.approval_level == "strict"
    assert asset.permission_scope == "read_only:project_kb"
    assert asset.status == "draft"
    assert asset.origin == "synthetic"
    assert set(asset.content) == {"server_id", "tools", "timeout_s", "max_output_bytes"}
    with pytest.raises(McpProfileError) as excinfo:
        assert_profile_executable(asset)
    assert excinfo.value.code == "profile_not_active"


def test_profile_origin_manual_is_rejected_but_sidecar_records_it():
    spec = load_entry().require_tool("project_kb", "search_docs")
    with pytest.raises(McpProfileError) as excinfo:
        build_profile_asset(spec, origin="manual")
    assert excinfo.value.code == "origin_invalid"
    with pytest.raises(McpProfileError) as excinfo:
        build_profile_asset(spec, origin="real_run")
    assert excinfo.value.code == "real_run_requires_evidence"
    sidecar = profile_sidecar(spec)
    assert sidecar["declared_origin"] == "manual"
    assert sidecar["authorship"] == "operator"
    assert sidecar["visibility"] == "internal"


# -- artifact / spec hash binding ----------------------------------------


def make_repo_copy(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "mcp_servers", root / "mcp_servers")
    return root


def repo_spec(root: Path) -> McpServerSpec:
    return McpAllowlist.from_mapping(
        {"servers": [base_entry()]}, repo_root=root
    ).require_tool("project_kb", "search_docs")


def test_artifact_hash_matches_and_has_no_host_paths(tmp_path):
    root = make_repo_copy(tmp_path)
    spec = repo_spec(root)
    asset = build_profile_asset(spec, repo_root=root)
    assert asset.content["artifact_hash"].startswith("sha256:")
    assert asset.content["spec_hash"].startswith("sha256:")
    assert str(root) not in json.dumps(asset.content)
    verify_artifact_hash(asset, root, spec=spec)


def test_corpus_tampering_fails_verification(tmp_path):
    root = make_repo_copy(tmp_path)
    spec = repo_spec(root)
    asset = build_profile_asset(spec, repo_root=root)
    corpus_file = root / spec.corpus / "zero-write.md"
    corpus_file.write_text(corpus_file.read_text(encoding="utf-8") + "\n篡改\n", encoding="utf-8")
    with pytest.raises(McpIntegrityError) as excinfo:
        verify_artifact_hash(asset, root, spec=spec)
    assert excinfo.value.code == "artifact_changed"


def test_server_source_tampering_fails_verification(tmp_path):
    root = make_repo_copy(tmp_path)
    spec = repo_spec(root)
    asset = build_profile_asset(spec, repo_root=root)
    server_file = root.joinpath(*MODULE.split(".")).with_suffix(".py")
    server_file.write_text(
        server_file.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8"
    )
    with pytest.raises(McpIntegrityError) as excinfo:
        verify_artifact_hash(asset, root, spec=spec)
    assert excinfo.value.code == "artifact_changed"


def test_spec_timeout_change_fails_spec_hash():
    spec = load_entry().require_tool("project_kb", "search_docs")
    asset = build_profile_asset(spec, repo_root=REPO_ROOT)
    with pytest.raises(McpIntegrityError) as excinfo:
        verify_artifact_hash(asset, REPO_ROOT, spec=replace(spec, timeout_s=99.0))
    assert excinfo.value.code == "spec_changed"


def test_asset_without_hashes_is_not_verifiable():
    spec = load_entry().require_tool("project_kb", "search_docs")
    asset = build_profile_asset(spec)
    with pytest.raises(McpIntegrityError) as excinfo:
        verify_artifact_hash(asset, REPO_ROOT, spec=spec)
    assert excinfo.value.code == "artifact_hash_missing"


# -- truncated frames, handshake state, response shape -------------------


def test_read_message_rejects_truncated_frame():
    with pytest.raises(McpFrameError) as excinfo:
        read_message(io.BytesIO(b'{"jsonrpc":"2.0","id":1}'))
    assert excinfo.value.code == "truncated_frame"


def test_tools_require_ready_state():
    spec = load_entry().require_tool("project_kb", "search_docs")
    client = McpClient(spec, REPO_ROOT, bwrap=False)
    client.start()
    try:
        with pytest.raises(McpProtocolError) as excinfo:
            client.list_tools()
        assert excinfo.value.code == "not_initialized"
        with pytest.raises(McpProtocolError) as excinfo:
            client.call_tool("search_docs", {"query": "x"})
        assert excinfo.value.code == "not_initialized"
        client.initialize()
        assert client.state == "ready"
        with pytest.raises(McpProtocolError) as excinfo:
            client.initialize()
        assert excinfo.value.code == "already_initialized"
    finally:
        client.close()
    assert client.state == "closed"


def test_missing_id_frame_is_a_protocol_error(tmp_path):
    script = write_server(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'result': {}}) + '\\n')\n"
        "    sys.stdout.flush()\n",
    )
    client = McpClient(direct_spec([sys.executable, "-B", str(script)]), REPO_ROOT, bwrap=False)
    client.start()
    try:
        with pytest.raises(McpProtocolError) as excinfo:
            client.initialize()
        assert excinfo.value.code == "missing_id"
    finally:
        client.close()


def test_progress_notification_is_ignored(tmp_path):
    script = write_server(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    if 'id' not in msg:\n"
        "        continue\n"
        "    note = {'jsonrpc': '2.0', 'method': 'notifications/progress',"
        " 'params': {'progress': 1}}\n"
        "    sys.stdout.write(json.dumps(note) + '\\n')\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': {"
        "'protocolVersion': '2025-06-18', 'capabilities': {},"
        " 'serverInfo': {'name': 'fake', 'version': '0'}}}) + '\\n')\n"
        "    sys.stdout.flush()\n",
    )
    client = McpClient(direct_spec([sys.executable, "-B", str(script)]), REPO_ROOT, bwrap=False)
    client.start()
    try:
        assert client.initialize()["protocolVersion"] == "2025-06-18"
        assert client.state == "ready"
    finally:
        client.close()


@pytest.mark.parametrize(
    "reply, code",
    [
        ('{"id": 1, "result": {}}', "invalid_jsonrpc_version"),
        (
            '{"jsonrpc": "2.0", "id": 1, "result": {}, "error": {"code": 1, "message": "x"}}',
            "invalid_response_shape",
        ),
        ('{"jsonrpc": "2.0", "id": 1}', "invalid_response_shape"),
    ],
)
def test_response_shape_is_validated(tmp_path, reply, code):
    script = write_server(
        tmp_path,
        "import sys\n"
        f"for line in sys.stdin:\n"
        f"    sys.stdout.write({reply!r} + '\\n')\n"
        "    sys.stdout.flush()\n",
    )
    client = McpClient(direct_spec([sys.executable, "-B", str(script)]), REPO_ROOT, bwrap=False)
    client.start()
    try:
        with pytest.raises(McpProtocolError) as excinfo:
            client.initialize()
        assert excinfo.value.code == code
    finally:
        client.close()


def test_initialize_missing_server_info_fails(tmp_path):
    script = write_server(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    if 'id' not in msg:\n"
        "        continue\n"
        "    result = {'protocolVersion': '2025-06-18', 'capabilities': {}}\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result})"
        " + '\\n')\n"
        "    sys.stdout.flush()\n",
    )
    client = McpClient(direct_spec([sys.executable, "-B", str(script)]), REPO_ROOT, bwrap=False)
    client.start()
    try:
        with pytest.raises(McpProtocolError) as excinfo:
            client.initialize()
        assert excinfo.value.code == "missing_server_info"
    finally:
        client.close()


# -- stdout budgets -------------------------------------------------------


def test_oversized_frame_trips_frame_budget(tmp_path):
    script = write_server(
        tmp_path,
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    if 'id' not in msg:\n"
        "        continue\n"
        "    result = {'protocolVersion': '2025-06-18', 'capabilities': {},"
        " 'serverInfo': {'name': 'huge', 'version': '0'}, 'padding': 'p' * (2 * 1024 * 1024)}\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result})"
        " + '\\n')\n"
        "    sys.stdout.flush()\n",
    )
    client = McpClient(direct_spec([sys.executable, "-B", str(script)]), REPO_ROOT, bwrap=False)
    client.start()
    try:
        with pytest.raises(McpBudgetError) as excinfo:
            client.initialize()
        assert excinfo.value.code == "frame_budget_exceeded"
    finally:
        client.close()


def test_byte_budget_is_enforced(tmp_path, monkeypatch):
    # 走真实的 16 MiB 阈值要洪泛 ~18 MB，慢 runner 上读取耗时可能超过
    # wait_for_failure 的默认超时。把会话预算压到 1 MiB：单帧 400 KB
    # （仍远小于 MAX_FRAME_BYTES），3 帧即确定越界，与墙钟无关。
    monkeypatch.setattr(client_module, "MAX_SESSION_BYTES", 1024 * 1024)
    body = (
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    if 'id' not in msg:\n"
        "        continue\n"
        "    result = {'protocolVersion': '2025-06-18', 'capabilities': {},"
        " 'serverInfo': {'name': 'flood', 'version': '0'}}\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result})"
        " + '\\n')\n"
        "    sys.stdout.flush()\n"
        "    chunk = json.dumps({'jsonrpc': '2.0', 'method': 'notifications/message',"
        " 'params': {'data': 'x' * 400000}})\n"
        "    for _ in range(4):\n"
        "        sys.stdout.write(chunk + '\\n')\n"
        "        sys.stdout.flush()\n"
    )
    script = write_server(tmp_path, body)
    client = McpClient(direct_spec([sys.executable, "-B", str(script)]), REPO_ROOT, bwrap=False)
    client.start()
    try:
        client.initialize()
        # 超时只是慢机器的兜底，正确性由 1 MiB 阈值 + 1.6 MB 洪泛保证。
        failure = wait_for_failure(client, timeout=15.0)
        assert isinstance(failure, McpBudgetError)
        assert failure.code == "byte_budget_exceeded"
        with pytest.raises(McpBudgetError):
            client.list_tools()
    finally:
        client.close()


def test_queue_full_trips_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(client_module, "QUEUE_MAX_FRAMES", 2)
    monkeypatch.setattr(client_module, "QUEUE_PUT_TIMEOUT_S", 0.05)
    script = write_server(
        tmp_path,
        "import json, sys, time\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    if 'id' not in msg:\n"
        "        continue\n"
        "    result = {'protocolVersion': '2025-06-18', 'capabilities': {},"
        " 'serverInfo': {'name': 'flood', 'version': '0'}}\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result})"
        " + '\\n')\n"
        "    sys.stdout.flush()\n"
        "    for i in range(50):\n"
        "        note = {'jsonrpc': '2.0', 'method': 'notifications/progress',"
        " 'params': {'progress': i}}\n"
        "        sys.stdout.write(json.dumps(note) + '\\n')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(30)\n",
    )
    client = McpClient(direct_spec([sys.executable, "-B", str(script)]), REPO_ROOT, bwrap=False)
    client.start()
    try:
        client.initialize()
        failure = wait_for_failure(client)
        assert isinstance(failure, McpBudgetError)
        assert failure.code == "queue_full"
        with pytest.raises(McpBudgetError):
            client.list_tools()
    finally:
        client.close()
