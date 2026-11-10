"""molt：自进化 agent 引擎的外层编排器。

分层：``molt/core``（协议与引擎，stdlib only）→ shell（Task/Harness/Verifier）→
adapter（Runtime）。B1 提供 fixture adapter 与 code-repair shell。
"""

__version__ = "1.0.0"

__all__ = ["__version__"]
