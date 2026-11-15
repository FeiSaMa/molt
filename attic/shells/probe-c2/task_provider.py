"""probe-c2 shell 的 TaskProvider：只从 bench/probe-c2 读取 C2 题。"""

from __future__ import annotations

import json
from pathlib import Path

from molt.core.types import Task

SCOPE_DIRS: dict[str, str] = {"search": "bench/probe-c2", "report": "bench/probe-c2/report"}


class ProbeTaskProvider:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = Path(repo_root)
        self._cache: dict[str, Task] | None = None

    def _load_all(self) -> dict[str, Task]:
        if self._cache is not None:
            return self._cache
        tasks: dict[str, Task] = {}
        for scope, rel in SCOPE_DIRS.items():
            for path in sorted((self.repo_root / rel).glob("*/task.json")):
                task = Task.from_dict(json.loads(path.read_text(encoding="utf-8")))
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


def build_task_provider(repo_root: Path, **_: object) -> ProbeTaskProvider:
    return ProbeTaskProvider(repo_root)
