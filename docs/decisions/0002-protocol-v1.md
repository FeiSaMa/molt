# ADR 0002：协议 v1

- 状态：已决定
- 日期：2026-11-10
- 关联：[`docs/protocol.md`](../protocol.md)、`schemas/v1/`、`molt/core/types.py`

## 背景

v0 只有 `docs/contracts.md` 的 4 份落盘文件，字段缺 `model` / `thinking_level` / `cost_usd` /
`cache_read_tokens` / `turns` / `max_context_tokens`；`suite` 三值枚举绑定本仓目录；
`tests_hash` 的算法与范围未定义；没有 `scenario` / `genome` / `event` / `proposal`。
多种口径（字典序、cost_per_solved、掉 k 题）同时存在，读者无法判断哪套有效。

## 决定

1. **唯一事实源**：`schemas/v1/*.schema.json` 与 `molt/core/types.py` 双校验。前者是字段清单，
   后者是运行时约束；二者对 golden 全通过、对 mutation 全拒绝。
2. **11 类记录**：`scenario`、`shell_manifest`、`genome`、`task`、`run`、`result`、
   `gate_decision`、`snapshot`、`archive`、`event`、`proposal`。每份含 `schema_version` 与 `ext`。
3. **顶层冻结**：同 major 内顶层字段不增、不改名、不删。minor 只允许在 `ext` 内新增，
   或新增记录类型 / 新事件 type。新增顶层字段 = major 升级 + 新 ADR + `schemas/vN/`。
4. **读者行为**：v1 读者拒绝未知顶层字段；保留未知 `ext`；`ext` 不参与核心判定。
5. **事件分流**：`event.scope ∈ {search, report}`，产物分别为 `events/search.jsonl` 与
   `events/report.jsonl`（per-run 文件在 `events/<scope>/<run_id>.jsonl`，聚合产物单写者物化）。
   已知 type 的 payload 是 tagged union，未知 type 视为 open：保留原文，审计不断言完整性。
6. **必带字段**：`scenario` / `genome` / `gate_decision` / `proposal` 必带 `scenario_version`；
   `run` / `gate_decision` 必带 `shell_version` 与 `verifier_hash`；`result` 必带
   `seed_control: none | fixed`。
7. **result 双形态**：`scope=search` 只含搜索集分数；`scope=report` 必须同时含
   `scores.search` 与 `scores.holdout`（防止只在搜索集变好）。
8. **文档**：`docs/protocol.md` 由 `python -m molt schema docs` 从 schema 生成，不手写字段表；
   `docs/contracts.md` 归档为 `docs/archive/contracts-v0.md`。

## 兼容矩阵

| 变更 | 级别 | 处理 |
| --- | --- | --- |
| `ext` 内新增键 | minor | 旧读者保留、忽略 |
| 新增记录类型 | minor | 旧读者不认识但不影响已读记录 |
| 新增已知 event type | minor | 旧读者按 open 处理 |
| 已知 event payload 新增字段 | major | 或放进 `payload.ext` / 顶层 `ext` |
| 新增 / 改名 / 删除顶层字段 | major | 新 `schemas/vN/` + ADR + 迁移 |

## 后果

- 旧 `configs/v0-baseline.json` 不再有契约地位，迁成 `shells/code-repair/genomes/v0.json`；
- `molt/store.py`、`molt/score.py`、`molt/runner.py` 退役，职责拆进 `molt/core/`；
- 事件流可被 HTML / 审计独立解析，跨 scope 泄漏（如 `holdout.accessed` 进 search 流）在
  schema 与数据类两层都被拒绝。
