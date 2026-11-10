"""Pi adapter：argv 三连、tools 映射、runtime 预算、trust_missing、降级。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from molt.adapters.pi.argv import (
    BUILTIN_READ_TOOLS,
    FORBIDDEN_FLAGS,
    assert_required_flags,
    build_pi_argv,
    safe_session_id,
)
from molt.adapters.pi.runtime import PiRuntime
from molt.adapters.pi.trust import TrustResult
from molt.core.engine import Engine, EngineConfig
from molt.core.errors import ValidationError
from molt.core.ports import HarnessOutcome, RuntimeOutcome, VerifyOutcome
from molt.core.store import StateStore
from molt.core.types import Genome, Task, Usage
from molt.shell_loader import load_shell_module

ROOT = Path(__file__).resolve().parents[1]
GENOME_PATH = ROOT / "shells/code-repair/genomes/v0-pi.json"
FIXED = "2026-01-01T00:00:00Z"

pytestmark = pytest.mark.pi


def genome(budget: dict | None = None) -> Genome:
    data = json.loads(GENOME_PATH.read_text(encoding="utf-8"))
    if budget:
        data["budget"] = budget
    return Genome.from_dict(data)


def task() -> Task:
    return Task.from_dict(
        json.loads((ROOT / "bench/search/smoke-add/task.json").read_text(encoding="utf-8"))
    )


def harness_module():
    return load_shell_module(ROOT, "shells/code-repair/harness.py", "molt_test_pi_harness")


def usage(
    *,
    input_tokens: int = 100,
    output: int = 20,
    cache_read: int = 5,
    cost: float = 0.001,
    provider: bool = False,
) -> dict:
    return {
        "input": input_tokens,
        "output": output,
        "cacheRead": cache_read,
        "cacheWrite": 0,
        "reasoning": 1,
        "totalTokens": input_tokens + output + cache_read,
        "cost": {
            "input": 0.0,
            "output": 0.0,
            "cacheRead": 0.0,
            "cacheWrite": 0.0,
            "total": cost,
            "totalFromProvider": provider,
        },
    }


def session_text(entries: list[dict]) -> str:
    header = {
        "type": "session",
        "version": 3,
        "id": "session-1",
        "timestamp": FIXED,
        "cwd": "/tmp/work",
    }
    return "\n".join(json.dumps(item) for item in [header, *entries]) + "\n"


def assistant(entry_id: str, entry_usage: dict) -> dict:
    return {
        "type": "message",
        "id": entry_id,
        "parentId": None,
        "timestamp": FIXED,
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
            "usage": entry_usage,
        },
    }


def compaction(entry_id: str, entry_usage: dict) -> dict:
    return {
        "type": "compaction",
        "id": entry_id,
        "parentId": None,
        "timestamp": FIXED,
        "firstKeptEntryId": entry_id,
        "tokensBefore": 1000,
        "usage": entry_usage,
    }


class StubTrust:
    def __init__(self, ok: bool = True, project_trust: str = "cli_approve") -> None:
        self.ok = ok
        self.project_trust = project_trust
        self.calls = 0

    def ensure(self, workdir, *, shell_version, shell_path):
        self.calls += 1
        return TrustResult(
            ok=self.ok,
            project_trust=self.project_trust if self.ok else "trust_missing",
            reason=None if self.ok else "preflight_failed:1",
            cache_hit=False,
            settings_hash="a" * 64,
            settings_path=str(Path(workdir) / ".pi/settings.json"),
            sentinel_path=str(Path(workdir) / ".molt/trust-sentinel"),
            log_path=str(Path(workdir) / ".molt/shell-path.log"),
        )


def probe_runner(argv, *, cwd, env, timeout):
    command = argv[-1]
    if "curl" in command:
        return subprocess.CompletedProcess(argv, 7, "", "no network")
    if "test -e" in command:
        return subprocess.CompletedProcess(argv, 1, "", "")
    return subprocess.CompletedProcess(argv, 0, "", "")


class FakeProcess:
    pid = 999_999

    def __init__(self, spawn_state) -> None:
        self.spawn_state = spawn_state
        self.calls = 0
        self.returncode = spawn_state.get("returncode", 0)

    def communicate(self, timeout=None):
        self.calls += 1
        if self.spawn_state.get("timeout_once") and self.calls == 1:
            raise subprocess.TimeoutExpired(cmd="pi", timeout=timeout or 1)
        if not self.spawn_state.get("written"):
            self.spawn_state["written"] = True
            self.spawn_state["write"]()
        return ("", "")


def spawn_factory(
    entries: list[dict],
    *,
    timeout_once: bool = False,
    returncode: int = 0,
    write_solution: bool = True,
    stream_assistant: bool = True,
):
    state: dict = {
        "argv": None,
        "timeout_once": timeout_once,
        "returncode": returncode,
        "written": False,
    }

    def write() -> None:
        argv = state["argv"]
        session_dir = Path(argv[argv.index("--session-dir") + 1])
        session_id = argv[argv.index("--session-id") + 1]
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / f"2026-01-01T00-00-00Z_{session_id}.jsonl").write_text(
            session_text(entries), encoding="utf-8"
        )
        if write_solution:
            workdir = Path(state["cwd"])
            (workdir / "solution.py").write_text("def add(a, b):\n    return a + b\n")
        if stream_assistant:
            stdout = state["stdout"]
            for entry in entries:
                if entry.get("type") != "message":
                    continue
                message = entry["message"]
                if message.get("role") != "assistant":
                    continue
                record = {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "usage": message["usage"],
                        "responseId": entry["id"],
                    },
                }
                stdout.write(json.dumps(record) + "\n")

    state["write"] = write

    def spawn(argv, **kwargs):
        state["argv"] = argv
        state["cwd"] = kwargs["cwd"]
        state["stdout"] = kwargs["stdout"]
        return FakeProcess(state)

    return spawn, state


def make_runtime(tmp_path: Path, *, trust: StubTrust | None = None, wrapper: bool = True):
    wrapper_path = tmp_path / "wrapper.sh"
    wrapper_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    wrapper_path.chmod(0o755)
    return PiRuntime(
        repo_root=ROOT,
        tools_argv_fn=harness_module().tools_argv_for,
        sandbox_wrapper=wrapper_path if wrapper else None,
        trust_probe=trust or StubTrust(),
        probe_runner=probe_runner,
        spawn=spawn_factory([])[0],
    )


# -- argv -------------------------------------------------------------------


def test_argv_contains_required_flags() -> None:
    argv = build_pi_argv(
        model="deepseek/deepseek-flash",
        thinking="low",
        session_id="molt-run-1",
        session_dir="/tmp/sessions",
        tools_args=(),
        prompt="fix it",
    )
    assert_required_flags(argv)
    assert argv[:2] == ["pi", "-p"]
    assert argv[-1] == "fix it"
    assert "--approve" in argv
    assert ("--no-" + "tools") not in argv


def test_tools_mode_mapping_is_owned_by_harness() -> None:
    module = harness_module()
    assert module.tools_argv_for("default") == []
    assert module.tools_argv_for("lean") == ["--exclude-tools", "write"]
    with pytest.raises(ValueError):
        module.tools_argv_for("max")


def test_default_argv_has_no_tools_mode_but_excludes_builtin_reads() -> None:
    module = harness_module()
    argv = build_pi_argv(
        model="deepseek/deepseek-flash",
        thinking="off",
        session_id="molt-run-1",
        session_dir="/tmp/sessions",
        tools_args=module.tools_argv_for("default"),
        builtin_excludes=BUILTIN_READ_TOOLS,
    )
    assert "--tools" not in argv
    value = argv[argv.index("--exclude-tools") + 1]
    assert set(value.split(",")) == set(BUILTIN_READ_TOOLS)
    assert ("--no-" + "tools") not in argv


def test_lean_argv_contains_exclude_tools_write() -> None:
    module = harness_module()
    argv = build_pi_argv(
        model="deepseek/deepseek-flash",
        thinking="high",
        session_id="molt-run-1",
        session_dir="/tmp/sessions",
        tools_args=module.tools_argv_for("lean"),
        builtin_excludes=BUILTIN_READ_TOOLS,
    )
    value = argv[argv.index("--exclude-tools") + 1]
    assert value.split(",")[0] == "write"
    assert set(BUILTIN_READ_TOOLS) <= set(value.split(","))


@pytest.mark.parametrize("flag", ["--no-session", "--continue", "-c", "--resume", "-r"])
def test_forbidden_flags_rejected(flag: str) -> None:
    with pytest.raises(ValidationError):
        build_pi_argv(
            model="deepseek/deepseek-flash",
            thinking="off",
            session_id="molt-run-1",
            session_dir="/tmp/sessions",
            tools_args=(flag,),
        )


def test_no_tools_flag_rejected() -> None:
    with pytest.raises(ValidationError):
        build_pi_argv(
            model="deepseek/deepseek-flash",
            thinking="off",
            session_id="molt-run-1",
            session_dir="/tmp/sessions",
            tools_args=("--no-" + "tools",),
        )
    assert "--no-" + "tools" in FORBIDDEN_FLAGS


def test_session_id_is_normalized() -> None:
    assert safe_session_id("molt/run 1") == "molt-run-1"
    assert safe_session_id("-bad-") == "bad"
    assert safe_session_id("a.b_c-d") == "a.b_c-d"


# -- runtime ----------------------------------------------------------------


def test_runtime_normal_run_and_artifacts(tmp_path: Path) -> None:
    entries = [
        assistant("m1", usage()),
        compaction("c1", usage(input_tokens=10, output=2, cache_read=0, cost=0.0004)),
    ]
    spawn, state = spawn_factory(entries)
    runtime = make_runtime(tmp_path)
    runtime.spawn = spawn
    workdir = tmp_path / "work"
    result = runtime.run(genome(), task(), workdir)

    assert result.outcome == "passed"
    assert result.usage.tokens_in == 110
    assert result.usage.tokens_out == 22
    assert result.usage.cost_usd == pytest.approx(0.0014)
    assert result.usage.turns == 1
    assert result.artifacts["solution.py"]
    runtime_meta = result.diagnostic["runtime"]
    assert runtime_meta["project_trust"] == "cli_approve"
    assert runtime_meta["provenance"] == "live"
    assert runtime_meta["isolation"]["level"] == "provider-sandboxed"
    assert runtime_meta["read_tools_excluded"] == list(BUILTIN_READ_TOOLS)
    assert runtime_meta["ledger"]["matches"] is True
    assert state["argv"][:2] == ["pi", "-p"]
    assert_required_flags(state["argv"])


def test_runtime_trust_missing_does_not_spawn(tmp_path: Path) -> None:
    spawn, state = spawn_factory([])
    runtime = make_runtime(tmp_path, trust=StubTrust(ok=False))
    runtime.spawn = spawn
    result = runtime.run(genome(), task(), tmp_path / "work")
    assert result.outcome == "invalid"
    assert result.diagnostic["runtime"]["project_trust"] == "trust_missing"
    assert result.diagnostic["runtime"]["reason"] == "trust_missing"
    assert state["argv"] is None


def test_runtime_wall_timeout_is_invalid_and_cost_kept(tmp_path: Path) -> None:
    entries = [assistant("m1", usage())]
    spawn, _ = spawn_factory(entries, timeout_once=True)
    runtime = make_runtime(tmp_path)
    runtime.spawn = spawn
    result = runtime.run(
        genome({"tokens": 1000, "wall_ms": 10, "turns": 4, "attempts": 1}),
        task(),
        tmp_path / "work",
    )
    assert result.outcome == "invalid"
    assert "wall_timeout" in result.diagnostic["runtime"]["budget"]["exceeded"]
    assert result.usage.cost_usd == pytest.approx(0.001)


def test_runtime_turns_budget_is_invalid(tmp_path: Path) -> None:
    entries = [assistant("m1", usage()), assistant("m2", usage())]
    spawn, _ = spawn_factory(entries)
    runtime = make_runtime(tmp_path)
    runtime.spawn = spawn
    result = runtime.run(
        genome({"tokens": 1000, "wall_ms": 10000, "turns": 1, "attempts": 1}),
        task(),
        tmp_path / "work",
    )
    assert result.outcome == "invalid"
    assert "turns_exceeded" in result.diagnostic["runtime"]["budget"]["exceeded"]


def test_runtime_token_budget_is_invalid(tmp_path: Path) -> None:
    spawn, _ = spawn_factory([assistant("m1", usage())])
    runtime = make_runtime(tmp_path)
    runtime.spawn = spawn
    result = runtime.run(
        genome({"tokens": 10, "wall_ms": 10000, "turns": 4, "attempts": 1}),
        task(),
        tmp_path / "work",
    )
    assert result.outcome == "invalid"
    assert "tokens_exceeded" in result.diagnostic["runtime"]["budget"]["exceeded"]


def test_runtime_duplicate_ledger_entry_is_invalid(tmp_path: Path) -> None:
    entry = assistant("m1", usage())
    spawn, _ = spawn_factory([entry, entry])
    runtime = make_runtime(tmp_path)
    runtime.spawn = spawn
    result = runtime.run(genome(), task(), tmp_path / "work")
    assert result.outcome == "invalid"
    assert "ledger_mismatch" in result.diagnostic["runtime"]["budget"]["exceeded"]
    assert result.diagnostic["runtime"]["ledger"]["matches"] is False


def test_runtime_without_wrapper_degrades_to_permissions_only(tmp_path: Path) -> None:
    spawn, state = spawn_factory([assistant("m1", usage(cost=0.001))])
    runtime = make_runtime(tmp_path, wrapper=False)
    runtime.spawn = spawn
    result = runtime.run(genome(), task(), tmp_path / "work")
    isolation = result.diagnostic["runtime"]["isolation"]
    assert isolation["level"] == "permissions-only"
    assert isolation["degraded"] is True
    assert isolation["claim_marker"] == "sandbox unavailable"
    assert result.diagnostic["runtime"]["project_trust"] == "not_required"
    assert state["argv"] is not None


def test_harness_writes_prompt_for_runtime(tmp_path: Path) -> None:
    module = harness_module()

    class DummyRuntime:
        def run(self, genome, task, workdir):
            return RuntimeOutcome(
                outcome="passed",
                usage=Usage(1, 1, 0.0, 1, 1),
                started_at=FIXED,
                finished_at=FIXED,
            )

    class DummyVerifier:
        def verify(self, task, workdir):
            return VerifyOutcome(True, "a" * 64, "a" * 64, True)

    harness = module.CodeRepairHarness(
        DummyRuntime(),
        DummyVerifier(),
        ROOT,
        clock=lambda: FIXED,
    )
    workdir = tmp_path / "work"
    workdir.mkdir()
    harness.run(genome(), task(), workdir)
    prompt = workdir / ".molt/prompt.txt"
    assert prompt.is_file()
    assert "{{thinking}}" not in prompt.read_text(encoding="utf-8")


def test_engine_persists_pi_runtime_ext(tmp_path: Path) -> None:
    class Provider:
        def list_tasks(self, scope):
            return [task()]

        def load_task(self, task_id):
            return task()

    class Harness:
        def run(self, genome, task, workdir):
            return HarnessOutcome(
                RuntimeOutcome(
                    outcome="passed",
                    usage=Usage(1, 1, 0.0, 1, 1),
                    started_at=FIXED,
                    finished_at=FIXED,
                    diagnostic={
                        "runtime": {
                            "adapter": "pi",
                            "provenance": "live",
                            "project_trust": "cli_approve",
                            "isolation": {"level": "provider-sandboxed"},
                        }
                    },
                ),
                VerifyOutcome(True, "a" * 64, "a" * 64, True),
            )

        def propose(self, history, parent):
            raise AssertionError("not used")

    class Verifier:
        def verify(self, task, workdir):
            return VerifyOutcome(True, "a" * 64, "a" * 64, True)

    store = StateStore(tmp_path / "state")
    engine = Engine(
        store,
        Provider(),
        Harness(),
        Verifier(),
        EngineConfig(
            repo_root=ROOT,
            work_root=tmp_path / "work",
            shell_version="1.0.0",
            verifier_hash="a" * 64,
        ),
    )
    run = engine.run_task(genome(), task(), scope="search")
    assert run.ext["runtime"]["project_trust"] == "cli_approve"
    assert store.read_run(run.run_id).ext["runtime"]["provenance"] == "live"
