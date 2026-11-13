"""本地 agent API（Batch A+B 合并 MVP）。

只依赖标准库：HTTP 服务、会话存储、SSE 事件与 chat adapter 都在这里实现。
入口见 :func:`molt.agent.server.run_server`（CLI：``molt serve``）。
"""

from __future__ import annotations

API_VERSION = "1.0.0"

__all__ = ["API_VERSION"]
