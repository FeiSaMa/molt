"""外部校验：日志枚举与重试范围 + 与黄金配置逐字段一致。"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = Path.cwd() / "config" / "app.json"
EXPECTED = HERE / "expected" / "app.json"

LEVELS = ("debug", "info", "warning", "error")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    if not CONFIG.is_file():
        print(f"缺少配置文件: {CONFIG}")
        return 1
    try:
        actual = _load(CONFIG)
    except json.JSONDecodeError as exc:
        print(f"配置不是合法 JSON: {exc}")
        return 1
    expected = _load(EXPECTED)
    if actual != expected:
        print(f"黄金 diff 不一致: actual={actual!r} expected={expected!r}")
        return 1
    logging = actual.get("logging", {})
    if logging.get("level") not in LEVELS:
        print(f"level 非法: {logging.get('level')!r}")
        return 1
    retries = logging.get("retries")
    if isinstance(retries, bool) or not isinstance(retries, int) or not 1 <= retries <= 5:
        print(f"retries 越界: {retries!r}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
