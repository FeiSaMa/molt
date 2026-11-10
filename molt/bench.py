"""bench 题面哈希：只覆盖 scenario 实际引用的题，不扫未引用候选。

树哈希口径与 ``molt.core.hash`` 一致：按相对 POSIX 路径排序，逐行
``path\\0content_hash\\n``，不含 mtime。范围是每题的 ``task.json`` +
``workspace_ref`` 目录 + ``tests_ref`` 目录；未挂到 ``task_sets`` 的文件不影响哈希。

注意：``task_paths`` 为了建索引会解析 ``bench/**`` 下**全部** ``task.json``，所以未引用的
残缺 task.json 会让 ``scenario validate`` 失败（严格性，不是 bug）；新增候选请写完整 schema。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from molt.core.errors import ValidationError
from molt.core.hash import sha256_file, tree_files, tree_hash_from_files
from molt.core.types import Task


def task_paths(root: Path) -> dict[str, Path]:
    """返回 ``{task_id: task.json 路径}``。"""
    root = Path(root)
    index: dict[str, Path] = {}
    for path in sorted(root.glob("bench/**/task.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValidationError(f"task.json 缺少 task_id: {path}")
        if task_id in index:
            raise ValidationError(f"task_id 重复 {task_id!r}: {path} 与 {index[task_id]}")
        index[task_id] = path
    return index


def compute_bench_hash(root: Path, task_ids: Iterable[str]) -> str:
    """计算引用题集的题面树哈希；缺题即报错，不做静默跳过。"""
    root = Path(root)
    paths = task_paths(root)
    files: dict[str, str] = {}
    for task_id in sorted(set(task_ids)):
        task_path = paths.get(task_id)
        if task_path is None:
            raise ValidationError(f"bench 中找不到 task: {task_id}")
        files[task_path.relative_to(root).as_posix()] = sha256_file(task_path)
        task = Task.from_dict(json.loads(task_path.read_text(encoding="utf-8")))
        for ref in (task.workspace_ref, task.tests_ref):
            base = root / ref
            if not base.is_dir():
                raise ValidationError(f"task {task_id} 的 {ref} 不是目录")
            for rel, digest in tree_files(base).items():
                files[f"{ref.rstrip('/')}/{rel}"] = digest
    return tree_hash_from_files(files)
