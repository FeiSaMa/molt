## 变更摘要

<!-- 做了什么、为什么；一条提交能说清就不要写三段。 -->

## 影响的 D 编号

<!-- 例：B2-D6（协作六件套 + main 保护）；没有就写「无」。 -->

- D：

## 契约影响

- [ ] 无
- [ ] 有（`schemas/**` / `molt/core/ports.py` / `docs/protocol.md` / ADR）

<!-- 有契约影响时：贴 ADR 链接，确认 2 位 approve；冻结窗口（2026-11-12 18:00 UTC+8 后）
     只允许 ext 新增或新增记录类型。 -->

## 触碰范围

- [ ] holdout（`bench/holdout/**`、report 流、双分数）
- [ ] verifier / `scenario_version` 构件（`harness.py` / `verifier.py` / `task_provider.py` /
      `switches.schema.json` / `bench/**`）→ 必须升 `scenario_version` 并重跑基线
- [ ] 只新增候选文件（`genomes/` / `prompts/` / `tools/`）→ 只改 `genome_id`
- [ ] 以上都不涉及

## 验证证据（命令 + 输出）

```bash
# 必贴
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q
uv run ruff check .
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt schema validate --all
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt scenario validate
```

```
<粘贴最后几行输出；没有输出的检查写「通过」并给出退出码>
```

涉及执行/门禁时补：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt gate --fixture tests/fixtures/gate/
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt audit --scenario demo
```

## 回滚方式

<!-- 例：revert 本提交 / `molt revert --to <archive_id>` / 重新生成 web/data 并提交。 -->

## Checklist

- [ ] 测试：新增行为有用例，`pytest -q` 全绿
- [ ] Schema：`schema validate --all` + `scenario validate` 通过；`docs/protocol.md` 未手改
- [ ] 零写入：`git status --porcelain` 为空；`tests/test_zero_write.py` 通过
- [ ] Provenance：`run.ext.provenance.kind` 与实际一致（fixture 未标 live，replay 带 `replay_of_run_id`）
- [ ] 脱敏：diff 中无 key/token/个人信息；`redaction` 报告覆盖字段未泄漏
- [ ] claim 措辞符合 `docs/style.md` §六（描述性、附配对表、不跨 `scenario_version`）
