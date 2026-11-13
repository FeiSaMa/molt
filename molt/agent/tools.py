"""只读工具定义与执行（OpenAI function calling 形状）。

工具只有两个，名字固定为下划线形式：

* ``experiments_list``：返回 ``GET /api/experiments`` 同一份投影；
* ``experiments_get``：按 id 返回单条实验，未知 id 返回 ``status="error"`` +
  ``reason="not_found"``（仍会进入第二次上游调用，见 ``turn.py``）。

工具不读文件、不碰网络、不返回敏感字段；索引在启动时已校验并冻结。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from molt.agent.probe import find_experiment, index_public_view

TOOL_NAMES = ("experiments_list", "experiments_get")

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "experiments_list",
            "description": "列出 probe 实验投影（只读，不含 prompt / response / 原始 run 数据）",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "experiments_get",
            "description": "按 experiment_id 读取单条 probe 实验投影",
            "parameters": {
                "type": "object",
                "properties": {
                    "experiment_id": {"type": "string", "minLength": 1},
                },
                "required": ["experiment_id"],
                "additionalProperties": False,
            },
        },
    },
]


@dataclass
class ToolOutcome:
    status: str  # ok | error | denied
    result: Any = None
    reason: str | None = None

    def event_payload(self, call_id: str, tool: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "call_id": call_id,
            "tool": tool,
            "status": self.status,
        }
        if self.status == "ok":
            payload["result"] = self.result
        elif self.reason is not None:
            payload["reason"] = self.reason
        return payload

    def tool_message_content(self) -> str:
        if self.status == "ok":
            body: dict[str, Any] = {"status": "ok", "result": self.result}
        else:
            body = {"status": self.status, "reason": self.reason}
        return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def _parse_arguments(arguments: Any) -> dict[str, Any] | None:
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str):
        return None
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def execute_tool(name: str, arguments: Any, index: dict[str, Any]) -> ToolOutcome:
    """执行一次工具调用；任何异常都收敛成 error（不会打断 turn）。"""

    if name not in TOOL_NAMES:
        return ToolOutcome(status="denied", reason="unknown_tool")
    parsed = _parse_arguments(arguments)
    if parsed is None:
        return ToolOutcome(status="error", reason="internal")

    try:
        if name == "experiments_list":
            return ToolOutcome(status="ok", result=index_public_view(index))
        experiment_id = parsed.get("experiment_id")
        if not isinstance(experiment_id, str) or experiment_id == "":
            return ToolOutcome(status="error", reason="internal")
        experiment = find_experiment(index, experiment_id)
        if experiment is None:
            return ToolOutcome(status="error", reason="not_found")
        return ToolOutcome(status="ok", result=experiment)
    except Exception:  # noqa: BLE001 - 工具内部错误必须收敛成 reason=internal
        return ToolOutcome(status="error", reason="internal")
