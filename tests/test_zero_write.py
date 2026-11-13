"""零写入验收：文件系统树快照（不是 git status）。

子进程一律带 ``PYTHONDONTWRITEBYTECODE=1``；缓存目录与 ``.git`` / ``.venv`` 不参与快照。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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


COMMANDS = [
    ["schema", "validate", "--all"],
    ["doctor", "--deps"],
    ["genome", "hash", "shells/code-repair/genomes/v0.json"],
    ["genome", "verify", "shells/code-repair/genomes/v0.json"],
    ["gate", "--fixture", "tests/fixtures/gate/"],
    ["audit", "--scenario", "demo"],
    ["calibrate", "--scenario", "demo", "--min-pass", "0"],
]


def test_commands_do_not_write_repository() -> None:
    before = tree_snapshot(ROOT)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="molt-propose-state-") as propose_state:
        commands = [
            *COMMANDS,
            ["propose", "--scenario", "demo", "--state", propose_state, "--offline"],
            ["export", "--scenario", "demo", "--out", str(Path(propose_state) / "web-data")],
            [
                "demo",
                "--scenario",
                "demo",
                "--mode",
                "replay",
                "--state",
                str(Path(propose_state) / "demo-state"),
                "--out",
                str(Path(propose_state) / "demo-web"),
            ],
        ]
        for command in commands:
            completed = subprocess.run(
                [sys.executable, "-m", "molt", *command],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=240,
            )
            assert completed.returncode == 0, (command, completed.stdout, completed.stderr)

        # serve 是长驻进程：启动 → 读 stdout 的一行 listen → terminate；
        # token/state 都指向临时目录，仓库树不得变化。
        serve = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "molt",
                "serve",
                "--port",
                "0",
                "--state",
                str(Path(propose_state) / "serve-state"),
                "--token-file",
                str(Path(propose_state) / "serve-state" / "token"),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            line = serve.stdout.readline() if serve.stdout else ""
            assert line.startswith("{"), (line, serve.stderr.read() if serve.stderr else "")
            startup = json.loads(line)
            assert startup["listen"].startswith("http://127.0.0.1:")
            assert Path(startup["token_file"]).is_file()
        finally:
            serve.terminate()
            try:
                serve.wait(timeout=10)
            except subprocess.TimeoutExpired:
                serve.kill()
                serve.wait()
    after = tree_snapshot(ROOT)
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(key for key in set(before) & set(after) if before[key] != after[key])
    assert added == [], f"新增路径: {added}"
    assert removed == [], f"删除路径: {removed}"
    assert changed == [], f"内容变化: {changed}"
