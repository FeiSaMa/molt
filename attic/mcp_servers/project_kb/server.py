#!/usr/bin/env python3
"""project_kb MCP server: read-only keyword retrieval over a small markdown corpus.

Legacy stdio transport: one JSON-RPC 2.0 message per line on stdin/stdout,
UTF-8, no Content-Length header. stdout carries protocol frames only; nothing
is logged there. Standard library only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "project-kb", "version": "1.0.0"}
CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
MAX_LIMIT = 5
DEFAULT_LIMIT = 3
SNIPPET_PADDING = 60
MAX_SNIPPET_CHARS = 200

TOOLS = (
    {
        "name": "search_docs",
        "description": "在 project_kb 语料中做大小写不敏感的关键词/子串检索",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_doc",
        "description": "按 doc_id 取回整篇文档",
        "inputSchema": {
            "type": "object",
            "properties": {"doc_id": {"type": "string", "minLength": 1}},
            "required": ["doc_id"],
            "additionalProperties": False,
        },
    },
)


class RpcError(Exception):
    """JSON-RPC level failure; ``code`` is the numeric protocol code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ToolFailure(Exception):
    """Tool-level failure surfaced as ``isError: true`` (not a protocol error)."""


def _parse_doc(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    meta: dict[str, str] = {}
    body: list[str] = []
    for line in lines:
        if not body and ":" in line and line.split(":", 1)[0].strip() in {"doc_id", "title"}:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
            continue
        if line.startswith("source:"):
            continue
        body.append(line)
    doc_id = meta.get("doc_id") or path.stem
    title = meta.get("title") or doc_id
    return {
        "doc_id": doc_id,
        "title": title,
        "text": "\n".join(body).strip(),
        "path": path,
    }


_DOC_CACHE: dict[str, dict[str, Any]] = {}


def load_corpus() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for path in sorted(CORPUS_DIR.glob("*.md")):
        key = str(path)
        stat = path.stat()
        cached = _DOC_CACHE.get(key)
        if cached is None or cached.get("mtime_ns") != stat.st_mtime_ns:
            document = _parse_doc(path)
            document["mtime_ns"] = stat.st_mtime_ns
            _DOC_CACHE[key] = document
            cached = document
        docs.append(cached)
    return docs


def _snippet(text: str, start: int, end: int) -> str:
    left = max(0, start - SNIPPET_PADDING)
    right = min(len(text), end + SNIPPET_PADDING)
    chunk = re.sub(r"\s+", " ", text[left:right]).strip()
    if len(chunk) > MAX_SNIPPET_CHARS:
        chunk = chunk[:MAX_SNIPPET_CHARS].rstrip() + "…"
    return chunk


def tool_search_docs(arguments: dict[str, Any]) -> dict[str, Any]:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise RpcError(-32602, "invalid params: query must be a non-empty string")
    limit = arguments.get("limit", DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise RpcError(-32602, f"invalid params: limit must be an integer in 1..{MAX_LIMIT}")
    pattern = re.compile(re.escape(query.strip()), re.IGNORECASE)
    matches: list[dict[str, Any]] = []
    for document in load_corpus():
        haystack = f"{document['title']}\n{document['text']}"
        found = list(pattern.finditer(haystack))
        if not found:
            continue
        matches.append(
            {
                "doc_id": document["doc_id"],
                "title": document["title"],
                "snippet": _snippet(haystack, found[0].start(), found[0].end()),
                "score": len(found),
            }
        )
    matches.sort(key=lambda item: (-item["score"], item["doc_id"]))
    return {"matches": matches[:limit]}


def tool_get_doc(arguments: dict[str, Any]) -> dict[str, Any]:
    doc_id = arguments.get("doc_id")
    if not isinstance(doc_id, str) or not doc_id.strip():
        raise RpcError(-32602, "invalid params: doc_id must be a non-empty string")
    for document in load_corpus():
        if document["doc_id"] == doc_id.strip():
            return {
                "doc_id": document["doc_id"],
                "title": document["title"],
                "text": document["text"],
            }
    raise ToolFailure(f"unknown doc_id: {doc_id}")


TOOL_HANDLERS = {"search_docs": tool_search_docs, "get_doc": tool_get_doc}


def _text_result(payload: Any) -> dict[str, Any]:
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}
        ]
    }


def _error_result(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def handle_tools_call(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str) or not name:
        raise RpcError(-32602, "invalid params: tool name required")
    if not isinstance(arguments, dict):
        raise RpcError(-32602, "invalid params: arguments must be an object")
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise RpcError(-32602, f"unknown tool: {name}")
    try:
        return _text_result(handler(arguments))
    except ToolFailure as failure:
        return _error_result(str(failure))


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    is_notification = "id" not in message
    params = message.get("params")
    if not isinstance(method, str) or not method:
        if is_notification:
            return None
        raise RpcError(-32600, "invalid request: method required")
    if method != "initialize" and params is None:
        params = {}
    if params is not None and not isinstance(params, dict):
        raise RpcError(-32602, "invalid params: must be an object")

    if method.startswith("notifications/"):
        return None

    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": dict(SERVER_INFO),
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [dict(tool) for tool in TOOLS]}
    elif method == "tools/call":
        result = handle_tools_call(params or {})
    else:
        raise RpcError(-32601, f"method not found: {method}")

    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def _error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve() -> None:
    stream = sys.stdin.buffer
    for raw in stream:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            _write(_error_response(None, -32700, "parse error"))
            continue
        if not isinstance(message, dict):
            _write(_error_response(None, -32600, "invalid request: not an object"))
            continue
        try:
            response = handle(message)
        except RpcError as error:
            if "id" not in message:
                continue
            response = _error_response(message.get("id"), error.code, error.message)
        except Exception as error:  # noqa: BLE001 - never leak a traceback to stdout
            if "id" not in message:
                continue
            response = _error_response(message.get("id"), -32603, f"internal error: {error}")
        if response is not None:
            _write(response)


if __name__ == "__main__":
    serve()
