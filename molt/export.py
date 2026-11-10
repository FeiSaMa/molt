"""把 ``state/`` 导出成 ``web/data``（静态 JSON + ``bundle.js``）。

设计原则：

- **只导出数据**，不生成页面（HTML/CSS 页面属于后续批次）。
- 全部字段来自既有记录：run/result/decision/proposal/archive/snapshot/event；
  不做二次评分，也不补造明细。
- holdout 只给聚合分：``report_results.json`` 的 ``holdout`` 块按白名单投影；
  ``scope=report`` 的 run 与事件明细不导出，只保留聚合统计。
- provenance 必须可区分 fixture / live / replay；fixture 不得伪装成 live，
  live 必须有证据（model 或 pi runtime），冲突标记 fail closed。
- 只依赖 stdlib；导出过程不写仓库（``--out`` 指向哪里就只写哪里）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from molt.core.archive import ArchiveStore
from molt.core.audit import audit_state
from molt.core.errors import AuditError, ValidationError
from molt.core.events import EventLog, utc_now
from molt.core.hash import canonical_json_text, content_hash, sha256_file
from molt.core.redact import redaction_manifest_path
from molt.core.store import StateStore
from molt.core.types import Event, Genome, Run, Scenario

EXPORT_SCHEMA_VERSION = "1.0.0"
PROVENANCE_KINDS = ("fixture", "live", "replay")
AGGREGATE_KEYS = (
    "solved",
    "total",
    "pass_at_1_first_attempt",
    "cost_total_usd",
    "cost_per_solved_usd",
    "invalid_ratio",
)

# 15 个契约文件（含 bundle.js）。index.json 自身不在 files 清单里。
DATA_FILES: tuple[str, ...] = (
    "index.json",
    "scenario.json",
    "variants.json",
    "runs.json",
    "search_results.json",
    "report_results.json",
    "gate.json",
    "proposals.json",
    "archive.json",
    "snapshots.json",
    "audit.json",
    "redaction.json",
    "events/search.jsonl",
    "events/report.jsonl",
    "bundle.js",
)

# claim 只描述 demo 证据链实际能证明的东西；ADR-0003 明确不承诺字节级复现。
CLAIM: dict[str, Any] = {
    "runtime": "fixture",
    "live_model_calls": False,
    "holdout_export": "aggregate-only",
    "reproducibility": "指标一致；不承诺字节级复现（run_id 含随机后缀）",
    "provenance": "runs[].provenance.kind ∈ {fixture, live, replay}；fixture 不是 live",
}
NOT_CLAIMED: list[str] = [
    "live 模型的质量或成本",
    "跨 scenario_version 的比较",
    "holdout 逐题明细",
    "字节级证据复现",
]

REPO_PLACEHOLDER = "<repo>"
STATE_PLACEHOLDER = "<state>"
HOME_PLACEHOLDER = "<home>"


class ExportError(ValueError):
    """数据不满足导出契约（provenance 冲突 / live 证据缺失等）。"""


# ---------------------------------------------------------------------------
# 投影
# ---------------------------------------------------------------------------


def _genome_runtime_id(genomes: dict[str, Genome], run: Run) -> str:
    genome = genomes.get(run.genome_id)
    runtime = genome.runtime if genome is not None else {}
    return str(runtime.get("runtime_id", ""))


def _provenance(run: Run, genomes: dict[str, Genome]) -> dict[str, Any]:
    """把 run 的 provenance 归成 fixture / live / replay；证据不足即报错（fail closed）。"""
    ext = run.ext if isinstance(run.ext, dict) else {}
    explicit = ext.get("provenance")
    replay_of = ext.get("replay_of_run_id")

    if isinstance(explicit, dict):
        kind = explicit.get("kind")
        if kind not in PROVENANCE_KINDS:
            raise ExportError(f"run {run.run_id}: 未知 provenance.kind={kind!r}")
        if kind == "live":
            if replay_of:
                raise ExportError(
                    f"run {run.run_id}: provenance.kind=live 与 replay_of_run_id 冲突，"
                    "不得静默标 live"
                )
            model = explicit.get("model")
            runtime_id = str(explicit.get("runtime_id") or _genome_runtime_id(genomes, run))
            if not model and not runtime_id.startswith("pi"):
                raise ExportError(
                    f"run {run.run_id}: provenance.kind=live 缺少 model 或 pi runtime 证据"
                )
        elif kind == "replay":
            replay_of = replay_of or explicit.get("replay_of_run_id")
            if not replay_of:
                raise ExportError(f"run {run.run_id}: provenance.kind=replay 缺少 replay_of_run_id")
        result = dict(explicit)
        if kind == "replay":
            result["replay_of_run_id"] = replay_of
        result.setdefault("live", kind == "live")
        if kind == "fixture":
            result["note"] = "B1 fixture runtime：确定性回放，不是 live 模型调用"
        return result

    if replay_of:
        return {"kind": "replay", "replay_of_run_id": replay_of, "live": False}
    runtime_id = _genome_runtime_id(genomes, run)
    kind = "live" if runtime_id.startswith(("pi", "live")) else "fixture"
    result: dict[str, Any] = {
        "kind": kind,
        "live": kind == "live",
        "runtime_id": runtime_id or None,
    }
    if kind == "fixture":
        if runtime_id.startswith("deterministic"):
            result["note"] = "deterministic runtime：零成本确定性执行，不是 live 模型调用"
        else:
            result["note"] = "B1 fixture runtime：确定性回放，不是 live 模型调用"
    return result


def _scenario_payload(scenario: Scenario) -> dict[str, Any]:
    search = list(scenario.task_sets["search"])
    report = list(scenario.task_sets["report"])
    return {
        "schema_version": scenario.schema_version,
        "scenario_id": scenario.scenario_id,
        "scenario_version": scenario.scenario_version,
        "title": scenario.title,
        "shell": {
            "shell_id": scenario.shell_id,
            "shell_version": scenario.shell_version,
        },
        "bench_hash": scenario.bench_hash,
        # holdout 只暴露题数，不导出具体 task id。
        "task_sets": {"search": search},
        "task_counts": {
            "search": len(search),
            "report": len(report),
            "total": len(search) + len(report),
        },
        "holdout": {"task_count": len(report), "detail": "not-exported"},
        "policy": scenario.policy.to_dict(),
        "budget": scenario.policy.budget.to_dict(),
        "isolation": scenario.isolation.to_dict(),
        "claim": {
            **CLAIM,
            "runtime": str(scenario.ext.get("default_runtime", "fixture")),
        },
        "not_claimed": list(NOT_CLAIMED),
        "ext": dict(scenario.ext),
    }


def _variants_payload(genomes: Iterable[Genome], adopted_ids: set[str]) -> dict[str, Any]:
    variants = []
    for genome in sorted(genomes, key=lambda item: item.genome_id):
        variants.append(
            {
                "id": genome.genome_id,
                "parent": genome.parent_id,
                "switches": genome.switches.to_dict(),
                "runtime": dict(genome.runtime),
                "adopted": genome.genome_id in adopted_ids,
                "budget": genome.budget.to_dict(),
                "scenario_version": genome.scenario_version,
            }
        )
    return {"variants": variants}


def _runs_payload(runs: Iterable[Run], genomes: dict[str, Genome]) -> dict[str, Any]:
    payload = []
    for run in sorted(runs, key=lambda item: item.run_id):
        # holdout run 的 run_id 与 task_id 都是逐题细节，整行不导出；
        # 聚合统计见 report_results.json 的 report_runs。
        if run.scope == "report":
            continue
        usage = run.usage.to_dict()
        verification = run.verification.to_dict()
        payload.append(
            {
                "run_id": run.run_id,
                "genome_id": run.genome_id,
                "task_id": run.task_id,
                "scope": run.scope,
                "attempt": run.attempt,
                "solved": run.outcome == "passed",
                "status": run.outcome,
                "cost_usd": usage["cost_usd"],
                "tokens": {"in": usage["tokens_in"], "out": usage["tokens_out"]},
                "wall_ms": usage["wall_ms"],
                "turns": usage["turns"],
                "verifier": {
                    "hash": run.verifier_hash,
                    "matches": verification["matches"],
                    "tests_hash_before": verification["tests_hash_before"],
                    "tests_hash_after": verification["tests_hash_after"],
                },
                "provenance": _provenance(run, genomes),
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            }
        )
    return {"runs": payload}


def _score_block(block: dict[str, Any], *, aggregate_only: bool) -> dict[str, Any]:
    if not aggregate_only:
        return dict(block)
    return {key: block[key] for key in AGGREGATE_KEYS if key in block}


def _search_results_payload(results: Iterable[Any]) -> dict[str, Any]:
    payload = []
    for result in sorted(results, key=lambda item: item.result_id):
        if result.scope != "search":
            continue
        payload.append(
            {
                "result_id": result.result_id,
                "genome_id": result.genome_id,
                "scope": "search",
                "scores": {
                    "search": _score_block(result.scores["search"].to_dict(), aggregate_only=False)
                },
                "generated_at": result.generated_at,
            }
        )
    return {"scope": "search", "results": payload}


def _report_run_stats(runs: Iterable[Run]) -> dict[str, Any]:
    """holdout run 的聚合统计：只有数量/状态/成本，没有 run_id 或 task_id。"""
    selected = [run for run in runs if run.scope == "report"]
    by_status: dict[str, int] = {}
    cost_total = 0.0
    wall_ms = 0
    tokens_in = 0
    tokens_out = 0
    for run in selected:
        by_status[run.outcome] = by_status.get(run.outcome, 0) + 1
        usage = run.usage.to_dict()
        cost_total += usage["cost_usd"]
        wall_ms += usage["wall_ms"]
        tokens_in += usage["tokens_in"]
        tokens_out += usage["tokens_out"]
    return {
        "count": len(selected),
        "solved": sum(1 for run in selected if run.outcome == "passed"),
        "by_status": dict(sorted(by_status.items())),
        "cost_usd": round(cost_total, 6),
        "wall_ms": wall_ms,
        "tokens": {"in": tokens_in, "out": tokens_out},
    }


def _report_results_payload(results: Iterable[Any], runs: Iterable[Run]) -> dict[str, Any]:
    report_runs = [run for run in runs if run.scope == "report"]
    payload = []
    for result in sorted(results, key=lambda item: item.result_id):
        if result.scope != "report":
            continue
        payload.append(
            {
                "result_id": result.result_id,
                "genome_id": result.genome_id,
                "scope": "report",
                "scores": {
                    "search": _score_block(result.scores["search"].to_dict(), aggregate_only=False),
                    # holdout 只给聚合分，逐题明细不得出现在 web/data。
                    "holdout": _score_block(
                        result.scores["holdout"].to_dict(), aggregate_only=True
                    ),
                },
                # 该 genome 的 holdout run 聚合（count/status/cost），不含任何 id。
                "report_runs": _report_run_stats(
                    run for run in report_runs if run.genome_id == result.genome_id
                ),
                "generated_at": result.generated_at,
            }
        )
    return {
        "scope": "report",
        "results": payload,
        "report_runs_total": _report_run_stats(report_runs),
    }


def _delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(right - left, 6)


def _gate_payload(scenario: Scenario, decisions: Iterable[Any]) -> dict[str, Any]:
    rows = []
    for decision in sorted(decisions, key=lambda item: item.decision_id):
        data = decision.to_dict()
        pairs = []
        for row in data["paired"]:
            pairs.append(
                {
                    **row,
                    "passed_delta": int(row["candidate_passed"]) - int(row["baseline_passed"]),
                    "cost_delta_usd": _delta(row["baseline_cost_usd"], row["candidate_cost_usd"]),
                }
            )
        scores = data["scores"]
        pair_cost_delta = round(sum(row["cost_delta_usd"] or 0.0 for row in pairs), 6)
        if scores["baseline"].get("cost_total_usd") is not None:
            cost_total_delta = _delta(
                scores["baseline"]["cost_total_usd"], scores["candidate"]["cost_total_usd"]
            )
        else:
            cost_total_delta = pair_cost_delta
        rows.append(
            {
                "decision_id": data["decision_id"],
                "baseline_genome_id": data["baseline_genome_id"],
                "candidate_genome_id": data["candidate_genome_id"],
                "verdict": data["verdict"],
                "reason": data["reason"],
                "reasons": [data["reason"]],
                "k": data["k"],
                "limits_identical": data["limits_identical"],
                "budget_check": dict(data["budget_check"]),
                "invalid_ratio": dict(data["invalid_ratio"]),
                "scores": dict(scores),
                "pairs": pairs,
                "deltas": {
                    "solved": scores["candidate"]["solved"] - scores["baseline"]["solved"],
                    "cost_total_usd": cost_total_delta,
                    "cost_per_solved_usd": _delta(
                        scores["baseline"].get("cost_per_solved_usd"),
                        scores["candidate"].get("cost_per_solved_usd"),
                    ),
                },
                "created_at": data["created_at"],
                "ext": dict(data["ext"]),
            }
        )
    return {
        "policy_sha256": content_hash(scenario.policy.to_dict()),
        "decision_count": len(rows),
        "decisions": rows,
        "reasons": [row["reason"] for row in rows],
    }


def _proposals_payload(proposals: Iterable[Any]) -> dict[str, Any]:
    payload = [proposal.to_dict() for proposal in proposals]
    payload.sort(key=lambda item: item["proposal_id"])
    return {"proposals": payload}


def _archive_payload(store: StateStore, genomes: list[Genome]) -> dict[str, Any]:
    archives = [record.to_dict() for record in ArchiveStore(store.root).list()]
    archives.sort(key=lambda item: (item["generation"], item["archive_id"]))
    adopted = {item["genome_id"]: item for item in archives}
    lineage = []
    for genome in sorted(genomes, key=lambda item: item.genome_id):
        record = adopted.get(genome.genome_id)
        lineage.append(
            {
                "genome_id": genome.genome_id,
                "parent_id": genome.parent_id,
                "adopted": record is not None,
                "archive_id": record["archive_id"] if record else None,
                "generation": record["generation"] if record else None,
                "adopted_at": record["adopted_at"] if record else None,
            }
        )
    return {"lineage": lineage, "archives": archives}


def _snapshots_payload(store: StateStore) -> dict[str, Any]:
    snapshots = []
    for snapshot in sorted(store.snapshots(), key=lambda item: item.snapshot_id):
        snapshots.append(
            {
                "snapshot_id": snapshot.snapshot_id,
                "generation": snapshot.generation,
                "created_at": snapshot.created_at,
                "roots": list(snapshot.roots),
                "tree_hash": snapshot.tree_hash,
                "file_count": sum(len(files) for files in snapshot.files.values()),
            }
        )
    return {"snapshots": snapshots}


def _audit_payload(store: StateStore, scenario: Scenario) -> dict[str, Any]:
    try:
        report = audit_state(store, scenario)
    except AuditError as exc:
        mismatches = [
            line.removeprefix("- ").strip()
            for line in str(exc).splitlines()
            if line.strip().startswith("- ")
        ]
        return {"ok": False, "checked": None, "mismatches": mismatches or [str(exc)]}
    return {"ok": True, "checked": report, "mismatches": []}


def _redaction_payload(store: StateStore, scenario: Scenario) -> dict[str, Any] | None:
    path = redaction_manifest_path(store.root, scenario.scenario_id)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _read_scope_events(store: StateStore, scope: str) -> list[Event]:
    """优先读 per-run 事件目录；只有物化文件时回退读 ``events/<scope>.jsonl``。"""
    log = EventLog(store.root)
    directory = store.root / "events" / scope
    events: list[Event] = []
    if directory.is_dir() and any(directory.glob("*.jsonl")):
        events = list(log.iter_scope(scope))
    else:
        materialized = store.root / "events" / f"{scope}.jsonl"
        if materialized.is_file():
            for line in materialized.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(Event.from_dict(json.loads(line)))
    return sorted(events, key=lambda event: (event.ts, event.event_id))


def _sanitize_report_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """report 流只导出聚合事件：per-run 明细与 holdout task id 一律剥离。"""
    sanitized = []
    for event in events:
        if event.get("run_id"):
            continue
        payload = dict(event.get("payload") or {})
        for key in ("task_id", "task_ids"):
            if key in payload:
                value = payload.pop(key)
                if isinstance(value, list):
                    payload.setdefault("count", len(value))
                elif "count" not in payload:
                    payload["count"] = 1
        event = dict(event)
        event["payload"] = payload
        sanitized.append(event)
    return sanitized


def _event_dicts(store: StateStore, scope: str) -> list[dict[str, Any]]:
    payload = [event.to_dict() for event in _read_scope_events(store, scope)]
    if scope == "report":
        payload = _sanitize_report_events(payload)
    return payload


# ---------------------------------------------------------------------------
# 路径脱敏与落盘
# ---------------------------------------------------------------------------


def _scrub(value: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        for old, new in replacements:
            if old and old in value:
                value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_scrub(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _scrub(item, replacements) for key, item in value.items()}
    return value


def _replacements(root: Path, state_root: Path) -> list[tuple[str, str]]:
    # 先替换更长的前缀，避免 /home/<user> 先把仓库路径吃掉。
    pairs = [
        (str(Path(state_root).resolve()), STATE_PLACEHOLDER),
        (str(Path(root).resolve()), REPO_PLACEHOLDER),
        (str(Path.home()), HOME_PLACEHOLDER),
    ]
    unique: list[tuple[str, str]] = []
    for old, new in pairs:
        if old and old != "/" and all(existing != old for existing, _ in unique):
            unique.append((old, new))
    return unique


def _json_text(payload: Any, replacements: list[tuple[str, str]]) -> str:
    return (
        json.dumps(
            _scrub(payload, replacements),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )


def _jsonl_text(events: Iterable[dict[str, Any]], replacements: list[tuple[str, str]]) -> str:
    lines = []
    for event in events:
        payload = _scrub(event, replacements)
        lines.append(canonical_json_text(payload))
    return "".join(line + "\n" for line in lines)


def _write_text(path: Path, text: str) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"sha256": sha256_file(path), "bytes": path.stat().st_size}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def export_web_data(
    root: Path,
    scenario: Scenario,
    state: StateStore | Path,
    out_dir: Path,
) -> dict[str, Any]:
    """把 ``state`` 导出到 ``out_dir``，返回 ``index.json`` 的内容。"""
    root = Path(root)
    store = state if isinstance(state, StateStore) else StateStore(Path(state))
    out_dir = Path(out_dir)

    genomes_list = store.list_genomes()
    genomes = {genome.genome_id: genome for genome in genomes_list}
    runs = store.list_runs()
    results = store.list_results()
    decisions = store.list_decisions()
    proposals = [
        store.read_proposal(path.stem) for path in sorted((store.root / "proposals").glob("*.json"))
    ]
    adopted_ids = {record.genome_id for record in ArchiveStore(store.root).list()}
    events = {
        "search": _event_dicts(store, "search"),
        "report": _event_dicts(store, "report"),
    }

    replacements = _replacements(root, store.root)

    payloads: dict[str, Any] = {
        "scenario.json": _scenario_payload(scenario),
        "variants.json": _variants_payload(genomes_list, adopted_ids),
        "runs.json": _runs_payload(runs, genomes),
        "search_results.json": _search_results_payload(results),
        "report_results.json": _report_results_payload(results, runs),
        "gate.json": _gate_payload(scenario, decisions),
        "proposals.json": _proposals_payload(proposals),
        "archive.json": _archive_payload(store, genomes_list),
        "snapshots.json": _snapshots_payload(store),
        "audit.json": _audit_payload(store, scenario),
        "redaction.json": _redaction_payload(store, scenario),
    }

    files: dict[str, dict[str, Any]] = {}
    for name, payload in payloads.items():
        files[name] = _write_text(out_dir / name, _json_text(payload, replacements))
    for scope in ("search", "report"):
        relative = f"events/{scope}.jsonl"
        files[relative] = _write_text(out_dir / relative, _jsonl_text(events[scope], replacements))

    bundle_view = _scrub(
        {
            "scenario": payloads["scenario.json"],
            "variants": payloads["variants.json"],
            "runs": payloads["runs.json"],
            "search_results": payloads["search_results.json"],
            "report_results": payloads["report_results.json"],
            "gate": payloads["gate.json"],
            "proposals": payloads["proposals.json"],
            "archive": payloads["archive.json"],
            "snapshots": payloads["snapshots.json"],
            "audit": payloads["audit.json"],
            "redaction": payloads["redaction.json"],
            "events": {scope: events[scope] for scope in ("search", "report")},
        },
        replacements,
    )
    bundle_text = (
        "// 由 `molt export` 生成，请勿手改；页面只读 window.MOLT_VIEW。\n"
        "window.MOLT_VIEW = "
        + json.dumps(bundle_view, ensure_ascii=False, sort_keys=True, indent=2)
        + ";\n"
    )
    files["bundle.js"] = _write_text(out_dir / "bundle.js", bundle_text)

    revision = content_hash({name: entry["sha256"] for name, entry in files.items()})
    index = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "scenario_id": scenario.scenario_id,
        "revision": revision,
        "generated_at": utc_now(),
        "files": dict(sorted(files.items())),
        "counts": {
            "genomes": len(genomes_list),
            "runs": len(payloads["runs.json"]["runs"]),
            "report_runs": payloads["report_results.json"]["report_runs_total"]["count"],
            "results": len(results),
            "search_results": len(payloads["search_results.json"]["results"]),
            "report_results": len(payloads["report_results.json"]["results"]),
            "decisions": len(decisions),
            "proposals": len(proposals),
            "archives": len(payloads["archive.json"]["archives"]),
            "snapshots": len(payloads["snapshots.json"]["snapshots"]),
            "events_search": len(events["search"]),
            "events_report": len(events["report"]),
        },
    }
    _write_text(out_dir / "index.json", _json_text(index, replacements))

    missing = sorted(set(DATA_FILES) - set(_relative_files(out_dir)))
    if missing:  # pragma: no cover - 防御：文件集必须恰好是契约清单
        raise ValidationError(f"导出缺少文件: {missing}")
    return index


def _relative_files(out_dir: Path) -> list[str]:
    return sorted(str(path.relative_to(out_dir)) for path in out_dir.rglob("*") if path.is_file())
