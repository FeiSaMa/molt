#!/usr/bin/env python3
"""Skill evolution demo: propose → two-phase eval → lifecycle → rollback.

Everything here is manual/scripted: ``ScriptedAdapter`` has ``real_model=False``,
so every evidence record is ``mode="manual"`` and ``calls_model=False``. No model
is called, nothing is claimed to be ``live``, and context loading is not treated
as a behaviour improvement — the only signal is per-task pass/fail from
deterministic substring grading, gated by the existing ``UpperEvaluator._decide``.

The provider branch is attempted first and expected to report
``provider_unavailable`` on a machine without a configured real adapter; the demo
then continues with the manual body supplied by this script.

State (all outside the repository)::

    /tmp/molt-skill-evo-state/
        candidates/{keep,reject,rollback}/<skill_id>/{manifest.json,SKILL.md}
        state/{skills/current.json,...}      binding store
        upper/registry.json                  upper registry
        sidecars/<asset_id>.json             lifecycle metadata
        state/skills/evaluations/<id>.json   evidence

Run::

    PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/demo_skill_evolution.py
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from molt.skills.binding import BindingStore  # noqa: E402
from molt.skills.eval_runner import ScriptedAdapter, two_phase_evaluate  # noqa: E402
from molt.skills.eval_tasks import load_tasks  # noqa: E402
from molt.skills.evolution import (  # noqa: E402
    ProviderUnavailable,
    SkillEvolutionError,
    propose_candidate,
)
from molt.skills.lifecycle import (  # noqa: E402
    activate_asset,
    approve_asset,
    bootstrap_baseline,
    mark_evaluated_from_evidence,
    register_asset,
    rollback_asset,
)
from molt.skills.loader import load_package  # noqa: E402
from molt.upper.registry import UpperRegistry  # noqa: E402

SKILLS_ROOT = ROOT / "skills"
EVAL_ROOT = ROOT / "skills_eval"
SKILL_ID = "repo-evidence"
STATE_ROOT = Path("/tmp/molt-skill-evo-state")
OPERATOR = "operator"

CANDIDATE_BODY = """\
# 仓库证据与零写入验收（修订版）

## 零写入

- 执行前后各取一次**树快照**并逐项比对；`git status --porcelain` 为空只是必要条件。

## 证据

- run / ledger 必须能重算，逐题配对表随聚合数字一起给出，聚合数字不得单独出现。

## 比较域

- `scenario_version` 不同即 `incomparable`，不得放进同一张表。

## 标注

