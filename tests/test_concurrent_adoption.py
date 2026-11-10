"""D6：两进程同时 adopt，一成一败；不排队、不重试。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from molt.core.store import StateStore

ROOT = Path(__file__).resolve().parents[1]
FIXED = "2026-01-01T00:00:00Z"


def test_two_processes_one_wins(tmp_path: Path) -> None:
    state = tmp_path / "state"
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    roots = {"molt/core": tmp_path / "repo" / "molt" / "core"}
    roots["molt/core"].mkdir(parents=True)
    (roots["molt/core"] / "file.py").write_text("# core\n", encoding="utf-8")

    script = f"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})
from molt.core.errors import TxLocked
from molt.core.store import StateStore
from molt.core.types import Genome
index = sys.argv[1]
store = StateStore(Path({str(state)!r}))
genome = Genome.from_dict(json.loads(Path({str(ROOT / "shells/code-repair/genomes/v0.json")!r}).read_text()))
roots = {{'molt/core': Path({str(roots["molt/core"])!r})}}
ready = Path({str(barrier)!r}) / ('ready-' + index)
ready.write_text('1')
deadline = time.time() + 10
while len(list(Path({str(barrier)!r}).glob('ready-*'))) < 2:
    if time.time() > deadline:
        print('barrier-timeout')
        sys.exit(2)
    time.sleep(0.01)
try:
    archive = store.adopt(genome, roots, result_refs=['r1'], note='race',
                          adopted_at='{FIXED}', lock_hold_s=1.2)
    print('ok', archive.generation)
except TxLocked as exc:
    print('tx_locked', exc.code)
"""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(index)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(2)
    ]
    outputs = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        outputs.append((process.returncode, stdout.strip(), stderr.strip()))

    successes = [item for item in outputs if item[1].startswith("ok ")]
    locked = [item for item in outputs if "tx_locked" in item[1]]
    assert len(successes) == 1, outputs
    assert len(locked) == 1, outputs
    assert successes[0][0] == 0 and locked[0][0] == 0

    store = StateStore(state)
    pointer = store.read_current()
    assert pointer is not None
    assert pointer["generation"] == 1
    archives = store.archive.list()
    assert len(archives) == 1
    assert len(list((store.root / "journal").glob("*.json"))) == 0
