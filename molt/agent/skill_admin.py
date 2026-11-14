"""HTTP-facing skill administration: propose → evaluate → approve → activate → rollback.

This is a thin orchestration layer over the evolution/lifecycle core:

* candidates are written under ``<state>/skill-candidates/<uuid>``;
* registry assets live in ``<state>/skill-registry`` (separate from the MCP
  profile registry in ``--upper-state``);
* evaluation evidence is the core runner's ``<state>/skills/evaluations/*.json``;
* a small ``<state>/skill-admin/evaluations.json`` index keeps the API's
  ``evidence_id → asset_id`` mapping and the binding revision at evaluation time.

The core modules are not modified. All public responses are safe projections:
no absolute paths, no package bodies, no environment values. Manual evaluation
uses ``ScriptedAdapter`` and is always labelled ``manual`` / ``calls_model=false``;
a real adapter is the only way to get ``live``.
"""

from __future__ import annotations

import inspect
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from molt.skills import lifecycle as lifecycle_module
from molt.skills.binding import BindingError, BindingStore
from molt.skills.eval_runner import ScriptedAdapter, SkillEvaluator, two_phase_evaluate
from molt.skills.eval_tasks import EvalTask, EvalTaskError, load_tasks
from molt.skills.evolution import (
    ProviderUnavailable,
    SkillEvolutionError,
    propose_candidate,
)
from molt.skills.lifecycle import (
    activate_asset,
    approve_asset,
    mark_evaluated_from_evidence,
    register_asset,
    rollback_asset,
)
from molt.skills.loader import SkillPackage, SkillPackageError, load_package, validate_package
from molt.upper.registry import UpperRegistry, UpperRegistryError
from molt.upper.storage import StatePathError, atomic_write_json, ensure_outside_repo
from molt.upper.types import UpperAsset, utc_now

EVIDENCE_SUBDIR = ("skills", "evaluations")
ADMIN_SUBDIR = ("skill-admin",)
INDEX_FILENAME = "evaluations.json"
REGISTRY_DIRNAME = "skill-registry"
CANDIDATE_DIRNAME = "skill-candidates"
DRIVER = "molt.agent.skill_admin"
_EVIDENCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


