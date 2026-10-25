"""molt 命令行入口。

骨架阶段只做参数解析与回显：任何子命令都打印"未实现"并返回 0，
不 spawn 进程、不读写 runs/、不动 git。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

DEFAULT_CONFIG: Path = Path("configs/v0-baseline.json")
SUITES: tuple[str, ...] = ("fixed", "family", "holdout")


def build_parser() -> argparse.ArgumentParser:
    """构造 argparse 解析器，四个子命令：run / score / revert / doctor。"""
    parser = argparse.ArgumentParser(
        prog="molt",
        description="自进化 agent 引擎的外层编排器（骨架阶段，仅解析参数）",
        epilog="文档：docs/architecture.md、docs/contracts.md",
    )
    sub = parser.add_subparsers(dest="command", metavar="{run,score,revert,doctor}")

    p_run = sub.add_parser("run", help="用给定 config 跑一轮题集")
    p_run.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="开关配置文件路径")
    p_run.add_argument("--suite", choices=SUITES, default="fixed", help="题集，默认 fixed")
    p_run.add_argument("--tag", default=None, help="跑完后给 config 打的 git tag，可选")

    p_score = sub.add_parser("score", help="汇总分数并跑回归门禁")
    p_score.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="要打分的 config")
    p_score.add_argument("--against", default=None, help="对比的上一版 config_version，默认上一版")
    p_score.add_argument("--suite", choices=SUITES, default="fixed", help="题集，默认 fixed")

    p_revert = sub.add_parser("revert", help="回退到某个 config 版本")
    p_revert.add_argument("--to", required=True, help="目标 config_version，例如 v0")
    p_revert.add_argument("--keep-runs", action="store_true", help="保留当前 runs/ 不归档")

    p_doctor = sub.add_parser("doctor", help="检查环境：pi / SoL-Pi / 题集 / 凭据")
    p_doctor.add_argument("--config", type=Path, default=None, help="顺便校验某个 config")

    return parser


def _echo_not_implemented(command: str, args: argparse.Namespace) -> int:
    """统一桩输出：首行"未实现"，随后回显解析结果。"""
    print("未实现")
    print(f"子命令: {command}")
    for key in sorted(vars(args)):
        if key == "command":
            continue
        print(f"参数: {key}={getattr(args, key)}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """执行 run：按 config 跑题集、落盘 runs/、可选打 tag。"""
    return _echo_not_implemented("run", args)


def cmd_score(args: argparse.Namespace) -> int:
    """执行 score：字典序打分 + 回归门禁（成功率不得低于上一版）。"""
    return _echo_not_implemented("score", args)


def cmd_revert(args: argparse.Namespace) -> int:
    """执行 revert：切回旧 config 版本，并要求用同一题集复跑。"""
    return _echo_not_implemented("revert", args)


def cmd_doctor(args: argparse.Namespace) -> int:
    """执行 doctor：检查 pi 可执行文件、SoL-Pi 安装、题集完整性。"""
    return _echo_not_implemented("doctor", args)


HANDLERS = {
    "run": cmd_run,
    "score": cmd_score,
    "revert": cmd_revert,
    "doctor": cmd_doctor,
}


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口；无子命令时打印用法并返回 0。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return HANDLERS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
