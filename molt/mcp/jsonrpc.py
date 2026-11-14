"""Newline-delimited JSON-RPC 2.0 framing for the legacy stdio MCP transport.

One message per line, UTF-8, no ``Content-Length`` header. ``stdout`` of a
server carries protocol frames only; anything else belongs on ``stderr``.

Error codes used across this package are string codes carried in the
``message`` of :class:`McpError` (for example ``"frame_too_large"``) so that
callers never have to parse human prose.
"""

from __future__ import annotations

import json
from typing import Any, BinaryIO

PROTOCOL_VERSION = "2025-06-18"
DEFAULT_MAX_BYTES = 1024 * 1024


class McpError(Exception):
    """Base class for every MCP runtime error (string ``code`` as message)."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.code = code
        self.detail = detail


class McpFrameError(McpError):
    """The byte stream is not a valid single-line JSON-RPC frame."""


class McpProtocolError(McpError):
    """The peer violated the handshake or request/response pairing rules."""


class McpRemoteError(McpProtocolError):
    """The peer answered with a JSON-RPC ``error`` object."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__("jsonrpc_error", f"{code}: {message}")
        self.rpc_code = code
        self.rpc_message = message
        self.rpc_data = data


class McpPermissionError(McpError):
    """The allowlist does not approve the server/tool pair."""


class McpTimeout(McpError):
    """A request exceeded its per-request timeout; the process group is killed."""


class McpBudgetError(McpError):
    """A tool result exceeded ``max_output_bytes``."""


class McpUnavailable(McpError):
    """Required isolation (bubblewrap) is missing; the client refuses to run."""


class McpToolError(McpError):
    """``tools/call`` returned ``isError: true``."""

    def __init__(self, tool: str, result: dict[str, Any]) -> None:
        super().__init__("tool_error", tool)
        self.tool = tool
        self.result = result


class McpIntegrityError(McpError):
    """An ``mcp_profile`` asset no longer matches the code/corpus it describes."""


def encode_message(obj: object) -> bytes:
    """Serialise one message as canonical JSON plus a single ``b"\\n"``.

    A ``str`` argument is treated as an already-serialised JSON body, which is
    the only way an embedded raw newline can appear; such input is rejected.
    """

    if isinstance(obj, str):
        text = obj
    else:
        try:
            text = json.dumps(
                obj,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise McpFrameError("unserializable_message", str(exc)) from exc
    if "\n" in text or "\r" in text:
        raise McpFrameError("embedded_newline")
    try:
        payload = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise McpFrameError("non_utf8_message", str(exc)) from exc
    return payload + b"\n"


def read_frame(
    stream: BinaryIO, max_bytes: int = DEFAULT_MAX_BYTES
) -> tuple[dict[str, Any], int] | None:
    """Read one frame and return ``(message, raw_bytes)``, or ``None`` at clean EOF.

    Every frame must be terminated by ``\\n``. A partial line at EOF is a
    ``truncated_frame`` error: half a frame is never a message. Blank lines are
    skipped, oversized frames raise ``frame_too_large``.
    """

    while True:
        line = stream.readline(max_bytes + 2)
        if not line:
            return None
        if len(line) > max_bytes:
            raise McpFrameError("frame_too_large", f">{max_bytes} bytes")
        if not line.endswith(b"\n"):
            raise McpFrameError("truncated_frame", f"{len(line)} bytes at EOF")
        if not line.strip():
            continue
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise McpFrameError("non_utf8_frame", str(exc)) from exc
        try:
            message = json.loads(text)
        except ValueError as exc:
            raise McpFrameError("invalid_json", str(exc)) from exc
        if not isinstance(message, dict):
            raise McpFrameError("not_an_object", type(message).__name__)
        return message, len(line)


def read_message(stream: BinaryIO, max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any] | None:
    """Read one JSON object from ``stream`` (see :func:`read_frame`)."""

    frame = read_frame(stream, max_bytes)
    return None if frame is None else frame[0]
