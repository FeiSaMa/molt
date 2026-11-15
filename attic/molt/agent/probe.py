"""Probe 实验索引的构建、校验与只读投影。

索引投影是 ``probes/probe_experiments_manifest.json`` 的唯一对外形态：
manifest 保存逐文件的 sha256 / bytes 与逐实验的冻结值，本模块负责校验后
投影成 ``GET /api/experiments`` / ``molt serve --probe-index`` 读取的 JSON。

约束：

* 生成物只含白名单字段；prompt / response / thinking / memory / cases / stdout /
  stderr / solution_snapshot / ext / task_id / run_id / task_ids 一律不出现。
* ``revision`` 是「去掉 revision 后的规范化 JSON」的 sha256，用作自校验哈希；
  ``molt serve --probe-index`` 加载时会重算，不一致即 ``probe_index_invalid``。
* ``generated_at`` 只从 manifest 取，构建过程不读时钟，两次构建字节一致。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"
MANIFEST_NAME = "probe_experiments_manifest.json"
MANIFEST_SCHEMA_VERSION = "1.0.0"

#: caveats 闭集（16 项）。值是一行中文文案，供文档与工具输出使用。
CAVEATS: dict[str, str] = {
    "missing_scenario_version": "源记录未保存 scenario_version，不能跨版本比较",
    "missing_shell_version": "源记录未保存 shell_version，不能跨版本比较",
    "missing_verifier_hash": "源记录未保存 verifier_hash，判分口径不可复算",
    "partial_provenance": "只有部分记录带可复算的运行证据",
    "function_check_violations": "function check 存在目标函数未改动或缺失的违规行",
    "budget_stopped": "预算触顶后提前停止",
    "cost_estimated": "成本是运行记录汇总估算，不是账单口径",
    "cost_includes_control": "成本含对照组（control）开销",
    "cost_includes_reflection": "成本含反思（reflection）调用开销",
    "cost_includes_retries": "成本含被判 invalid 的重试开销",
    "wall_excludes_reflection": "墙钟不含反思阶段耗时",
    "wall_includes_reflection": "墙钟包含反思阶段耗时，不能与不含反思的记录直接比较",
    "wall_final_rows_only": "墙钟只统计最终行，不含被判 invalid 的重试",
    "rows_verdict_count_differs": "记录行数与 verdict 覆盖的计数不同，分母不可混用",
    "validation_not_capability": "验证/彩排类记录只说明流程或检查通过，不构成能力结论",
    "mixed_provenance": "证据混合 live 与 replay/派生记录，不能整体标成 live",
}

#: 投影绝不返回的键（递归扫描，manifest 与索引都会被拒）。
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "prompt",
        "response",
        "thinking",
        "memory",
        "cases",
        "stdout",
        "stderr",
        "solution_snapshot",
        "ext",
        "task_id",
        "task_ids",
        "run_id",
        "run_ids",
        "state",
        "command",
        "raw",
    }
)

_REQUIRED_EXPERIMENT_KEYS = (
    "experiment_id",
    "title",
    "study_kind",
    "stage",
    "status",
    "provenance",
    "scenario_version",
    "shell_version",
    "verifier_hash",
    "task_scope",
    "metrics",
    "comparison",
    "evidence",
    "comparability_key",
    "caveats",
)

_SCORE_SCOPES = frozenset({"run", "task", "cell"})
_COMPLETENESS_VALUES = frozenset({"present", "absent", "partial", "not_applicable"})
_PROVENANCE_KINDS = frozenset({"fixture", "live", "replay", "mixed"})
_STATUSES = frozenset({"completed", "partial", "failed"})


class ProbeIndexError(ValueError):
    """manifest / index 不合法（fail loud，不降级）。"""


def canonical_json(payload: Any) -> bytes:
    """规范化 JSON：键排序、无多余空白、UTF-8。"""

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def compute_revision(document: dict[str, Any]) -> str:
    """``revision`` = 去掉 revision 字段后的规范化 JSON sha256。"""

    body = {key: value for key, value in document.items() if key != "revision"}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def _fail(message: str) -> ProbeIndexError:
    return ProbeIndexError(message)


def _scan_sensitive(value: Any, where: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise _fail(f"{where}: 键必须是字符串")
            if key in SENSITIVE_KEYS:
                raise _fail(f"{where}: 出现敏感字段 {key!r}")
            _scan_sensitive(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_sensitive(item, f"{where}[{index}]")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise _fail(message)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_int(value: Any, where: str, minimum: int = 0) -> None:
    _require(_is_int(value) and value >= minimum, f"{where} 必须 >= {minimum} 的整数")


def _check_optional_str(value: Any, where: str) -> None:
    _require(value is None or (isinstance(value, str) and value != ""), f"{where} 必须是非空字符串或 null")


def _check_str(value: Any, where: str) -> None:
    _require(isinstance(value, str) and value != "", f"{where} 必须是非空字符串")


def validate_experiment(
    experiment: Any, where: str = "experiment", *, expect_pass_rate: bool = False
) -> None:
    """校验单条投影（字段、取值域、闭集与敏感键）。

    manifest 里的 metrics 没有 ``pass_rate``（由构建器计算）；索引里的必须有。
    """

    _require(isinstance(experiment, dict), f"{where} 必须是对象")
    missing = [key for key in _REQUIRED_EXPERIMENT_KEYS if key not in experiment]
    if missing:
        raise _fail(f"{where} 缺少字段: {', '.join(missing)}")
    extra = sorted(set(experiment) - set(_REQUIRED_EXPERIMENT_KEYS))
    if extra:
        raise _fail(f"{where} 含未知字段: {', '.join(extra)}")

    _check_str(experiment["experiment_id"], f"{where}.experiment_id")
    _check_str(experiment["title"], f"{where}.title")
    _check_str(experiment["study_kind"], f"{where}.study_kind")
    _check_str(experiment["stage"], f"{where}.stage")
    _require(experiment["status"] in _STATUSES, f"{where}.status 不在闭集")
    _require(experiment["comparability_key"] is None, f"{where}.comparability_key 必须为 null（本批冻结）")

    provenance = experiment["provenance"]
    _require(isinstance(provenance, dict), f"{where}.provenance 必须是对象")
    _require(
        set(provenance) == {"kind", "basis", "coverage"},
        f"{where}.provenance 字段必须是 kind/basis/coverage",
    )
    _require(provenance["kind"] in _PROVENANCE_KINDS, f"{where}.provenance.kind 不在闭集")
    _check_str(provenance["basis"], f"{where}.provenance.basis")
    coverage = provenance["coverage"]
    _require(isinstance(coverage, dict), f"{where}.provenance.coverage 必须是对象")
    _require(
        set(coverage) == {"records_with_evidence", "records_total"},
        f"{where}.provenance.coverage 字段必须是 records_with_evidence/records_total",
    )
    _check_int(coverage["records_with_evidence"], f"{where}.provenance.coverage.records_with_evidence")
    _check_int(coverage["records_total"], f"{where}.provenance.coverage.records_total")
    _require(
        coverage["records_with_evidence"] <= coverage["records_total"],
        f"{where}.provenance.coverage 证据数不能超过总数",
    )

    for key in ("scenario_version", "shell_version", "verifier_hash"):
        _check_optional_str(experiment[key], f"{where}.{key}")

    scope = experiment["task_scope"]
    _require(isinstance(scope, dict), f"{where}.task_scope 必须是对象")
    _require(
        set(scope) == {"count", "reps", "cells"},
        f"{where}.task_scope 字段必须是 count/reps/cells",
    )
    _check_int(scope["count"], f"{where}.task_scope.count", minimum=1)
    if scope["reps"] is not None:
        _check_int(scope["reps"], f"{where}.task_scope.reps", minimum=1)
    _check_int(scope["cells"], f"{where}.task_scope.cells", minimum=1)

    metrics = experiment["metrics"]
    _require(isinstance(metrics, dict), f"{where}.metrics 必须是对象")
    metric_keys = {"recorded_runs", "passed", "total", "score_scope", "cost", "wall_ms"}
    if expect_pass_rate:
        metric_keys.add("pass_rate")
    _require(set(metrics) == metric_keys, f"{where}.metrics 字段不合法")
    _check_int(metrics["recorded_runs"], f"{where}.metrics.recorded_runs")
    _check_int(metrics["passed"], f"{where}.metrics.passed")
    _check_int(metrics["total"], f"{where}.metrics.total", minimum=1)
    _require(metrics["passed"] <= metrics["total"], f"{where}.metrics.passed 不能大于 total")
    _require(metrics["score_scope"] in _SCORE_SCOPES, f"{where}.metrics.score_scope 不在闭集")
    if expect_pass_rate:
        expected_rate = round(metrics["passed"] / metrics["total"], 4)
        _require(
            metrics["pass_rate"] == expected_rate,
            f"{where}.metrics.pass_rate 应为 {expected_rate}",
        )

    cost = metrics["cost"]
    _require(isinstance(cost, dict), f"{where}.metrics.cost 必须是对象")
    _require(
        set(cost) == {
            "usd",
            "scope",
            "includes_control",
            "includes_reflection",
            "includes_retries",
            "estimated",
        },
        f"{where}.metrics.cost 字段不合法",
    )
    _require(
        isinstance(cost["usd"], (int, float)) and not isinstance(cost["usd"], bool) and cost["usd"] >= 0,
        f"{where}.metrics.cost.usd 必须是非负数",
    )
    _check_str(cost["scope"], f"{where}.metrics.cost.scope")
    for key in ("includes_control", "includes_reflection", "includes_retries", "estimated"):
        _require(isinstance(cost[key], bool), f"{where}.metrics.cost.{key} 必须是布尔值")

    wall = metrics["wall_ms"]
    _require(isinstance(wall, dict), f"{where}.metrics.wall_ms 必须是对象")
    _require(
        set(wall) == {"value", "scope", "includes_reflection"},
        f"{where}.metrics.wall_ms 字段不合法",
    )
    _check_int(wall["value"], f"{where}.metrics.wall_ms.value")
    _check_str(wall["scope"], f"{where}.metrics.wall_ms.scope")
    _require(
        isinstance(wall["includes_reflection"], bool),
        f"{where}.metrics.wall_ms.includes_reflection 必须是布尔值",
    )

    comparison = experiment["comparison"]
    _require(isinstance(comparison, dict), f"{where}.comparison 必须是对象")
    _require(
        set(comparison) == {"baseline", "control", "oracle"},
        f"{where}.comparison 字段必须是 baseline/control/oracle",
    )
    for group, value in comparison.items():
        if value is None:
            continue
        _require(isinstance(value, dict), f"{where}.comparison.{group} 必须是对象或 null")
        _require(
            set(value) == {"runs", "runs_solved", "tasks", "tasks_solved"},
            f"{where}.comparison.{group} 必须是四元组 runs/runs_solved/tasks/tasks_solved",
        )
        for key, number in value.items():
            _check_int(number, f"{where}.comparison.{group}.{key}")

    evidence = experiment["evidence"]
    _require(isinstance(evidence, dict), f"{where}.evidence 必须是对象")
    _require(
        set(evidence) == {"files", "rows_count", "verdict_runs_count", "completeness"},
        f"{where}.evidence 字段必须是 files/rows_count/verdict_runs_count/completeness",
    )
    _require(isinstance(evidence["files"], list) and evidence["files"], f"{where}.evidence.files 必须是非空数组")
    for index, file_path in enumerate(evidence["files"]):
        _check_str(file_path, f"{where}.evidence.files[{index}]")
        _require(
            not Path(file_path).is_absolute() and ".." not in Path(file_path).parts,
            f"{where}.evidence.files[{index}] 必须是仓库相对路径",
        )
    _check_int(evidence["rows_count"], f"{where}.evidence.rows_count")
    if evidence["verdict_runs_count"] is not None:
        _check_int(evidence["verdict_runs_count"], f"{where}.evidence.verdict_runs_count")

    completeness = evidence["completeness"]
    _require(isinstance(completeness, dict), f"{where}.evidence.completeness 必须是对象")
    _require(
        set(completeness) == {"rows", "verdict", "reflection", "traces"},
        f"{where}.evidence.completeness 字段必须是 rows/verdict/reflection/traces",
    )
    for key, value in completeness.items():
        _require(
            value in _COMPLETENESS_VALUES,
            f"{where}.evidence.completeness.{key} 不在闭集",
        )

    caveats = experiment["caveats"]
    _require(isinstance(caveats, list), f"{where}.caveats 必须是数组")
    _require(len(caveats) == len(set(caveats)), f"{where}.caveats 不能重复")
    unknown = [item for item in caveats if item not in CAVEATS]
    if unknown:
        raise _fail(f"{where}.caveats 含闭集外条目: {', '.join(map(str, unknown))}")

    _scan_sensitive(experiment, where)


def validate_index(document: Any) -> dict[str, Any]:
    """校验一个完整的 index 文档（含 revision 自校验）。"""

    _require(isinstance(document, dict), "index 必须是对象")
    required = {"schema_version", "generated_at", "revision", "experiments", "excluded"}
    missing = sorted(required - set(document))
    if missing:
        raise _fail(f"index 缺少字段: {', '.join(missing)}")
    extra = sorted(set(document) - required)
    if extra:
        raise _fail(f"index 含未知字段: {', '.join(extra)}")
    _require(document["schema_version"] == SCHEMA_VERSION, "index.schema_version 不匹配")
    _check_str(document["generated_at"], "index.generated_at")
    _require(
        isinstance(document["revision"], str) and len(document["revision"]) == 64,
        "index.revision 必须是 64 位十六进制 sha256",
    )
    expected = compute_revision(document)
    _require(document["revision"] == expected, "index.revision 与内容哈希不符")

    experiments = document["experiments"]
    _require(isinstance(experiments, list) and experiments, "index.experiments 必须是非空数组")
    seen: set[str] = set()
    for index, experiment in enumerate(experiments):
        validate_experiment(experiment, f"index.experiments[{index}]", expect_pass_rate=True)
        experiment_id = experiment["experiment_id"]
        _require(experiment_id not in seen, f"index.experiments 重复 id: {experiment_id}")
        seen.add(experiment_id)

    excluded = document["excluded"]
    _require(isinstance(excluded, list), "index.excluded 必须是数组")
    for index, item in enumerate(excluded):
        _require(isinstance(item, dict), f"index.excluded[{index}] 必须是对象")
        _require(
            set(item) == {"experiment_id", "reason"},
            f"index.excluded[{index}] 字段必须是 experiment_id/reason",
        )
        _check_str(item["experiment_id"], f"index.excluded[{index}].experiment_id")
        _check_str(item["reason"], f"index.excluded[{index}].reason")

    _scan_sensitive(document, "index")
    return document


def _load_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise _fail(f"无法读取 {path}: {exc.strerror or exc}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(f"{path} 不是合法 JSON: {exc}") from exc


def _verify_sources(root: Path, sources: Any) -> None:
    _require(isinstance(sources, list) and sources, "manifest.sources 必须是非空数组")
    seen: set[str] = set()
    for index, entry in enumerate(sources):
        where = f"manifest.sources[{index}]"
        _require(isinstance(entry, dict), f"{where} 必须是对象")
        _require(
            set(entry) == {"path", "role", "bytes", "sha256"},
            f"{where} 字段必须是 path/role/bytes/sha256",
        )
        relative = entry["path"]
        _check_str(relative, f"{where}.path")
        _check_str(entry["role"], f"{where}.role")
        _require(relative not in seen, f"{where}.path 重复: {relative}")
        seen.add(relative)
        rel_path = Path(relative)
        _require(not rel_path.is_absolute() and ".." not in rel_path.parts, f"{where}.path 必须是仓库相对路径")
        _check_int(entry["bytes"], f"{where}.bytes", minimum=0)
        _require(
            isinstance(entry["sha256"], str) and len(entry["sha256"]) == 64,
            f"{where}.sha256 必须是 64 位十六进制",
        )
        target = root / rel_path
        if not target.is_file():
            raise _fail(f"源文件缺失: {relative}")
        data = target.read_bytes()
        if len(data) != entry["bytes"]:
            raise _fail(f"源文件字节数不符: {relative}（期望 {entry['bytes']}，实际 {len(data)}）")
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry["sha256"]:
            raise _fail(f"源文件哈希不符: {relative}（期望 {entry['sha256']}，实际 {digest}）")


def _verify_manifest_experiments(root: Path, experiments: Any, sources: set[str]) -> None:
    _require(isinstance(experiments, list) and experiments, "manifest.experiments 必须是非空数组")
    seen: set[str] = set()
    for index, entry in enumerate(experiments):
        where = f"manifest.experiments[{index}]"
        _require(isinstance(entry, dict), f"{where} 必须是对象")
        validate_experiment({k: v for k, v in entry.items() if k != "source_verdict"}, where)
        experiment_id = entry["experiment_id"]
        _require(experiment_id not in seen, f"{where}.experiment_id 重复: {experiment_id}")
        seen.add(experiment_id)
        source_verdict = entry.get("source_verdict")
        if source_verdict is None:
            continue
        _require(
            isinstance(source_verdict, dict) and set(source_verdict) == {"path", "verdict"},
            f"{where}.source_verdict 字段必须是 path/verdict",
        )
        path = source_verdict["path"]
        _require(path in sources, f"{where}.source_verdict.path 未列入 sources: {path}")
        payload = _load_json(root / path)
        _require(isinstance(payload, dict), f"{path} 必须是对象")
        _require(
            payload.get("verdict") == source_verdict["verdict"],
            f"{where}.source_verdict.verdict 与 {path} 不一致",
        )


def load_manifest(root: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    """读取并校验 manifest（含逐文件哈希与 verdict 交叉核对）。"""

    path = Path(manifest_path) if manifest_path is not None else root / "probes" / MANIFEST_NAME
    manifest = _load_json(path)
    _require(isinstance(manifest, dict), "manifest 必须是对象")
    required = {
        "schema_version",
        "generated_at",
        "caveat_catalog",
        "coverage_rules",
        "experiments",
        "excluded",
        "sources",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise _fail(f"manifest 缺少字段: {', '.join(missing)}")
    extra = sorted(set(manifest) - required)
    if extra:
        raise _fail(f"manifest 含未知字段: {', '.join(extra)}")
    _require(
        manifest["schema_version"] == MANIFEST_SCHEMA_VERSION,
        "manifest.schema_version 不匹配",
    )
    _check_str(manifest["generated_at"], "manifest.generated_at")
    catalog = manifest["caveat_catalog"]
    _require(isinstance(catalog, dict), "manifest.caveat_catalog 必须是对象")
    _require(set(catalog) == set(CAVEATS), "manifest.caveat_catalog 必须与闭集完全一致")
    for key, text in catalog.items():
        _require(text == CAVEATS[key], f"manifest.caveat_catalog.{key} 与代码内文案不一致")
    rules = manifest["coverage_rules"]
    _require(isinstance(rules, dict) and rules, "manifest.coverage_rules 必须是非空对象")
    for key, value in rules.items():
        _check_str(value, f"manifest.coverage_rules.{key}")

    _verify_sources(root, manifest["sources"])
    source_paths = {entry["path"] for entry in manifest["sources"]}
    _verify_manifest_experiments(root, manifest["experiments"], source_paths)

    excluded = manifest["excluded"]
    _require(isinstance(excluded, list), "manifest.excluded 必须是数组")
    for index, item in enumerate(excluded):
        _require(
            isinstance(item, dict) and set(item) == {"experiment_id", "reason"},
            f"manifest.excluded[{index}] 字段必须是 experiment_id/reason",
        )
    _scan_sensitive(manifest, "manifest")
    return manifest


def _project(manifest_entry: dict[str, Any]) -> dict[str, Any]:
    """manifest（无 pass_rate）→ 公开投影（metrics.pass_rate 由构建器计算）。"""

    experiment = {key: value for key, value in manifest_entry.items() if key != "source_verdict"}
    metrics = experiment["metrics"]
    experiment["metrics"] = {
        "recorded_runs": metrics["recorded_runs"],
        "passed": metrics["passed"],
        "total": metrics["total"],
        "pass_rate": round(metrics["passed"] / metrics["total"], 4),
        "score_scope": metrics["score_scope"],
        "cost": metrics["cost"],
        "wall_ms": metrics["wall_ms"],
    }
    return experiment


def build_index(root: Path | str, manifest_path: Path | str | None = None) -> dict[str, Any]:
    """从 manifest 构建 index（纯内存；不写文件、不读时钟）。"""

    root_path = Path(root).resolve()
    manifest = load_manifest(
        root_path, Path(manifest_path).resolve() if manifest_path is not None else None
    )
    document: dict[str, Any] = {
        "schema_version": manifest["schema_version"],
        "generated_at": manifest["generated_at"],
        "experiments": [_project(entry) for entry in manifest["experiments"]],
        "excluded": manifest["excluded"],
    }
    document["revision"] = compute_revision(document)
    # 顺序按对外契约：schema_version / generated_at / revision / experiments / excluded
    ordered = {
        "schema_version": document["schema_version"],
        "generated_at": document["generated_at"],
        "revision": document["revision"],
        "experiments": document["experiments"],
        "excluded": document["excluded"],
    }
    return validate_index(ordered)


def load_index(path: Path | str) -> dict[str, Any]:
    """只读加载已导出的 index，并做 revision 自校验。"""

    return validate_index(_load_json(Path(path)))


def dump_index(document: dict[str, Any]) -> bytes:
    """稳定序列化（缩进 2、键排序、结尾换行）。"""

    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def experiments_of(document: dict[str, Any]) -> list[dict[str, Any]]:
    return list(document["experiments"])


def find_experiment(document: dict[str, Any], experiment_id: str) -> dict[str, Any] | None:
    for experiment in document["experiments"]:
        if experiment["experiment_id"] == experiment_id:
            return experiment
    return None


def index_public_view(document: dict[str, Any]) -> dict[str, Any]:
    """``GET /api/experiments`` 的响应体（字段顺序即契约）。"""

    return {
        "schema_version": document["schema_version"],
        "generated_at": document["generated_at"],
        "revision": document["revision"],
        "experiments": experiments_of(document),
    }
