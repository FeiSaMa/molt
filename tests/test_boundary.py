"""D2：core 依赖边界（AST + 屏蔽 importer）+ 七端口归属。"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = set(sys.stdlib_module_names) | {"__future__"}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                found.append(node.module)
    return found


def test_core_only_imports_stdlib_and_core() -> None:
    violations: list[str] = []
    for path in sorted((ROOT / "molt" / "core").rglob("*.py")):
        for module in _imports(path):
            top = module.split(".")[0]
            if module == "molt" or module.startswith("molt."):
                if module != "molt.core" and not module.startswith("molt.core."):
                    violations.append(f"{path.relative_to(ROOT)}: {module}")
            elif top not in ALLOWED:
                violations.append(f"{path.relative_to(ROOT)}: {module}")
    assert violations == []


def test_core_imports_with_third_party_blocked() -> None:
    script = """
import sys
class Blocker:
    def find_spec(self, fullname, path=None, target=None):
        top = fullname.split('.')[0]
        if top in sys.stdlib_module_names or top in ('molt', '__future__', 'builtins'):
            return None
        raise ImportError('blocked third-party import: ' + fullname)
sys.meta_path.insert(0, Blocker())
import molt.core.engine
import molt.core.store
import molt.core.audit
import molt.core.isolation
import molt.core.redact
print('core-import-ok')
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "core-import-ok" in completed.stdout


def test_doctor_deps_exit_zero() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tests/run_cli.py"), "doctor", "--deps"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "stdlib" in completed.stdout


def test_seven_ports_are_defined_in_core() -> None:
    import inspect

    from molt.core import ports

    protocol_names = sorted(
        name
        for name, value in vars(ports).items()
        if inspect.isclass(value)
        and getattr(value, "_is_protocol", False)
        and value.__module__ == ports.__name__
    )
    assert protocol_names == [
        "ArchivePort",
        "GatePort",
        "Harness",
        "LedgerPort",
        "Runtime",
        "TaskProvider",
        "Verifier",
    ]


def test_shell_and_adapter_are_outside_core() -> None:
    assert not (ROOT / "molt" / "core" / "fixture.py").exists()
    assert (ROOT / "molt" / "adapters" / "fixture" / "runtime.py").is_file()
    assert (ROOT / "shells" / "code-repair" / "harness.py").is_file()
    assert (ROOT / "shells" / "code-repair" / "verifier.py").is_file()
    assert (ROOT / "shells" / "code-repair" / "task_provider.py").is_file()
