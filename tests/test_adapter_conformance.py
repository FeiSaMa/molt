"""D3 adapter conformance：两 shell（code-repair / svc-config）× 两 runtime。

覆盖：

- ``Runtime`` / ``TaskProvider`` / ``Verifier`` / ``Harness`` 的 ``@runtime_checkable`` 协议；
- 每个 (shell, runtime) 组合都能通过同一 ``build_engine`` 跑出合法 run；
- deterministic runtime 的 ``usage`` 全 0、``cost_source=none``、无网络/无 LLM；
- ``molt search --scenario svc-config --runtime deterministic`` 秒级、成本 0；
- 两 shell 的证据都由同一个 ``molt audit`` 重算通过；
- ``schema validate --all`` 覆盖两个 shell manifest。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from molt import schema
from molt.adapters.deterministic.runtime import DeterministicRuntime
from molt.adapters.fixture.runtime import FixtureRuntime
from molt.core.ports import Harness, Runtime, TaskProvider, Verifier
from molt.core.store import StateStore
from molt.core.types import Genome, Scenario, Task, Usage
from molt.demo import build_engine, default_genome_path, load_genome
from molt.shell_bundle import (
    build_harness,
    build_task_provider,
    build_verifier,
    load_shell_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
FIXED_TS = "2026-01-01T00:00:00Z"
SCENARIOS: dict[str, Path] = {
    "code-repair": ROOT / "scenarios/demo/scenario.json",
    "svc-config": ROOT / "scenarios/svc-config/scenario.json",
}
RUNTIMES: dict[str, type] = {
    "fixture": FixtureRuntime,
    "deterministic": DeterministicRuntime,
}
ZERO_USAGE = Usage(tokens_in=0, tokens_out=0, cost_usd=0.0, wall_ms=0, turns=0)


def _scenario(shell_id: str) -> Scenario:
    return Scenario.from_dict(json.loads(SCENARIOS[shell_id].read_text(encoding="utf-8")))


def _bundle(shell_id: str):
    scenario = _scenario(shell_id)
    return load_shell_bundle(ROOT, shell_id, shell_version=scenario.shell_version)


def _run_cli(*args: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "molt", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def test_runtimes_satisfy_runtime_protocol() -> None:
    for runtime_cls in RUNTIMES.values():
        assert isinstance(runtime_cls(), Runtime)


@pytest.mark.parametrize("shell_id", sorted(SCENARIOS))
def test_shell_components_satisfy_ports(shell_id: str, tmp_path: Path) -> None:
    bundle = _bundle(shell_id)
    provider = build_task_provider(bundle, ROOT)
    verifier = build_verifier(bundle, ROOT)
    assert isinstance(provider, TaskProvider)
    assert isinstance(verifier, Verifier)
    assert provider.list_tasks("search"), f"{shell_id} 搜索集为空"
    assert provider.list_tasks("report"), f"{shell_id} 留出集为空"
    task = provider.list_tasks("search")[0]
    assert isinstance(verifier.guard(task, tmp_path), str)
    assert len(verifier.guard(task, tmp_path)) == 64
    for runtime_cls in RUNTIMES.values():
        runtime = runtime_cls()
        harness = build_harness(
            bundle,
            runtime,
            verifier,
            ROOT,
            shell_id=shell_id,
            shell_version=bundle.shell_version,
            clock=lambda: FIXED_TS,
            domains=None,
            llm_proposer=None,
        )
        assert isinstance(runtime, Runtime)
        assert isinstance(harness, Harness)


@pytest.mark.parametrize("runtime_name", sorted(RUNTIMES))
@pytest.mark.parametrize("shell_id", sorted(SCENARIOS))
def test_shell_runtime_matrix_runs_one_task(
    shell_id: str, runtime_name: str, tmp_path: Path
) -> None:
    scenario = _scenario(shell_id)
    genome = load_genome(ROOT, default_genome_path(ROOT, scenario))
    store = StateStore(tmp_path / "state")
    engine = build_engine(ROOT, scenario, store, tmp_path / "work", runtime=runtime_name)
    task = engine.task_provider.load_task(scenario.task_sets["search"][0])
    run = engine.run_task(genome, task, scope="search")

    assert run.outcome in {"passed", "failed", "invalid", "timeout", "budget_exceeded", "error"}
    assert len(run.verification.tests_hash_before) == 64
    assert len(run.verification.tests_hash_after) == 64
    assert run.shell_version == scenario.shell_version
    if runtime_name == "deterministic":
        assert run.usage == ZERO_USAGE
        assert run.ext.get("runtime") == "deterministic"
        assert run.ext.get("cost_source") == "none"


def test_deterministic_runtime_applies_switch_gated_edits(tmp_path: Path) -> None:
    scenario = _scenario("svc-config")
    genome = load_genome(ROOT, default_genome_path(ROOT, scenario))
    workdir = tmp_path / "workspace"
    (workdir / "config").mkdir(parents=True)
    (workdir / "config/app.json").write_text(
        '{\n  "logging": {\n    "level": "verbose",\n    "retries": 0\n  }\n}\n',
        encoding="utf-8",
    )
    task = Task.from_dict(
        {
            "schema_version": "1.0.0",
            "task_id": "unit",
            "scenario_version": scenario.scenario_version,
            "shell_id": "svc-config",
            "scope": "search",
            "title": "unit",
            "workspace_ref": "bench/search/svc-logging/workspace",
            "tests_ref": "bench/search/svc-logging/tests",
            "timeout_s": 30,
            "ext": {
                "deterministic": {
                    "edits": [
                        {
                            "kind": "json_set",
                            "path": "config/app.json",
                            "pointer": "/logging/level",
                            "value": "info",
                        },
                        {
                            "kind": "json_set",
                            "path": "config/app.json",
                            "pointer": "/logging/retries",
                            "value": 3,
                            "when": {"thinking": ["high"]},
                        },
                    ]
                }
            },
        }
    )
    runtime = DeterministicRuntime()
    high = runtime.run(genome, task, workdir)
    assert high.outcome == "passed"
    assert high.usage == ZERO_USAGE
    assert high.diagnostic["cost_source"] == "none"
    assert high.diagnostic["applied_edits"] == ["edit[0]", "edit[1]"]

    low_genome = Genome.from_dict(
        {
            **genome.to_dict(),
            "switches": {**genome.switches.to_dict(), "thinking": "low"},
        }
    )
    low = runtime.run(low_genome, task, workdir)
    assert low.outcome == "passed"
    assert low.diagnostic["skipped_edits"] == ["edit[1]"]

    escape = Task.from_dict(
        {
            **task.to_dict(),
            "ext": {
                "deterministic": {
                    "edits": [
                        {"kind": "json_set", "path": "../escape.json", "pointer": "/x", "value": 1}
                    ]
                }
            },
        }
    )
    bad = runtime.run(genome, escape, workdir)
    assert bad.outcome == "error"
    assert bad.usage == ZERO_USAGE
    assert "escape" in bad.diagnostic["error"]

    text = workdir / "service.env"
    text.write_text("PORT=0\nMODE=verbose\n", encoding="utf-8")
    text_task = Task.from_dict(
        {
            **task.to_dict(),
            "ext": {
                "deterministic": {
                    "edits": [
                        {
                            "kind": "text_replace",
                            "path": "service.env",
                            "find": "PORT=0",
                            "replace": "PORT=8080",
                        }
                    ]
                }
            },
        }
    )
    replaced = runtime.run(genome, text_task, workdir)
    assert replaced.outcome == "passed"
    assert text.read_text(encoding="utf-8") == "PORT=8080\nMODE=verbose\n"
    assert "service.env" in replaced.artifacts


def test_scenario_validate_both_shells() -> None:
    for path in (
        "scenarios/demo/scenario.json",
        "scenarios/svc-config/scenario.json",
    ):
        completed = _run_cli("scenario", "validate", "--scenario", path)
        assert completed.returncode == 0, completed.stderr


def test_svc_config_search_is_zero_cost(tmp_path: Path) -> None:
    state = tmp_path / "state"
    started = time.monotonic()
    completed = _run_cli(
        "search",
        "--scenario",
        "svc-config",
        "--runtime",
        "deterministic",
        "--state",
        str(state),
    )
    elapsed = time.monotonic() - started
    assert completed.returncode == 0, completed.stderr
    assert "search ok" in completed.stdout
    assert elapsed < 60, f"确定性 search 应为秒级，实际 {elapsed:.1f}s"

    runs = StateStore(state).list_runs()
    assert len(runs) == 2
    for run in runs:
        assert run.usage == ZERO_USAGE
        assert run.ext.get("cost_source") == "none"
        assert run.ext.get("runtime") == "deterministic"


def test_svc_config_audit_passes() -> None:
    completed = _run_cli("audit", "--scenario", "svc-config")
    assert completed.returncode == 0, completed.stderr
    assert "audit ok" in completed.stdout


def test_demo_audit_still_passes() -> None:
    completed = _run_cli("audit", "--scenario", "demo")
    assert completed.returncode == 0, completed.stderr
    assert "audit ok" in completed.stdout


def test_schema_validate_all_includes_both_shell_manifests() -> None:
    validated = schema.validate_all(ROOT)
    relative = {str(path.relative_to(ROOT)) for path, _ in validated}
    assert "shells/code-repair/manifest.json" in relative
    assert "shells/svc-config/manifest.json" in relative
