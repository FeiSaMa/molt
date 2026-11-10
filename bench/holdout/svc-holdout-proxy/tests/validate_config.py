"""外部校验（留出题）：端口/超时范围 + 与黄金配置逐字段一致。"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = Path.cwd() / "config" / "proxy.json"
EXPECTED = HERE / "expected" / "proxy.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


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
    upstream = actual.get("upstream", {})
    port = upstream.get("port")
    timeout_ms = upstream.get("timeout_ms")
    if not _is_int(port) or not 1 <= port <= 65535:
        print(f"port 越界: {port!r}")
        return 1
    if not _is_int(timeout_ms) or not 1000 <= timeout_ms <= 30000:
        print(f"timeout_ms 越界: {timeout_ms!r}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
