"""D4：文档三层数字、时限语义与 schema description 的契约测试。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _section(text: str, start: str, stop: str | None) -> str:
    assert start in text, f"缺少段落起点 {start!r}"
    chunk = text.split(start, 1)[1]
    if stop is not None:
        assert stop in chunk, f"缺少段落终点 {stop!r}"
        chunk = chunk.split(stop, 1)[0]
    return chunk


def _between(text: str, start: str, stop: str) -> str:
    return _section(text, start, stop)


def _structure(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _structure(item)
            for key, item in value.items()
            if key != "description"
        }
    if isinstance(value, list):
        return [_structure(item) for item in value]
    return value


# -- 三层数字：历史扫描 claim / 当前 tracked 扫描复算 / gate cps --


def test_readme_links_to_canonical_claim_sources() -> None:
    readme = _read("README.md")
    assert "[AGENTS.md](AGENTS.md)" in readme
    assert "[ADR-0008](docs/decisions/0008-demo.md)" in readme


def test_agents_three_layer_numbers_are_separated() -> None:
    section = _section(_read("AGENTS.md"), "**claim（只允许这样说）**", "**not claimed**")
    blockquote = next(line for line in section.splitlines() if line.startswith("> 模型能力"))
    assert "tracked 证据" in blockquote
    assert "未随仓库导出" in blockquote

    historical = _between(section, "历史扫描级 claim", "当前 tracked 扫描级复算")
    assert "[ADR-0008](docs/decisions/0008-demo.md)" in historical
    assert "$0.009978" in historical and "41.7s" in historical
    assert "−72%" in historical and "−61%" in historical
    assert "不能表述为当前 tracked 数据已完整复算" in historical

    current = _between(section, "当前 tracked 扫描级复算", "**gate 级**")
    assert "$0.010684" in current and "42.6s" in current
    assert "−70.0%" in current and "−60.1%" in current
    assert "verified=false" in current
    assert "$0.009978" not in current and "41.7s" not in current

    gate = section.split("**gate 级**", 1)[1]
    assert "cps" in gate
    assert "0.003556022" in gate and "0.001068401" in gate
    assert "$0.010684" not in gate and "42.6s" not in gate
    assert "不是 gate 级复算" in gate


# -- 已知缺口与局限段 --


def test_core_known_gaps_marks_k5_k6_status() -> None:
    text = _read("docs/research/core-known-gaps.md")
    k5 = _section(text, "## K5", "## K6")
    k6 = _section(text, "## K6", "## 为什么")
    assert "历史缺口" in k5 and "本轮修复" in k5 and "剩余边界" in k5
    assert "invalid_ratio" in k5 and "paired_rows_from_runs" in k5
    assert "两侧 `invalid_ratio` 也从同一分侧结果重算" in k5
    assert "不再使用 result 的 union" in k5
    assert "不按 scenario search task 集二次过滤" in k5
    assert "仍使用各 result 引用的全部 search-scope runs" not in k5
    assert "顶层 `baseline_genome_id` / `candidate_genome_id`" in k5
    assert "历史缺口" in k6 and "本轮修复" in k6 and "剩余边界" in k6
    assert "verifier_hash" in k6 and "不证明 verifier 源码真值" in k6


def test_limitation_sections_include_k4() -> None:
    final = _section(_read("probes/report/FINAL.md"), "## 已知局限", "## 文件地图")
    assert "K4" in final and "verifier" in final

    pitch = _section(
        _read("probes/report/PITCH.md"), "## 7:00–8:00 局限与收尾", "\n---\n"
    )
    assert "K4" in pitch and "verifier" in pitch

    headroom = _section(
        _read("docs/research/headroom-probe.md"), "## 8. 局限", "## 9. 复算命令"
    )
    assert "K4" in headroom and "sitecustomize" in headroom


# -- 时限 / 降级语义 --


def test_visual_spec_timeout_partial_and_stale_semantics() -> None:
    text = _read("probes/report/VISUAL_SPEC.md")
    head = text.split("## 0.", 1)[0]
    assert "probe 页面尚未实现" in head
    assert "只是规格" in head

    badge = _section(text, "### 9.2 徽章判定", "### 9.3")
    assert "force PARTIAL" not in badge
    assert "360s cap" not in badge
    assert badge.index('phase === "timeout"') < badge.index('phase === "partial"')
    assert badge.index('phase === "partial"') < badge.index("live === false")
    assert "120s 只是展示阈值" in badge
    assert "fast 默认 360s" in badge and "full 默认 420s" in badge
    assert "--max-wall" in badge
    assert "剩余不足 20s" in badge
    assert "单题超时或成本到限" in badge
    assert "页面**不自行**按 360s 强制 partial" in badge

    assert "360s 未 done → PARTIAL" not in text
    assert "`timeout` / `partial` 直接读运行器 `phase`" in text


def test_pitch_replay_vs_stale_and_timeout() -> None:
    degrade = _section(_read("probes/report/PITCH.md"), "# 现场降级", None)
    assert "REPLAY · STALE" in degrade
    assert "120s 只是展示阈值" in degrade
    assert "PARTIAL · 360s cap" not in degrade
    assert "phase=timeout" in degrade and "TIMEOUT" in degrade
    assert "fast 默认 360s" in degrade and "full 默认 420s" in degrade
    assert "--max-wall" in degrade
    assert "单题超时或成本到限" in degrade


def test_final_replay_snapshot_is_not_partial_transfer_example() -> None:
    final = _read("probes/report/FINAL.md")
    entry = _section(final, "## 现场入口", "## 关键数字")
    assert "v3_stable_headroom" in entry and "live=false" in entry
    assert "不是** `partial_transfer` 示例" in entry
    assert "不以 `partial_transfer` 为唯一触发" in entry
    assert "降级：抽到 `partial_transfer` 切预录" not in final


# -- schema description 与结构 --


def test_schema_descriptions_follow_runner_semantics() -> None:
    schema = json.loads(_read("probes/report/probe_state_schema.json"))
    phase = schema["properties"]["phase"]["description"]
    assert "300s" not in phase
    assert "单题超时或成本到限" in phase
    assert "总墙钟到限" in phase
    assert "fast 默认 360s" in phase
    assert "full 默认 420s" in phase
    assert "--max-wall" in phase

    updated = schema["properties"]["updated_at"]["description"]
    assert "120s" in updated
    assert "只是展示阈值" in updated
    assert "不终止运行" in updated


def test_schema_structure_unchanged_except_descriptions() -> None:
    schema = json.loads(_read("probes/report/probe_state_schema.json"))
    baseline = subprocess.run(
        ["git", "-C", str(ROOT), "show", "HEAD:probes/report/probe_state_schema.json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if baseline.returncode == 0:
        assert _structure(schema) == _structure(json.loads(baseline.stdout))

    assert schema["required"] == [
        "schema_version",
        "live",
        "phase",
        "started_at",
        "updated_at",
        "revision",
        "lineage",
        "run1",
        "memory",
        "run2",
        "verdict",
        "verdict_note",
        "cost_usd",
        "wall_ms",
        "note",
    ]
    assert schema["properties"]["phase"]["enum"] == [
        "idle",
        "run1",
        "reflection",
        "run2",
        "done",
        "partial",
        "timeout",
    ]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["revision"]["pattern"] == "^[0-9a-f]{64}$"
    assert set(schema["definitions"]) == {
        "lineage_node",
        "run",
        "task",
        "case",
        "diff_line",
        "memory",
        "negative_transfer",
    }
