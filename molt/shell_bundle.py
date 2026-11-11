"""按 ``shells/<id>/manifest.json`` 的 ``entrypoints`` 装载 shell 模块。

shell 目录名含 ``-``，不能走普通 import；本模块统一用 ``load_shell_module`` 按路径加载。

实例化约定（shell ABI，随 ADR-0007 固定）：

- 模块若提供 ``build_<role>(...)`` 工厂，优先调用工厂；
- 否则按角色后缀在模块内查找**本模块定义**的类：
  ``*TaskProvider`` / ``*Verifier`` / ``*Harness``；
- provider / verifier 的构造签名为 ``cls(repo_root)``；
- harness 的构造签名为
  ``cls(runtime, verifier, repo_root, *, shell_id, shell_version, clock, domains, llm_proposer)``。

后缀查找会排除从 ``molt.core`` 导入的 Protocol 同名类（``__module__`` 不是本模块），
所以 shell 只需遵守命名与签名，不需要在 manifest 里写类名。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from molt.core.types import ShellManifest
from molt.shell_loader import load_shell_module

ROLE_SUFFIXES: dict[str, str] = {
    "task_provider": "TaskProvider",
    "verifier": "Verifier",
    "harness": "Harness",
}


@dataclass(frozen=True, slots=True)
class ShellBundle:
    """一个 shell 的 manifest 与三份已加载 entrypoint 模块。"""

    shell_id: str
    shell_version: str
    manifest: ShellManifest
    task_provider_module: ModuleType
    harness_module: ModuleType
    verifier_module: ModuleType
    verifier_path: Path

    def module(self, role: str) -> ModuleType:
        if role == "task_provider":
            return self.task_provider_module
        if role == "verifier":
            return self.verifier_module
        if role == "harness":
            return self.harness_module
        raise ValueError(f"未知 entrypoint role: {role!r}")


def load_shell_bundle(
    repo_root: Path,
    shell_id: str,
    *,
    shell_version: str | None = None,
) -> ShellBundle:
    """读 manifest 并按 ``entrypoints`` 三键加载模块；版本不一致即拒绝。"""
    repo_root = Path(repo_root).resolve()
    manifest_path = repo_root / "shells" / shell_id / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"shell manifest 不存在: {manifest_path}")
    manifest = ShellManifest.from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))
    if manifest.shell_id != shell_id:
        raise ValueError(f"manifest.shell_id={manifest.shell_id!r} 与请求 {shell_id!r} 不一致")
    if shell_version is not None and manifest.shell_version != shell_version:
        raise ValueError(
            f"shell {shell_id} 版本不一致: manifest={manifest.shell_version!r} "
            f"请求={shell_version!r}"
        )
    key = shell_id.replace("-", "_")
    modules = {
        role: load_shell_module(
            repo_root,
            manifest.entrypoints[role],
            f"molt_shell_{key}_{role}",
        )
        for role in ROLE_SUFFIXES
    }
    return ShellBundle(
        shell_id=manifest.shell_id,
        shell_version=manifest.shell_version,
        manifest=manifest,
        task_provider_module=modules["task_provider"],
        harness_module=modules["harness"],
        verifier_module=modules["verifier"],
        verifier_path=repo_root / manifest.entrypoints["verifier"],
    )


def _find_shell_class(module: ModuleType, role: str) -> type:
    """在模块自身命名空间里按后缀找唯一实现类；歧义或缺失都 fail closed。"""
    suffix = ROLE_SUFFIXES[role]
    candidates = [
        (name, obj)
        for name, obj in vars(module).items()
        if isinstance(obj, type) and obj.__module__ == module.__name__ and name.endswith(suffix)
    ]
    if not candidates:
        raise TypeError(f"{module.__name__} 未提供 {role}: 需要 `build_{role}()` 或 `*{suffix}` 类")
    if len(candidates) > 1:
        names = sorted(name for name, _ in candidates)
        raise TypeError(f"{module.__name__} 的 {role} 实现不唯一: {names}")
    return candidates[0][1]


def build_task_provider(bundle: ShellBundle, repo_root: Path) -> Any:
    module = bundle.task_provider_module
    factory = getattr(module, "build_task_provider", None)
    if callable(factory):
        return factory(repo_root)
    return _find_shell_class(module, "task_provider")(repo_root)


def build_verifier(bundle: ShellBundle, repo_root: Path) -> Any:
    module = bundle.verifier_module
    factory = getattr(module, "build_verifier", None)
    if callable(factory):
        return factory(repo_root)
    return _find_shell_class(module, "verifier")(repo_root)


def build_harness(
    bundle: ShellBundle,
    runtime: Any,
    verifier: Any,
    repo_root: Path,
    **kwargs: Any,
) -> Any:
    module = bundle.harness_module
    factory = getattr(module, "build_harness", None)
    if callable(factory):
        return factory(runtime, verifier, repo_root, **kwargs)
    return _find_shell_class(module, "harness")(runtime, verifier, repo_root, **kwargs)
