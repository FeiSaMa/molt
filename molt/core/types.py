"""v1 协议记录的数据类校验器。

顶层字段封闭：未知顶层字段一律拒绝；``ext`` 内的未知键原样保留。
本模块只依赖 stdlib（I11）。JSON Schema 校验器在 ``molt.schema``，两者构成双校验。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, TypeVar

from molt.core.errors import ValidationError
from molt.core.hash import is_sha256_hex, tree_hash_from_files

SCHEMA_VERSION_RE = re.compile(r"^1\.\d+\.\d+$")

SCOPES: tuple[str, ...] = ("search", "report")
THINKING_LEVELS: tuple[str, ...] = ("off", "low", "high")
CONTEXT_MODES: tuple[str, ...] = ("full", "minimal")
TOOLS_MODES: tuple[str, ...] = ("bash",)
DIMENSIONS: dict[str, tuple[str, ...]] = {
    "thinking": THINKING_LEVELS,
    "context": CONTEXT_MODES,
    "tools": TOOLS_MODES,
}
SWITCH_KEYS: tuple[str, ...] = tuple(DIMENSIONS)
NETWORK_LEVELS: tuple[str, ...] = ("none", "provider_only", "open")
NETWORK_TO_LEVEL: dict[str, str] = {
    "none": "sandboxed",
    "provider_only": "provider-sandboxed",
    "open": "permissions-only",
}
SEED_CONTROLS: tuple[str, ...] = ("none", "fixed")
OUTCOMES: tuple[str, ...] = (
    "passed",
    "failed",
    "invalid",
    "timeout",
    "budget_exceeded",
    "error",
)
VERDICTS: tuple[str, ...] = ("adopt", "reject", "incomparable", "unstable")
VERDICT_REASONS: dict[str, tuple[str, ...]] = {
    "adopt": ("lower_cost_per_solved", "non_inferior"),
    "reject": ("solved_dropped", "cost_not_lower", "budget_exceeded"),
    "incomparable": ("limits_differ", "zero_denominator"),
    "unstable": ("invalid_ratio_exceeded",),
}

SHELL_FORBIDDEN_KEYS: tuple[str, ...] = (
    "tolerance",
    "cost_threshold",
    "invalid_ratio",
    "repeat_policy",
    "gate",
    "policy",
)

# 已知事件类型 → (允许的 scope, 必需 payload 键)。
EVENT_TYPES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "run.started": (("search",), ("task_id", "genome_id")),
    "run.finished": (("search",), ("task_id", "outcome")),
    "proposal.generated": (("search",), ("proposal_id", "parent_genome_id")),
    "gate.decided": (("search",), ("decision_id", "verdict")),
    "adopt.committed": (("search",), ("archive_id", "generation")),
    "revert.committed": (("search",), ("archive_id", "generation")),
    "budget.exceeded": (("search",), ("cap",)),
    "isolation.degraded": (("search",), ("level", "reason")),
    "holdout.accessed": (("report",), ("task_ids",)),
    "report.scored": (("report",), ("result_id",)),
}

T = TypeVar("T")


def _fail(path: str, message: str) -> None:
    raise ValidationError(f"{path}: {message}")


def _as_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, f"期望 object，实际 {type(value).__name__}")
    return value


def _as_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, f"期望 array，实际 {type(value).__name__}")
    return value


def _as_str(value: Any, path: str, *, non_empty: bool = True) -> str:
    if not isinstance(value, str):
        _fail(path, f"期望 string，实际 {type(value).__name__}")
    if non_empty and not value:
        _fail(path, "不得为空字符串")
    return value


def _as_opt_str(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _as_str(value, path)


def _as_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(path, f"期望 boolean，实际 {type(value).__name__}")
    return value


def _as_int(value: Any, path: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(path, f"期望 integer，实际 {type(value).__name__}")
    if minimum is not None and value < minimum:
        _fail(path, f"必须 >= {minimum}，实际 {value}")
    return value


def _as_number(value: Any, path: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, f"期望 number，实际 {type(value).__name__}")
    number = float(value)
    if minimum is not None and number < minimum:
        _fail(path, f"必须 >= {minimum}，实际 {number}")
    return number


def _as_opt_number(value: Any, path: str) -> float | None:
    if value is None:
        return None
    return _as_number(value, path, minimum=0.0)


def _as_str_list(value: Any, path: str) -> list[str]:
    items = _as_list(value, path)
    result = []
    for index, item in enumerate(items):
        result.append(_as_str(item, f"{path}[{index}]"))
    if len(set(result)) != len(result):
        _fail(path, "不得有重复项")
    return result


def _check_keys(
    data: Mapping[str, Any],
    path: str,
    required: Iterable[str],
    optional: Iterable[str] = (),
    forbidden: Iterable[str] = (),
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    keys = set(data)
    missing = sorted(required_set - keys)
    if missing:
        _fail(path, f"缺少必需字段 {missing}")
    unknown = sorted(keys - allowed)
    if unknown:
        _fail(path, f"未知顶层字段 {unknown}（v1 顶层字段冻结，扩展只能进 ext）")
    blocked = sorted(keys & set(forbidden))
    if blocked:
        _fail(path, f"shell manifest 不得声明门禁相关字段 {blocked}")


def _check_version(data: Mapping[str, Any], path: str) -> str:
    version = _as_str(data.get("schema_version"), f"{path}.schema_version")
    if not SCHEMA_VERSION_RE.match(version):
        _fail(f"{path}.schema_version", f"必须匹配 1.x.y，实际 {version!r}")
    return version


def _as_ext(value: Any, path: str) -> dict[str, Any]:
    return _as_dict(value, path)


def _as_sha256(value: Any, path: str) -> str:
    text = _as_str(value, path)
    if not is_sha256_hex(text):
        _fail(path, "必须是 64 位小写 sha256 十六进制")
    return text


def _rel_path(value: Any, path: str) -> str:
    text = _as_str(value, path)
    normalized = text.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        _fail(path, f"必须是仓库内相对路径，实际 {text!r}")
    return normalized


def _check_iso_timestamp(value: Any, path: str) -> str:
    text = _as_str(value, path)
    if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$", text):
        _fail(path, f"必须是 UTC ISO 8601（...Z），实际 {text!r}")
    return text


# ---------------------------------------------------------------------------
# 嵌套值对象
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Switches:
    """v1 三维开关；第四维或 compaction 一律拒绝。"""

    thinking: str
    context: str
    tools: str

    KEYS: ClassVar[tuple[str, ...]] = SWITCH_KEYS

    def __post_init__(self) -> None:
        for name in SWITCH_KEYS:
            value = getattr(self, name)
            if value not in DIMENSIONS[name]:
                raise ValidationError(
                    f"switches.{name}: 越域 {value!r}，合法值 {list(DIMENSIONS[name])}"
                )

    @classmethod
    def from_dict(cls, data: Any, path: str = "switches") -> Switches:
        obj = _as_dict(data, path)
        _check_keys(obj, path, SWITCH_KEYS)
        return cls(
            thinking=_as_str(obj["thinking"], f"{path}.thinking"),
            context=_as_str(obj["context"], f"{path}.context"),
            tools=_as_str(obj["tools"], f"{path}.tools"),
        )

    def to_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in SWITCH_KEYS}


@dataclass(frozen=True, slots=True)
class Budget:
    """单个 genome 的执行上限；四项必须逐项可比。"""

    tokens: int
    wall_ms: int
    turns: int
    attempts: int

    KEYS: ClassVar[tuple[str, ...]] = ("tokens", "wall_ms", "turns", "attempts")

    @classmethod
    def from_dict(cls, data: Any, path: str = "budget") -> Budget:
        obj = _as_dict(data, path)
        _check_keys(obj, path, cls.KEYS)
        return cls(
            tokens=_as_int(obj["tokens"], f"{path}.tokens", minimum=1),
            wall_ms=_as_int(obj["wall_ms"], f"{path}.wall_ms", minimum=1),
            turns=_as_int(obj["turns"], f"{path}.turns", minimum=1),
            attempts=_as_int(obj["attempts"], f"{path}.attempts", minimum=1),
        )

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.KEYS}


@dataclass(frozen=True, slots=True)
class BudgetCaps:
    """两个不可互相抵消的美元 cap。"""

    execution_budget_usd: float
    search_overhead_budget_usd: float

    @classmethod
    def from_dict(cls, data: Any, path: str) -> BudgetCaps:
        obj = _as_dict(data, path)
        _check_keys(obj, path, ("execution_budget_usd", "search_overhead_budget_usd"))
        return cls(
            execution_budget_usd=_as_number(
                obj["execution_budget_usd"], f"{path}.execution_budget_usd", minimum=0.0
            ),
            search_overhead_budget_usd=_as_number(
                obj["search_overhead_budget_usd"],
                f"{path}.search_overhead_budget_usd",
                minimum=0.0,
            ),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "execution_budget_usd": self.execution_budget_usd,
            "search_overhead_budget_usd": self.search_overhead_budget_usd,
        }


@dataclass(frozen=True, slots=True)
class Policy:
    """门禁阈值的唯一 owner：``scenario.policy``。"""

    k: int
    invalid_ratio_max: float
    budget: BudgetCaps

    @classmethod
    def from_dict(cls, data: Any, path: str = "policy") -> Policy:
        obj = _as_dict(data, path)
        _check_keys(obj, path, ("k", "invalid_ratio_max", "budget"))
        ratio = _as_number(obj["invalid_ratio_max"], f"{path}.invalid_ratio_max", minimum=0.0)
        if ratio > 1.0:
            _fail(f"{path}.invalid_ratio_max", f"必须 <= 1.0，实际 {ratio}")
        return cls(
            k=_as_int(obj["k"], f"{path}.k", minimum=0),
            invalid_ratio_max=ratio,
            budget=BudgetCaps.from_dict(obj["budget"], f"{path}.budget"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "invalid_ratio_max": self.invalid_ratio_max,
            "budget": self.budget.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class Isolation:
    """``isolation.network`` 三档；level 映射写死在 ``NETWORK_TO_LEVEL``。"""

    network: str

    @classmethod
    def from_dict(cls, data: Any, path: str = "isolation") -> Isolation:
        obj = _as_dict(data, path)
        _check_keys(obj, path, ("network",))
        network = _as_str(obj["network"], f"{path}.network")
        if network not in NETWORK_LEVELS:
            _fail(f"{path}.network", f"越域 {network!r}，合法值 {list(NETWORK_LEVELS)}")
        return cls(network=network)

    @property
    def level(self) -> str:
        return NETWORK_TO_LEVEL[self.network]

    def to_dict(self) -> dict[str, str]:
        return {"network": self.network}


@dataclass(frozen=True, slots=True)
class Usage:
    tokens_in: int
    tokens_out: int
    cost_usd: float
    wall_ms: int
    turns: int

    @classmethod
    def from_dict(cls, data: Any, path: str = "usage") -> Usage:
        obj = _as_dict(data, path)
        _check_keys(obj, path, ("tokens_in", "tokens_out", "cost_usd", "wall_ms", "turns"))
        return cls(
            tokens_in=_as_int(obj["tokens_in"], f"{path}.tokens_in", minimum=0),
            tokens_out=_as_int(obj["tokens_out"], f"{path}.tokens_out", minimum=0),
            cost_usd=_as_number(obj["cost_usd"], f"{path}.cost_usd", minimum=0.0),
            wall_ms=_as_int(obj["wall_ms"], f"{path}.wall_ms", minimum=0),
            turns=_as_int(obj["turns"], f"{path}.turns", minimum=0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": self.cost_usd,
            "wall_ms": self.wall_ms,
            "turns": self.turns,
        }


@dataclass(frozen=True, slots=True)
class Verification:
    tests_hash_before: str
    tests_hash_after: str
    matches: bool

    @classmethod
    def from_dict(cls, data: Any, path: str = "verification") -> Verification:
        obj = _as_dict(data, path)
        _check_keys(obj, path, ("tests_hash_before", "tests_hash_after", "matches"))
        return cls(
            tests_hash_before=_as_sha256(obj["tests_hash_before"], f"{path}.tests_hash_before"),
            tests_hash_after=_as_sha256(obj["tests_hash_after"], f"{path}.tests_hash_after"),
            matches=_as_bool(obj["matches"], f"{path}.matches"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tests_hash_before": self.tests_hash_before,
            "tests_hash_after": self.tests_hash_after,
            "matches": self.matches,
        }


@dataclass(frozen=True, slots=True)
class ScoreBlock:
    solved: int
    total: int
    pass_at_1_first_attempt: float
    cost_total_usd: float
    cost_per_solved_usd: float | None
    invalid_ratio: float

    @classmethod
    def from_dict(cls, data: Any, path: str) -> ScoreBlock:
        obj = _as_dict(data, path)
        _check_keys(
            obj,
            path,
            (
                "solved",
                "total",
                "pass_at_1_first_attempt",
                "cost_total_usd",
                "cost_per_solved_usd",
                "invalid_ratio",
            ),
        )
        total = _as_int(obj["total"], f"{path}.total", minimum=0)
        solved = _as_int(obj["solved"], f"{path}.solved", minimum=0)
        if solved > total:
            _fail(f"{path}.solved", f"不得大于 total（{solved} > {total}）")
        pass_at_1 = _as_number(obj["pass_at_1_first_attempt"], f"{path}.pass_at_1_first_attempt")
        expected = solved / total if total else 0.0
        if abs(pass_at_1 - expected) > 1e-9:
            _fail(
                f"{path}.pass_at_1_first_attempt",
                f"与 solved/total 不一致：{pass_at_1} != {expected}",
            )
        ratio = _as_number(obj["invalid_ratio"], f"{path}.invalid_ratio", minimum=0.0)
        if ratio > 1.0:
            _fail(f"{path}.invalid_ratio", f"必须 <= 1.0，实际 {ratio}")
        total_cost = _as_number(obj["cost_total_usd"], f"{path}.cost_total_usd", minimum=0.0)
        cost_per = _as_opt_number(obj["cost_per_solved_usd"], f"{path}.cost_per_solved_usd")
        if solved == 0:
            if cost_per is not None:
                _fail(f"{path}.cost_per_solved_usd", "分母为 0 时必须为 null")
        else:
            expected_cost = total_cost / solved
            if cost_per is None or abs(cost_per - expected_cost) > 1e-9:
                _fail(
                    f"{path}.cost_per_solved_usd",
                    f"与 cost_total_usd/solved 不一致：{cost_per} != {expected_cost}",
                )
        return cls(
            solved=solved,
            total=total,
            pass_at_1_first_attempt=pass_at_1,
            cost_total_usd=total_cost,
            cost_per_solved_usd=cost_per,
            invalid_ratio=ratio,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "solved": self.solved,
            "total": self.total,
            "pass_at_1_first_attempt": self.pass_at_1_first_attempt,
            "cost_total_usd": self.cost_total_usd,
            "cost_per_solved_usd": self.cost_per_solved_usd,
            "invalid_ratio": self.invalid_ratio,
        }


# ---------------------------------------------------------------------------
# 11 类记录
# ---------------------------------------------------------------------------


class _Record:
    record_type: ClassVar[str] = ""

    @classmethod
    def from_dict(cls, data: Any) -> Any:  # pragma: no cover - 由子类实现
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:  # pragma: no cover - 由子类实现
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class Scenario(_Record):
    schema_version: str
    scenario_version: str
    scenario_id: str
    title: str
    shell_id: str
    shell_version: str
    bench_hash: str
    task_sets: dict[str, list[str]]
    policy: Policy
    isolation: Isolation
    ext: dict[str, Any]

    record_type: ClassVar[str] = "scenario"

    @classmethod
    def from_dict(cls, data: Any) -> Scenario:
        path = "scenario"
        obj = _as_dict(data, path)
        _check_keys(
            obj,
            path,
            (
                "schema_version",
                "scenario_version",
                "scenario_id",
                "title",
                "shell_id",
                "shell_version",
                "bench_hash",
                "task_sets",
                "policy",
                "isolation",
                "ext",
            ),
        )
        version = _check_version(obj, path)
        sets_obj = _as_dict(obj["task_sets"], f"{path}.task_sets")
        _check_keys(sets_obj, f"{path}.task_sets", SCOPES)
        task_sets = {
            scope: _as_str_list(sets_obj[scope], f"{path}.task_sets.{scope}") for scope in SCOPES
        }
        overlap = set(task_sets["search"]) & set(task_sets["report"])
        if overlap:
            _fail(f"{path}.task_sets", f"search 与 report 题集不得重叠：{sorted(overlap)}")
        return cls(
            schema_version=version,
            scenario_version=_as_str(obj["scenario_version"], f"{path}.scenario_version"),
            scenario_id=_as_str(obj["scenario_id"], f"{path}.scenario_id"),
            title=_as_str(obj["title"], f"{path}.title"),
            shell_id=_as_str(obj["shell_id"], f"{path}.shell_id"),
            shell_version=_as_str(obj["shell_version"], f"{path}.shell_version"),
            bench_hash=_as_sha256(obj["bench_hash"], f"{path}.bench_hash"),
            task_sets=task_sets,
            policy=Policy.from_dict(obj["policy"], f"{path}.policy"),
            isolation=Isolation.from_dict(obj["isolation"], f"{path}.isolation"),
            ext=_as_ext(obj["ext"], f"{path}.ext"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scenario_version": self.scenario_version,
            "scenario_id": self.scenario_id,
            "title": self.title,
            "shell_id": self.shell_id,
            "shell_version": self.shell_version,
            "bench_hash": self.bench_hash,
            "task_sets": {scope: list(self.task_sets[scope]) for scope in SCOPES},
            "policy": self.policy.to_dict(),
            "isolation": self.isolation.to_dict(),
            "ext": dict(self.ext),
        }


@dataclass(frozen=True, slots=True)
class ShellManifest(_Record):
    schema_version: str
    shell_id: str
    shell_version: str
    name: str
    artifact_roots: list[str]
    entrypoints: dict[str, str]
    capabilities: dict[str, Any]
    ext: dict[str, Any]

    record_type: ClassVar[str] = "shell_manifest"
    ENTRYPOINTS: ClassVar[tuple[str, ...]] = ("harness", "verifier", "task_provider")
    CAPABILITIES: ClassVar[tuple[str, ...]] = (
        "switch_domains",
        "max_execution_budget_usd",
        "max_wall_ms",
        "max_turns",
        "max_attempts",
    )

    @classmethod
    def from_dict(cls, data: Any) -> ShellManifest:
        path = "shell_manifest"
        obj = _as_dict(data, path)
        _check_keys(
            obj,
            path,
            (
                "schema_version",
                "shell_id",
                "shell_version",
                "name",
                "artifact_roots",
                "entrypoints",
                "capabilities",
                "ext",
            ),
            forbidden=SHELL_FORBIDDEN_KEYS,
        )
        version = _check_version(obj, path)
        roots = [
            _rel_path(item, f"{path}.artifact_roots")
            for item in _as_str_list(obj["artifact_roots"], f"{path}.artifact_roots")
        ]
        entrypoints = _as_dict(obj["entrypoints"], f"{path}.entrypoints")
        _check_keys(entrypoints, f"{path}.entrypoints", cls.ENTRYPOINTS)
        caps = _as_dict(obj["capabilities"], f"{path}.capabilities")
        _check_keys(
            caps,
            f"{path}.capabilities",
            ("switch_domains", "max_execution_budget_usd"),
            optional=("max_wall_ms", "max_turns", "max_attempts"),
            forbidden=SHELL_FORBIDDEN_KEYS,
        )
        domains = _as_dict(caps["switch_domains"], f"{path}.capabilities.switch_domains")
        _check_keys(domains, f"{path}.capabilities.switch_domains", SWITCH_KEYS)
        parsed_domains: dict[str, list[str]] = {}
        for name in SWITCH_KEYS:
            values = _as_str_list(domains[name], f"{path}.capabilities.switch_domains.{name}")
            if not set(values) <= set(DIMENSIONS[name]):
                _fail(
                    f"{path}.capabilities.switch_domains.{name}",
                    f"含 v1 未定义取值 {sorted(set(values) - set(DIMENSIONS[name]))}",
                )
            parsed_domains[name] = values
        parsed_caps: dict[str, Any] = {
            "switch_domains": parsed_domains,
            "max_execution_budget_usd": _as_number(
                caps["max_execution_budget_usd"],
                f"{path}.capabilities.max_execution_budget_usd",
                minimum=0.0,
            ),
        }
        for optional in ("max_wall_ms", "max_turns", "max_attempts"):
            if optional in caps:
                parsed_caps[optional] = _as_int(
                    caps[optional], f"{path}.capabilities.{optional}", minimum=1
                )
        return cls(
            schema_version=version,
            shell_id=_as_str(obj["shell_id"], f"{path}.shell_id"),
            shell_version=_as_str(obj["shell_version"], f"{path}.shell_version"),
            name=_as_str(obj["name"], f"{path}.name"),
            artifact_roots=roots,
            entrypoints={
                key: _rel_path(entrypoints[key], f"{path}.entrypoints.{key}")
                for key in cls.ENTRYPOINTS
            },
            capabilities=parsed_caps,
            ext=_as_ext(obj["ext"], f"{path}.ext"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "shell_id": self.shell_id,
            "shell_version": self.shell_version,
            "name": self.name,
            "artifact_roots": list(self.artifact_roots),
            "entrypoints": dict(self.entrypoints),
            "capabilities": {
                "switch_domains": {
                    name: list(values)
                    for name, values in self.capabilities["switch_domains"].items()
                },
                **{
                    key: self.capabilities[key]
                    for key in self.capabilities
                    if key != "switch_domains"
                },
            },
            "ext": dict(self.ext),
        }


@dataclass(frozen=True, slots=True)
class Genome(_Record):
    schema_version: str
    scenario_version: str
    genome_id: str
    parent_id: str | None
    shell_id: str
    shell_version: str
    switches: Switches
    prompt_ref: str
    tools_ref: str
    skills_ref: list[str]
    runtime: dict[str, str]
    budget: Budget
    artifacts: dict[str, str]
    ext: dict[str, Any]

    record_type: ClassVar[str] = "genome"

    @classmethod
    def from_dict(cls, data: Any) -> Genome:
        path = "genome"
        obj = _as_dict(data, path)
        _check_keys(
            obj,
            path,
            (
                "schema_version",
                "scenario_version",
                "genome_id",
                "parent_id",
                "shell_id",
                "shell_version",
                "switches",
                "prompt_ref",
                "tools_ref",
                "skills_ref",
                "runtime",
                "budget",
                "artifacts",
                "ext",
            ),
        )
        version = _check_version(obj, path)
        skills = _as_str_list(obj["skills_ref"], f"{path}.skills_ref")
        if skills:
            _fail(f"{path}.skills_ref", "v1 恒为空；skills 维度留给后续 major")
        runtime_obj = _as_dict(obj["runtime"], f"{path}.runtime")
        _check_keys(runtime_obj, f"{path}.runtime", ("runtime_id", "version"))
        artifacts_obj = _as_dict(obj["artifacts"], f"{path}.artifacts")
        if not artifacts_obj:
            _fail(f"{path}.artifacts", "必须内嵌被引用 artifact 的内容哈希")
        artifacts: dict[str, str] = {}
        for key, value in artifacts_obj.items():
            artifacts[_rel_path(key, f"{path}.artifacts")] = _as_sha256(
                value, f"{path}.artifacts[{key}]"
            )
        return cls(
            schema_version=version,
            scenario_version=_as_str(obj["scenario_version"], f"{path}.scenario_version"),
            genome_id=_as_str(obj["genome_id"], f"{path}.genome_id"),
            parent_id=_as_opt_str(obj["parent_id"], f"{path}.parent_id"),
            shell_id=_as_str(obj["shell_id"], f"{path}.shell_id"),
            shell_version=_as_str(obj["shell_version"], f"{path}.shell_version"),
            switches=Switches.from_dict(obj["switches"], f"{path}.switches"),
            prompt_ref=_rel_path(obj["prompt_ref"], f"{path}.prompt_ref"),
            tools_ref=_rel_path(obj["tools_ref"], f"{path}.tools_ref"),
            skills_ref=skills,
            runtime={
                "runtime_id": _as_str(runtime_obj["runtime_id"], f"{path}.runtime.runtime_id"),
                "version": _as_str(runtime_obj["version"], f"{path}.runtime.version"),
            },
            budget=Budget.from_dict(obj["budget"], f"{path}.budget"),
            artifacts=artifacts,
            ext=_as_ext(obj["ext"], f"{path}.ext"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scenario_version": self.scenario_version,
            "genome_id": self.genome_id,
            "parent_id": self.parent_id,
            "shell_id": self.shell_id,
            "shell_version": self.shell_version,
            "switches": self.switches.to_dict(),
            "prompt_ref": self.prompt_ref,
            "tools_ref": self.tools_ref,
            "skills_ref": list(self.skills_ref),
            "runtime": dict(self.runtime),
            "budget": self.budget.to_dict(),
            "artifacts": dict(self.artifacts),
            "ext": dict(self.ext),
        }


@dataclass(frozen=True, slots=True)
class Task(_Record):
    schema_version: str
    task_id: str
    scenario_version: str
    shell_id: str
    scope: str
    title: str
    workspace_ref: str
    tests_ref: str
    timeout_s: float
    ext: dict[str, Any]

    record_type: ClassVar[str] = "task"

    @classmethod
    def from_dict(cls, data: Any) -> Task:
        path = "task"
        obj = _as_dict(data, path)
        _check_keys(
            obj,
            path,
            (
                "schema_version",
                "task_id",
                "scenario_version",
                "shell_id",
                "scope",
                "title",
                "workspace_ref",
                "tests_ref",
                "timeout_s",
                "ext",
            ),
        )
        version = _check_version(obj, path)
        scope = _as_str(obj["scope"], f"{path}.scope")
        if scope not in SCOPES:
            _fail(f"{path}.scope", f"越域 {scope!r}，合法值 {list(SCOPES)}")
        timeout = _as_number(obj["timeout_s"], f"{path}.timeout_s", minimum=0.0)
        if timeout <= 0:
            _fail(f"{path}.timeout_s", "必须 > 0")
        return cls(
            schema_version=version,
            task_id=_as_str(obj["task_id"], f"{path}.task_id"),
            scenario_version=_as_str(obj["scenario_version"], f"{path}.scenario_version"),
            shell_id=_as_str(obj["shell_id"], f"{path}.shell_id"),
            scope=scope,
            title=_as_str(obj["title"], f"{path}.title"),
            workspace_ref=_rel_path(obj["workspace_ref"], f"{path}.workspace_ref"),
            tests_ref=_rel_path(obj["tests_ref"], f"{path}.tests_ref"),
            timeout_s=timeout,
            ext=_as_ext(obj["ext"], f"{path}.ext"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "scenario_version": self.scenario_version,
            "shell_id": self.shell_id,
            "scope": self.scope,
            "title": self.title,
            "workspace_ref": self.workspace_ref,
            "tests_ref": self.tests_ref,
            "timeout_s": self.timeout_s,
            "ext": dict(self.ext),
        }


@dataclass(frozen=True, slots=True)
class Run(_Record):
    schema_version: str
    run_id: str
    scenario_version: str
    genome_id: str
    task_id: str
    scope: str
    attempt: int
    shell_version: str
    verifier_hash: str
    seed_control: str
    started_at: str
    finished_at: str
    outcome: str
    usage: Usage
    verification: Verification
    artifacts: dict[str, str]
    ext: dict[str, Any]

    record_type: ClassVar[str] = "run"

    @classmethod
    def from_dict(cls, data: Any) -> Run:
        path = "run"
        obj = _as_dict(data, path)
        _check_keys(
            obj,
            path,
            (
                "schema_version",
                "run_id",
                "scenario_version",
                "genome_id",
                "task_id",
                "scope",
                "attempt",
                "shell_version",
                "verifier_hash",
                "seed_control",
                "started_at",
                "finished_at",
                "outcome",
                "usage",
                "verification",
                "artifacts",
                "ext",
            ),
        )
        version = _check_version(obj, path)
        scope = _as_str(obj["scope"], f"{path}.scope")
        if scope not in SCOPES:
            _fail(f"{path}.scope", f"越域 {scope!r}，合法值 {list(SCOPES)}")
        outcome = _as_str(obj["outcome"], f"{path}.outcome")
        if outcome not in OUTCOMES:
            _fail(f"{path}.outcome", f"越域 {outcome!r}，合法值 {list(OUTCOMES)}")
        seed_control = _as_str(obj["seed_control"], f"{path}.seed_control")
        if seed_control not in SEED_CONTROLS:
            _fail(f"{path}.seed_control", f"越域 {seed_control!r}，合法值 {list(SEED_CONTROLS)}")
        verification = Verification.from_dict(obj["verification"], f"{path}.verification")
        if not verification.matches and outcome != "invalid":
            _fail(
                f"{path}.outcome",
                "测试目录哈希前后不一致时必须判废（outcome=invalid）",
            )
        artifacts_obj = _as_dict(obj["artifacts"], f"{path}.artifacts")
        artifacts = {
            _rel_path(key, f"{path}.artifacts"): _as_sha256(value, f"{path}.artifacts[{key}]")
            for key, value in artifacts_obj.items()
        }
        return cls(
            schema_version=version,
            run_id=_as_str(obj["run_id"], f"{path}.run_id"),
            scenario_version=_as_str(obj["scenario_version"], f"{path}.scenario_version"),
            genome_id=_as_str(obj["genome_id"], f"{path}.genome_id"),
            task_id=_as_str(obj["task_id"], f"{path}.task_id"),
            scope=scope,
            attempt=_as_int(obj["attempt"], f"{path}.attempt", minimum=1),
            shell_version=_as_str(obj["shell_version"], f"{path}.shell_version"),
            verifier_hash=_as_sha256(obj["verifier_hash"], f"{path}.verifier_hash"),
            seed_control=seed_control,
            started_at=_check_iso_timestamp(obj["started_at"], f"{path}.started_at"),
            finished_at=_check_iso_timestamp(obj["finished_at"], f"{path}.finished_at"),
            outcome=outcome,
            usage=Usage.from_dict(obj["usage"], f"{path}.usage"),
            verification=verification,
            artifacts=artifacts,
            ext=_as_ext(obj["ext"], f"{path}.ext"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "scenario_version": self.scenario_version,
            "genome_id": self.genome_id,
            "task_id": self.task_id,
            "scope": self.scope,
            "attempt": self.attempt,
            "shell_version": self.shell_version,
            "verifier_hash": self.verifier_hash,
            "seed_control": self.seed_control,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "outcome": self.outcome,
            "usage": self.usage.to_dict(),
            "verification": self.verification.to_dict(),
            "artifacts": dict(self.artifacts),
            "ext": dict(self.ext),
        }


@dataclass(frozen=True, slots=True)
class Result(_Record):
    schema_version: str
    result_id: str
    scenario_version: str
    genome_id: str
    scope: str
    seed_control: str
    run_refs: list[str]
    scores: dict[str, ScoreBlock]
    generated_at: str
    ext: dict[str, Any]

    record_type: ClassVar[str] = "result"

    @classmethod
    def from_dict(cls, data: Any) -> Result:
        path = "result"
        obj = _as_dict(data, path)
        _check_keys(
            obj,
            path,
            (
                "schema_version",
                "result_id",
                "scenario_version",
                "genome_id",
                "scope",
                "seed_control",
                "run_refs",
                "scores",
                "generated_at",
                "ext",
            ),
        )
        version = _check_version(obj, path)
        scope = _as_str(obj["scope"], f"{path}.scope")
        if scope not in SCOPES:
            _fail(f"{path}.scope", f"越域 {scope!r}，合法值 {list(SCOPES)}")
        seed_control = _as_str(obj["seed_control"], f"{path}.seed_control")
        if seed_control not in SEED_CONTROLS:
            _fail(f"{path}.seed_control", f"越域 {seed_control!r}")
        scores_obj = _as_dict(obj["scores"], f"{path}.scores")
        expected_keys = ("search", "holdout") if scope == "report" else ("search",)
        _check_keys(scores_obj, f"{path}.scores", expected_keys)
        scores = {
            key: ScoreBlock.from_dict(scores_obj[key], f"{path}.scores.{key}")
            for key in expected_keys
        }
        return cls(
            schema_version=version,
            result_id=_as_str(obj["result_id"], f"{path}.result_id"),
            scenario_version=_as_str(obj["scenario_version"], f"{path}.scenario_version"),
            genome_id=_as_str(obj["genome_id"], f"{path}.genome_id"),
            scope=scope,
            seed_control=seed_control,
            run_refs=_as_str_list(obj["run_refs"], f"{path}.run_refs"),
            scores=scores,
            generated_at=_check_iso_timestamp(obj["generated_at"], f"{path}.generated_at"),
            ext=_as_ext(obj["ext"], f"{path}.ext"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "result_id": self.result_id,
            "scenario_version": self.scenario_version,
            "genome_id": self.genome_id,
            "scope": self.scope,
            "seed_control": self.seed_control,
            "run_refs": list(self.run_refs),
            "scores": {key: value.to_dict() for key, value in self.scores.items()},
            "generated_at": self.generated_at,
            "ext": dict(self.ext),
        }


@dataclass(frozen=True, slots=True)
class GateDecision(_Record):
    schema_version: str
    decision_id: str
    scenario_version: str
    shell_version: str
    verifier_hash: str
    baseline_genome_id: str
    candidate_genome_id: str
    verdict: str
    reason: str
    k: int
    limits_identical: bool
    budget_check: dict[str, Any]
    invalid_ratio: dict[str, float]
    scores: dict[str, dict[str, Any]]
    paired: list[dict[str, Any]]
    created_at: str
    ext: dict[str, Any]

    record_type: ClassVar[str] = "gate_decision"

    @classmethod
    def from_dict(cls, data: Any) -> GateDecision:
        path = "gate_decision"
        obj = _as_dict(data, path)
        _check_keys(
            obj,
            path,
            (
                "schema_version",
                "decision_id",
                "scenario_version",
                "shell_version",
                "verifier_hash",
                "baseline_genome_id",
                "candidate_genome_id",
                "verdict",
                "reason",
                "k",
                "limits_identical",
                "budget_check",
                "invalid_ratio",
                "scores",
                "paired",
                "created_at",
                "ext",
            ),
        )
        version = _check_version(obj, path)
        verdict = _as_str(obj["verdict"], f"{path}.verdict")
        if verdict not in VERDICTS:
            _fail(f"{path}.verdict", f"越域 {verdict!r}，合法值 {list(VERDICTS)}")
        reason = _as_str(obj["reason"], f"{path}.reason")
        if reason not in VERDICT_REASONS[verdict]:
            _fail(
                f"{path}.reason",
                f"verdict={verdict} 不允许 reason={reason!r}，合法值 {list(VERDICT_REASONS[verdict])}",
            )
        budget_obj = _as_dict(obj["budget_check"], f"{path}.budget_check")
        _check_keys(
            budget_obj,
            f"{path}.budget_check",
            ("execution_exceeded", "overhead_exceeded", "reason"),
        )
        budget_check = {
            "execution_exceeded": _as_bool(
                budget_obj["execution_exceeded"], f"{path}.budget_check.execution_exceeded"
            ),
            "overhead_exceeded": _as_bool(
                budget_obj["overhead_exceeded"], f"{path}.budget_check.overhead_exceeded"
            ),
            "reason": _as_str(budget_obj["reason"], f"{path}.budget_check.reason"),
        }
        invalid_obj = _as_dict(obj["invalid_ratio"], f"{path}.invalid_ratio")
        _check_keys(invalid_obj, f"{path}.invalid_ratio", ("baseline", "candidate", "threshold"))
        invalid_ratio = {
            "baseline": _as_number(invalid_obj["baseline"], f"{path}.invalid_ratio.baseline"),
            "candidate": _as_number(invalid_obj["candidate"], f"{path}.invalid_ratio.candidate"),
            "threshold": _as_number(invalid_obj["threshold"], f"{path}.invalid_ratio.threshold"),
        }
        for key, value in invalid_ratio.items():
            if not 0.0 <= value <= 1.0:
                _fail(f"{path}.invalid_ratio.{key}", f"必须位于 [0,1]，实际 {value}")

        paired_raw = _as_list(obj["paired"], f"{path}.paired")
        if not paired_raw:
            _fail(f"{path}.paired", "结论必须携带逐题配对表，聚合数字不得单独出现")
        paired: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, item in enumerate(paired_raw):
            item_path = f"{path}.paired[{index}]"
            item_obj = _as_dict(item, item_path)
            _check_keys(
                item_obj,
                item_path,
                (
                    "task_id",
                    "baseline_passed",
                    "candidate_passed",
                    "baseline_cost_usd",
                    "candidate_cost_usd",
                ),
            )
            task_id = _as_str(item_obj["task_id"], f"{item_path}.task_id")
            if task_id in seen:
                _fail(item_path, f"重复 task_id {task_id!r}")
            seen.add(task_id)
            paired.append(
                {
                    "task_id": task_id,
                    "baseline_passed": _as_bool(
                        item_obj["baseline_passed"], f"{item_path}.baseline_passed"
                    ),
                    "candidate_passed": _as_bool(
                        item_obj["candidate_passed"], f"{item_path}.candidate_passed"
                    ),
                    "baseline_cost_usd": _as_number(
                        item_obj["baseline_cost_usd"], f"{item_path}.baseline_cost_usd", minimum=0.0
                    ),
                    "candidate_cost_usd": _as_number(
                        item_obj["candidate_cost_usd"],
                        f"{item_path}.candidate_cost_usd",
                        minimum=0.0,
                    ),
                }
            )

        scores_obj = _as_dict(obj["scores"], f"{path}.scores")
        _check_keys(scores_obj, f"{path}.scores", ("baseline", "candidate"))
        scores: dict[str, dict[str, Any]] = {}
        for side in ("baseline", "candidate"):
            side_obj = _as_dict(scores_obj[side], f"{path}.scores.{side}")
            _check_keys(
                side_obj,
                f"{path}.scores.{side}",
                ("genome_id", "solved", "total", "cost_per_solved_usd"),
            )
            scores[side] = {
                "genome_id": _as_str(side_obj["genome_id"], f"{path}.scores.{side}.genome_id"),
                "solved": _as_int(side_obj["solved"], f"{path}.scores.{side}.solved", minimum=0),
                "total": _as_int(side_obj["total"], f"{path}.scores.{side}.total", minimum=0),
                "cost_per_solved_usd": _as_opt_number(
                    side_obj["cost_per_solved_usd"],
                    f"{path}.scores.{side}.cost_per_solved_usd",
                ),
            }
            solved = scores[side]["solved"]
            total = scores[side]["total"]
            if solved > total:
                _fail(f"{path}.scores.{side}.solved", "不得大于 total")
            if solved == 0 and scores[side]["cost_per_solved_usd"] is not None:
                _fail(f"{path}.scores.{side}.cost_per_solved_usd", "分母为 0 时必须为 null")
            passed = sum(1 for row in paired if row[f"{side}_passed"])
            if solved != passed or total != len(paired):
                _fail(
                    f"{path}.scores.{side}",
                    f"与配对表不一致：solved={solved} passed={passed} total={total}",
                )
            cost_total = sum(row[f"{side}_cost_usd"] for row in paired)
            cost_per = scores[side]["cost_per_solved_usd"]
            if solved:
                expected = cost_total / solved
                if cost_per is None or abs(cost_per - expected) > 1e-9:
                    _fail(
                        f"{path}.scores.{side}.cost_per_solved_usd",
                        f"与配对表成本不一致：{cost_per} != {expected}",
                    )

        return cls(
            schema_version=version,
            decision_id=_as_str(obj["decision_id"], f"{path}.decision_id"),
            scenario_version=_as_str(obj["scenario_version"], f"{path}.scenario_version"),
            shell_version=_as_str(obj["shell_version"], f"{path}.shell_version"),
            verifier_hash=_as_sha256(obj["verifier_hash"], f"{path}.verifier_hash"),
            baseline_genome_id=_as_str(obj["baseline_genome_id"], f"{path}.baseline_genome_id"),
            candidate_genome_id=_as_str(obj["candidate_genome_id"], f"{path}.candidate_genome_id"),
            verdict=verdict,
            reason=reason,
            k=_as_int(obj["k"], f"{path}.k", minimum=0),
            limits_identical=_as_bool(obj["limits_identical"], f"{path}.limits_identical"),
            budget_check=budget_check,
            invalid_ratio=invalid_ratio,
            scores=scores,
            paired=paired,
            created_at=_check_iso_timestamp(obj["created_at"], f"{path}.created_at"),
            ext=_as_ext(obj["ext"], f"{path}.ext"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "scenario_version": self.scenario_version,
            "shell_version": self.shell_version,
            "verifier_hash": self.verifier_hash,
            "baseline_genome_id": self.baseline_genome_id,
            "candidate_genome_id": self.candidate_genome_id,
            "verdict": self.verdict,
            "reason": self.reason,
            "k": self.k,
            "limits_identical": self.limits_identical,
            "budget_check": dict(self.budget_check),
            "invalid_ratio": dict(self.invalid_ratio),
            "scores": {key: dict(value) for key, value in self.scores.items()},
            "paired": [dict(row) for row in self.paired],
            "created_at": self.created_at,
            "ext": dict(self.ext),
        }


@dataclass(frozen=True, slots=True)
class Snapshot(_Record):
    schema_version: str
    snapshot_id: str
    generation: int
    created_at: str
    roots: list[str]
    tree_hash: str
    files: dict[str, dict[str, str]]
    ext: dict[str, Any]

    record_type: ClassVar[str] = "snapshot"

    @classmethod
    def from_dict(cls, data: Any) -> Snapshot:
        path = "snapshot"
        obj = _as_dict(data, path)
        _check_keys(
            obj,
            path,
            (
                "schema_version",
                "snapshot_id",
                "generation",
                "created_at",
                "roots",
                "tree_hash",
                "files",
                "ext",
            ),
        )
        version = _check_version(obj, path)
        roots = _as_str_list(obj["roots"], f"{path}.roots")
        files_obj = _as_dict(obj["files"], f"{path}.files")
        if set(files_obj) != set(roots):
            _fail(
                f"{path}.files",
                f"roots 与文件哈希必须同键，缺 {sorted(set(roots) - set(files_obj))}",
            )
        files: dict[str, dict[str, str]] = {}
        flat: dict[str, str] = {}
        for root in roots:
            root_files = _as_dict(files_obj[root], f"{path}.files.{root}")
            if not root_files:
                _fail(f"{path}.files.{root}", "root 内至少需要一个文件")
            files[root] = {}
            for rel, digest in root_files.items():
                clean = _rel_path(rel, f"{path}.files.{root}")
                files[root][clean] = _as_sha256(digest, f"{path}.files.{root}[{rel}]")
                flat[f"{root}/{clean}"] = files[root][clean]
        expected = tree_hash_from_files(flat)
        declared = _as_sha256(obj["tree_hash"], f"{path}.tree_hash")
        if declared != expected:
            _fail(f"{path}.tree_hash", f"与 files 不一致：{declared} != {expected}")
        return cls(
            schema_version=version,
            snapshot_id=_as_str(obj["snapshot_id"], f"{path}.snapshot_id"),
            generation=_as_int(obj["generation"], f"{path}.generation", minimum=1),
            created_at=_check_iso_timestamp(obj["created_at"], f"{path}.created_at"),
            roots=roots,
            tree_hash=declared,
            files=files,
            ext=_as_ext(obj["ext"], f"{path}.ext"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "generation": self.generation,
            "created_at": self.created_at,
            "roots": list(self.roots),
            "tree_hash": self.tree_hash,
            "files": {root: dict(files) for root, files in self.files.items()},
            "ext": dict(self.ext),
        }


@dataclass(frozen=True, slots=True)
class Archive(_Record):
    schema_version: str
    archive_id: str
    generation: int
    genome_id: str
    parent_id: str | None
    adopted_at: str
    snapshot_id: str
    result_refs: list[str]
    note: str
    ext: dict[str, Any]

    record_type: ClassVar[str] = "archive"
    FORBIDDEN: ClassVar[tuple[str, ...]] = (
        "scores",
        "metrics",
        "search",
        "holdout",
        "cost_per_solved_usd",
    )

    @classmethod
    def from_dict(cls, data: Any) -> Archive:
        path = "archive"
        obj = _as_dict(data, path)
        _check_keys(
            obj,
            path,
            (
                "schema_version",
                "archive_id",
                "generation",
                "genome_id",
                "parent_id",
                "adopted_at",
                "snapshot_id",
                "result_refs",
                "note",
                "ext",
            ),
            forbidden=cls.FORBIDDEN,
        )
        version = _check_version(obj, path)
        return cls(
            schema_version=version,
            archive_id=_as_str(obj["archive_id"], f"{path}.archive_id"),
            generation=_as_int(obj["generation"], f"{path}.generation", minimum=1),
            genome_id=_as_str(obj["genome_id"], f"{path}.genome_id"),
            parent_id=_as_opt_str(obj["parent_id"], f"{path}.parent_id"),
            adopted_at=_check_iso_timestamp(obj["adopted_at"], f"{path}.adopted_at"),
            snapshot_id=_as_str(obj["snapshot_id"], f"{path}.snapshot_id"),
            result_refs=_as_str_list(obj["result_refs"], f"{path}.result_refs"),
            note=_as_str(obj["note"], f"{path}.note", non_empty=False),
            ext=_as_ext(obj["ext"], f"{path}.ext"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "archive_id": self.archive_id,
            "generation": self.generation,
            "genome_id": self.genome_id,
            "parent_id": self.parent_id,
            "adopted_at": self.adopted_at,
            "snapshot_id": self.snapshot_id,
            "result_refs": list(self.result_refs),
            "note": self.note,
            "ext": dict(self.ext),
        }


@dataclass(frozen=True, slots=True)
class Event(_Record):
    schema_version: str
    event_id: str
    scope: str
    type: str
    ts: str
    run_id: str | None
    payload: dict[str, Any]
    ext: dict[str, Any]

    record_type: ClassVar[str] = "event"

    @classmethod
    def from_dict(cls, data: Any) -> Event:
        path = "event"
        obj = _as_dict(data, path)
        _check_keys(
            obj,
            path,
            ("schema_version", "event_id", "scope", "type", "ts", "run_id", "payload", "ext"),
        )
        version = _check_version(obj, path)
        scope = _as_str(obj["scope"], f"{path}.scope")
        if scope not in SCOPES:
            _fail(f"{path}.scope", f"越域 {scope!r}，合法值 {list(SCOPES)}")
        event_type = _as_str(obj["type"], f"{path}.type")
        payload = _as_dict(obj["payload"], f"{path}.payload")
        known = EVENT_TYPES.get(event_type)
        if known is not None:
            allowed_scopes, required_payload = known
            if scope not in allowed_scopes:
                _fail(
                    f"{path}.scope",
                    f"事件 {event_type!r} 只允许出现在 {list(allowed_scopes)} 流，实际 {scope!r}",
                )
            _check_keys(payload, f"{path}.payload", required_payload)
        return cls(
            schema_version=version,
            event_id=_as_str(obj["event_id"], f"{path}.event_id"),
            scope=scope,
            type=event_type,
            ts=_check_iso_timestamp(obj["ts"], f"{path}.ts"),
            run_id=_as_opt_str(obj["run_id"], f"{path}.run_id"),
            payload=payload,
            ext=_as_ext(obj["ext"], f"{path}.ext"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "scope": self.scope,
            "type": self.type,
            "ts": self.ts,
            "run_id": self.run_id,
            "payload": dict(self.payload),
            "ext": dict(self.ext),
        }


@dataclass(frozen=True, slots=True)
class Proposal(_Record):
    schema_version: str
    proposal_id: str
    scenario_version: str
    proposer_id: str
    parent_id: str
    switches: Switches
    rationale: str
    created_at: str
    cost_usd: float
    ext: dict[str, Any]

    record_type: ClassVar[str] = "proposal"

    @classmethod
    def from_dict(cls, data: Any) -> Proposal:
        path = "proposal"
        obj = _as_dict(data, path)
        _check_keys(
            obj,
            path,
            (
                "schema_version",
                "proposal_id",
                "scenario_version",
                "proposer_id",
                "parent_id",
                "switches",
                "rationale",
                "created_at",
                "cost_usd",
                "ext",
            ),
        )
        version = _check_version(obj, path)
        return cls(
            schema_version=version,
            proposal_id=_as_str(obj["proposal_id"], f"{path}.proposal_id"),
            scenario_version=_as_str(obj["scenario_version"], f"{path}.scenario_version"),
            proposer_id=_as_str(obj["proposer_id"], f"{path}.proposer_id"),
            parent_id=_as_str(obj["parent_id"], f"{path}.parent_id"),
            switches=Switches.from_dict(obj["switches"], f"{path}.switches"),
            rationale=_as_str(obj["rationale"], f"{path}.rationale", non_empty=False),
            created_at=_check_iso_timestamp(obj["created_at"], f"{path}.created_at"),
            cost_usd=_as_number(obj["cost_usd"], f"{path}.cost_usd", minimum=0.0),
            ext=_as_ext(obj["ext"], f"{path}.ext"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "scenario_version": self.scenario_version,
            "proposer_id": self.proposer_id,
            "parent_id": self.parent_id,
            "switches": self.switches.to_dict(),
            "rationale": self.rationale,
            "created_at": self.created_at,
            "cost_usd": self.cost_usd,
            "ext": dict(self.ext),
        }


RECORD_CLASSES: dict[str, type[_Record]] = {
    "scenario": Scenario,
    "shell_manifest": ShellManifest,
    "genome": Genome,
    "task": Task,
    "run": Run,
    "result": Result,
    "gate_decision": GateDecision,
    "snapshot": Snapshot,
    "archive": Archive,
    "event": Event,
    "proposal": Proposal,
}
RECORD_TYPES: tuple[str, ...] = tuple(RECORD_CLASSES)


def parse_record(kind: str, data: Any) -> Any:
    """按记录类型做数据类校验并返回实例。"""
    try:
        record_cls = RECORD_CLASSES[kind]
    except KeyError:
        raise ValidationError(f"未知记录类型: {kind!r}") from None
    return record_cls.from_dict(data)


# ---------------------------------------------------------------------------
# 裁判隔离类型：SearchHistory 与 ReportArchive 不可互换
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    task_id: str
    passed: bool
    cost_usd: float


@dataclass
class SearchHistory:
    """搜索回路可见的历史：没有 holdout、没有 report 分数。"""

    scenario_version: str
    genome_id: str
    entries: list[HistoryEntry] = field(default_factory=list)

    def add(self, entry: HistoryEntry) -> None:
        self.entries.append(entry)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_version": self.scenario_version,
            "genome_id": self.genome_id,
            "entries": [
                {"task_id": item.task_id, "passed": item.passed, "cost_usd": item.cost_usd}
                for item in self.entries
            ],
        }


@dataclass(frozen=True, slots=True)
class ReportArchive:
    """报告侧归档：双分数都在，但不可传给 ``Harness.propose``。"""

    scenario_version: str
    genome_id: str
    search: ScoreBlock
    holdout: ScoreBlock
    result_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_version": self.scenario_version,
            "genome_id": self.genome_id,
            "search": self.search.to_dict(),
            "holdout": self.holdout.to_dict(),
            "result_refs": list(self.result_refs),
        }
