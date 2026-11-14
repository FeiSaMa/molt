"""Skill/MCP runtime facade for chat turns and read-only API surfaces.

This module is the **only** place where the chat layer touches ``molt.skills``
and ``molt.mcp``. It keeps four concerns apart:

* *approval*: a skill or MCP server is usable only when the binding section
  names it **and** the package/profile still matches its hash;
* *selection*: which bound skill (if any) matches a task text;
* *exposure*: which MCP tools a model may see, derived strictly from the skill
  manifest's ``permissions.tools`` tokens (``mcp:<server>:<tool>``);
* *execution*: an allowlist-checked MCP call with a hard 64 KiB output budget.

MCP validation is delegated to slice B, not re-implemented here:
``verify_artifact_hash`` re-hashes the approved code/corpus and
``assert_profile_executable`` requires an active, strict, implemented profile
with no pending permission change. A binding entry that does not match the
registry record fails closed. If no registry record exists at all, the profile
is surfaced as ``manual_operator_config`` (``approved=False``), never as an
evaluated or human-approved asset.

Facts that must never leak into API projections: filesystem paths, the
allowlist command/module/env, transcript lines, and raw skill bodies.

A turn reads the binding exactly once. ``begin_turn`` returns an immutable
``TurnSnapshot``; tool exposure and execution use that snapshot only, so a
binding change mid-turn cannot alter the running turn.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from molt.agent.tools import ToolOutcome
from molt.mcp.allowlist import McpAllowlist, McpAllowlistError, McpServerSpec
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
)
from molt.mcp.profile import (
    McpProfileError,
    assert_profile_executable,
    build_profile_asset,
    verify_artifact_hash,
)
from molt.skills.binding import BindingError, BindingStore
from molt.skills.evaluate import run_context_check
from molt.skills.loader import (
    SkillPackage,
    SkillPackageError,
    load_package,
    render_system_block,
    validate_package,
)
from molt.skills.selector import SkillSelection, select
from molt.upper.registry import UpperRegistry, UpperRegistryError
from molt.upper.storage import StatePathError, ensure_outside_repo
from molt.upper.types import UpperAsset, utc_now

MAX_TOOL_OUTPUT_BYTES = 64 * 1024
MCP_TOOL_PREFIX = "mcp__"
MCP_TOKEN_PREFIX = "mcp:"
RUNTIME_MODES = ("off", "active")
DEFAULT_MCP_BWRAP = True
OPERATOR_APPROVER = "operator-config"
OPERATOR_NOTE = "manual operator configuration; not model-evaluated"


class AgentRuntimeError(RuntimeError):
    """Stable runtime failure code; never contains paths, commands or env."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class LoadedSkill:
    """One bound package that won deterministic selection for a task text."""

    package: SkillPackage
    selection: SkillSelection
    binding_revision: int
    system_block: str

    def event_payload(self, turn_id: str) -> dict[str, Any]:
        return {
            "turn_id": turn_id,
            "skill_id": self.package.skill_id,
            "version": self.package.version,
            "content_hash": self.package.content_hash,
            "reason": self.selection.reason,
            "binding_revision": self.binding_revision,
            "mode": "active",
        }


@dataclass(frozen=True)
class McpProfileSnapshot:
    """One approved MCP profile, frozen for a turn."""

    server_id: str
    asset_id: str
    version: str
    artifact_hash: str
    binding_revision: int
    approval_source: str
    approved: bool
    approved_by: str | None
    approval_note: str | None

    def to_public(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "asset_id": self.asset_id,
            "version": self.version,
            "artifact_hash": self.artifact_hash,
            "binding_revision": self.binding_revision,
            "approval_source": self.approval_source,
            "approved": self.approved,
            "approved_by": self.approved_by,
            "approval_note": self.approval_note,
        }


@dataclass(frozen=True)
class McpSurface:
    """Result of validating the whole allowlist: available or a stable reason."""

    available: bool
    reason: str | None
    profiles: Mapping[str, McpProfileSnapshot]
    specs: tuple[dict[str, Any], ...]
    servers: tuple[str, ...]


@dataclass(frozen=True)
class TurnSnapshot:
    """Immutable per-turn view; binding is never re-read inside the turn."""

    revision: int
    skill: LoadedSkill | None
    mcp: McpSurface
    allowed_tools: tuple[str, ...]
    specs: tuple[dict[str, Any], ...]

    @property
    def mcp_profiles(self) -> Mapping[str, McpProfileSnapshot]:
        return self.mcp.profiles


