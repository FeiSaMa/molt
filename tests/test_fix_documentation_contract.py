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


def test_readme_links_to_product_operations_and_compatibility() -> None:
    readme = _read("README.md")
    assert "[AGENTS.md](AGENTS.md)" in readme
    for target in (
        "docs/architecture.md",
        "docs/operations/kernel-cli.md",
        "docs/operations/kernel-cli.md#v1-只读兼容与显式迁移",
        "docs/operations/molt-kernel-systemd.md",
        "docs/operations/synthetic-versions.md",
        "docs/operations/v2-backup-restore.md",
    ):
        assert f"]({target})" in readme


def test_agents_preserves_transaction_and_retirement_boundaries() -> None:
    text = _read("AGENTS.md")
    boundaries = _section(text, "## 二、边界与不变量", "## 三、协议与兼容")
    for required in (
        "不透明对象", "单写者", "持久日志提交点", "同 request ID", "unknown",
        "禁止换新 ID 盲重试", "不等于取消外部预约", "不证明执行真实性", "synthetic",
    ):
        assert required in boundaries
    compatibility = _section(text, "## 三、协议与兼容", "## 四、本地执行与证据")
    assert "1.1.x" in compatibility and "只读审计与显式迁移" in compatibility
    assert "冻结 schema 不改字段或语义" in compatibility
    retirement = _section(text, "## 五、退出与删除", "## 六、所有权与协作")
    assert "明确确认" in retirement and "gio trash" in retirement
    assert "完整 dry-run 清单" in retirement
    assert "变化项重新核对" in retirement and "新增路径重新预览确认" in retirement


# -- 已知缺口与局限段 --


def test_core_known_gaps_marks_k5_k6_status() -> None:
    text = _read("docs/research/core-known-gaps.md")
    k5 = _section(text, "## K5", "## K6")
    k6 = _section(text, "## K6", "## 兼容性")
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