class SkillAdminError(RuntimeError):
    """Stable domain error with an HTTP status; messages never carry paths."""

    def __init__(
        self,
        code: str,
        status: int = 409,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.status = status
        self.message = message or code
        self.details = details or {}


class SkillAdmin:
    """Operator-facing skill lifecycle service."""

    def __init__(
        self,
        repo_root: Path | str,
        skills_root: Path | str,
        state_dir: Path | str,
        registry_state: Path | str | None,
        adapter: Any,
        binding_store: BindingStore | None,
        *,
        tasks_root: Path | str | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.skills_root = Path(skills_root).expanduser()
        try:
            self.state_dir = ensure_outside_repo(Path(state_dir).expanduser(), self.repo_root)
            registry_root = (
                Path(registry_state).expanduser()
                if registry_state is not None
                else self.state_dir / REGISTRY_DIRNAME
            )
            self.registry_state = ensure_outside_repo(registry_root, self.repo_root)
        except StatePathError as exc:
            raise SkillAdminError(
                "skill_state_invalid", 503, "技能 state 必须在仓库外", {"reason": "skill_state_invalid"}
            ) from exc
        self.registry = UpperRegistry(self.registry_state, repo_root=self.repo_root)
        self.sidecar_dir = self.state_dir / "skills" / "sidecars"
        self.candidate_root = self.state_dir / CANDIDATE_DIRNAME
        self.tasks_root = (
            Path(tasks_root).expanduser()
            if tasks_root is not None
            else self.repo_root / "skills_eval"
        )
        self.adapter = adapter
        self.binding_store = binding_store

    # -- helpers -----------------------------------------------------------
    def _binding(self) -> BindingStore:
        if self.binding_store is None:
            raise SkillAdminError(
                "skill_binding_invalid", 503, "技能绑定不可用", {"reason": "skill_binding_invalid"}
            )
        return self.binding_store

    def _asset(self, asset_id: Any) -> UpperAsset:
        if not isinstance(asset_id, str) or not asset_id.strip():
            raise SkillAdminError("bad_request", 400, "asset_id 必须是非空字符串")
        try:
            return self.registry.get(asset_id)
        except UpperRegistryError:
            raise SkillAdminError(
                "skill_not_found", 404, "技能资产不存在", {"reason": "skill_not_found"}
            ) from None

    def _skill_asset(self, asset_id: Any) -> UpperAsset:
        asset = self._asset(asset_id)
        if asset.kind != "skill":
            raise SkillAdminError(
                "skill_not_found", 404, "资产不是技能", {"reason": "skill_not_found"}
            )
        return asset

    def _package_from_asset(self, asset: UpperAsset) -> SkillPackage:
        recorded = asset.content.get("package_dir") if isinstance(asset.content, dict) else None
        if not isinstance(recorded, str) or not recorded:
            raise SkillAdminError(
                "skill_package_invalid", 409, "技能资产缺少包位置", {"reason": "skill_package_invalid"}
            )
        package_dir = Path(recorded)
        try:
            package = load_package(package_dir.parent, asset.name, asset.version)
            validate_package(package)
        except SkillPackageError:
            raise SkillAdminError(
                "skill_package_invalid", 409, "技能包不可用", {"reason": "skill_package_invalid"}
            ) from None
        expected = str(asset.content.get("package_hash", ""))
        if package.content_hash != expected:
            raise SkillAdminError(
                "skill_package_invalid", 409, "技能包 hash 不匹配", {"reason": "skill_package_invalid"}
            )
        return package

    def _baseline(self, skill_id: str, baseline_asset_id: Any) -> SkillPackage:
        if baseline_asset_id is not None:
            asset = self._skill_asset(baseline_asset_id)
            if asset.name != skill_id:
                raise SkillAdminError(
                    "skill_baseline_invalid", 400, "baseline 与候选 skill_id 不一致"
                )
            return self._package_from_asset(asset)
        try:
            return load_package(self.skills_root, skill_id)
        except SkillPackageError:
            raise SkillAdminError(
                "skill_not_found", 404, "基础技能包不存在", {"reason": "skill_not_found"}
            ) from None

    def _task_sets(self) -> tuple[list[EvalTask], list[EvalTask]]:
        try:
            tasks = load_tasks(self.tasks_root)
        except EvalTaskError:
            raise SkillAdminError(
                "skill_eval_invalid", 503, "评测任务不可用", {"reason": "skill_eval_invalid"}
            ) from None
        transfer = [task for task in tasks if task.phase == "transfer"]
        regression = [task for task in tasks if task.phase == "regression"]
        if not transfer or not regression:
            raise SkillAdminError(
                "skill_eval_invalid", 503, "评测任务不完整", {"reason": "skill_eval_invalid"}
            )
        return transfer, regression

    @staticmethod
    def _manual_map(
        manual_outputs: Any, tasks: Iterable[EvalTask]
    ) -> dict[str, str]:
        if not isinstance(manual_outputs, dict):
            raise SkillAdminError(
                "provider_unavailable", 409, "缺少 manual_outputs", {"reason": "provider_unavailable"}
            )
        normalized: dict[str, str] = {}
        for key, value in manual_outputs.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise SkillAdminError("bad_request", 400, "manual_outputs 必须是字符串映射")
            normalized[key] = value
        task_list = list(tasks)
        known = {task.task_id for task in task_list}
        allowed = set(known)
        for task_id in known:
            allowed.add(f"baseline:{task_id}")
            allowed.add(f"candidate:{task_id}")
        unknown = sorted(set(normalized) - allowed)
        if unknown:
            raise SkillAdminError(
                "bad_request", 400, "manual_outputs 含未知 task_id", {"unknown_task_ids": unknown}
            )
        missing = []
        for task_id in sorted(known):
            if task_id in normalized:
                continue
            if f"baseline:{task_id}" in normalized and f"candidate:{task_id}" in normalized:
                continue
            missing.append(task_id)
        if missing:
            raise SkillAdminError(
                "provider_unavailable",
                409,
                "缺少 provider 或 manual_outputs",
                {"reason": "provider_unavailable", "missing_task_ids": missing},
            )
        return normalized

    @staticmethod
    def _phase_projection(result: dict[str, Any]) -> dict[str, Any]:
        metrics = result.get("metrics", {})
        candidate = metrics.get("candidate", {})
        baseline = metrics.get("baseline", {})
        return {
            "verdict": result.get("verdict"),
            "reason": result.get("reason"),
            "metrics": {
                "phase": metrics.get("phase"),
                "task_count": metrics.get("task_count"),
                "candidate": {
                    "attempts": candidate.get("attempts"),
                    "passed": candidate.get("passed"),
                    "pass_rate": candidate.get("pass_rate"),
                },
                "baseline": {
                    "attempts": baseline.get("attempts"),
                    "passed": baseline.get("passed"),
                    "pass_rate": baseline.get("pass_rate"),
                },
                "transfer_delta": metrics.get("transfer_delta"),
                "tasks_improved": metrics.get("tasks_improved", []),
                "tasks_regressed": metrics.get("tasks_regressed", []),
                "regression_count": metrics.get("regression_count"),
            },
        }

    def _revision(self) -> int:
        if self.binding_store is None:
            return 0
        try:
            return int(self.binding_store.load()["revision"])
        except (BindingError, KeyError, TypeError, ValueError):
            raise SkillAdminError(
                "skill_binding_invalid", 503, "技能绑定不可读", {"reason": "skill_binding_invalid"}
            ) from None

    def _reconcile(self) -> list[dict[str, Any]]:
        """Best-effort, idempotent commit-record reconciliation."""

        if self.binding_store is None:
            return []
        reconcile = getattr(lifecycle_module, "reconcile_commits", None)
        if not callable(reconcile):
            return []
        try:
            result = reconcile(self.state_dir, self.binding_store, self.registry)
        except (BindingError, UpperRegistryError, OSError, ValueError):
            return []
        adopted: list[dict[str, Any]] = []
        if isinstance(result, list):
            for item in result:
                if not isinstance(item, dict) or item.get("action") != "adopt":
                    continue
                intent_id = item.get("intent_id")
                if isinstance(intent_id, str) and intent_id:
                    adopted.append(
                        {"intent_id": intent_id, "revision": item.get("revision")}
                    )
        return adopted

    def status(self) -> dict[str, Any]:
        reconciled = self._reconcile()
        return {"reconciled": reconciled, "revision": self._revision()}

    # -- propose -----------------------------------------------------------
    def propose(
        self,
        skill_id: str,
        feedback: str,
        *,
        base_version: str | None = None,
        manual_body: str | None = None,
        version: str | None = None,
    ) -> dict[str, Any]:
        try:
            base = load_package(self.skills_root, skill_id, version=base_version)
        except SkillPackageError:
            raise SkillAdminError(
                "skill_not_found", 404, "基础技能包不存在", {"reason": "skill_not_found"}
            ) from None
        candidate_root = self.candidate_root / uuid.uuid4().hex
        try:
            candidate = propose_candidate(
                base,
                feedback,
                adapter=self.adapter,
                manual_body=manual_body,
                version=version,
                candidate_root=candidate_root,
            )
        except ProviderUnavailable:
            raise SkillAdminError(
                "provider_unavailable", 409, "未配置 provider 且未提供 manual_body",
                {"reason": "provider_unavailable"},
            ) from None
        except SkillEvolutionError:
            raise SkillAdminError(
                "skill_proposal_invalid", 400, "候选包不合法", {"reason": "skill_proposal_invalid"}
            ) from None
        try:
            package = load_package(
                candidate.package_dir.parent, candidate.skill_id, candidate.version
            )
            validate_package(package)
            asset = register_asset(
                self.registry,
                package,
                created_by="operator",
                origin="synthetic",
                sidecar_dir=self.sidecar_dir,
                declared_origin=candidate.origin,
            )
        except (SkillPackageError, UpperRegistryError, SkillEvolutionError):
            raise SkillAdminError(
                "skill_registry_conflict", 409, "候选包无法登记", {"reason": "skill_registry_conflict"}
            ) from None
        hash_short = asset.content.get("package_hash", "")[-8:]
        return {
            "asset_id": asset.asset_id,
            "candidate_id": f"candidate-{asset.name}-{asset.version}-{hash_short}",
            "package_hash": str(asset.content.get("package_hash", "")),
            "origin": candidate.origin,
            "calls_model": bool(candidate.generation_meta.get("calls_model", False)),
        }

    # -- evaluate ----------------------------------------------------------
    def evaluate(
        self,
        asset_id: str,
        *,
        baseline_asset_id: str | None = None,
        manual_outputs: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        reconciled = self._reconcile()
        asset = self._skill_asset(asset_id)
        if asset.status != "draft":
            raise SkillAdminError(
                "skill_state_conflict", 409, "只有 draft 资产可以评测",
                {"reason": "skill_state_conflict", "status": asset.status},
            )
        candidate = self._package_from_asset(asset)
        baseline = self._baseline(asset.name, baseline_asset_id)
        transfer, regression = self._task_sets()
        adapter = self.adapter
        if adapter is not None and getattr(adapter, "real_model", False):
            evaluator = SkillEvaluator(None, adapter, state_dir=self.state_dir, driver=DRIVER)
        else:
            normalized = self._manual_map(manual_outputs, [*transfer, *regression])
            evaluator = SkillEvaluator(
                None,
                ScriptedAdapter(responses=normalized),
                state_dir=self.state_dir,
                driver=DRIVER,
            )
        try:
            result = two_phase_evaluate(
                baseline,
                candidate,
                transfer,
                regression,
                self.state_dir,
                evaluator=evaluator,
                asset_id=asset_id,
            )
            evidence_path = Path(result["evidence_path"])
            mark_evaluated_from_evidence(self.registry, asset_id, evidence_path)
        except ProviderUnavailable:
            raise SkillAdminError(
                "provider_unavailable", 409, "未配置 provider 或缺少 manual_outputs",
                {"reason": "provider_unavailable"},
            ) from None
        except (SkillEvolutionError, UpperRegistryError, EvalTaskError):
            raise SkillAdminError(
                "skill_eval_invalid", 409, "评测失败", {"reason": "skill_eval_invalid"}
            ) from None
        evidence_id = evidence_path.stem
        revision = self._revision()
        item = {
            "evidence_id": evidence_id,
            "asset_id": asset_id,
            "skill_id": asset.name,
            "version": asset.version,
            "package_hash": str(asset.content.get("package_hash", "")),
            "status": asset.status,
            "verdict": result["final"],
            "mode": evaluator.mode,
            "calls_model": evaluator.calls_model,
            "revision": revision,
            "created_at": utc_now(),
        }
        self._write_index(item)
        payload = {
            "evidence_id": evidence_id,
            "transfer": self._phase_projection(result["transfer"]),
            "regression": self._phase_projection(result["regression"]),
            "final": result["final"],
            "mode": evaluator.mode,
            "calls_model": evaluator.calls_model,
        }
        if reconciled:
            payload["reconciled"] = reconciled
        return payload

    # -- approve / activate / rollback ------------------------------------
    def _evidence_path(self, evidence_id: Any) -> Path:
        if not isinstance(evidence_id, str) or not _EVIDENCE_ID_RE.match(evidence_id):
            raise SkillAdminError(
                "skill_evidence_mismatch", 409, "evidence_id 不合法",
                {"reason": "skill_evidence_mismatch"},
            )
        return self.state_dir.joinpath(*EVIDENCE_SUBDIR, f"{evidence_id}.json")

    def _load_evidence(self, evidence_id: Any) -> tuple[dict[str, Any], Path]:
        path = self._evidence_path(evidence_id)
        if not path.is_file():
            raise SkillAdminError(
                "skill_evidence_mismatch", 409, "evidence 不存在",
                {"reason": "skill_evidence_mismatch"},
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise SkillAdminError(
                "skill_evidence_mismatch", 409, "evidence 不可读",
                {"reason": "skill_evidence_mismatch"},
            ) from None
        if not isinstance(payload, dict):
            raise SkillAdminError(
                "skill_evidence_mismatch", 409, "evidence 不是对象",
                {"reason": "skill_evidence_mismatch"},
            )
        return payload, path

    @staticmethod
    def _evidence_hash(evidence: dict[str, Any]) -> str:
        value = evidence.get("candidate_package_hash")
        if isinstance(value, str) and value:
            return value
        candidate = evidence.get("candidate")
        if isinstance(candidate, dict):
            nested = candidate.get("content_hash")
            if isinstance(nested, str):
                return nested
        return ""

    def _evidence_for_asset(
        self, evidence_id: Any, asset: UpperAsset
    ) -> tuple[dict[str, Any], Path]:
        evidence, path = self._load_evidence(evidence_id)
        if evidence.get("final") != "keep":
            raise SkillAdminError(
                "skill_not_keep", 409, "只有 final=keep 的 evidence 可以审批/激活",
                {"reason": "skill_not_keep", "final": evidence.get("final")},
            )
        if evidence.get("asset_id") != asset.asset_id:
            raise SkillAdminError(
                "skill_evidence_mismatch", 409, "evidence 与资产不匹配",
                {"reason": "skill_evidence_mismatch"},
            )
        expected = str(asset.content.get("package_hash", ""))
        if not expected or self._evidence_hash(evidence) != expected:
            raise SkillAdminError(
                "skill_evidence_mismatch", 409, "evidence 候选 hash 与资产不匹配",
                {"reason": "skill_evidence_mismatch"},
            )
        return evidence, path

    @staticmethod
    def _call_supported(func: Any, *args: Any, **kwargs: Any) -> Any:
        signature = inspect.signature(func)
        if any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        ):
            return func(*args, **kwargs)
        filtered = {
            name: value for name, value in kwargs.items() if name in signature.parameters
        }
        return func(*args, **filtered)

    def approve(
        self,
        asset_id: str,
        approved_by: str,
        *,
        evidence_id: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        asset = self._skill_asset(asset_id)
        if asset.status != "evaluated":
            raise SkillAdminError(
                "skill_state_conflict", 409, "只有 evaluated 资产可以审批",
                {"reason": "skill_state_conflict", "status": asset.status},
            )
        evidence, evidence_path = self._evidence_for_asset(evidence_id, asset)
        kwargs: dict[str, Any] = {"approved_by": approved_by, "note": note or ""}
        kwargs["evidence_id"] = evidence_id
        kwargs["evidence"] = evidence
        kwargs["evidence_path"] = str(evidence_path)
        try:
            approved = self._call_supported(
                approve_asset, self.registry, asset_id, **kwargs
            )
        except (SkillEvolutionError, UpperRegistryError) as exc:
            text = str(exc)
            if "keep" in text:
                raise SkillAdminError(
                    "skill_not_keep", 409, "evidence 未通过 keep 门禁",
                    {"reason": "skill_not_keep"},
                ) from None
            raise SkillAdminError(
                "skill_state_conflict", 409, "资产无法审批",
                {"reason": "skill_state_conflict", "status": asset.status},
            ) from None
        return {"asset_id": asset_id, "status": approved.status}

    def _assert_identity_unique(self, asset: UpperAsset) -> None:
        target_hash = str(asset.content.get("package_hash", ""))
        for other in self.registry.list_assets(kind="skill"):
            if other.asset_id == asset.asset_id:
                continue
            if other.name != asset.name or other.version != asset.version:
                continue
            other_hash = str(other.content.get("package_hash", ""))
            if other_hash != target_hash:
                raise SkillAdminError(
                    "skill_identity_conflict",
                    409,
                    "同 skill_id@version 存在不同 package_hash",
                    {
                        "reason": "skill_identity_conflict",
                        "conflicting_asset_id": other.asset_id,
                    },
                )

    def activate(
        self,
        asset_id: str,
        expected_revision: int,
        *,
        actor: str,
        evidence_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        reconciled = self._reconcile()
        asset = self._skill_asset(asset_id)
        self._assert_identity_unique(asset)
        _evidence, _path = self._evidence_for_asset(evidence_id, asset)
        store = self._binding()
        try:
            result = self._call_supported(
                activate_asset,
                self.registry,
                store,
                asset_id,
                expected_revision=expected_revision,
                sidecar_dir=self.sidecar_dir,
                actor=actor,
                reason=reason or "",
                evidence_id=evidence_id,
                evidence_path=str(_path),
                fault_hook=None,
            )
        except (SkillEvolutionError, BindingError, UpperRegistryError):
            raise SkillAdminError(
                "skill_state_conflict", 409, "资产无法激活",
                {"reason": "skill_state_conflict"},
            ) from None
        entry = result["entry"]
        revision = int(result["revision"])
        payload = {
            "asset_id": entry["asset_id"],
            "skill_id": entry["skill_id"],
            "version": entry["version"],
            "package_hash": entry["package_hash"],
            "revision": revision,
            "commit_record_id": str(revision),
        }
        if reconciled:
            payload["reconciled"] = reconciled
        return payload

    def _previous_entry(
        self, skill_id: str, current_asset_id: str, current_revision: int
    ) -> dict[str, Any] | None:
        store = self._binding()
        candidates: list[tuple[int, dict[str, Any]]] = []
        for path in store.history_files():
            try:
                revision = int(path.name.split("-", 1)[0])
                snapshot = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if revision >= current_revision:
                continue
            entry = snapshot.get("skills", {}).get(skill_id) if isinstance(snapshot, dict) else None
            if isinstance(entry, dict) and entry.get("asset_id") != current_asset_id:
                candidates.append((revision, entry))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1] if candidates else None

    def rollback(
        self,
        *,
        reason: str,
        rolled_back_by: str,
        expected_revision: int,
        evidence_id: str | None = None,
    ) -> dict[str, Any]:
        reconciled = self._reconcile()
        store = self._binding()
        snapshot = store.load()
        skills = snapshot.get("skills", {})
        if not isinstance(skills, dict) or len(skills) != 1:
            raise SkillAdminError(
                "skill_rollback_ambiguous", 409, "当前绑定不是单一 skill",
                {"reason": "skill_rollback_ambiguous", "skill_count": len(skills)},
            )
        skill_id, current = next(iter(skills.items()))
        current_asset_id = str(current.get("asset_id", ""))
        previous = self._previous_entry(skill_id, current_asset_id, snapshot["revision"])
        if previous is None:
            raise SkillAdminError(
                "rollback_target_missing", 404, "找不到上一版本",
                {"reason": "rollback_target_missing"},
            )
        if evidence_id is not None:
            self._load_evidence(evidence_id)
        try:
            result = self._call_supported(
                rollback_asset,
                self.registry,
                store,
                current_asset_id,
                reason=reason,
                rolled_back_by=rolled_back_by,
                previous_entry=previous,
                expected_revision=expected_revision,
                sidecar_dir=self.sidecar_dir,
                evidence_id=evidence_id,
                fault_hook=None,
            )
        except SkillEvolutionError as exc:
            text = str(exc)
            if "rollback_target_missing" in text:
                raise SkillAdminError(
                    "rollback_target_missing", 404, "上一版本缺失",
                    {"reason": "rollback_target_missing"},
                ) from None
            if "rollback_target" in text:
                raise SkillAdminError(
                    "rollback_target_unavailable", 409, "上一版本不可用",
                    {"reason": "rollback_target_unavailable"},
                ) from None
            raise SkillAdminError(
                "skill_state_conflict", 409, "无法回退", {"reason": "skill_state_conflict"}
            ) from None
        except (BindingError, UpperRegistryError):
            raise SkillAdminError(
                "skill_state_conflict", 409, "无法回退", {"reason": "skill_state_conflict"}
            ) from None
        entry = result["entry"]
        revision = int(result["revision"])
        payload = {
            "target_asset_id": entry["asset_id"],
            "skill_id": entry["skill_id"],
            "version": entry["version"],
            "package_hash": entry["package_hash"],
            "revision": revision,
            "commit_record_id": str(revision),
        }
        if reconciled:
            payload["reconciled"] = reconciled
        return payload

    # -- evaluation index --------------------------------------------------
    @property
    def _index_path(self) -> Path:
        return self.state_dir.joinpath(*ADMIN_SUBDIR, INDEX_FILENAME)

    def _read_index(self) -> list[dict[str, Any]]:
        if not self._index_path.is_file():
            return []
        try:
            payload = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        items = payload.get("items") if isinstance(payload, dict) else None
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    def _write_index(self, item: dict[str, Any]) -> None:
        items = [
            entry
            for entry in self._read_index()
            if entry.get("evidence_id") != item["evidence_id"]
        ]
        items.append(item)
        items.sort(key=lambda entry: str(entry.get("created_at", "")), reverse=True)
        atomic_write_json(self._index_path, {"items": items})

    def _status_of(self, asset_id: str) -> str:
        try:
            return self.registry.get(asset_id).status
        except UpperRegistryError:
            return "unknown"

    def _public_item(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "evidence_id": item.get("evidence_id"),
            "asset_id": item.get("asset_id"),
            "skill_id": item.get("skill_id"),
            "version": item.get("version"),
            "package_hash": item.get("package_hash"),
            "status": self._status_of(str(item.get("asset_id", ""))),
            "verdict": item.get("verdict"),
            "mode": item.get("mode"),
            "calls_model": bool(item.get("calls_model", False)),
            "revision": item.get("revision"),
            "created_at": item.get("created_at"),
        }

    def list_evaluations(self) -> dict[str, Any]:
        return {"items": [self._public_item(item) for item in self._read_index()]}

    def get_evaluation(self, evidence_id: str) -> dict[str, Any]:
        if not isinstance(evidence_id, str) or not _EVIDENCE_ID_RE.match(evidence_id):
            raise SkillAdminError("not_found", 404, "评测不存在", {"reason": "not_found"})
        item = next(
            (
                entry
                for entry in self._read_index()
                if entry.get("evidence_id") == evidence_id
            ),
            None,
        )
        path = self.state_dir.joinpath(*EVIDENCE_SUBDIR, f"{evidence_id}.json")
        if item is None and not path.is_file():
            raise SkillAdminError("not_found", 404, "评测不存在", {"reason": "not_found"})
        payload = self._public_item(item) if item is not None else {
            "evidence_id": evidence_id,
            "calls_model": False,
            "mode": "manual",
        }
        if path.is_file():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                record = {}
            if isinstance(record, dict):
                payload["transfer"] = self._phase_projection(
                    {
                        "metrics": record.get("transfer", {}).get("metrics", {}),
                        "verdict": record.get("transfer", {}).get("verdict"),
                        "reason": record.get("transfer", {}).get("reason"),
                    }
                )
                payload["regression"] = self._phase_projection(
                    {
                        "metrics": record.get("regression", {}).get("metrics", {}),
                        "verdict": record.get("regression", {}).get("verdict"),
                        "reason": record.get("regression", {}).get("reason"),
                    }
                )
                payload["final"] = record.get("final")
        return payload
