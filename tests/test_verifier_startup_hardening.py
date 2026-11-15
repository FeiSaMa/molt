"""启动加固：保留的 code-repair verifier 只从仓库外私有 staging 加载候选解。

覆盖 D1/D2：
- 15 题正确解全部通过、确定性错误解全部失败；
- 七项启动 / 发现攻击在加固版上不能改变判定；
- benign side-file（helper / 启动 hook / 模块遮蔽 / pytest 配置）不影响正确解；
- 非法 solution 类型、超时、启动异常与临时资源清理；
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import pytest

from molt.core.types import Task

ROOT = Path(__file__).resolve().parents[1]
TRUSTED_PATH = "/usr/bin:/bin"
TRUSTED_ENV_KEYS = {
    "PATH",
    "HOME",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONNOUSERSITE",
    "PYTHONHASHSEED",
    "PYTHONPATH",
    "PWD",
}
FORBIDDEN_ENV_KEYS = {"OLDPWD", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONWARNINGS"}

SHELLS = {"code-repair": ("shells/code-repair/verifier.py", "PytestVerifier", "demo")}
ATTACK_TASK = {"code-repair": "smoke-add"}
WRONG_SOLUTION = {"code-repair": "def add(a, b):\n    return 0\n"}

_MODULES: dict[str, object] = {}


def _module(shell_id: str):
    if shell_id not in _MODULES:
        rel_path, _, _ = SHELLS[shell_id]
        name = f"molt_startup_hardening_{shell_id.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(name, ROOT / rel_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        _MODULES[shell_id] = module
    return _MODULES[shell_id]


def _verifier(shell_id: str, repo_root: Path = ROOT, **kwargs):
    _, class_name, _ = SHELLS[shell_id]
    return getattr(_module(shell_id), class_name)(repo_root, **kwargs)


def _task_index() -> tuple[dict[str, Task], dict[str, Path]]:
    tasks: dict[str, Task] = {}
    dirs: dict[str, Path] = {}
    for path in sorted((ROOT / "bench").glob("**/task.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        tasks[data["task_id"]] = Task.from_dict(data)
        dirs[data["task_id"]] = path.parent
    return tasks, dirs


TASKS, TASK_DIRS = _task_index()


def _scenario_task_ids(scenario_id: str) -> list[str]:
    data = json.loads(
        (ROOT / "scenarios" / scenario_id / "scenario.json").read_text(encoding="utf-8")
    )
    task_sets = data["task_sets"]
    return list(task_sets["search"]) + list(task_sets["report"])


ACTIVE = [
    (shell_id, task_id)
    for shell_id, (_, _, scenario_id) in SHELLS.items()
    for task_id in _scenario_task_ids(scenario_id)
]
assert len(ACTIVE) == 15, f"活动题集应为 15 题，实际 {len(ACTIVE)}"


def _correct_solution(task: Task) -> str:
    fixture = task.ext.get("fixture")
    if isinstance(fixture, dict) and fixture.get("solution"):
        return str(fixture["solution"])
    path = TASK_DIRS[task.task_id] / "solutions" / "reference.py"
    return path.read_text(encoding="utf-8")


def _workdir(tmp_path: Path, name: str = "work") -> Path:
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_solution(workdir: Path, content: str) -> None:
    (workdir / "solution.py").write_text(content, encoding="utf-8")


def _run(shell_id: str, task: Task, workdir: Path):
    verifier = _verifier(shell_id)
    before = verifier.guard(task, workdir)
    return verifier.verify(task, workdir, before=before)


def _staging_dirs() -> set[Path]:
    return set(Path(tempfile.gettempdir()).glob("molt-verify-*"))


def _within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


# -- 15 题正反例 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("shell_id", "task_id"), ACTIVE, ids=[f"{s}-{t}" for s, t in ACTIVE]
)
def test_active_task_correct_solution_passes(tmp_path: Path, shell_id: str, task_id: str) -> None:
    task = TASKS[task_id]
    workdir = _workdir(tmp_path)
    _write_solution(workdir, _correct_solution(task))

    outcome = _run(shell_id, task, workdir)

    assert outcome.passed is True
    assert outcome.outcome == "passed"
    assert outcome.matches is True
    assert outcome.timed_out is False
    if shell_id != "code-repair":
        assert outcome.diagnostic["cases"]
        assert all(case["passed"] is True for case in outcome.diagnostic["cases"])


@pytest.mark.parametrize(
    ("shell_id", "task_id"), ACTIVE, ids=[f"{s}-{t}" for s, t in ACTIVE]
)
def test_active_task_wrong_solution_fails(tmp_path: Path, shell_id: str, task_id: str) -> None:
    task = TASKS[task_id]
    workdir = _workdir(tmp_path)
    _write_solution(workdir, 'raise RuntimeError("wrong solution")\n')

    outcome = _run(shell_id, task, workdir)

    assert outcome.passed is False
    assert outcome.outcome == "failed"
    assert outcome.matches is True
    assert outcome.timed_out is False


# -- 七项攻击：加固版必须拒绝 --------------------------------------------


FORGED_JSON = (
    '{"cases": [{"case_id": "forged", "expected": null, "actual": null, '
    '"passed": true}], "error": null}'
)
ATTACKS = [
pytest.param(
        "code-repair",
        "sitecustomize-early-exit",
        {"sitecustomize.py": "import os\n\nos._exit(0)\n"},
        id="code-repair-sitecustomize-early-exit",
    ),
pytest.param(
        "code-repair",
        "pytest-shadow",
        {"pytest.py": "raise SystemExit(0)\n"},
        id="code-repair-pytest-shadow",
    ),
pytest.param(
        "code-repair",
        "pytest-ini-collect-only",
        {"pytest.ini": "[pytest]\naddopts = --collect-only\n"},
        id="code-repair-pytest-ini-collect-only",
    ),
pytest.param(
        "code-repair",
        "conftest-force-pass",
        {
            "conftest.py": (
                "def pytest_collection_modifyitems(items):\n"
                "    for item in items:\n"
                "        item.obj = lambda *args, **kwargs: None\n"
            )
        },
        id="code-repair-conftest-force-pass",
    )
]


@pytest.mark.parametrize(("shell_id", "attack", "side_files"), ATTACKS)
def test_attack_side_files_cannot_change_verdict(
    tmp_path: Path, shell_id: str, attack: str, side_files: dict[str, str]
) -> None:
    task = TASKS[ATTACK_TASK[shell_id]]
    workdir = _workdir(tmp_path)
    _write_solution(workdir, WRONG_SOLUTION[shell_id])
    for filename, content in side_files.items():
        (workdir / filename).write_text(content, encoding="utf-8")

    outcome = _run(shell_id, task, workdir)

    assert outcome.passed is False, f"{attack} 仍可改变判定"
    assert outcome.outcome == "failed"
    assert outcome.matches is True
    assert outcome.timed_out is False


# -- benign side-file 正例 ------------------------------------------------


BENIGN_SIDE_FILES = {
    "helper.py": "VALUE = 1\n",
    "data.txt": "benign\n",
    "sitecustomize.py": "BENIGN = True\n",
    "pytest.py": "BENIGN = True\n",
    "json.py": "BENIGN = True\n",
    "decimal.py": "BENIGN = True\n",
    "pytest.ini": "[pytest]\naddopts = --collect-only\n",
    "conftest.py": "def pytest_collection_modifyitems(items):\n    pass\n",
}


@pytest.mark.parametrize("shell_id", list(SHELLS))
def test_benign_side_files_do_not_break_correct_solution(
    tmp_path: Path, shell_id: str
) -> None:
    task = TASKS[ATTACK_TASK[shell_id]]

    correct_dir = _workdir(tmp_path, "correct")
    _write_solution(correct_dir, _correct_solution(task))
    for filename, content in BENIGN_SIDE_FILES.items():
        (correct_dir / filename).write_text(content, encoding="utf-8")
    correct = _run(shell_id, task, correct_dir)
    assert correct.passed is True
    assert correct.matches is True
    assert correct.timed_out is False

    wrong_dir = _workdir(tmp_path, "wrong")
    _write_solution(wrong_dir, WRONG_SOLUTION[shell_id])
    for filename, content in BENIGN_SIDE_FILES.items():
        (wrong_dir / filename).write_text(content, encoding="utf-8")
    wrong = _run(shell_id, task, wrong_dir)
    assert wrong.passed is False
    assert wrong.matches is True


# -- 真实 argv / env / cwd / 导入位置 ------------------------------------


_OBSERVER = """

