"""仓库日志入口。"""

from __future__ import annotations

import logging

REJECTION_PREFIX = "rejected:"

_CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    """返回指定模块的 logger。"""
    global _CONFIGURED
    if not _CONFIGURED:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
        _CONFIGURED = True
    return logging.getLogger(name)
