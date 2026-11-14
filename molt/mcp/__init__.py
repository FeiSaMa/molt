"""MCP runtime (legacy stdio, protocol 2025-06-18) for molt.

Slice B: framing, allowlist, sandboxed client, profile asset builder.
The transport is newline-delimited JSON-RPC 2.0 over stdin/stdout; stdout
carries protocol frames only, stderr is log-only.
"""

from molt.mcp.allowlist import McpAllowlist, McpServerSpec
from molt.mcp.client import McpClient
from molt.mcp.jsonrpc import (
    McpBudgetError,
    McpError,
    McpFrameError,
    McpIntegrityError,
    McpPermissionError,
    McpProtocolError,
    McpRemoteError,
    McpTimeout,
    McpToolError,
    McpUnavailable,
    encode_message,
    read_frame,
    read_message,
)
from molt.mcp.profile import (
    McpProfileError,
    assert_profile_executable,
    build_profile_asset,
    compute_artifact_hash,
    compute_spec_hash,
    profile_sidecar,
    verify_artifact_hash,
)

PROTOCOL_VERSION = "2025-06-18"

__all__ = [
    "PROTOCOL_VERSION",
    "McpAllowlist",
    "McpBudgetError",
    "McpClient",
    "McpError",
    "McpFrameError",
    "McpIntegrityError",
    "McpPermissionError",
    "McpProfileError",
    "McpProtocolError",
    "McpRemoteError",
    "McpServerSpec",
    "McpTimeout",
    "McpToolError",
    "McpUnavailable",
    "assert_profile_executable",
    "build_profile_asset",
    "compute_artifact_hash",
    "compute_spec_hash",
    "encode_message",
    "profile_sidecar",
    "read_frame",
    "read_message",
    "verify_artifact_hash",
]