import json as _obs_json
import os as _obs_os
import stat as _obs_stat
import sys as _obs_sys

_OBS_DIR = _obs_os.path.dirname(_obs_os.path.abspath(__file__))
with open(__OBS_PATH__, "w", encoding="utf-8") as _obs_handle:
    _obs_json.dump(
        {
            "cwd": _obs_os.getcwd(),
            "file": _obs_os.path.abspath(__file__),
            "argv": list(_obs_sys.argv),
            "env": dict(_obs_os.environ),
            "sys_path": list(_obs_sys.path),
            "listing": sorted(_obs_os.listdir(_OBS_DIR)),
            "staging_mode": _obs_stat.S_IMODE(_obs_os.stat(_OBS_DIR).st_mode),
            "home_mode": _obs_stat.S_IMODE(
                _obs_os.stat(_obs_os.environ["HOME"]).st_mode
            ),
        },
        _obs_handle,
    )
"""


@pytest.mark.parametrize("shell_id", list(SHELLS))
def test_candidate_process_is_isolated_from_workdir(
    tmp_path: Path, shell_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 父进程投毒：子进程 env 从零构造，不得继承任何 PYTEST_*。
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "poison")
    monkeypatch.setenv("PYTEST_VERSION", "poison")
    task = TASKS[ATTACK_TASK[shell_id]]
    obs_path = tmp_path / "observation.json"
    workdir = _workdir(tmp_path)
    solution = _correct_solution(task).rstrip("\n") + "\n" + _OBSERVER.replace(
        "__OBS_PATH__", repr(str(obs_path))
    )
    _write_solution(workdir, solution)

    outcome = _run(shell_id, task, workdir)
    assert outcome.passed is True
    assert outcome.matches is True

    obs = json.loads(obs_path.read_text(encoding="utf-8"))
    staging = Path(obs["cwd"]).resolve()
    workdir_resolved = workdir.resolve()

    assert Path(obs["file"]).resolve().parent == staging
    assert staging != workdir_resolved
    assert not _within(staging, workdir_resolved)
    assert not _within(workdir_resolved, staging)
    assert obs["staging_mode"] == 0o700
    assert obs["home_mode"] == 0o700

    env = obs["env"]
    assert set(env) == TRUSTED_ENV_KEYS
    assert env["PATH"] == TRUSTED_PATH
    assert Path(env["PWD"]).resolve() == staging
    assert Path(env["PYTHONPATH"]).resolve() == staging
    assert Path(env["HOME"]).resolve() == staging / "home"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["PYTHONHASHSEED"] == "0"
    for key in FORBIDDEN_ENV_KEYS:
        assert key not in env
    assert "PYTEST_ADDOPTS" not in env
    assert "PYTEST_CURRENT_TEST" not in env
    assert "PYTEST_VERSION" not in env

    assert str(workdir_resolved) not in " ".join(obs["argv"])
    for arg in obs["argv"]:
        assert not _within(Path(arg).resolve(), workdir_resolved)
    for entry in obs["sys_path"]:
        assert not _within(Path(entry or staging).resolve(), workdir_resolved)

    expected_listing = ["home", "sitecustomize.py", "solution.py"]
    if shell_id == "code-repair":
        expected_listing.extend(["molt_verify_env.py", "pytest.ini"])
    assert obs["listing"] == sorted(expected_listing)


# -- 非法 solution 类型 ---------------------------------------------------


@pytest.mark.parametrize("shell_id", list(SHELLS))
@pytest.mark.parametrize("kind", ["missing", "directory", "symlink", "fifo"])
def test_invalid_solution_is_rejected_without_executing(
    tmp_path: Path, shell_id: str, kind: str
) -> None:
    if kind == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("平台不支持 FIFO")
    task = TASKS[ATTACK_TASK[shell_id]]
    workdir = _workdir(tmp_path)
    source = workdir / "solution.py"
    marker = tmp_path / "executed.txt"

    if kind == "directory":
        source.mkdir()
    elif kind == "symlink":
        payload = workdir / "payload.py"
        payload.write_text(f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")
        source.symlink_to(payload)
    elif kind == "fifo":
        os.mkfifo(source)

    started = time.monotonic()
    outcome = _run(shell_id, task, workdir)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, "非法 solution 不应阻塞"
    assert outcome.passed is False
    assert outcome.outcome == "failed"
    assert outcome.matches is True
    assert outcome.timed_out is False
    assert marker.exists() is False
    if shell_id == "code-repair":
        assert outcome.diagnostic["output_tail"] == "invalid_solution"
    else:
        assert outcome.diagnostic["error"] == "invalid_solution"
        assert outcome.diagnostic["cases"] == []




# -- 清理：成功 / 失败 / 超时 / 启动异常 ---------------------------------


@pytest.mark.parametrize("shell_id", list(SHELLS))
def test_staging_is_cleaned_up_on_every_path(tmp_path: Path, shell_id: str) -> None:
    task = TASKS[ATTACK_TASK[shell_id]]
    before = _staging_dirs()

    correct_dir = _workdir(tmp_path, "correct")
    _write_solution(correct_dir, _correct_solution(task))
    assert _run(shell_id, task, correct_dir).passed is True

    wrong_dir = _workdir(tmp_path, "wrong")
    _write_solution(wrong_dir, WRONG_SOLUTION[shell_id])
    assert _run(shell_id, task, wrong_dir).passed is False

    invalid_dir = _workdir(tmp_path, "invalid")
    assert _run(shell_id, task, invalid_dir).passed is False

    timeout_dir = _workdir(tmp_path, "timeout")
    _write_solution(timeout_dir, "while True:\n    pass\n")
    timeout_outcome = _verifier(shell_id).verify(
        replace(task, timeout_s=0.3), timeout_dir, before=None
    )
    assert timeout_outcome.timed_out is True
    assert timeout_outcome.outcome == "timeout"

    broken = _verifier(shell_id, python="/nonexistent/python-for-molt-test")
    startup_dir = _workdir(tmp_path, "startup")
    _write_solution(startup_dir, _correct_solution(task))
    startup_outcome = broken.verify(task, startup_dir)
    assert startup_outcome.passed is False
    assert startup_outcome.timed_out is False

    assert _staging_dirs() == before




