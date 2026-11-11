"""确定性 Runtime：按 ``task.ext.deterministic`` 对配置做可复现的文本 / JSON 变换。

约束（B2-D3）：

- 零 LLM、零网络、零成本：``usage`` 全 0，``diagnostic.cost_source="none"``；
- 只读写 ``workdir`` 内的文件，路径逃逸即判 ``error``；
- 变换本身由题面声明，runtime 不推断、不搜索、不评分；
- ``switches`` 只作为题面声明的 ``when`` 条件的匹配输入，不做任何“智能”选择。

``ext.deterministic`` 形状：

```json
{
  "edits": [
    {"kind": "json_set", "path": "config/service.json",
     "pointer": "/server/port", "value": 8080,
     "when": {"thinking": ["high"]}},
    {"kind": "text_replace", "path": "config/app.json",
     "find": "level=verbose", "replace": "level=info"}
  ],
  "started_at": "2026-01-01T00:00:00Z",
  "finished_at": "2026-01-01T00:00:00Z"
}
```

``when`` 是维度名到「单值或值列表」的偏映射；全部维度命中才执行该条 edit。
缺少 ``when`` 即无条件执行。未知维度、非法指针、目标文件/文本不存在都让本次 run
以 ``outcome="error"`` 结束（由 core 记录，不崩溃）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from molt.core.hash import sha256_file
from molt.core.ports import RuntimeOutcome
from molt.core.types import SWITCH_KEYS, Genome, Switches, Task, Usage

DEFAULT_STARTED_AT = "2026-01-01T00:00:00Z"
COST_SOURCE = "none"
EDIT_KINDS: tuple[str, ...] = ("json_set", "text_replace")


class DeterministicEditError(ValueError):
    """题面声明的变换无法执行；映射为 run 的 ``error`` 终态。"""


def _zero_usage() -> Usage:
    return Usage(tokens_in=0, tokens_out=0, cost_usd=0.0, wall_ms=0, turns=0)


def _safe_join(workdir: Path, rel: Any) -> Path:
    if not isinstance(rel, str) or not rel:
        raise DeterministicEditError(f"edit.path 必须是非空字符串，实际 {rel!r}")
    normalized = rel.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        raise DeterministicEditError(f"edit.path 必须是工作区相对路径，实际 {rel!r}")
    root = Path(workdir).resolve()
    target = (root / normalized).resolve()
    if not target.is_relative_to(root):
        raise DeterministicEditError(f"edit.path 逃逸工作区: {rel!r}")
    return target


def _when_matches(when: Any, switches: Switches) -> bool:
    if when is None:
        return True
    if not isinstance(when, dict):
        raise DeterministicEditError(f"edit.when 必须是 object，实际 {type(when).__name__}")
    for dim, expected in when.items():
        if dim not in SWITCH_KEYS:
            raise DeterministicEditError(f"edit.when 含未知维度 {dim!r}")
        values = expected if isinstance(expected, list) else [expected]
        if not all(isinstance(value, str) for value in values):
            raise DeterministicEditError(f"edit.when.{dim} 必须是字符串或字符串列表")
        if getattr(switches, dim) not in values:
            return False
    return True


def _json_pointer_tokens(pointer: Any) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise DeterministicEditError(f"json_set.pointer 必须以 / 开头，实际 {pointer!r}")
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer.split("/")[1:]]


def _json_set(path: Path, pointer: Any, value: Any) -> None:
    tokens = _json_pointer_tokens(pointer)
    if not tokens:
        raise DeterministicEditError("json_set.pointer 不能指向根")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DeterministicEditError(f"{path.name} 不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DeterministicEditError(f"{path.name} 根节点必须是 object")
    node: dict[str, Any] = data
    for token in tokens[:-1]:
        child = node.get(token)
        if child is None:
            child = {}
            node[token] = child
        if not isinstance(child, dict):
            raise DeterministicEditError(f"json_set 中间节点 {token!r} 不是 object")
        node = child
    node[tokens[-1]] = value
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def _text_replace(path: Path, find: Any, replace: Any) -> None:
    if not isinstance(find, str) or not find:
        raise DeterministicEditError("text_replace.find 必须是非空字符串")
    if not isinstance(replace, str):
        raise DeterministicEditError("text_replace.replace 必须是字符串")
    text = path.read_text(encoding="utf-8")
    occurrences = text.count(find)
    if occurrences == 0:
        raise DeterministicEditError(f"text_replace.find 在 {path.name} 中不存在")
    path.write_text(text.replace(find, replace), encoding="utf-8")


def _apply_edit(workdir: Path, edit: Any) -> str:
    if not isinstance(edit, dict):
        raise DeterministicEditError(f"edit 必须是 object，实际 {type(edit).__name__}")
    kind = edit.get("kind")
    if kind not in EDIT_KINDS:
        raise DeterministicEditError(f"未知 edit.kind={kind!r}，合法值 {list(EDIT_KINDS)}")
    target = _safe_join(workdir, edit.get("path"))
    if not target.is_file():
        raise DeterministicEditError(f"edit 目标文件不存在: {edit.get('path')!r}")
    if kind == "json_set":
        if "value" not in edit:
            raise DeterministicEditError("json_set 缺少 value")
        _json_set(target, edit.get("pointer"), edit["value"])
    else:
        _text_replace(target, edit.get("find"), edit.get("replace"))
    return str(edit["path"]).replace("\\", "/")


@dataclass(frozen=True, slots=True)
class DeterministicRuntime:
    """零成本执行器；同一 genome + task + workspace 必得同一产物。"""

    def _spec(self, task: Task) -> dict[str, Any]:
        spec = task.ext.get("deterministic")
        if spec is None:
            return {}
        if not isinstance(spec, dict):
            raise DeterministicEditError("task.ext.deterministic 必须是 object")
        return spec

    def run(self, genome: Genome, task: Task, workdir: Path) -> RuntimeOutcome:
        started_at = DEFAULT_STARTED_AT
        finished_at = DEFAULT_STARTED_AT
        applied: list[str] = []
        skipped: list[str] = []
        artifacts: dict[str, str] = {}
        error: str | None = None
        try:
            spec = self._spec(task)
            started_at = str(spec.get("started_at", DEFAULT_STARTED_AT))
            finished_at = str(spec.get("finished_at", DEFAULT_STARTED_AT))
            edits = spec.get("edits", [])
            if not isinstance(edits, list):
                raise DeterministicEditError("ext.deterministic.edits 必须是数组")
            for index, edit in enumerate(edits):
                label = f"edit[{index}]"
                when = edit.get("when") if isinstance(edit, dict) else None
                if not _when_matches(when, genome.switches):
                    skipped.append(label)
                    continue
                rel = _apply_edit(workdir, edit)
                applied.append(label)
                artifacts[rel] = sha256_file(Path(workdir) / rel)
        except DeterministicEditError as exc:
            error = f"{type(exc).__name__}: {exc}"
        diagnostic: dict[str, Any] = {
            "runtime": "deterministic",
            "runtime_version": "deterministic-1",
            "cost_source": COST_SOURCE,
            "switches": genome.switches.to_dict(),
            "applied_edits": applied,
            "skipped_edits": skipped,
        }
        if error is not None:
            diagnostic["error"] = error
        return RuntimeOutcome(
            outcome="error" if error is not None else "passed",
            usage=_zero_usage(),
            started_at=started_at,
            finished_at=finished_at,
            artifacts=artifacts,
            diagnostic=diagnostic,
        )
