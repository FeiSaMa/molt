"""D4：文档三层数字、时限语义与 schema description 的契约测试。"""

from __future__ import annotations

from pathlib import Path

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




# -- 时限 / 降级语义 --








# -- schema description 与结构 --




