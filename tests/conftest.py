"""共享 synthetic v1 审计夹具，各测试复制后按需篡改。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from molt.core import write_policy
from molt.core.store import StateStore

ROOT = Path(__file__).resolve().parents[1]
write_policy.DEFAULT_BACKEND = "v1"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def demo_artifacts(tmp_path_factory: pytest.TempPathFactory):
    from synthetic_audit import build_audit_fixture

    work = tmp_path_factory.mktemp("synthetic-audit")
    store, scenario, summary = build_audit_fixture(work / "state")
    return store, scenario, summary


@pytest.fixture()
def demo_copy(demo_artifacts, tmp_path: Path):
    source, scenario, summary = demo_artifacts
    target = tmp_path / "state"
    shutil.copytree(source.root, target)
    return StateStore(target), scenario, summary
