"""快照：覆盖 manifest 声明的全部 roots，缺一即拒绝。

root 可以是目录，也可以是单个文件（如 shell 装载器 ``molt/shell_bundle.py``）：
- 目录按 ``root/rel`` 逐文件哈希；
- 文件按 sha256 记录，键为文件名；
- 两种形态都拒绝 symlink，generation 复制文件时保留 mode。
``snapshot.files`` 的顶层键与 ``snapshot.roots`` 必须完全相同；
树哈希口径为 ``root/rel\\0content_hash\\n``（排序）。
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path

from molt.core.errors import HashMismatch, ValidationError
from molt.core.hash import tree_files, tree_hash_from_files
from molt.core.types import Snapshot


def build_snapshot(
    roots: Mapping[str, Path],
    *,
    generation: int,
    created_at: str,
    snapshot_id: str,
) -> Snapshot:
    """计算快照记录；``roots`` 为 ``{root_key: 绝对路径}``，支持目录与单文件。"""
    if not roots:
        raise ValidationError("shell manifest 未声明任何 artifact root")
    files: dict[str, dict[str, str]] = {}
    flat: dict[str, str] = {}
    for key in sorted(roots):
        root = Path(roots[key])
        if not root.exists():
            raise HashMismatch(f"artifact root 不存在: {key} ({root})")
        if root.is_symlink():
            raise HashMismatch(f"artifact root 不得是 symlink: {root}")
        # tree_files 对单文件返回 {文件名: sha256}，对目录返回逐文件清单。
        root_files = tree_files(root)
        if not root_files:
            raise HashMismatch(f"artifact root 为空: {key} ({root})")
        files[key] = root_files
        for rel, digest in root_files.items():
            flat[f"{key}/{rel}"] = digest
    payload = {
        "schema_version": "1.0.0",
        "snapshot_id": snapshot_id,
        "generation": generation,
        "created_at": created_at,
        "roots": sorted(roots),
        "tree_hash": tree_hash_from_files(flat),
        "files": files,
        "ext": {},
    }
    return Snapshot.from_dict(payload)


def copy_generation(
    roots: Mapping[str, Path],
    dest: Path,
) -> None:
    """把 roots 原样复制到 ``dest/<root_key>``（目录或单文件；拒绝 symlink）。"""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for key, root in roots.items():
        root = Path(root)
        if root.is_symlink():
            raise HashMismatch(f"artifact root 不得是 symlink: {root}")
        if not root.exists():
            raise HashMismatch(f"artifact root 不存在: {key} ({root})")
        target = dest / key
        target.parent.mkdir(parents=True, exist_ok=True)
        if root.is_file():
            # copy2 保留 mode / mtime；单文件 root 不再要求目录。
            shutil.copy2(root, target)
        else:
            shutil.copytree(root, target, symlinks=False)
    for path in dest.rglob("*"):
        if path.is_symlink():
            raise HashMismatch(f"generation 内出现 symlink: {path}")


def verify_generation(generation_dir: Path, snapshot: Snapshot) -> bool:
    """重算 generation 目录的树哈希并与快照比对；目录与单文件 root 同一口径。"""
    flat: dict[str, str] = {}
    for root in snapshot.roots:
        root_path = Path(generation_dir) / root
        if not root_path.exists():
            return False
        for rel, digest in tree_files(root_path).items():
            flat[f"{root}/{rel}"] = digest
    return tree_hash_from_files(flat) == snapshot.tree_hash
