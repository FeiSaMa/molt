"""Synthetic opaque v1 genomes and artifact bytes, never loaded as code."""

import json
from pathlib import Path

from molt.core.genome import compute_genome_id
from molt.core.hash import sha256_file
from molt.core.types import Budget, Genome, Switches


def synthetic_genome() -> Genome:
    genome = Genome(
        "1.0.0", "synthetic-genome-1", "pending", None, "synthetic", "1.0.0",
        Switches("low", "full", "bash"), "synthetic/shell/prompts/code-repair.md",
        "synthetic/shell/tools", [], {"runtime_id": "fixture", "version": "synthetic-1"},
        Budget(100, 1000, 1, 1),
        {"synthetic/shell/prompts/code-repair.md": "a" * 64,
         "synthetic/shell/tools/read_file.json": "a" * 64},
        {"provenance": "synthetic"},
    )
    data = genome.to_dict()
    data["genome_id"] = compute_genome_id(data)
    return Genome.from_dict(data)


def build_genome_tree(root: Path) -> None:
    """Materialize invented bytes in a fresh test root, no retired assets."""
    root.mkdir(parents=True, exist_ok=False)
    genome = synthetic_genome().to_dict()
    for name in ("manifest.json", "harness.py", "verifier.py", "task_provider.py",
                 "switches.schema.json", "prompts/code-repair.md", "tools/read_file.json"):
        rel = f"synthetic/shell/{name}"
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"synthetic opaque artifact: {name}\n", encoding="utf-8")
        genome["artifacts"][rel] = sha256_file(p)
    genome["genome_id"] = compute_genome_id(genome)
    genomes = root / "synthetic/shell/genomes"
    genomes.mkdir()
    (genomes / "v0.json").write_text(json.dumps(genome), encoding="utf-8")
    child = {**genome, "parent_id": genome["genome_id"],
             "runtime": {"runtime_id": "fixture", "version": "synthetic-2"}}
    child["genome_id"] = compute_genome_id(child)
    (genomes / "v0-pi.json").write_text(json.dumps(child), encoding="utf-8")
    scenario = root / "synthetic/scenario"
    scenario.mkdir()
    (scenario / "scenario.json").write_text(
        json.dumps({"scenario_version": genome["scenario_version"], "provenance": "synthetic"}),
        encoding="utf-8",
    )
