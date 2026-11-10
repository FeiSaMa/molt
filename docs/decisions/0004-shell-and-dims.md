# ADR 0004：Shell 边界与三维开关

- 状态：已决定
- 日期：2026-11-10
- 关联：`shells/code-repair/`、`molt/core/genome.py`、`molt/core/ports.py`

## 背景

v0 的 `Switches` 有 6 个开关，其中 `reducer_enabled` / `action_fusion` 依赖已明确禁止装回的
SoL-Pi，`compaction_ratio` 的语义在上游不存在，`tool_granularity` 与 `escalation_threshold`
没有可靠宿主。README 与 plan 对"搜几维"说法不一。

## 决定

1. **搜索空间锁定**：`thinking ∈ {off, low, high}` × `context ∈ {full, minimal}`，
   共 6 组；`tools` 在 B2b 锁定为单值 `bash`（搜索空间不再含工具维度）。不得有第四维；
   `compaction` 不进 v1；bash 读写由 sandbox wrapper 隔离，read/write/edit 不启用。
   code / skills / memory / 模型维度只留 `ext` 与后续 major 扩展点。
2. **genome 是声明式清单**：必含字段与嵌套结构以
   [`docs/protocol.md`](../protocol.md) 的 `genome` 表为准；本 ADR 只记录不可变的语义
   （6 组搜索空间、parent 谱系、artifact 哈希、v1 空 skills）。
3. **可复现哈希**：`genome_id = "sha256:" + sha256(canonical(manifest 去掉 genome_id))`；
   `artifacts` 内嵌全部被引用文件的内容哈希（`harness.py`、`verifier.py`、`task_provider.py`、
   `switches.schema.json`、prompts、tools、manifest）。任一字节变化 ⇒ verify 失败；
   同步更新清单 ⇒ id 改变。新增未被引用的候选文件不改变 id。
4. **shell 只声明能力与上限**：`manifest.json` 的 `capabilities` 只含 `switch_domains` 与
   `max_execution_budget_usd` / `max_wall_ms` / `max_turns` / `max_attempts`。
   出现 `tolerance` / `cost_threshold` / `invalid_ratio` / `repeat_policy` 即非法。
5. **依赖方向**：core 定义七端口并自带 Gate / Archive / Ledger 默认实现；shell 提供
   Task / Harness / Verifier；adapter 提供 Runtime。core 零第三方依赖，换 shell 不需要改 core。
6. **scenario_version 触发面**：`harness.py` / `verifier.py` / `task_provider.py` /
   `switches.schema.json` / `bench/**` 任一变更 ⇒ 必须升版本并重跑基线。
   每次 run 与 gate decision 带 `shell_version` 与 `verifier_hash`；禁止跨版本比较。
7. **提案越域**：proposal 的 switches 必须落在当前域内（thinking/context 枚举 + tools=bash）；
   第四维、`compaction`、未知键都是非法候选，schema 与数据类双重拒绝。

## 后果

- `molt/config.py` 与 `configs/v0-baseline.json` 退役；基线迁为
  `shells/code-repair/genomes/v0.json`。
- `python -m molt genome hash/verify/validate` 成为 genome 的唯一机械检查点。
- 换 shell 或改判定器只需升 `scenario_version` 并重跑基线，已存结论不会与新结论混比。
