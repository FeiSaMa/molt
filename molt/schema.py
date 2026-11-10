"""JSON Schema 校验与文档生成（dev 依赖 jsonschema）。

核心契约事实源是 ``schemas/v1/*.schema.json``；数据类校验器是第二道。
两份校验器对 golden 全通过、对 mutation 全拒绝。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from molt.core.errors import SchemaError, ValidationError
from molt.core.types import RECORD_CLASSES, RECORD_TYPES, parse_record

SCHEMA_SUBDIR = Path("schemas") / "v1"
RECORD_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("shells/code-repair/manifest.json", "shell_manifest"),
)
GENOME_GLOB = "shells/*/genomes/*.json"
TASK_GLOB = "bench/**/task.json"
SCENARIO_GLOB = "scenarios/*/scenario.json"


def find_repo_root(start: Path | None = None) -> Path:
    """向上找到含 ``schemas/v1`` 的仓库根。"""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / SCHEMA_SUBDIR).is_dir():
            return candidate
    package_root = Path(__file__).resolve().parent.parent
    if (package_root / SCHEMA_SUBDIR).is_dir():
        return package_root
    raise SchemaError("找不到仓库根（缺少 schemas/v1）")


def schema_path(kind: str, root: Path | None = None) -> Path:
    base = root or find_repo_root()
    return base / SCHEMA_SUBDIR / f"{kind}.schema.json"


def load_schema(kind: str, root: Path | None = None) -> dict[str, Any]:
    if kind not in RECORD_CLASSES:
        raise SchemaError(f"未知记录类型: {kind!r}")
    path = schema_path(kind, root)
    if not path.exists():
        raise SchemaError(f"schema 不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SchemaError(f"schema 不是合法 JSON: {path}: {exc}") from exc
    Draft202012Validator.check_schema(data)
    return data


def _validator(kind: str, root: Path | None = None) -> Draft202012Validator:
    return Draft202012Validator(load_schema(kind, root), format_checker=FormatChecker())


def validate_schema(kind: str, data: Any, root: Path | None = None) -> None:
    """只跑 JSON Schema 校验；失败抛 ``ValidationError``。"""
    errors = sorted(_validator(kind, root).iter_errors(data), key=lambda err: list(err.path))
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "<root>"
        raise ValidationError(f"{kind} schema 校验失败 @ {location}: {first.message}")


def validate_record(kind: str, data: Any, root: Path | None = None) -> Any:
    """双校验：JSON Schema + 数据类。"""
    validate_schema(kind, data, root)
    return parse_record(kind, data)


def validate_artifact(path: Path, kind: str, root: Path | None = None) -> Any:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_record(kind, data, root)


def artifact_paths(root: Path) -> list[tuple[Path, str]]:
    """仓库内全部需要校验的记录文件。"""
    found: list[tuple[Path, str]] = []
    for rel, kind in RECORD_ARTIFACTS:
        path = root / rel
        if path.exists():
            found.append((path, kind))
    for pattern, kind in (
        (GENOME_GLOB, "genome"),
        (TASK_GLOB, "task"),
        (SCENARIO_GLOB, "scenario"),
    ):
        for path in sorted(root.glob(pattern)):
            found.append((path, kind))
    for path in sorted((root / "tests" / "fixtures" / "records").glob("*.json")):
        kind = path.stem
        if kind in RECORD_CLASSES:
            found.append((path, kind))
    return found


def validate_all(root: Path) -> list[tuple[Path, str]]:
    """校验全部 schema 与仓库内全部记录；返回已校验清单。"""
    for kind in RECORD_TYPES:
        load_schema(kind, root)
    validated: list[tuple[Path, str]] = []
    for path, kind in artifact_paths(root):
        validate_artifact(path, kind, root)
        validated.append((path, kind))
    return validated


def _type_label(prop: dict[str, Any]) -> str:
    if "$ref" in prop:
        return str(prop["$ref"]).split("/")[-1]
    if "const" in prop:
        return f"const({prop['const']})"
    if "enum" in prop:
        return "enum(" + " | ".join(str(item) for item in prop["enum"]) + ")"
    if "type" in prop:
        value = prop["type"]
        if isinstance(value, list):
            return " | ".join(str(item) for item in value)
        if value == "array":
            return f"array<{_type_label(prop.get('items', {}))}>"
        return str(value)
    return "object"


def _property_rows(schema: dict[str, Any]) -> Iterable[tuple[str, str, str, str]]:
    required = set(schema.get("required", ()))
    for name, prop in schema.get("properties", {}).items():
        yield (
            f"`{name}`",
            _type_label(prop),
            "是" if name in required else "否",
            str(prop.get("description", "")),
        )


def render_protocol_docs(root: Path | None = None) -> str:
    """从 schema 生成字段表；不手写字段清单。"""
    root = root or find_repo_root()
    lines = [
        "# 协议 v1",
        "",
        "> 本文件由 `python -m molt schema docs` 从 `schemas/v1/*.schema.json` 生成，"
        "禁止手改；字段事实源是 schema 文件与 `molt/core/types.py`。",
        "",
        "## 通用规则",
        "",
        "- 11 类记录：`scenario`、`shell_manifest`、`genome`、`task`、`run`、`result`、"
        "`gate_decision`、`snapshot`、`archive`、`event`、`proposal`。",
        "- 每份记录都含 `schema_version`（`1.x.y`）与 `ext`。同 major 内顶层字段冻结；"
        "minor 只允许在 `ext` 内新增或新增记录类型。",
        "- 新增顶层字段 / 改名 / 删字段 = major 升级 + ADR + 新 `schemas/vN/`。",
        "- v1 读者拒绝未知顶层字段，保留未知 `ext`。",
        "- `event` 必带 `scope: search | report`；已知 type 的 payload 是 tagged union，"
        "未知 type 视为 open（保留原文，审计不断言完整性）。",
        "- canonical JSON：键排序、UTF-8、无多余空白；哈希 sha256；树哈希按相对路径 "
        "POSIX 排序，逐行 `path\\0content_hash\\n`，不含 mtime，拒绝 symlink。",
        "",
        "## 记录",
        "",
    ]
    for kind in RECORD_TYPES:
        schema = load_schema(kind, root)
        lines.append(f"### `{kind}`")
        lines.append("")
        lines.append(str(schema.get("description", "")))
        lines.append("")
        lines.append("| 字段 | 类型 | 必需 | 说明 |")
        lines.append("| --- | --- | --- | --- |")
        for row in _property_rows(schema):
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_protocol_docs(root: Path | None = None) -> Path:
    root = root or find_repo_root()
    target = root / "docs" / "protocol.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_protocol_docs(root), encoding="utf-8")
    return target
