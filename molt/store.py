"""runs/ 落盘、读取与列举。

目录约定：runs/<run_id>/run.json + log.txt；一次配置跑完整题集后，
在该配置版本目录下再写 result.json。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

DEFAULT_RUNS_DIR: Path = Path("runs")


def run_dir(config_version: str, run_id: str, *, root: Path = DEFAULT_RUNS_DIR) -> Path:
    """返回单次 run 的目录：<root>/<config_version>/<run_id>/。

    Raises:
        NotImplementedError: 骨架阶段未实现。
    """
    raise NotImplementedError("store.run_dir 未实现")


def write_run(record: dict[str, object], *, root: Path = DEFAULT_RUNS_DIR) -> Path:
    """把一条 run 记录原子写入 run.json，返回文件路径。

    先写临时文件再 rename，避免中断留下半个 JSON。

    Raises:
        NotImplementedError: 骨架阶段未实现。
    """
    raise NotImplementedError("store.write_run 未实现")


def read_run(run_path: Path) -> dict[str, object]:
    """读单条 run.json；目录则取其中的 run.json。

    Raises:
        NotImplementedError: 骨架阶段未实现。
    """
    raise NotImplementedError("store.read_run 未实现")


def list_runs(
    config_version: str | None = None,
    *,
    root: Path = DEFAULT_RUNS_DIR,
) -> Sequence[dict[str, object]]:
    """列出 run 记录，按 (config_version, run_id) 升序；可按版本过滤。

    Raises:
        NotImplementedError: 骨架阶段未实现。
    """
    raise NotImplementedError("store.list_runs 未实现")


def write_result(config_version: str, result: dict[str, object], *, root: Path = DEFAULT_RUNS_DIR) -> Path:
    """落盘某版本的 result.json，返回文件路径。

    Raises:
        NotImplementedError: 骨架阶段未实现。
    """
    raise NotImplementedError("store.write_result 未实现")


def read_result(config_version: str, *, root: Path = DEFAULT_RUNS_DIR) -> dict[str, object]:
    """读某版本的 result.json。

    Raises:
        NotImplementedError: 骨架阶段未实现。
    """
    raise NotImplementedError("store.read_result 未实现")
