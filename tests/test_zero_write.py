"""零写入验收：文件系统树快照（不是 git status）。

子进程一律带 ``PYTHONDONTWRITEBYTECODE=1``；缓存目录与 ``.git`` / ``.venv`` 不参与快照。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}


def tree_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.is_symlink():
            snapshot[str(relative)] = "symlink:" + os.readlink(path)
        elif path.is_dir():
            snapshot[str(relative)] = "dir"
        elif path.suffix == ".pyc":
            continue
        else:
            import hashlib

            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot[str(relative)] = f"file:{path.stat().st_size}:{digest}"
    return snapshot


def _invoke(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "molt", *command], cwd=ROOT, env=env,
                          capture_output=True, text=True, check=False, timeout=30)


def _assert_unchanged(before: dict[str, str]) -> None:
    after = tree_snapshot(ROOT)
    assert sorted(set(after) - set(before)) == [], "新增路径"
    assert sorted(set(before) - set(after)) == [], "删除路径"
    assert [key for key in before.keys() & after.keys() if before[key] != after[key]] == [], "内容变化"


def test_read_only_commands_do_not_write_repository(tmp_path) -> None:
    from test_v1_compatibility import _snapshot, _write, build_compatibility_fixture

    source = tmp_path / "v1"
    scenario = build_compatibility_fixture(source)
    scenario_file = tmp_path / "scenario.json"
    _write(scenario_file, scenario.to_dict())
    source_before = _snapshot(source)
    before = tree_snapshot(ROOT)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT), MOLT_KERNEL_BACKEND="v1")
    commands = [["--help"], ["schema", "validate", "--all"], ["doctor", "--deps"],
                ["doctor", "--repo-policy"],
                ["schema", "docs", "--output", str(tmp_path / "protocol.md")],
                ["audit", "--state", str(source), "--scenario", str(scenario_file)]]
    for command in commands:
        completed = _invoke(command, env)
        assert completed.returncode == 0, (command, completed.stdout, completed.stderr)
    assert _snapshot(source) == source_before
    _assert_unchanged(before)


def test_kernel_commands_do_not_write_repository(tmp_path) -> None:
    import json

    from test_kernel_transactions import TS, _binary, _run, _server

    binary = _binary()
    state = tmp_path / "kernel"
    _run(binary, "init", "--state", state)
    payload = tmp_path / "synthetic.json"
    payload.write_text('{"provenance":"synthetic"}')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT), MOLT_KERNEL_BACKEND="kernel")
    before = tree_snapshot(ROOT)
    with _server(binary, state) as socket:
        env["MOLT_KERNEL_SOCKET"] = socket
        def run(command):
            result = _invoke(command, env)
            assert result.returncode == 0, (command, result.stdout, result.stderr)
            return json.loads(result.stdout)
        digest = run(["object", "put", str(payload)])["hash"]
        roots = tmp_path / "roots.json"
        roots.write_text(json.dumps({"workspace": {"synthetic.json": {
            "type": "file", "mode": 0o600, "size": payload.stat().st_size, "sha256": digest,
        }}}))
        manifest = run(["manifest", "create", str(roots)])["hash"]
        first = run(["commit", "--manifest", manifest, "--request-id", "zero-write", "--at", TS])
        assert run(["current"]) == first
        assert run(["request-status", "zero-write"])["result"] == first
        run(["inspect"])
        run(["manifest", "get", manifest])
        run(["object", "get", digest, "--out", str(tmp_path / "download.json")])
        run(["audit"])
    env["MOLT_KERNEL_BIN"] = str(binary)
    for command in (["checkpoint", "export", "--state", str(state), "--out", str(tmp_path / "checkpoint.json"),
                     "--created-at", TS],
                    ["recover", "--state", str(state)],
                    ["audit", "--state", str(state), "--checkpoint", str(tmp_path / "checkpoint.json")]):
        result = _invoke(command, env)
        assert result.returncode == 0, (command, result.stdout, result.stderr)
    _assert_unchanged(before)
