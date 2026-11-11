"""svc-config shell 的 TaskProvider：只提供 ``shell_id=svc-config`` 的题。

搜索集与报告集物理分目录：``bench/search/``、``bench/holdout/``；
同一目录下其它 shell 的题会被过滤掉，避免跨 shell 串题。
"""

from __future__ import annotations

import json
from pathlib import Path

from molt.core.types import Task

SHELL_ID = "svc-config"
SCOPE_DIRS: dict[str, str] = {"search": "bench/search", "report": "bench/holdout"}


class SvcConfigTaskProvider:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = Path(repo_root)
        self._cache: dict[str, Task] | None = None

    def _load_all(self) -> dict[str, Task]:
        if self._cache is not None:
            return self._cache
        tasks: dict[str, Task] = {}
        for scope, rel in SCOPE_DIRS.items():
            for path in sorted((self.repo_root / rel).glob("*/task.json")):
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("shell_id") != SHELL_ID:
                    continue
                task = Task.from_dict(data)
                if task.scope != scope:
                    raise ValueError(f"{path} scope={task.scope!r} 与目录 {rel} 不一致")
                if task.task_id in tasks:
                    raise ValueError(f"重复 task_id: {task.task_id}")
                tasks[task.task_id] = task
        self._cache = tasks
        return tasks

    def list_tasks(self, scope: str) -> list[Task]:
        if scope not in SCOPE_DIRS:
            raise ValueError(f"未知 scope: {scope!r}")
        return [task for task in self._load_all().values() if task.scope == scope]

    def load_task(self, task_id: str) -> Task:
        try:
            return self._load_all()[task_id]
        except KeyError:
            raise KeyError(f"未知 task_id: {task_id!r}") from None