@dataclass(frozen=True)
class RuntimeSnapshot:
    """Public summary of the turn snapshot (no paths, no bodies)."""

    skill_id: str | None
    version: str | None
    content_hash: str | None
    binding_revision: int
    mcp_servers: tuple[str, ...]
    mcp_tools: tuple[str, ...]
    mode: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "content_hash": self.content_hash,
            "binding_revision": self.binding_revision,
            "mcp_servers": list(self.mcp_servers),
            "mcp_tools": list(self.mcp_tools),
            "mode": self.mode,
        }


@dataclass
class _TurnState:
    turn: TurnSnapshot | None = None


def _parse_namespaced(name: Any) -> tuple[str, str] | None:
    """``mcp__<server>__<tool>`` → ``(server, tool)``; anything else → None."""

    if not isinstance(name, str) or not name.startswith(MCP_TOOL_PREFIX):
        return None
    parts = name.split("__")
    if len(parts) != 3 or parts[0] != "mcp" or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def _function_spec(server_id: str, tool: dict[str, Any]) -> dict[str, Any]:
    """Convert one MCP tool description into an OpenAI function schema."""

    name = tool.get("name")
    description = tool.get("description")
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}, "additionalProperties": False}
    return {
        "type": "function",
        "function": {
            "name": f"{MCP_TOOL_PREFIX}{server_id}__{name}",
            "description": description if isinstance(description, str) else "",
            "parameters": schema,
        },
    }