- replay / fixture / live 如实标注；回放必须带 `replay_of_run_id`，并记录 `calls_model`。
"""


@dataclass
class DemoState:
    events: list[dict[str, Any]] = field(default_factory=list)

    def add(self, step: str, **payload: Any) -> None:
        self.events.append({"step": step, **payload})


def good_output(task) -> str:
    tokens = "；".join(task.expect["must_include"])
    return f"结论：{tokens}。步骤需在仓库外执行，并保留逐题记录以便重算。"


def scripted_responses(
    transfer: list, regression: list, *, baseline_fail: int, candidate_fail: int,
    regression_candidate_fail: str | None,
) -> dict[str, str]:
    responses: dict[str, str] = {}
    for index, task in enumerate(transfer):
        responses[f"baseline:{task.task_id}"] = (
            "未验证。" if index < baseline_fail else good_output(task)
        )
        responses[f"candidate:{task.task_id}"] = (
            "未验证。" if index >= len(transfer) - candidate_fail else good_output(task)
        )
    for task in regression:
        responses[f"baseline:{task.task_id}"] = good_output(task)
        responses[f"candidate:{task.task_id}"] = good_output(task)
    if regression_candidate_fail is not None:
        responses[f"candidate:{regression_candidate_fail}"] = "未验证。"
    return responses


def run_scenario(
    name: str,
    baseline,
    state: DemoState,
    adapter: ScriptedAdapter,
    transfer: list,
    regression: list,
    *,
    version: str,
) -> dict[str, Any]:
    candidate = propose_candidate(
        baseline,
        f"评审反馈：{name} 场景，补充零写入与比较域的可执行步骤。",
        manual_body=CANDIDATE_BODY + f"\n<!-- scenario: {name} -->\n",
        version=version,
        candidate_root=STATE_ROOT / "candidates" / name,
    )
    result = two_phase_evaluate(
        baseline,
        candidate,
        transfer,
        regression,
        STATE_ROOT / "state",
        adapter=adapter,
    )
    evidence = json.loads(Path(result["evidence_path"]).read_text(encoding="utf-8"))
    state.add(
        "two_phase",
        scenario=name,
        candidate_version=candidate.version,
        candidate_origin=candidate.origin,
        transfer_verdict=result["transfer"]["verdict"],
        regression_verdict=result["regression"]["verdict"],
        final=result["final"],
        evidence_kind=evidence["evidence_kind"],
        mode=evidence["mode"],
        calls_model=evidence["calls_model"],
        evidence_path=result["evidence_path"],
    )
    return {"candidate": candidate, "result": result, "evidence": evidence}


def main() -> int:
    if STATE_ROOT.exists():
        shutil.rmtree(STATE_ROOT)
    STATE_ROOT.mkdir(parents=True)
    state = DemoState()

    baseline = load_package(SKILLS_ROOT, SKILL_ID)
    tasks = load_tasks(EVAL_ROOT)
    transfer = [task for task in tasks if task.phase == "transfer"]
    regression = [task for task in tasks if task.phase == "regression"]

    # 1. Provider branch: no real adapter here → report and continue manually.
    provider_status = "provider_unavailable"
    try:
        propose_candidate(
            baseline,
            "反馈：补零写入",
            adapter=ScriptedAdapter(responses={}),  # real_model=False, not a provider
            candidate_root=STATE_ROOT / "candidates" / "provider",
        )
    except ProviderUnavailable as exc:
        provider_status = str(exc)
        print(f"provider_unavailable: {exc}")
    state.add("provider_probe", status=provider_status, writes_candidate=False)

    # 2. Three gate outcomes, all scripted/manual.
    keep = run_scenario(
        "keep",
        baseline,
        state,
        ScriptedAdapter(
            responses=scripted_responses(
                transfer,
                regression,
                baseline_fail=1,
                candidate_fail=0,
                regression_candidate_fail=None,
            )
        ),
        transfer,
        regression,
        version="1.0.1",
    )
    reject = run_scenario(
        "reject",
        baseline,
        state,
        ScriptedAdapter(
            responses=scripted_responses(
                transfer,
                regression,
                baseline_fail=0,
                candidate_fail=0,
                regression_candidate_fail=None,
            )
        ),
        transfer,
        regression,
        version="1.0.2",
    )
    rollback = run_scenario(
        "rollback",
        baseline,
        state,
        ScriptedAdapter(
            responses=scripted_responses(
                transfer,
                regression,
                baseline_fail=1,
                candidate_fail=0,
                regression_candidate_fail=regression[-1].task_id,
            )
        ),
        transfer,
        regression,
        version="1.0.3",
    )

    # 3. Lifecycle: operator baseline → keep candidate → activation.
    registry = UpperRegistry(STATE_ROOT / "upper")
    store = BindingStore(STATE_ROOT / "state")
    sidecars = STATE_ROOT / "sidecars"

    def register(pkg):
        return register_asset(
            registry,
            pkg,
            created_by=OPERATOR,
            origin="synthetic",
            sidecar_dir=sidecars,
            declared_origin="manual",
        )

    # The deployed v1.0.0 is an operator baseline: registered as a real active
    # skill asset with no keep evidence and no human review claim.
    boot = bootstrap_baseline(registry, store, baseline, actor=OPERATOR)
    baseline_asset = boot["asset"]
    state.add(
        "operator_baseline",
        asset_id=baseline_asset.asset_id,
        version=baseline.version,
        revision=boot["revision"],
        status=baseline_asset.status,
        approved=False,
        approval_note="manual operator baseline; not model-evaluated; no keep evidence",
        package_dir=boot["package_dir"],
        commit_record=boot["commit_record"],
    )

    candidate = keep["candidate"]
    candidate_pkg = load_package(
        candidate.package_dir.parent, SKILL_ID, candidate.version
    )
    candidate_asset = register(candidate_pkg)
    keep_evidence = keep["evidence"]
    mark_evaluated_from_evidence(
        registry, candidate_asset.asset_id, keep["result"]["evidence_path"]
    )
    approve_asset(
        registry,
        candidate_asset.asset_id,
        approved_by=OPERATOR,
        note="two-phase keep (scripted/manual)",
        evidence=keep["result"]["evidence_path"],
    )
    second = activate_asset(
        registry,
        store,
        candidate_asset.asset_id,
        expected_revision=boot["revision"],
        actor=OPERATOR,
        evidence_id=keep_evidence["eval_id"],
        reason="two-phase keep",
        sidecar_dir=sidecars,
    )
    state.add(
        "activate",
        asset_id=candidate_asset.asset_id,
        version=second["entry"]["version"],
        revision=second["revision"],
        package_hash=second["entry"]["package_hash"],
        superseded=second["superseded"],
        evidence_id=keep_evidence["eval_id"],
        commit_record=second["commit_record"],
        cleanup=second["cleanup"],
    )

    # reject/rollback evidence must never be approvable (D5.8).
    approve_rejected_for: list[dict[str, Any]] = []
    for name, scenario in (("reject", reject), ("rollback", rollback)):
        scenario_pkg = load_package(
            scenario["candidate"].package_dir.parent,
            SKILL_ID,
            scenario["candidate"].version,
        )
        asset = register(scenario_pkg)
        mark_evaluated_from_evidence(
            registry, asset.asset_id, scenario["result"]["evidence_path"]
        )
        try:
            approve_asset(
                registry,
                asset.asset_id,
                approved_by=OPERATOR,
                note=f"{name} must not be approved",
                evidence=scenario["result"]["evidence_path"],
            )
        except SkillEvolutionError as exc:
            approve_rejected_for.append(
                {
                    "scenario": name,
                    "version": scenario_pkg.version,
                    "asset_id": asset.asset_id,
                    "reason": str(exc),
                }
            )
            state.add(
                "approve_rejected",
                scenario=name,
                asset_id=asset.asset_id,
                reason=str(exc),
            )

    # 4. Roll back to the operator baseline. The target is derived from the
    # commit chain (the `from` of the activation that adopted the candidate).
    rolled = rollback_asset(
        registry,
        store,
        candidate_asset.asset_id,
        reason="demo: regression phase reported rollback",
        rolled_back_by=OPERATOR,
        expected_revision=second["revision"],
        sidecar_dir=sidecars,
    )
    binding = store.get_section("skills", SKILL_ID)
    state.add(
        "rollback",
        rolled_back_asset=candidate_asset.asset_id,
        target_asset=rolled["entry"]["asset_id"],
        revision=rolled["revision"],
        binding_asset_id=binding["asset_id"],
        binding_version=binding["version"],
        binding_package_hash=binding["package_hash"],
        binding_matches_baseline=binding["package_hash"] == baseline.content_hash,
        target_registry_status=registry.get(rolled["entry"]["asset_id"]).status,
        cleanup=rolled["cleanup"],
        commit_record=rolled["commit_record"],
        history_files=len(store.history_files()),
    )

    evidence_paths = [
        keep["result"]["evidence_path"],
        reject["result"]["evidence_path"],
        rollback["result"]["evidence_path"],
    ]
    commit_dir = store.state_dir / "skills" / "commits"
    commit_records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(commit_dir.glob("*.json"), key=lambda item: int(item.stem))
    ]
    summary = {
        "demo": "skill-evolution",
        "evidence_kind": "evaluation",
        "mode": "manual",
        "calls_model": False,
        "provider_status": provider_status,
        "skill_id": SKILL_ID,
        "baseline_version": baseline.version,
        "task_counts": {"transfer": len(transfer), "regression": len(regression)},
        "gate": "UpperEvaluator._decide (molt/upper/evaluator.py:891-917)",
        "packages_path": str(store.state_dir / "skills" / "packages"),
        "commit_records": commit_records,
        "approve_rejected_for": approve_rejected_for,
        "scenarios": {
            "keep": {
                "final": keep["result"]["final"],
                "transfer": keep["result"]["transfer"]["reason"],
                "regression": keep["result"]["regression"]["reason"],
            },
            "reject": {
                "final": reject["result"]["final"],
                "transfer": reject["result"]["transfer"]["reason"],
                "regression": reject["result"]["regression"]["reason"],
            },
            "rollback": {
                "final": rollback["result"]["final"],
                "transfer": rollback["result"]["transfer"]["reason"],
                "regression": rollback["result"]["regression"]["reason"],
            },
        },
        "evidence_paths": evidence_paths,
        "state_root": str(STATE_ROOT),
        "steps": state.events,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
