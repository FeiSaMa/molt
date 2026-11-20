"""D3：genome 规范化哈希、artifact 冻结、三维锁定。"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from molt import schema
from molt.core.errors import ValidationError
from molt.core.genome import compute_genome_id, verify_genome
from molt.core.types import Genome

CODE_ROOT = Path(__file__).resolve().parents[1]
ROOT = CODE_ROOT
GENOME = ROOT / "synthetic/shell/genomes/v0.json"


@pytest.fixture(autouse=True)
def synthetic_tree(tmp_path, monkeypatch):
    from synthetic_genome import build_genome_tree

    root = tmp_path / "synthetic-repo"
    build_genome_tree(root)
    shutil.copytree(CODE_ROOT / "schemas", root / "schemas")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", root)
    monkeypatch.setattr(sys.modules[__name__], "GENOME", root / "synthetic/shell/genomes/v0.json")


def load_genome() -> Genome:
    return Genome.from_dict(json.loads(GENOME.read_text(encoding="utf-8")))


def test_v0_genome_verifies() -> None:
    assert verify_genome(load_genome(), ROOT) == []


def test_committed_genomes_share_scenario_version() -> None:
    scenario = json.loads((ROOT / "synthetic/scenario/scenario.json").read_text(encoding="utf-8"))
    genomes = sorted((ROOT / "synthetic/shell/genomes").glob("*.json"))
    assert len(genomes) == 2
    for path in genomes:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["scenario_version"] == scenario["scenario_version"]
    pi = json.loads((ROOT / "synthetic/shell/genomes/v0-pi.json").read_text(encoding="utf-8"))
    v0 = json.loads((ROOT / "synthetic/shell/genomes/v0.json").read_text(encoding="utf-8"))
    assert pi["parent_id"] == v0["genome_id"]
    assert v0["parent_id"] is None


def test_hash_is_stable_across_calls_and_processes() -> None:
    data = json.loads(GENOME.read_text(encoding="utf-8"))
    first = compute_genome_id(data)
    second = compute_genome_id(copy.deepcopy(data))
    assert first == second == data["genome_id"]

    env = dict(os.environ)
    env["PYTHONPATH"] = str(CODE_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for _ in range(2):
        completed = subprocess.run(
            [sys.executable, "-c", "import json,sys; from molt.core.genome import compute_genome_id; print(compute_genome_id(json.load(open(sys.argv[1]))))", str(GENOME)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == first


def _copy_shell(tmp_path: Path) -> Path:
    for name in ("synthetic", "schemas"):
        shutil.copytree(ROOT / name, tmp_path / name)
    return tmp_path


@pytest.mark.parametrize(
    "rel",
    [
        "synthetic/shell/harness.py",
        "synthetic/shell/verifier.py",
        "synthetic/shell/task_provider.py",
        "synthetic/shell/switches.schema.json",
        "synthetic/shell/prompts/code-repair.md",
        "synthetic/shell/tools/read_file.json",
    ],
)
def test_artifact_byte_change_breaks_verify(tmp_path: Path, rel: str) -> None:
    root = _copy_shell(tmp_path)
    genome = load_genome()
    assert verify_genome(genome, root) == []
    target = root / rel
    target.write_bytes(target.read_bytes() + b"\n# mutate\n")
    errors = verify_genome(genome, root)
    assert any("artifact 哈希不一致" in error for error in errors)


def test_stored_id_must_match_recomputed_id(tmp_path: Path) -> None:
    data = json.loads(GENOME.read_text(encoding="utf-8"))
    data["switches"]["thinking"] = "off"
    genome = Genome.from_dict(data)
    errors = verify_genome(genome, ROOT)
    assert any("genome_id 不一致" in error for error in errors)


def test_new_unreferenced_candidate_file_does_not_break_verify(tmp_path: Path) -> None:
    root = _copy_shell(tmp_path)
    new_file = root / "synthetic/shell/prompts/extra-candidate.md"
    new_file.write_text("new candidate prompt\n", encoding="utf-8")
    assert verify_genome(load_genome(), root) == []


def test_fourth_dimension_rejected() -> None:
    data = json.loads(GENOME.read_text(encoding="utf-8"))
    data["switches"]["compaction"] = "auto"
    with pytest.raises(ValidationError):
        schema.validate_record("genome", data, ROOT)


def test_compaction_replacing_a_dimension_rejected() -> None:
    data = json.loads(GENOME.read_text(encoding="utf-8"))
    data["switches"] = {"thinking": "low", "context": "full", "compaction": "auto"}
    with pytest.raises(ValidationError):
        schema.validate_record("genome", data, ROOT)


def test_fourth_dimension_in_ext_is_allowed_but_not_core() -> None:
    data = json.loads(GENOME.read_text(encoding="utf-8"))
    data["ext"] = {"future_dimension": {"compaction": "auto"}}
    record = schema.validate_record("genome", data, ROOT)
    assert record.switches.to_dict() == {
        "thinking": "low",
        "context": "full",
        "tools": "bash",
    }


def test_skills_must_be_empty_in_v1() -> None:
    data = json.loads(GENOME.read_text(encoding="utf-8"))
    data["skills_ref"] = ["skills/demo"]
    with pytest.raises(ValidationError):
        schema.validate_record("genome", data, ROOT)


def test_all_6_combinations_are_valid() -> None:
    data = json.loads(GENOME.read_text(encoding="utf-8"))
    combinations = 0
    for thinking in ("off", "low", "high"):
        for context in ("full", "minimal"):
            candidate = copy.deepcopy(data)
            candidate["switches"] = {
                "thinking": thinking,
                "context": context,
                "tools": "bash",
            }
            candidate["genome_id"] = compute_genome_id(candidate)
            schema.validate_record("genome", candidate, ROOT)
            combinations += 1
    assert combinations == 6


def test_genome_verify_cli_fails_on_mutation(tmp_path: Path) -> None:
    root = _copy_shell(tmp_path)
    target = root / "synthetic/shell/verifier.py"
    target.write_bytes(target.read_bytes() + b"\n# mutate\n")
    errors = verify_genome(Genome.from_dict(json.loads((root / "synthetic/shell/genomes/v0.json").read_text())), root)
    assert errors
    assert any("artifact 哈希不一致" in error for error in errors)


def _run_cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(CODE_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(ROOT / "tests/run_cli.py"), "--root", str(root), *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_scenario_validate_passes_then_fails_after_verifier_change(tmp_path: Path) -> None:
    root = _copy_shell(tmp_path)
    genome = Genome.from_dict(json.loads((root / "synthetic/shell/genomes/v0.json").read_text()))
    assert verify_genome(genome, root) == []
    verifier = root / "synthetic/shell/verifier.py"
    verifier.write_bytes(verifier.read_bytes() + b"\n# breaking change\n")
    assert any("artifact 哈希不一致" in error for error in verify_genome(genome, root))


def test_new_candidate_file_does_not_require_version_bump(tmp_path: Path) -> None:
    root = _copy_shell(tmp_path)
    data = json.loads((root / "synthetic/shell/genomes/v0.json").read_text(encoding="utf-8"))
    data["parent_id"] = data["genome_id"]
    data["switches"] = {"thinking": "low", "context": "full", "tools": "bash"}
    data["genome_id"] = compute_genome_id(data)
    new_path = root / "synthetic/shell/genomes/v1-candidate.json"
    new_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    candidate = Genome.from_dict(data)
    assert verify_genome(candidate, root) == []
    assert candidate.scenario_version == load_genome().scenario_version
    assert candidate.parent_id == load_genome().genome_id
