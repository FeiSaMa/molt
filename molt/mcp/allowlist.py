"""Declarative allowlist for MCP servers.

Only what the operator wrote in the JSON file may ever run: a fixed
``python3 -m <module>`` invocation, a corpus/cwd inside the repository, and a
small tool set. Anything else (URLs, absolute host paths, shells) is rejected
at load time, and ``require_tool`` is the only way to obtain a spec.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from molt.mcp.jsonrpc import McpError, McpPermissionError

ALLOWED_ENV_KEYS = frozenset({"PATH", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "HOME"})
ALLOWED_MODULE_PREFIX = "mcp_servers."
ALLOWED_COMMAND_HEAD = ("python3", "-m")
MAX_TIMEOUT_S = 120.0

# Known tools per server module. A module absent from this table grants nothing.
KNOWN_TOOLS: dict[str, tuple[str, ...]] = {
    "mcp_servers.project_kb.server": ("search_docs", "get_doc"),
}

_MODULE_RE = re.compile(r"^mcp_servers(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")
_SERVER_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_SHELL_TOKENS = frozenset({"sh", "bash", "zsh", "fish", "cmd", "powershell", "-c", "-lc"})


class McpAllowlistError(McpError):
    """The allowlist file is malformed or contains a forbidden entry."""


@dataclass(frozen=True)
class McpServerSpec:
    """One approved server: exactly what may be spawned and called."""

    server_id: str
    command: tuple[str, ...]
    module: str
    cwd: str = "."
    env: Mapping[str, str] = field(default_factory=dict)
    tools: tuple[str, ...] = ()
    corpus: str = ""
    timeout_s: float = 10.0
    max_output_bytes: int = 65536  # retained for provenance; runtime no longer enforces it

    def allows(self, tool: str) -> bool:
        return tool in self.tools


@dataclass(frozen=True)
class McpAllowlist:
    """Loaded allowlist; the only source of :class:`McpServerSpec` objects."""

    servers: Mapping[str, McpServerSpec]
    repo_root: Path | None = None
    source_path: Path | None = None

    @classmethod
    def load(cls, path: str | Path, *, repo_root: str | Path | None = None) -> McpAllowlist:
        path = Path(path)
        root = Path(repo_root) if repo_root is not None else _guess_repo_root(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise McpAllowlistError("allowlist_missing", str(path)) from exc
        except ValueError as exc:
            raise McpAllowlistError("allowlist_invalid_json", str(exc)) from exc
        return cls.from_mapping(raw, repo_root=root, source_path=path)

    @classmethod
    def from_mapping(
        cls,
        raw: Any,
        *,
        repo_root: str | Path,
        source_path: Path | None = None,
    ) -> McpAllowlist:
        if not isinstance(raw, dict):
            raise McpAllowlistError("allowlist_not_an_object")
        entries = raw.get("servers")
        if not isinstance(entries, list) or not entries:
            raise McpAllowlistError("allowlist_servers_empty")
        root = Path(repo_root).resolve()
        specs: dict[str, McpServerSpec] = {}
        for entry in entries:
            spec = _parse_entry(entry, root)
            if spec.server_id in specs:
                raise McpAllowlistError("duplicate_server_id", spec.server_id)
            specs[spec.server_id] = spec
        return cls(servers=specs, repo_root=root, source_path=source_path)

    def get(self, server_id: str) -> McpServerSpec:
        try:
            return self.servers[server_id]
        except KeyError as exc:
            raise McpPermissionError("server_not_allowed", server_id) from exc

    def require_tool(self, server_id: str, tool: str) -> McpServerSpec:
        """Return the spec if ``(server_id, tool)`` is approved, else raise."""

        spec = self.get(server_id)
        if not spec.allows(tool):
            raise McpPermissionError("tool_not_allowed", f"{server_id}/{tool}")
        return spec

    def server_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.servers))


def default_allowlist_path(repo_root: str | Path) -> Path:
    return Path(repo_root).resolve() / "molt" / "mcp" / "allowlist.default.json"


def _guess_repo_root(path: Path) -> Path:
    resolved = path.resolve()
    for candidate in resolved.parents:
        if (candidate / "molt").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    return resolved.parent


def _parse_entry(entry: Any, root: Path) -> McpServerSpec:
    if not isinstance(entry, dict):
        raise McpAllowlistError("server_entry_not_an_object")
    unknown = set(entry) - {
        "id",
        "command",
        "module",
        "cwd",
        "env",
        "tools",
        "corpus",
        "timeout_s",
        "max_output_bytes",  # legacy field accepted but no longer enforced
    }
    if unknown:
        raise McpAllowlistError("server_entry_unknown_fields", ",".join(sorted(unknown)))

    server_id = _require_str(entry, "id")
    if not _SERVER_ID_RE.match(server_id):
        raise McpAllowlistError("server_id_invalid", server_id)

    module = _require_str(entry, "module")
    if not _MODULE_RE.match(module) or not module.startswith(ALLOWED_MODULE_PREFIX):
        raise McpAllowlistError("module_not_allowed", module)

    command_raw = entry.get("command")
    if not isinstance(command_raw, list) or not all(isinstance(item, str) for item in command_raw):
        raise McpAllowlistError("command_not_a_string_list", server_id)
    command = tuple(command_raw)
    if command[:2] != ALLOWED_COMMAND_HEAD or len(command) != 3:
        raise McpAllowlistError("command_not_python_module", server_id)
    if command[2] != module:
        raise McpAllowlistError("command_module_mismatch", server_id)
    _reject_forbidden_tokens(command, "command")

    cwd = _optional_str(entry, "cwd", ".")
    corpus = _optional_str(entry, "corpus", "")
    _reject_host_paths(cwd, "cwd")
    _reject_host_paths(corpus, "corpus")
    corpus_path = _inside_repo(root, corpus, "corpus") if corpus else None
    _inside_repo(root, cwd, "cwd")

    env_raw = entry.get("env", {})
    if not isinstance(env_raw, dict):
        raise McpAllowlistError("env_not_an_object", server_id)
    env: dict[str, str] = {}
    for key, value in env_raw.items():
        if key not in ALLOWED_ENV_KEYS:
            raise McpAllowlistError("env_key_not_allowed", str(key))
        if not isinstance(value, str):
            raise McpAllowlistError("env_value_not_a_string", str(key))
        _reject_forbidden_tokens((value,), f"env.{key}")
        env[key] = value

    tools_raw = entry.get("tools")
    if not isinstance(tools_raw, list) or not tools_raw:
        raise McpAllowlistError("tools_empty", server_id)
    if not all(isinstance(tool, str) and tool.strip() for tool in tools_raw):
        raise McpAllowlistError("tools_not_a_string_list", server_id)
    known = KNOWN_TOOLS.get(module, ())
    tools = tuple(tools_raw)
    for tool in tools:
        if tool not in known:
            raise McpAllowlistError("tool_not_known", f"{module}/{tool}")
    if len(set(tools)) != len(tools):
        raise McpAllowlistError("tools_duplicated", server_id)

    timeout_s = _number(entry, "timeout_s", 10.0)
    if not 0 < timeout_s <= MAX_TIMEOUT_S:
        raise McpAllowlistError("timeout_out_of_range", server_id)
    budget = _number(entry, "max_output_bytes", 65536)
    return McpServerSpec(
        server_id=server_id,
        command=command,
        module=module,
        cwd=cwd,
        env=env,
        tools=tools,
        corpus=str(corpus_path.relative_to(root)) if corpus_path else "",
        timeout_s=float(timeout_s),
        max_output_bytes=int(budget),
    )


def _require_str(entry: dict[str, Any], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise McpAllowlistError("field_required", key)
    return value


def _optional_str(entry: dict[str, Any], key: str, default: str) -> str:
    value = entry.get(key, default)
    if not isinstance(value, str):
        raise McpAllowlistError("field_not_a_string", key)
    return value


def _number(entry: dict[str, Any], key: str, default: float) -> float:
    value = entry.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise McpAllowlistError("field_not_a_number", key)
    return float(value)


def _inside_repo(root: Path, relative: str, label: str) -> Path:
    candidate = (root / relative).resolve() if relative else root
    if candidate != root and root not in candidate.parents:
        raise McpAllowlistError("path_outside_repo", f"{label}={relative}")
    return candidate


def _reject_host_paths(value: str, label: str) -> None:
    if not value:
        return
    if value.startswith("/") or value.startswith("~") or re.match(r"^[A-Za-z]:[\\/]", value):
        raise McpAllowlistError("absolute_host_path", f"{label}={value}")
    _reject_forbidden_tokens((value,), label)


def _reject_forbidden_tokens(values: tuple[str, ...], label: str) -> None:
    for value in values:
        lowered = value.strip().lower()
        if "://" in lowered or lowered.startswith(("http:", "https:", "file:")):
            raise McpAllowlistError("url_not_allowed", label)
        if lowered in _SHELL_TOKENS:
            raise McpAllowlistError("shell_not_allowed", label)
        if lowered.startswith("/") or lowered.startswith("~"):
            raise McpAllowlistError("absolute_host_path", f"{label}={value}")
