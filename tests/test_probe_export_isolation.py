"""导出层不过滤 probe；隔离必须靠 state 目录白名单。"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

from molt.core.store import StateStore
from molt.core.types import Run

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/records/run.json").read_text(encoding="utf-8"))
GENOME_ID = "sha256:e61508295c0bbb4c3832f0b56e0496f4fa0df631dfe0b8bdaa803f1547833db4"


def _run(run_id: str, *, ext: dict | None = None) -> Run:
    data = copy.deepcopy(FIXTURE)
    data["run_id"] = run_id
    data["scenario_version"] = "demo-6"
    data["genome_id"] = GENOME_ID
    data["task_id"] = "smoke-add"
    data["ext"] = ext or {}
    return Run.from_dict(data)


def _export(state: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "molt",
            "export",
            "--scenario",
            "demo",
            "--state",
            str(state),
            "--out",
            str(out),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin", "HOME": "/tmp"},
    )


def _payload(out: Path) -> tuple[list[str], int]:
    runs = json.loads((out / "runs.json").read_text(encoding="utf-8"))["runs"]
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    return [item["run_id"] for item in runs], int(index["counts"]["runs"])


def _export_one(tmp_path: Path, run: Run, name: str) -> tuple[list[str], int]:
    state = tmp_path / f"{name}-state"
    store = StateStore(state)
    store.ensure()
    store.write_run(run)
    out = tmp_path / f"{name}-out"
    completed = _export(state, out)
    assert completed.returncode == 0, completed.stderr
    return _payload(out)


def test_demo_only_state_has_no_probe_run(tmp_path: Path) -> None:
    ids, count = _export_one(tmp_path, _run("demo-run-1"), "demo")
    assert ids == ["demo-run-1"]
    assert count == 1
    assert not any(run_id.startswith("probe-") or "-probe-" in run_id for run_id in ids)


def test_probe_run_is_exported_not_filtered(tmp_path: Path) -> None:
    ids, count = _export_one(
        tmp_path,
        _run("probe-order-1-a1-deadbeef", ext={"probe": {"cell": "blind"}}),
        "probe",
    )
    assert ids == ["probe-order-1-a1-deadbeef"]
    assert count == 1


def test_demo_pi_shape_with_probe_cell_none_is_not_dropped(tmp_path: Path) -> None:
    ids, count = _export_one(
        tmp_path,
        _run("demo-pi-1", ext={"probe": {"cell": None}}),
        "demo-pi",
    )
    assert ids == ["demo-pi-1"]
    assert count == 1


def test_pilot_states_are_disjoint_from_repo_demo_states() -> None:
    commands = json.loads((ROOT / "probes/pilot-commands.json").read_text(encoding="utf-8"))
    probe_states = [
        Path(cmd["state"]).resolve() for cmd in commands["commands"] if cmd.get("state")
    ]
    demo_states = [p.resolve() for p in (ROOT / "state", ROOT / "state/demo") if p.is_dir()]
    for demo in demo_states:
        for state in probe_states:
            assert not state.is_relative_to(demo), f"probe state {state} 与 demo {demo} 相交"
            assert not demo.is_relative_to(state), f"demo {demo} 与 probe state {state} 相交"
