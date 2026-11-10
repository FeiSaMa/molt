"""按文件路径加载 shell 模块（目录名含 ``-``，不能走普通 import）。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_shell_module(repo_root: Path, rel_path: str, name: str) -> ModuleType:
    path = Path(repo_root) / rel_path
    if not path.is_file():
        raise FileNotFoundError(f"shell 模块不存在: {rel_path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 shell 模块: {rel_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module
