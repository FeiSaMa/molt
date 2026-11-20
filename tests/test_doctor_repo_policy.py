"""D6：``molt doctor --repo-policy`` 只读检查协作文件、冻结面与契约域。"""

from __future__ import annotations

import os
from pathlib import Path

from molt.cli import REPO_POLICY_CONTRACT, REPO_POLICY_FILES, REPO_POLICY_FROZEN, main

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}


def _make_repo(root: Path, *, skip: frozenset[str] = frozenset()) -> Path:
    """构造最小仓库夹具；``skip`` 里的相对路径不创建。"""
    for rel in REPO_POLICY_FILES:
        if rel in skip:
            continue
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# placeholder\n", encoding="utf-8")
    if "schemas/v1/*.schema.json" not in skip:
        path = root / "schemas/v1/synthetic.schema.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    for rel in ("schemas", "docs/decisions"):
        if rel not in skip:
            (root / rel).mkdir(parents=True, exist_ok=True)
    if "molt/core/ports.py" not in skip:
        path = root / "molt/core/ports.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    if "docs/protocol.md" not in skip:
        (root / "docs").mkdir(parents=True, exist_ok=True)
        (root / "docs/protocol.md").write_text("", encoding="utf-8")
    return root


def _snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.is_symlink():
            snapshot[str(relative)] = "symlink:" + os.readlink(path)
        elif path.is_dir():
            snapshot[str(relative)] = "dir"
        else:
            import hashlib

            snapshot[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def test_repo_policy_ok_on_complete_fixture(tmp_path: Path, capsys) -> None:
    repo = _make_repo(tmp_path / "repo")
    assert main(["--root", str(repo), "doctor", "--repo-policy"]) == 0
    assert not (repo / "shells").exists()
    assert not (repo / "bench").exists()
    captured = capsys.readouterr()
    assert "repo-policy ok" in captured.out
    assert "repo-policy frozen:" in captured.out
    assert "repo-policy contract:" in captured.out
    assert captured.err == ""


def test_repo_policy_missing_files_exit_1(tmp_path: Path, capsys) -> None:
    repo = _make_repo(tmp_path / "repo", skip=frozenset({".github/CODEOWNERS", "molt/kernel_client.py", "schemas/v1/*.schema.json"}))
    assert main(["--root", str(repo), "doctor", "--repo-policy"]) == 1
    captured = capsys.readouterr()
    assert "repo-policy missing: .github/CODEOWNERS" in captured.err
    assert "repo-policy missing: molt/kernel_client.py" in captured.err
    assert "repo-policy missing: schemas/v1/*.schema.json" in captured.err
    assert "repo-policy ok" not in captured.out


def test_repo_policy_does_not_write(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    before = _snapshot(repo)
    assert main(["--root", str(repo), "doctor", "--repo-policy"]) == 0
    assert _snapshot(repo) == before


def test_repo_policy_matches_actual_checkout(capsys) -> None:
    """真实判断当前检出：协作文件可能尚未合入（B2d），结果必须如实反映。"""
    expected: list[str] = [rel for rel in REPO_POLICY_FILES if not (ROOT / rel).is_file()]
    expected.extend(pattern for pattern in REPO_POLICY_FROZEN if not any(ROOT.glob(pattern)))
    expected.extend(rel for rel in REPO_POLICY_CONTRACT if not (ROOT / rel).exists())
    code = main(["--root", str(ROOT), "doctor", "--repo-policy"])
    captured = capsys.readouterr()
    assert code == (0 if not expected else 1)
    if expected:
        assert all(f"repo-policy missing: {item}" in captured.err for item in expected)
    else:
        assert "repo-policy ok" in captured.out
