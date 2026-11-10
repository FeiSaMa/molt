"""隔离策略：网络三档映射写死，降级必须显式标记。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from molt.core.errors import IsolationError
from molt.core.types import NETWORK_LEVELS, NETWORK_TO_LEVEL

PROVIDER_ENDPOINT = "https://api.deepseek.com"
DEFAULT_DENY_PATHS: tuple[str, ...] = ("bench", "web", "evidence")
SANDBOX_UNAVAILABLE_MARKER = "sandbox unavailable"


def _normalize(path: str) -> str:
    text = path.replace("\\", "/").lstrip("./")
    return str(PurePosixPath(text))


def _under(path: str, root: str) -> bool:
    path = _normalize(path)
    root = _normalize(root)
    return path == root or path.startswith(root + "/")


@dataclass(frozen=True, slots=True)
class IsolationPolicy:
    """``network`` 三档 → ``level`` 的映射是协议的一部分，不得配置化。"""

    network: str
    level: str
    degraded: bool
    sandbox_available: bool
    deny_paths: tuple[str, ...] = DEFAULT_DENY_PATHS

    @classmethod
    def resolve(
        cls,
        network: str,
        *,
        sandbox_available: bool = True,
        deny_paths: Iterable[str] = DEFAULT_DENY_PATHS,
    ) -> IsolationPolicy:
        if network not in NETWORK_LEVELS:
            raise IsolationError(
                f"isolation.network 越域: {network!r}，合法值 {list(NETWORK_LEVELS)}"
            )
        paths = tuple(_normalize(item) for item in deny_paths)
        if network == "open":
            return cls(network, "permissions-only", False, sandbox_available, paths)
        if not sandbox_available:
            # 无法建立隔离时不能再声称 provider_only/sandboxed：网络实际是开放的，
            # 因此把 network 也降为 open，并用 degraded/marker 显式标记。
            return cls("open", "permissions-only", True, False, paths)
        return cls(network, NETWORK_TO_LEVEL[network], False, True, paths)

    @property
    def sandbox_unavailable(self) -> bool:
        return self.degraded

    @property
    def claim_marker(self) -> str | None:
        return SANDBOX_UNAVAILABLE_MARKER if self.degraded else None

    def allows_model_call(self) -> bool:
        """模型调用发生在编排器 / Pi 主进程，不经过 agent 沙箱。"""
        return True

    def allows_bash_network(self, url: str) -> bool:
        """bash 工具永远不在有网环境跑；只有 permissions-only 例外。"""
        if self.level == "permissions-only":
            return True
        return False

    def check_agent_read(self, rel_path: str) -> None:
        """agent / bash 侧读取仓库路径的准入检查。"""
        if self.level == "permissions-only":
            return
        for root in self.deny_paths:
            if _under(rel_path, root):
                raise IsolationError(
                    f"{self.level} 隔离下禁止读取 {rel_path!r}（命中 deny root {root!r}）"
                )

    def check_command(self, command: str) -> None:
        """bash 侧网络命令的最保守检查：隔离生效时一律拒绝 curl/wget。"""
        if self.level == "permissions-only":
            return
        lowered = command.lower()
        for needle in ("curl ", "wget ", "http://", "https://"):
            if needle in lowered:
                raise IsolationError(f"{self.level} 隔离下禁止命令外联: {command!r}")


def network_mapping() -> dict[str, str]:
    """给文档 / 测试断言用的只读映射。"""
    return dict(NETWORK_TO_LEVEL)
