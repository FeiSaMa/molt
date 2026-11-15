#!/usr/bin/env python3
"""把 ``probes/probe_experiments_manifest.json`` 投影成 probe 实验索引。

用法::

    PYTHONDONTWRITEBYTECODE=1 python probes/build_probe_experiments.py --out /tmp/probe/index.json

行为约束（与 ``docs/api/probe-agent-api.md`` 一致）：

* 先校验 manifest 里每个源文件的 bytes / sha256，再校验 verdict 交叉引用；
  任一失败即非零退出，不写任何文件（构建在内存里完成）。
* ``--out`` 必须显式给出，且解析后位于仓库之外；仓库内路径直接拒绝。
* ``generated_at`` 取 manifest 的固定值，构建过程不读时钟；两次构建字节一致。
* ``revision`` 是去掉 revision 后的规范化 JSON sha256，供 ``molt serve --probe-index``
  加载时重算自校验。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 允许从任意 cwd 直接跑脚本
    sys.path.insert(0, str(ROOT))

from molt.agent.probe import ProbeIndexError, build_index, dump_index  # noqa: E402


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_probe_experiments.py",
        description="从 manifest 构建 probe 实验索引（只读源文件，输出仓库外）",
    )
    parser.add_argument("--out", type=Path, required=True, help="输出 JSON 路径（必须在仓库外）")
    parser.add_argument("--manifest", type=Path, default=None, help="manifest 路径；缺省用仓库内路径")
    parser.add_argument("--root", type=Path, default=ROOT, help="仓库根；缺省为脚本上两级")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    out = Path(args.out).resolve()
    if _is_within(out, root):
        print(
            f"probe_experiments_invalid: --out 必须位于仓库外（仓库根 {root}）",
            file=sys.stderr,
        )
        return 2
    try:
        document = build_index(root, args.manifest)
        payload = dump_index(document)
        _write_atomic(out, payload)
    except ProbeIndexError as exc:
        print(f"probe_experiments_invalid: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"probe_experiments_io_error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "out": str(out),
                "revision": document["revision"],
                "experiments": len(document["experiments"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
