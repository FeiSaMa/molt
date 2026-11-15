"""probe shell 的 Harness：渲染 prompt、调用 Runtime、交给 Verifier。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from molt.core.events import utc_now
from molt.core.ports import HarnessOutcome, Runtime, RuntimeOutcome, Verifier
from molt.core.types import Genome, Proposal, SearchHistory, Task


def tools_argv_for(mode: str) -> list[str]:
    """bash-only 的唯一映射。"""
    if mode == "bash":
        return ["--tools", "bash"]
    raise ValueError(f"probe 只允许 tools=bash，实际 {mode!r}")


class ProbeHarness:
    def __init__(
        self,
        runtime: Runtime,
        verifier: Verifier,
        repo_root: Path,
        *,
        shell_id: str = "probe",
        shell_version: str = "1.0.0",
        runtime_version: str = "1.5.2",
        clock: Callable[[], str] = utc_now,
        domains: Mapping[str, Sequence[str]] | None = None,
        llm_proposer: Callable[[SearchHistory, Genome], Proposal] | None = None,
        **_: Any,
    ) -> None:
        self.runtime = runtime
        self.verifier = verifier
        self.repo_root = Path(repo_root)
        self.shell_id = shell_id
        self.shell_version = shell_version
        self.runtime_version = runtime_version
        self.clock = clock
        self.domains = dict(domains or {})
        self.llm_proposer = llm_proposer

    @staticmethod
    def _probe_meta(genome: Genome) -> Mapping[str, Any]:
        raw = genome.ext.get("probe")
        return raw if isinstance(raw, Mapping) else {}

    def render_prompt(self, genome: Genome, task: Task) -> str:
        template = (self.repo_root / genome.prompt_ref).read_text(encoding="utf-8")
        meta = self._probe_meta(genome)
        probe = task.ext.get("probe")
        function = probe.get("function") if isinstance(probe, Mapping) else None
        if not isinstance(function, str) or not function:
            raise ValueError(f"task {task.task_id}: ext.probe.function missing")
        memory = self._oracle_section(meta) if meta.get("oracle") is True else ""
        memory_ref = genome.ext.get("memory_ref")
        if memory_ref is not None:
            if not isinstance(memory_ref, str) or not memory_ref:
                raise ValueError(f"genome {genome.genome_id}: memory_ref invalid")
            memory_path = self.repo_root / memory_ref
            if not memory_path.is_file():
                raise FileNotFoundError(f"memory_ref missing: {memory_ref}")
            memory += memory_path.read_text(encoding="utf-8")
        rendered = template.replace("{{function}}", function)
        return rendered.replace("{{memory_section}}", memory)

    def _oracle_section(self, meta: Mapping[str, Any]) -> str:
        data = json.loads((self.repo_root / "probes/design.json").read_text(encoding="utf-8"))
        convention_id = meta.get("convention_id")
        if convention_id == "C1":
            section = data
            intro = "本包的 C1 是 shortlex 变体"
        elif convention_id == "C2":
            section = data.get("C2")
            intro = "本包的 C2 是舍入约定"
        else:
            raise ValueError(f"unknown convention_id: {convention_id!r}")
        if not isinstance(section, dict) or section.get("convention_id") != convention_id:
            raise ValueError(f"unknown convention_id: {convention_id!r}")
        facets = section.get("facets")
        if not isinstance(facets, dict) or not facets:
            raise ValueError(f"convention {convention_id!r} has no facets")
        rules = "；".join(
            f"{name}：{spec.get('rule', '')}" for name, spec in facets.items()
        )
        return f"\n\n{intro}：{rules}。\n"

    def run(self, genome: Genome, task: Task, workdir: Path) -> HarnessOutcome:
        prompt = self.render_prompt(genome, task)
        prompt_path = Path(workdir) / ".molt" / "prompt.txt"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        digest_before = self.verifier.guard(task, workdir)
        runtime_outcome: RuntimeOutcome = self.runtime.run(genome, task, workdir)
        verification = self.verifier.verify(task, workdir, before=digest_before)
        return HarnessOutcome(runtime=runtime_outcome, verification=verification)

    def propose(self, history: SearchHistory, parent: Genome) -> Proposal:
        raise NotImplementedError("Batch 1 probe shell 只跑单题，不实现提案")


def build_harness(
    runtime: Runtime,
    verifier: Verifier,
    repo_root: Path,
    **kwargs: Any,
) -> ProbeHarness:
    return ProbeHarness(runtime, verifier, repo_root, **kwargs)
