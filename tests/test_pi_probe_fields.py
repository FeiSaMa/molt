"""Pi probe 运行期字段：七字段齐全、failure 带 argv/planned_argv、max 无 high。"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from molt.adapters.pi.ledger import SessionUsage
from molt.adapters.pi.probe import SandboxProbe
from molt.adapters.pi.runtime import PiRuntime
from molt.adapters.pi.trust import TrustResult
from molt.core.types import Genome, Task

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "model_requested",
    "model_effective",
    "thinking_requested",
    "thinking_effective",
    "thinking_source",
    "argv",
    "pi_version",
}


def _genome(name: str) -> Genome:
    path = ROOT / "shells/probe/genomes" / f"{name}.json"
    return Genome.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _task_for(task_id: str) -> Task:
    path = ROOT / "bench/probe" / task_id / "task.json"
    return Task.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _task() -> Task:
    return _task_for("probe-order-1")


def _harness():
    path = ROOT / "shells/probe/harness.py"
    spec = importlib.util.spec_from_file_location("probe_harness_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ProbeHarness


def _actual_pi_version() -> str:
    completed = subprocess.run(
        ["pi", "--version"], capture_output=True, text=True, check=False, timeout=10
    )
    return (completed.stdout or completed.stderr).strip().splitlines()[0]


def _runtime() -> PiRuntime:
    return PiRuntime(
        repo_root=ROOT,
        tools_argv_fn=lambda mode: ["--tools", "bash"],
        sandbox_wrapper=None,
        pi_executable="pi",
    )


class _FakeProcess:
    returncode = 0
    pid = 1

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        return ("", "")


def _usage() -> SessionUsage:
    return SessionUsage(
        input=10,
        output=5,
        total_tokens=15,
        cost_usd=0.001,
        cost_source="test",
        turns=1,
        matches=True,
    )


def _patch_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> PiRuntime:
    runtime = _runtime()
    runtime.spawn = lambda *args, **kwargs: _FakeProcess()
    usage = _usage()
    monkeypatch.setattr(
        "molt.adapters.pi.runtime._newest_session", lambda *a, **k: tmp_path / "session.jsonl"
    )
    monkeypatch.setattr("molt.adapters.pi.runtime.parse_session_file", lambda *a, **k: usage)
    monkeypatch.setattr("molt.adapters.pi.runtime.parse_stream_text", lambda *a, **k: usage)
    monkeypatch.setattr("molt.adapters.pi.runtime.cross_check", lambda *a, **k: (True, None))
    return runtime


def test_success_records_seven_fields_and_max(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime = _patch_success(monkeypatch, tmp_path)
    workdir = tmp_path / "work"
    (workdir / ".molt").mkdir(parents=True)
    (workdir / ".molt/prompt.txt").write_text("do the task", encoding="utf-8")

    result = runtime.run(_genome("v0-pro-max"), _task(), workdir)
    assert result.outcome == "passed"
    meta = result.diagnostic["runtime"]
    assert REQUIRED <= set(meta)
    assert meta["model_requested"] == "deepseek/deepseek-v4-pro"
    assert meta["model_effective"] == "deepseek/deepseek-v4-pro"
    assert meta["thinking_requested"] == "max"
    assert meta["thinking_effective"] == "max"
    assert meta["thinking_source"] == "ext.pi.thinking"
    assert meta["argv"][meta["argv"].index("--thinking") + 1] == "max"
    assert "high" not in meta["argv"]
    assert meta["pi_version"] == _actual_pi_version()
    assert result.diagnostic["probe"]["mode"] == "max"


def test_failure_after_argv_keeps_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime = _runtime()

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    runtime.spawn = boom
    workdir = tmp_path / "work"
    result = runtime.run(_genome("v0-pro-max"), _task(), workdir)
    assert result.outcome == "error"
    meta = result.diagnostic["runtime"]
    assert REQUIRED <= set(meta)
    assert meta["reason"] == "spawn_error"
    assert meta["argv"]
    assert meta["planned_argv"] == []
    assert meta["pi_version"] == _actual_pi_version()


def test_failure_before_argv_records_planned_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = _runtime()
    monkeypatch.setattr(
        PiRuntime,
        "_sandbox_probe",
        lambda self, workdir: SandboxProbe(True, {}, "test", ()),
    )
    monkeypatch.setattr(
        PiRuntime,
        "_trust",
        lambda self, workdir, genome: TrustResult(
            ok=False,
            project_trust="trust_missing",
            reason="trust_missing",
            cache_hit=False,
            settings_hash="",
            settings_path="",
            sentinel_path="",
            log_path="",
        ),
    )
    workdir = tmp_path / "work"
    result = runtime.run(_genome("v0-pro-max"), _task(), workdir)
    assert result.outcome == "invalid"
    meta = result.diagnostic["runtime"]
    assert REQUIRED <= set(meta)
    assert meta["reason"] == "trust_missing"
    assert meta["argv"] == []
    assert meta["planned_argv"]
    assert meta["planned_argv"][meta["planned_argv"].index("--thinking") + 1] == "max"
    assert meta["pi_version"] == _actual_pi_version()


def test_prompt_uses_task_function_and_omits_stale_high() -> None:
    harness = _harness()(runtime=None, verifier=None, repo_root=ROOT)
    t1 = _task()
    t2 = _task_for("probe-order-2")
    blind_prompt = harness.render_prompt(_genome("v0-pro-max"), t1)
    assert "high" not in blind_prompt.lower()
    assert "top_k" in blind_prompt
    assert "{{function}}" not in blind_prompt
    t2_prompt = harness.render_prompt(_genome("v0-pro-max"), t2)
    assert "dedupe" in t2_prompt
    assert "top_k" not in t2_prompt
    assert "{{function}}" not in t2_prompt
    oracle_prompt = harness.render_prompt(_genome("oracle"), t1)
    assert "shortlex" in oracle_prompt


def test_missing_task_function_fails_closed() -> None:
    data = json.loads(
        (ROOT / "bench/probe/probe-order-1/task.json").read_text(encoding="utf-8")
    )
    del data["ext"]["probe"]["function"]
    task = Task.from_dict(data)
    harness = _harness()(runtime=None, verifier=None, repo_root=ROOT)
    with pytest.raises(ValueError, match="ext.probe.function"):
        harness.render_prompt(_genome("v0-pro-max"), task)


def test_oracle_unknown_convention_fails_closed() -> None:
    data = json.loads(
        (ROOT / "shells/probe/genomes/oracle.json").read_text(encoding="utf-8")
    )
    data["ext"]["probe"]["convention_id"] = "C9"
    genome = Genome.from_dict(data)
    harness = _harness()(runtime=None, verifier=None, repo_root=ROOT)
    with pytest.raises(ValueError, match="unknown convention_id"):
        harness.render_prompt(genome, _task())


def test_memory_ref_injected_and_missing_fails_closed() -> None:
    harness = _harness()(runtime=None, verifier=None, repo_root=ROOT)
    prompt = harness.render_prompt(_genome("gen2"), _task_for("probe-order-2"))
    assert "Failures observed in the previous run" in prompt
    data = json.loads(
        (ROOT / "shells/probe/genomes/gen2.json").read_text(encoding="utf-8")
    )
    data["ext"]["memory_ref"] = "shells/probe/memory/missing.md"
    genome = Genome.from_dict(data)
    with pytest.raises(FileNotFoundError, match="memory_ref missing"):
        harness.render_prompt(genome, _task_for("probe-order-2"))
