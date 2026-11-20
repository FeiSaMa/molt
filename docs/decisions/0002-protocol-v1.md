# ADR 0002：协议 v1

本 ADR 保存历史字段及比较语义。后续产品的 v1 兼容范围见 [ADR-0019](0019-retire-v1-compatibility.md)。

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

## 门禁夹具的逐侧版本约定

`SIDE_KEYS`（`molt/cli.py`）固定为 `genome_id`、`limits`、`invalid_ratio`、
`overhead_spend_usd`、`tasks`，**不含** `scenario_version`。夹具可用
`baseline_scenario_version` / `candidate_scenario_version` 逐侧覆盖版本，缺省回落到 case 级
`scenario_version`；构造出的 `SideEval.scenario_version` 与 scenario 不一致时，
`policy.decide` 抛 `IncomparableError`，禁止跨版本比较。

## 兼容矩阵

| 变更 | 级别 | 处理 |
| --- | --- | --- |
| `ext` 内新增键 | minor | 旧读者保留、忽略 |
| 新增记录类型 | minor | 旧读者不认识但不影响已读记录 |
| 新增已知 event type | minor | 旧读者按 open 处理 |
| 已知 event payload 新增字段 | major | 或放进 `payload.ext` / 顶层 `ext` |
| 新增 / 改名 / 删除顶层字段 | major | 新 `schemas/vN/` + ADR + 迁移 |

## 补充：事务恢复与 bench 校验严格性（2026-11-10 复核后追加）

- **崩溃恢复入口**：恢复发生在「下一次写事务入口」——`StateStore.adopt()` / `revert()` 拿到
  同一把 `state/lock` 后先执行内部 `_recover_locked()`，再继续原操作；恢复幂等，结果只落旧态
  或新态。只读路径（`read_current` / `list_*`）不取写锁、不触发恢复，因此读者仍只看到两态之一。
  `molt doctor --recover` 保留为显式兜底（锁内异常后人工介入）。
  **与 D6 原文「下次启动自动恢复」的差异**：构造 `StateStore` 本身不做恢复，避免纯读场景强拿
  写锁；这里的「启动」解释为「下一次写事务启动」。若后续要做进程级启动恢复，必须显式加锁并
  说明对只读并发的影响。
- **`scenario validate` 的 bench 解析范围**：它会解析 `bench/**` 下的**全部** `task.json`
  以建立 `task_id → 路径` 索引，因此未被 `scenario.task_sets` 引用的残缺 task.json 也会让
  validate 失败。这是**严格性而非 bug**（索引不静默跳过）；新增候选题目请写完整 schema
  （`task_id` / `scenario_version` / `workspace_ref` / `tests_ref` 等齐全）。题面哈希
  `bench_hash` 仍只覆盖被引用题集，未引用文件的**内容**不影响哈希值。


## 后果

- 旧 `configs/v0-baseline.json` 不再有契约地位，迁成 `shells/code-repair/genomes/v0.json`；
- `molt/store.py`、`molt/score.py`、`molt/runner.py` 退役，职责拆进 `molt/core/`；
- 事件流可被 HTML / 审计独立解析，跨 scope 泄漏（如 `holdout.accessed` 进 search 流）在
  schema 与数据类两层都被拒绝。

## Pi runtime 的 ext 落点

B1 `run` 顶层字段冻结，没有 `runtime`。B2b 的 Pi runtime 元数据（`provenance`、
`project_trust`、`isolation`、`session_path`、`log_ref`、`ledger`、`budget`）全部写入
`run.ext.runtime`；`run.usage` 仍是唯一的执行成本来源。见 ADR-0006。
