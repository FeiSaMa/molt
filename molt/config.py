"""受控开关定义。

molt 只搜索这 6 个开关；开关值落在 configs/*.json 并由 git 版本化，
外部机制（Pi / SoL-Pi）的行为差异全部通过这些值表达。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, Final

BASELINE_VERSION: Final[str] = "v0"

TOOL_GRANULARITIES: Final[tuple[str, ...]] = ("file", "hunk", "command")


@dataclass(frozen=True)
class Switches:
    """6 个受控开关 + 配置版本号。

    默认值即 v0 基线：四个效率机制全关，作为对照组。
    """

    config_version: str = BASELINE_VERSION
    # 1. Evidence-Preserving Reducer：长诊断日志压成可校验的收据
    reducer_enabled: bool = False
    # 2. Online Context Compact 的压缩比例，0.0 表示不压缩
    compaction_ratio: float = 0.0
    # 3. Action Fusion：edit/write 同一次调用顺带跑验证命令
    action_fusion: bool = False
    # 4. 工具粒度：整文件 / 按 hunk / 直接给命令
    tool_granularity: str = "file"
    # 5. 连续失败多少次后升级到前沿模型
    escalation_threshold: int = 2
    # 6. 单题集内同时跑多少道题
    max_concurrency: int = 4

    def __post_init__(self) -> None:
        if self.tool_granularity not in TOOL_GRANULARITIES:
            raise ValueError(f"tool_granularity 必须是 {TOOL_GRANULARITIES} 之一")
        if not 0.0 <= self.compaction_ratio <= 1.0:
            raise ValueError("compaction_ratio 必须落在 [0.0, 1.0]")
        if self.escalation_threshold < 1:
            raise ValueError("escalation_threshold 至少为 1")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency 至少为 1")

    def to_dict(self) -> dict[str, Any]:
        """转成可与 configs/*.json 对齐的普通字典。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Switches":
        """从 JSON 字典构造；未知键直接报错，避免静默丢开关。"""
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"未知配置键: {sorted(unknown)}")
        return cls(**data)


def baseline() -> Switches:
    """返回 v0 基线配置。"""
    return Switches()
