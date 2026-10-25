"""跑题集：spawn Pi 子进程、超时、并发上限。

骨架阶段只保留签名与契约说明，不实现进程编排。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from molt.config import Switches

DEFAULT_TIMEOUT_S: float = 600.0
DEFAULT_RUNS_DIR: Path = Path("runs")


def build_pi_argv(
    task_id: str,
    workdir: Path,
    switches: Switches,
    *,
    prompt: str = "",
) -> list[str]:
    """拼出调用 Pi（加装 SoL-Pi）的命令行参数。

    约定：每道题在独立 workdir 内以非交互模式跑一次，题面通过 prompt 传入；
    sol-pi.json 由开关值渲染后写进 workdir 的 .pi/ 目录。

    Raises:
        NotImplementedError: 骨架阶段未实现。
    """
    raise NotImplementedError("runner.build_pi_argv 未实现")


def run_task(
    task_id: str,
    switches: Switches,
    *,
    runs_dir: Path = DEFAULT_RUNS_DIR,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, object]:
    """跑单道题，返回一条 run 记录（字段见 docs/contracts.md）。

    职责边界：
    - spawn Pi 子进程，工作目录隔离；
    - 超过 timeout_s 直接 kill，状态记为 timeout；
    - 全量 stdout/stderr 落盘，只把路径写进 log_path。

    Raises:
        NotImplementedError: 骨架阶段未实现。
    """
    raise NotImplementedError("runner.run_task 未实现")


def run_suite(
    task_ids: Sequence[str] | Iterable[str],
    switches: Switches,
    *,
    runs_dir: Path = DEFAULT_RUNS_DIR,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> list[dict[str, object]]:
    """在并发上限内跑完整个题集，返回按 task_id 排序的 run 记录列表。

    并发上限取 switches.max_concurrency，超出时排队；单题失败不影响其余题。

    Raises:
        NotImplementedError: 骨架阶段未实现。
    """
    raise NotImplementedError("runner.run_suite 未实现")
