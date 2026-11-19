"""共享 fixture：一次构建 demo 证据链，各测试复制后按需篡改。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from molt.core import write_policy
from molt.core.store import StateStore
from molt.demo import build_demo_state

ROOT = Path(__file__).resolve().parents[1]
write_policy.DEFAULT_BACKEND = "v1"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def demo_artifacts(tmp_path_factory: pytest.TempPathFactory):
    work = tmp_path_factory.mktemp("demo")
    store, scenario, summary = build_demo_state(ROOT, work)
    return store, scenario, summary


@pytest.fixture()
def demo_copy(demo_artifacts, tmp_path: Path):
    source, scenario, summary = demo_artifacts
    target = tmp_path / "state"
    shutil.copytree(source.root, target)
    return StateStore(target), scenario, summary