class AgentRuntime:
    """Skill selection + MCP profile validation/exposure/execution."""

    def __init__(
        self,
        repo_root: Path | str,
        skills_root: Path | str,
        skill_state_dir: Path | str,
        mcp_allowlist_path: Path | str | None,
        adapter: Any,
        *,
        enabled: bool,
        mcp_registry_state: Path | str | None = None,
        mcp_bwrap: bool = DEFAULT_MCP_BWRAP,
        client_factory: Callable[[McpServerSpec], McpClient] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.skills_root = Path(skills_root).expanduser()
        self.skill_state_dir = Path(skill_state_dir).expanduser()
        self.allowlist_path = Path(mcp_allowlist_path) if mcp_allowlist_path is not None else None
        self.registry_state = (
            Path(mcp_registry_state).expanduser() if mcp_registry_state is not None else None
        )
        self.enabled = bool(enabled)
        self._adapter = adapter
        self._mcp_bwrap = mcp_bwrap
        self._client_factory = client_factory

        self._local = threading.local()
        self._lock = threading.RLock()
        self._modes: dict[str, str] = {}
        self._clients: dict[str, McpClient] = {}
        self._client_artifacts: dict[str, str] = {}
        self._mcp_tools: dict[str, list[dict[str, Any]]] = {}
        self._binding_store: BindingStore | None = None
        self._binding_error: str | None = None

        self._allowlist: McpAllowlist | None = None
        self._allowlist_error: str | None = None
        if self.allowlist_path is not None:
            try:
                self._allowlist = McpAllowlist.load(self.allowlist_path, repo_root=self.repo_root)
            except McpAllowlistError as exc:
                self._allowlist_error = exc.code or "allowlist_invalid"

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
            self._client_artifacts.clear()
            self._mcp_tools.clear()
        for client in clients:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - teardown must not raise
                pass

    # -- session mode ------------------------------------------------------
    def set_mode(self, session_id: str, mode: str) -> None:
        if mode not in RUNTIME_MODES:
            raise AgentRuntimeError("invalid_mode", mode)
        with self._lock:
            self._modes[session_id] = mode

    def mode_for(self, session_id: str) -> str:
        with self._lock:
            return self._modes.get(session_id, "off")

    def enabled_for(self, session_id: str) -> bool:
        return self.enabled and self.mode_for(session_id) == "active"

    # -- turn scope --------------------------------------------------------
    def begin_turn(self, task_text: str) -> TurnSnapshot:
        """Read binding once, validate profiles, and freeze the turn scope."""

        if not isinstance(task_text, str):
            raise AgentRuntimeError("bad_task_text")
        snapshot, error = self._binding_snapshot_safe()
        if snapshot is None:
            raise AgentRuntimeError(error or "binding_invalid")
        packages, revision = self._load_bound_packages(snapshot)
        skill: LoadedSkill | None = None
        if packages:
            selection = select(task_text, packages)
            if selection is not None:
                package = next(
                    (
                        candidate
                        for candidate in packages
                        if candidate.skill_id == selection.skill_id
                        and candidate.version == selection.version
                    ),
                    None,
                )
                if package is None:  # pragma: no cover - select() returns loaded packages
                    raise AgentRuntimeError("skill_unavailable", "selection not loaded")
                skill = LoadedSkill(
                    package=package,
                    selection=selection,
                    binding_revision=revision,
                    system_block=render_system_block(package),
                )
        surface = self._mcp_surface_from_snapshot(snapshot, skill)
        allowed = tuple(spec["function"]["name"] for spec in surface.specs)
        turn = TurnSnapshot(
            revision=revision,
            skill=skill,
            mcp=surface,
            allowed_tools=allowed,
            specs=surface.specs,
        )
        self._local.state = _TurnState(turn=turn)
        return turn

    def clear_turn(self) -> None:
        self._local.__dict__.pop("state", None)

    def _state(self) -> _TurnState:
        state = getattr(self._local, "state", None)
        if state is None:
            state = _TurnState()
            self._local.state = state
        return state

    def _active_turn(self) -> TurnSnapshot | None:
        return self._state().turn

    # -- binding -----------------------------------------------------------
    def _binding(self) -> BindingStore | None:
        if self._binding_store is not None:
            return self._binding_store
        if self._binding_error is not None:
            return None
        try:
            self._binding_store = BindingStore(self.skill_state_dir, repo_root=self.repo_root)
        except BindingError:
            self._binding_error = "binding_invalid"
            return None
        return self._binding_store

    def _binding_snapshot_safe(self) -> tuple[dict[str, Any] | None, str | None]:
        store = self._binding()
        if store is None:
            return None, self._binding_error or "binding_invalid"
        try:
            return store.load(), None
        except BindingError:
            return None, "binding_invalid"

    def binding_public(self) -> dict[str, Any]:
        snapshot, error = self._binding_snapshot_safe()
        if snapshot is None:
            raise AgentRuntimeError(error or "binding_invalid")
        return {
            "revision": snapshot["revision"],
            "skills": snapshot["skills"],
            "mcp": snapshot["mcp"],
        }

    # -- skill loading -----------------------------------------------------
    def _load_bound_packages(
        self, snapshot: dict[str, Any] | None = None
    ) -> tuple[list[SkillPackage], int]:
        if snapshot is None:
            snapshot, error = self._binding_snapshot_safe()
            if snapshot is None:
                raise AgentRuntimeError(error or "binding_invalid")
        entries: Mapping[str, dict[str, Any]] = snapshot["skills"]
        packages: list[SkillPackage] = []
        for skill_id in sorted(entries):
            entry = entries[skill_id]
            try:
                package = load_package(
                    self.skills_root, skill_id, version=entry.get("version")
                )
            except SkillPackageError as exc:
                raise AgentRuntimeError("skill_unavailable", f"{skill_id}: {exc}") from None
            try:
                validate_package(package)
            except SkillPackageError as exc:
                raise AgentRuntimeError(
                    "skill_integrity_mismatch", f"{skill_id}: {exc}"
                ) from None
            if package.content_hash != entry.get("package_hash"):
                raise AgentRuntimeError(
                    "skill_integrity_mismatch",
                    f"{skill_id}@{package.version}: binding and package hashes differ",
                )
            packages.append(package)
        return packages, snapshot["revision"]

    def load_skill(self, task_text: str) -> LoadedSkill | None:
        """Select one bound skill without touching MCP (compatibility helper)."""

        if not isinstance(task_text, str):
            raise AgentRuntimeError("bad_task_text")
        packages, revision = self._load_bound_packages()
        if not packages:
            return None
        selection = select(task_text, packages)
        if selection is None:
            return None
        package = next(
            (
                candidate
                for candidate in packages
                if candidate.skill_id == selection.skill_id
                and candidate.version == selection.version
            ),
            None,
        )
        if package is None:  # pragma: no cover
            raise AgentRuntimeError("skill_unavailable", "selection not loaded")
        return LoadedSkill(
            package=package,
            selection=selection,
            binding_revision=revision,
            system_block=render_system_block(package),
        )

    def preview_selection(self, task_text: str) -> dict[str, Any]:
        """Selection projection only; never starts MCP or calls a model."""

        packages, revision = self._load_bound_packages()
        selection = select(task_text, packages) if packages else None
        return {
            "binding_revision": revision,
            "selected": selection is not None,
            "selection": selection.to_dict() if selection is not None else None,
            "mode": "deterministic-exact-prefix",
        }

    def context_check(self, task_text: str) -> dict[str, Any]:
        """Capture the exact messages a skill block would enter; no model call."""

        packages, revision = self._load_bound_packages()
        if not packages:
            return {
                "selected": False,
                "task_text": task_text,
                "reason": "no_binding",
                "binding_revision": revision,
                "calls_model": False,
                "mode": "manual",
            }
        evidence = run_context_check(task_text, packages)
        evidence["binding_revision"] = revision
        evidence["calls_model"] = False
        evidence["mode"] = "manual"
        return evidence

    # -- safe projections --------------------------------------------------
    def catalog(self) -> dict[str, Any]:
        snapshot, error = self._binding_snapshot_safe()
        if snapshot is None:
            raise AgentRuntimeError(error or "binding_invalid")
        skills: list[dict[str, Any]] = []
        for skill_id in sorted(snapshot["skills"]):
            entry = snapshot["skills"][skill_id]
            item: dict[str, Any] = {
                "skill_id": skill_id,
                "version": entry.get("version"),
                "asset_id": entry.get("asset_id"),
                "content_hash": entry.get("package_hash"),
                "bound": True,
                "available": False,
            }
            try:
                package = load_package(self.skills_root, skill_id, version=entry.get("version"))
                validate_package(package)
                if package.content_hash != entry.get("package_hash"):
                    raise AgentRuntimeError("skill_integrity_mismatch")
            except (SkillPackageError, AgentRuntimeError) as exc:
                item["reason"] = (
                    exc.code if isinstance(exc, AgentRuntimeError) else "skill_unavailable"
                )
                skills.append(item)
                continue
            item.update(
                {
                    "available": True,
                    "family": package.manifest.family,
                    "title": package.manifest.title,
                    "description": package.manifest.description,
                    "triggers": package.manifest.triggers.to_dict(),
                    "permissions": package.manifest.permissions.to_dict(),
                    "source": {
                        "count": len(package.manifest.source_refs),
                        "available_content": sorted(
                            {ref.available_content for ref in package.manifest.source_refs}
                        ),
                        "revisions": sorted(
                            {ref.revision for ref in package.manifest.source_refs}
                        ),
                    },
                }
            )
            skills.append(item)
        return {"revision": snapshot["revision"], "skills": skills}

    # -- MCP profile validation (slice B is authoritative) -----------------
    def _registry_asset(
        self, server_id: str, asset_id: str, version: str
    ) -> UpperAsset | None:
        """Select exactly ``asset_id`` + ``version`` for ``server_id``.

        * no registry file / no record for this server → ``None`` (bootstrap);
        * records exist for the server but none match → ``mcp_asset_not_found``;
        * more than one exact match → ``mcp_registry_ambiguous``.
        """

        if self.registry_state is None:
            return None
        try:
            state = ensure_outside_repo(self.registry_state, self.repo_root)
        except StatePathError:
            raise AgentRuntimeError("mcp_registry_invalid", "registry inside repository") from None
        if not (state / "registry.json").is_file():
            return None
        try:
            registry = UpperRegistry(state, repo_root=self.repo_root)
            server_assets = [
                asset
                for asset in registry.list_assets(kind="mcp_profile")
                if isinstance(asset.content, dict)
                and asset.content.get("server_id") == server_id
            ]
            if not server_assets:
                return None
            matches = [
                asset
                for asset in server_assets
                if asset.asset_id == asset_id and asset.version == version
            ]
            if not matches:
                raise AgentRuntimeError(
                    "mcp_asset_not_found", f"{server_id}:{asset_id}@{version}"
                )
            if len(matches) > 1:
                raise AgentRuntimeError(
                    "mcp_registry_ambiguous", f"{server_id}:{asset_id}@{version}"
                )
            return matches[0]
        except AgentRuntimeError:
            raise
        except (OSError, ValueError, UpperRegistryError) as exc:
            raise AgentRuntimeError("mcp_registry_invalid", str(exc)) from None

    def _profile_from_snapshot(
        self, snapshot: dict[str, Any], spec: McpServerSpec
    ) -> McpProfileSnapshot:
        entry = snapshot["mcp"].get(spec.server_id)
        if not isinstance(entry, dict):
            raise AgentRuntimeError("mcp_binding_missing", spec.server_id)
        entry_server = entry.get("server_id")
        asset_id = entry.get("asset_id")
        version = entry.get("version")
        package_hash = entry.get("package_hash")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (entry_server, asset_id, version, package_hash)
        ):
            raise AgentRuntimeError("mcp_binding_incomplete", spec.server_id)
        if entry_server != spec.server_id:
            raise AgentRuntimeError("mcp_binding_mismatch", spec.server_id)

        asset = self._registry_asset(spec.server_id, asset_id, version)
        if asset is None:
            asset = build_profile_asset(
                spec, repo_root=self.repo_root, created_by=OPERATOR_APPROVER
            )
            # Operator configuration is a manual, unevaluated bootstrap. It is
            # executable only because the operator explicitly configured it; the
            # response keeps approved=False and an explicit note.
            asset.status = "active"
            asset.approved_by = OPERATOR_APPROVER
            asset.approved_at = utc_now()
            asset.status_reason = OPERATOR_NOTE
            approval_source = "manual_operator_config"
            approved = False
            approved_by = OPERATOR_APPROVER
            approval_note = OPERATOR_NOTE
        else:
            approval_source = "registry"
            approved = True
            approved_by = asset.approved_by
            approval_note = None
            if not approved_by:
                raise AgentRuntimeError("mcp_profile_invalid", "registry asset has no approved_by")

        try:
            verify_artifact_hash(asset, self.repo_root, spec=spec)
            assert_profile_executable(asset)
        except McpIntegrityError as exc:
            raise AgentRuntimeError("mcp_integrity_mismatch", exc.code) from None
        except McpProfileError as exc:
            raise AgentRuntimeError("mcp_profile_invalid", exc.code) from None

        artifact_hash = asset.content.get("artifact_hash")
        if not isinstance(artifact_hash, str):
            raise AgentRuntimeError("mcp_integrity_mismatch", "artifact_hash_missing")
        if asset.asset_id != asset_id:
            raise AgentRuntimeError("mcp_binding_mismatch", spec.server_id)
        if asset.version != version:
            raise AgentRuntimeError("mcp_binding_mismatch", spec.server_id)
        if artifact_hash != package_hash:
            raise AgentRuntimeError("mcp_binding_mismatch", spec.server_id)
        return McpProfileSnapshot(
            server_id=spec.server_id,
            asset_id=asset.asset_id,
            version=asset.version,
            artifact_hash=artifact_hash,
            binding_revision=snapshot["revision"],
            approval_source=approval_source,
            approved=approved,
            approved_by=approved_by,
            approval_note=approval_note,
        )

    def _profiles_from_snapshot(
        self, snapshot: dict[str, Any]
    ) -> dict[str, McpProfileSnapshot]:
        allowlist = self._allowlist
        if allowlist is None:
            raise AgentRuntimeError(self._allowlist_error or "not_configured")
        return {
            server_id: self._profile_from_snapshot(snapshot, allowlist.servers[server_id])
            for server_id in allowlist.server_ids()
        }

    # -- MCP surface -------------------------------------------------------
    def _client_alive(self, client: McpClient) -> bool:
        if getattr(client, "state", None) == "closed":
            return False
        pending = getattr(client, "_pending_failure", None)
        if callable(pending):
            try:
                if pending() is not None:
                    return False
            except Exception:  # noqa: BLE001 - a failing health probe means dead
                return False
        proc = getattr(client, "_proc", None)
        if proc is not None:
            try:
                if proc.poll() is not None:
                    return False
            except Exception:  # noqa: BLE001
                return False
        return True

    def _drop_client(self, server_id: str) -> None:
        with self._lock:
            client = self._clients.pop(server_id, None)
            self._client_artifacts.pop(server_id, None)
            self._mcp_tools.pop(server_id, None)
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - teardown must not mask the cause
                pass

    def _client_for(self, server_id: str, profile: McpProfileSnapshot) -> McpClient:
        with self._lock:
            existing = self._clients.get(server_id)
            if existing is not None:
                same_artifact = self._client_artifacts.get(server_id) == profile.artifact_hash
                if same_artifact and self._client_alive(existing):
                    return existing
                self._clients.pop(server_id, None)
                self._client_artifacts.pop(server_id, None)
                self._mcp_tools.pop(server_id, None)
                try:
                    existing.close()
                except Exception:  # noqa: BLE001
                    pass
            allowlist = self._allowlist
            if allowlist is None:
                raise AgentRuntimeError(self._allowlist_error or "mcp_not_configured")
            try:
                spec = allowlist.get(server_id)
            except McpPermissionError as exc:
                raise AgentRuntimeError("server_not_allowed", exc.detail) from None
            if self._client_factory is not None:
                client = self._client_factory(spec)
            else:
                client = McpClient(spec, self.repo_root, bwrap=self._mcp_bwrap)
            try:
                client.start()
                client.initialize()
                tools = client.list_tools()
            except McpError as exc:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass
                raise AgentRuntimeError(exc.code or "mcp_error", exc.detail) from None
            except Exception as exc:  # noqa: BLE001 - never leak a host traceback
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass
                raise AgentRuntimeError("mcp_error", type(exc).__name__) from None
            self._clients[server_id] = client
            self._client_artifacts[server_id] = profile.artifact_hash
            self._mcp_tools[server_id] = tools
            return client

    def _list_tools(
        self, server_id: str, profile: McpProfileSnapshot
    ) -> list[dict[str, Any]]:
        self._client_for(server_id, profile)
        with self._lock:
            return list(self._mcp_tools.get(server_id, []))

    def _permitted_pairs(self, skill: LoadedSkill | None) -> set[tuple[str, str]]:
        if skill is None:
            return set()
        pairs: set[tuple[str, str]] = set()
        for token in skill.package.manifest.permissions.tools:
            if not token.startswith(MCP_TOKEN_PREFIX):
                continue
            parts = token.split(":")
            if len(parts) == 3 and parts[1] and parts[2]:
                pairs.add((parts[1], parts[2]))
        return pairs

    def _mcp_surface_from_snapshot(
        self, snapshot: dict[str, Any], skill: LoadedSkill | None
    ) -> McpSurface:
        allowlist = self._allowlist
        if allowlist is None:
            return McpSurface(
                available=False,
                reason=self._allowlist_error or "not_configured",
                profiles={},
                specs=(),
                servers=(),
            )
        try:
            profiles = self._profiles_from_snapshot(snapshot)
        except AgentRuntimeError as exc:
            return McpSurface(
                available=False,
                reason=exc.code,
                profiles={},
                specs=(),
                servers=(),
            )

        pairs = self._permitted_pairs(skill)
        specs: list[dict[str, Any]] = []
        try:
            for server_id in sorted(profiles):
                spec = allowlist.servers[server_id]
                tools = self._list_tools(server_id, profiles[server_id])
                by_name = {
                    tool.get("name"): tool
                    for tool in tools
                    if isinstance(tool, dict) and isinstance(tool.get("name"), str)
                }
                for tool_name in spec.tools:
                    if (server_id, tool_name) not in pairs:
                        continue
                    tool = by_name.get(tool_name)
                    if tool is None:
                        raise AgentRuntimeError(
                            "mcp_tool_missing", f"{server_id}/{tool_name}"
                        )
                    specs.append(_function_spec(server_id, tool))
        except AgentRuntimeError as exc:
            return McpSurface(
                available=False,
                reason=exc.code,
                profiles=profiles,
                specs=(),
                servers=tuple(sorted(profiles)),
            )
        return McpSurface(
            available=True,
            reason="ok",
            profiles=profiles,
            specs=tuple(specs),
            servers=tuple(sorted(profiles)),
        )

    def mcp_surface(self, skill: LoadedSkill | None = None) -> McpSurface:
        snapshot, error = self._binding_snapshot_safe()
        if snapshot is None:
            return McpSurface(
                available=False,
                reason=error or "binding_invalid",
                profiles={},
                specs=(),
                servers=(),
            )
        return self._mcp_surface_from_snapshot(snapshot, skill)

    def tool_specs(self, snapshot: TurnSnapshot | None = None) -> list[dict[str, Any]]:
        active = snapshot if snapshot is not None else self._active_turn()
        if active is None:
            return []
        if not active.mcp.available:
            raise AgentRuntimeError(active.mcp.reason or "mcp_unavailable")
        return [dict(spec) for spec in active.specs]

    def mcp_tool_catalog(self) -> dict[str, Any]:
        """Approved server/tool names + schemas + approval provenance."""

        allowlist = self._allowlist
        if allowlist is None:
            raise AgentRuntimeError(self._allowlist_error or "mcp_not_configured")
        snapshot, error = self._binding_snapshot_safe()
        if snapshot is None:
            raise AgentRuntimeError(error or "binding_invalid")
        servers: list[dict[str, Any]] = []
        for server_id in allowlist.server_ids():
            spec = allowlist.servers[server_id]
            profile: McpProfileSnapshot | None = None
            live: dict[str, dict[str, Any]] = {}
            available = False
            reason: str | None = None
            try:
                profile = self._profile_from_snapshot(snapshot, spec)
                tools = self._list_tools(server_id, profile)
                for tool in tools:
                    if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                        live[tool["name"]] = tool
                available = True
            except AgentRuntimeError as exc:
                reason = exc.code
            entries: list[dict[str, Any]] = []
            for tool_name in spec.tools:
                tool = live.get(tool_name, {})
                schema = tool.get("inputSchema")
                entries.append(
                    {
                        "name": tool_name,
                        "namespaced_name": f"{MCP_TOOL_PREFIX}{server_id}__{tool_name}",
                        "description": tool.get("description")
                        if isinstance(tool.get("description"), str)
                        else None,
                        "input_schema": schema if isinstance(schema, dict) else None,
                    }
                )
            servers.append(
                {
                    "server_id": server_id,
                    "available": available,
                    "reason": reason,
                    "binding_revision": (
                        profile.binding_revision if profile else snapshot["revision"]
                    ),
                    "approval_source": profile.approval_source if profile else None,
                    "approved": profile.approved if profile else False,
                    "approved_by": profile.approved_by if profile else None,
                    "approval_note": profile.approval_note if profile else None,
                    "tools": entries,
                }
            )
        return {"servers": servers}

    def describe_tool(self, name: str) -> dict[str, Any]:
        parsed = _parse_namespaced(name)
        if parsed is None:
            return {}
        server_id, tool = parsed
        return {
            "source": "mcp",
            "server": server_id,
            "tool": tool,
            "name": f"{MCP_TOOL_PREFIX}{server_id}__{tool}",
        }

    def _parse_arguments(self, arguments: Any) -> dict[str, Any] | None:
        if isinstance(arguments, dict):
            return arguments
        if not isinstance(arguments, str):
            return None
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _is_dead_error(exc: BaseException) -> bool:
        if isinstance(exc, (McpRemoteError, McpToolError, McpPermissionError)):
            return False
        return isinstance(
            exc, (McpTimeout, McpBudgetError, McpProtocolError, McpFrameError, McpUnavailable)
        )

    def execute_tool(
        self, snapshot: TurnSnapshot, namespaced_name: str, arguments: Any
    ) -> ToolOutcome:
        """Model-driven MCP call; uses the frozen turn snapshot only."""

        parsed = _parse_namespaced(namespaced_name)
        if parsed is None:
            return ToolOutcome(status="denied", reason="unknown_tool")
        if namespaced_name not in snapshot.allowed_tools:
            return ToolOutcome(status="denied", reason="tool_not_allowed")
        server_id, tool = parsed
        profile = snapshot.mcp.profiles.get(server_id)
        if profile is None:
            return ToolOutcome(status="denied", reason="mcp_not_configured")
        payload = self._parse_arguments(arguments)
        if payload is None:
            return ToolOutcome(status="error", reason="internal")
        try:
            client = self._client_for(server_id, profile)
            result = client.call_tool(tool, payload)
        except McpPermissionError:
            return ToolOutcome(status="denied", reason="tool_not_allowed")
        except McpTimeout:
            self._drop_client(server_id)
            return ToolOutcome(status="error", reason="mcp_timeout")
        except McpUnavailable:
            self._drop_client(server_id)
            return ToolOutcome(status="error", reason="mcp_unavailable")
        except McpBudgetError:
            self._drop_client(server_id)
            return ToolOutcome(status="error", reason="output_limit_exceeded")
        except McpToolError:
            return ToolOutcome(status="error", reason="tool_error")
        except McpIntegrityError:
            self._drop_client(server_id)
            return ToolOutcome(status="error", reason="mcp_integrity_mismatch")
        except McpError as exc:
            if self._is_dead_error(exc):
                self._drop_client(server_id)
            return ToolOutcome(status="error", reason=exc.code or "mcp_error")
        except AgentRuntimeError as exc:
            return ToolOutcome(status="error", reason=exc.code)
        except Exception:  # noqa: BLE001 - tool errors never break the turn
            self._drop_client(server_id)
            return ToolOutcome(status="error", reason="internal")

        size = len(json.dumps(result, ensure_ascii=False, default=str).encode("utf-8"))
        if size > MAX_TOOL_OUTPUT_BYTES:
            raise AgentRuntimeError(
                "tool_budget_exceeded", f"{size} > {MAX_TOOL_OUTPUT_BYTES}"
            )
        return ToolOutcome(status="ok", result=result)

    def call_mcp_tool(
        self, server_id: str, tool: str, arguments: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Manual MCP call: allowlist + profile gate; no model, no skill."""

        allowlist = self._allowlist
        if allowlist is None:
            raise AgentRuntimeError(self._allowlist_error or "mcp_not_configured")
        snapshot, error = self._binding_snapshot_safe()
        if snapshot is None:
            raise AgentRuntimeError(error or "binding_invalid")
        try:
            spec = allowlist.require_tool(server_id, tool)
        except McpPermissionError as exc:
            raise AgentRuntimeError(
                "server_not_allowed" if exc.code == "server_not_allowed" else "tool_not_allowed",
                exc.detail,
            ) from None
        profile = self._profile_from_snapshot(snapshot, spec)
        payload = dict(arguments or {})
        try:
            client = self._client_for(server_id, profile)
            result = client.call_tool(tool, payload)
        except McpTimeout as exc:
            self._drop_client(server_id)
            raise AgentRuntimeError("mcp_timeout", exc.detail) from None
        except McpUnavailable as exc:
            self._drop_client(server_id)
            raise AgentRuntimeError("mcp_unavailable", exc.detail) from None
        except McpBudgetError as exc:
            self._drop_client(server_id)
            raise AgentRuntimeError("output_limit_exceeded", exc.detail) from None
        except McpToolError as exc:
            raise AgentRuntimeError("tool_error", exc.detail) from None
        except McpError as exc:
            if self._is_dead_error(exc):
                self._drop_client(server_id)
            raise AgentRuntimeError(exc.code or "mcp_error", exc.detail) from None
        size = len(json.dumps(result, ensure_ascii=False, default=str).encode("utf-8"))
        if size > MAX_TOOL_OUTPUT_BYTES:
            self._drop_client(server_id)
            raise AgentRuntimeError(
                "tool_budget_exceeded", f"{size} > {MAX_TOOL_OUTPUT_BYTES}"
            )
        return result

    # -- snapshot ----------------------------------------------------------
    def snapshot(self) -> RuntimeSnapshot:
        turn = self._active_turn()
        if turn is None:
            snapshot, _ = self._binding_snapshot_safe()
            return RuntimeSnapshot(
                skill_id=None,
                version=None,
                content_hash=None,
                binding_revision=snapshot["revision"] if snapshot is not None else 0,
                mcp_servers=(),
                mcp_tools=(),
                mode="active" if self.enabled else "off",
            )
        return RuntimeSnapshot(
            skill_id=turn.skill.package.skill_id if turn.skill else None,
            version=turn.skill.package.version if turn.skill else None,
            content_hash=turn.skill.package.content_hash if turn.skill else None,
            binding_revision=turn.revision,
            mcp_servers=turn.mcp.servers,
            mcp_tools=turn.allowed_tools,
            mode="active" if self.enabled else "off",
        )

    # -- health ------------------------------------------------------------
    def _skills_health(
        self, snapshot: dict[str, Any] | None, error: str | None
    ) -> tuple[bool, str]:
        if snapshot is None:
            return False, error or "binding_invalid"
        if not snapshot["skills"]:
            return False, "no_binding"
        try:
            self._load_bound_packages(snapshot)
        except AgentRuntimeError as exc:
            return False, exc.code
        return True, "ok"

    def _mcp_health(self) -> tuple[bool, str, str | None, bool]:
        if self.allowlist_path is None:
            return False, "not_configured", None, False
        surface = self.mcp_surface(None)
        if not surface.profiles:
            return surface.available, surface.reason or "ok", None, False
        manual = any(
            profile.approval_source == "manual_operator_config"
            for profile in surface.profiles.values()
        )
        approval_source = "manual_operator_config" if manual else "registry"
        approved = all(profile.approved for profile in surface.profiles.values())
        return surface.available, surface.reason or "ok", approval_source, approved

    def health(self) -> dict[str, Any]:
        snapshot, binding_error = self._binding_snapshot_safe()
        skills_available, skills_reason = self._skills_health(snapshot, binding_error)
        mcp_available, mcp_reason, approvals, approved = self._mcp_health()
        binding: dict[str, Any] = {
            "revision": snapshot["revision"] if snapshot is not None else 0,
            "skills": snapshot["skills"] if snapshot is not None else {},
            "mcp": snapshot["mcp"] if snapshot is not None else {},
        }
        if binding_error is not None:
            binding["error"] = binding_error
        return {
            "enabled": self.enabled,
            "skills": {"available": skills_available, "reason": skills_reason},
            "mcp": {
                "available": mcp_available,
                "reason": mcp_reason,
                "approval_source": approvals,
                "approved": approved,
            },
            "provider": {
                "kind": "real" if getattr(self._adapter, "real_model", False) else "mock"
            },
            "binding": binding,
        }
