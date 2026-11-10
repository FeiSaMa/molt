"""canonical JSON、内容哈希与目录树哈希。

口径：
- canonical JSON：键排序、UTF-8、``separators=(",", ":")``、``ensure_ascii=False``；
- 内容哈希：sha256；
- 树哈希：按相对路径 POSIX 排序，逐行 ``path\\0content_hash\\n``，不含 mtime / mode；
- 目录树拒绝 symlink：出现即 ``HashMismatch``。
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from molt.core.errors import HashMismatch

_SHA256_HEX_LEN = 64
_CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache"})


def canonical_json_bytes(obj: Any) -> bytes:
    """把对象编码为 canonical JSON 字节。"""
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return text.encode("utf-8")


def canonical_json_text(obj: Any) -> str:
    """canonical JSON 的文本形式（无末尾换行）。"""
    return canonical_json_bytes(obj).decode("utf-8")


def sha256_bytes(data: bytes) -> str:
    """字节串的 sha256 十六进制摘要。"""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """UTF-8 文本的 sha256。"""
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    """文件的 sha256；流式读取，不把大文件读进内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_hash(obj: Any) -> str:
    """对象的 canonical 内容哈希。"""
    return sha256_bytes(canonical_json_bytes(obj))


def is_sha256_hex(value: str) -> bool:
    """判断字符串是否形如 sha256 十六进制摘要。"""
    if len(value) != _SHA256_HEX_LEN:
        return False
    return all(ch in "0123456789abcdef" for ch in value)


def tree_files(root: Path, *, prefix: str = "") -> dict[str, str]:
    """返回 ``{相对 POSIX 路径: sha256}``，路径不含 ``prefix``。

    拒绝 symlink（文件或目录）。空目录不产生条目。
    缓存目录（``__pycache__`` / ``.pytest_cache`` / ``.ruff_cache``）与 ``*.pyc``
    不属于源码树，跳过，避免解释器缓存破坏树哈希。
    """
    root = Path(root)
    if root.is_symlink():
        raise HashMismatch(f"拒绝 symlink 根目录: {root}")
    files: dict[str, str] = {}
    if not root.exists():
        return files
    if root.is_file():
        rel = prefix or root.name
        files[rel] = sha256_file(root)
        return files
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirpath_p = Path(dirpath)
        dirnames[:] = [name for name in dirnames if name not in _CACHE_DIRS]
        for dirname in list(dirnames):
            child = dirpath_p / dirname
            if child.is_symlink():
                raise HashMismatch(f"拒绝 symlink 目录: {child}")
        for filename in filenames:
            if filename.endswith(".pyc"):
                continue
            path = dirpath_p / filename
            if path.is_symlink():
                raise HashMismatch(f"拒绝 symlink 文件: {path}")
            rel = path.relative_to(root).as_posix()
            key = f"{prefix.rstrip('/')}/{rel}" if prefix else rel
            files[key] = sha256_file(path)
    return files


def tree_hash_from_files(files: Mapping[str, str]) -> str:
    """由 ``{相对路径: 内容哈希}`` 计算树哈希。"""
    lines = [f"{path}\0{files[path]}\n" for path in sorted(files)]
    return sha256_text("".join(lines))


def tree_hash(root: Path, *, prefix: str = "") -> tuple[str, dict[str, str]]:
    """计算目录树哈希，同时返回文件清单。"""
    files = tree_files(root, prefix=prefix)
    return tree_hash_from_files(files), files


def merge_tree_files(
    roots: Iterable[tuple[str, Path]],
) -> dict[str, str]:
    """把多个根目录合并成 ``{root/rel: hash}``。"""
    merged: dict[str, str] = {}
    for name, root in roots:
        for rel, digest in tree_files(Path(root)).items():
            merged[f"{name}/{rel}"] = digest
    return merged
