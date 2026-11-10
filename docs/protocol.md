# 协议 v1

> 本文件由 `python -m molt schema docs` 从 `schemas/v1/*.schema.json` 生成，禁止手改；字段事实源是 schema 文件与 `molt/core/types.py`。

## 通用规则

- 11 类记录：`scenario`、`shell_manifest`、`genome`、`task`、`run`、`result`、`gate_decision`、`snapshot`、`archive`、`event`、`proposal`。
- 每份记录都含 `schema_version`（`1.x.y`）与 `ext`。同 major 内顶层字段冻结；minor 只允许在 `ext` 内新增或新增记录类型。
- 新增顶层字段 / 改名 / 删字段 = major 升级 + ADR + 新 `schemas/vN/`。
- v1 读者拒绝未知顶层字段，保留未知 `ext`。
- `event` 必带 `scope: search | report`；已知 type 的 payload 是 tagged union，未知 type 视为 open（保留原文，审计不断言完整性）。
- canonical JSON：键排序、UTF-8、无多余空白；哈希 sha256；树哈希按相对路径 POSIX 排序，逐行 `path\0content_hash\n`，不含 mtime，拒绝 symlink。

## 记录

### `scenario`

场景：题集、门禁策略、隔离网络三档

| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 协议版本；同一 major 内顶层字段冻结，minor 只允许扩展 ext 或新增记录类型 |
| `scenario_version` | string | 是 | 比较域；跨版本禁止比较 |
| `scenario_id` | string | 是 |  |
| `title` | string | 是 |  |
| `shell_id` | string | 是 |  |
| `shell_version` | string | 是 |  |
| `bench_hash` | sha256 | 是 | scenario 引用题集的题面树哈希：task.json + workspace + tests 全部文件 |
| `task_sets` | task_sets | 是 |  |
| `policy` | policy | 是 |  |
| `isolation` | isolation | 是 |  |
| `ext` | object | 是 | 扩展点；v1 读者保留未知键，不参与核心判定 |

### `shell_manifest`

shell 只能声明能力与上限；门禁阈值唯一 owner 是 scenario.policy

| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 协议版本；同一 major 内顶层字段冻结，minor 只允许扩展 ext 或新增记录类型 |
| `shell_id` | string | 是 |  |
| `shell_version` | string | 是 |  |
| `name` | string | 是 |  |
| `artifact_roots` | array<string> | 是 |  |
| `entrypoints` | entrypoints | 是 |  |
| `capabilities` | capabilities | 是 |  |
| `ext` | object | 是 | 扩展点；v1 读者保留未知键，不参与核心判定 |

### `genome`

声明式三维候选；清单内嵌全部被引用 artifact 的内容哈希

| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 协议版本；同一 major 内顶层字段冻结，minor 只允许扩展 ext 或新增记录类型 |
| `scenario_version` | string | 是 |  |
| `genome_id` | string | 是 | 由规范化清单（含 artifact 哈希）计算 |
| `parent_id` | string | null | 是 |  |
| `shell_id` | string | 是 |  |
| `shell_version` | string | 是 |  |
| `switches` | switches | 是 |  |
| `prompt_ref` | string | 是 | 仓库内相对 POSIX 路径 |
| `tools_ref` | string | 是 | 仓库内相对 POSIX 路径 |
| `skills_ref` | array<string> | 是 | v1 恒空 |
| `runtime` | runtime | 是 |  |
| `budget` | budget | 是 |  |
| `artifacts` | artifacts | 是 |  |
| `ext` | object | 是 | 扩展点；v1 读者保留未知键，不参与核心判定 |

### `task`

题面：工作区与外部测试目录引用

| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 协议版本；同一 major 内顶层字段冻结，minor 只允许扩展 ext 或新增记录类型 |
| `task_id` | string | 是 |  |
| `scenario_version` | string | 是 |  |
| `shell_id` | string | 是 |  |
| `scope` | enum(search | report) | 是 |  |
| `title` | string | 是 |  |
| `workspace_ref` | string | 是 | 仓库内相对 POSIX 路径 |
| `tests_ref` | string | 是 | 仓库内相对 POSIX 路径 |
| `timeout_s` | number | 是 |  |
| `ext` | object | 是 | 扩展点；v1 读者保留未知键，不参与核心判定 |

### `run`

单题一次执行；必带 shell_version 与 verifier_hash

| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 协议版本；同一 major 内顶层字段冻结，minor 只允许扩展 ext 或新增记录类型 |
| `run_id` | string | 是 |  |
| `scenario_version` | string | 是 |  |
| `genome_id` | string | 是 |  |
| `task_id` | string | 是 |  |
| `scope` | enum(search | report) | 是 |  |
| `attempt` | integer | 是 |  |
| `shell_version` | string | 是 |  |
| `verifier_hash` | sha256 | 是 |  |
| `seed_control` | enum(none | fixed) | 是 |  |
| `started_at` | utc | 是 |  |
| `finished_at` | utc | 是 |  |
| `outcome` | enum(passed | failed | invalid | timeout | budget_exceeded | error) | 是 |  |
| `usage` | usage | 是 |  |
| `verification` | verification | 是 |  |
| `artifacts` | object | 是 |  |
| `ext` | object | 是 | 扩展点；v1 读者保留未知键，不参与核心判定 |

### `result`

汇总；scope=report 必含搜索集与 holdout 双分数

| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 协议版本；同一 major 内顶层字段冻结，minor 只允许扩展 ext 或新增记录类型 |
| `result_id` | string | 是 |  |
| `scenario_version` | string | 是 |  |
| `genome_id` | string | 是 |  |
| `scope` | enum(search | report) | 是 |  |
| `seed_control` | enum(none | fixed) | 是 |  |
| `run_refs` | array<string> | 是 |  |
| `scores` | object | 是 |  |
| `generated_at` | utc | 是 |  |
| `ext` | object | 是 | 扩展点；v1 读者保留未知键，不参与核心判定 |

### `gate_decision`

门禁结论必须携带逐题配对表；聚合数字不得单独出现

| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 协议版本；同一 major 内顶层字段冻结，minor 只允许扩展 ext 或新增记录类型 |
| `decision_id` | string | 是 |  |
| `scenario_version` | string | 是 |  |
| `shell_version` | string | 是 |  |
| `verifier_hash` | sha256 | 是 |  |
| `baseline_genome_id` | string | 是 |  |
| `candidate_genome_id` | string | 是 |  |
| `verdict` | enum(adopt | reject | incomparable | unstable) | 是 |  |
| `reason` | string | 是 |  |
| `k` | integer | 是 |  |
| `limits_identical` | boolean | 是 |  |
| `budget_check` | budget_check | 是 |  |
| `invalid_ratio` | invalid_ratio | 是 |  |
| `scores` | object | 是 |  |
| `paired` | array<paired_row> | 是 |  |
| `created_at` | utc | 是 |  |
| `ext` | object | 是 | 扩展点；v1 读者保留未知键，不参与核心判定 |

### `snapshot`

快照覆盖 manifest 声明的全部 roots；roots 与文件哈希同键，缺一即拒绝

| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 协议版本；同一 major 内顶层字段冻结，minor 只允许扩展 ext 或新增记录类型 |
| `snapshot_id` | string | 是 |  |
| `generation` | integer | 是 |  |
| `created_at` | utc | 是 |  |
| `roots` | array<string> | 是 |  |
| `tree_hash` | sha256 | 是 |  |
| `files` | object | 是 | root → {相对路径: sha256}；顶层键必须与 roots 完全相同 |
| `ext` | object | 是 | 扩展点；v1 读者保留未知键，不参与核心判定 |

### `archive`

采纳记录；只存 result_refs[]，不内联分数

| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 协议版本；同一 major 内顶层字段冻结，minor 只允许扩展 ext 或新增记录类型 |
| `archive_id` | string | 是 |  |
| `generation` | integer | 是 |  |
| `genome_id` | string | 是 |  |
| `parent_id` | string | null | 是 |  |
| `adopted_at` | utc | 是 |  |
| `snapshot_id` | string | 是 |  |
| `result_refs` | array<string> | 是 |  |
| `note` | string | 是 |  |
| `ext` | object | 是 | 扩展点；v1 读者保留未知键，不参与核心判定 |

### `event`

事件按 scope 分流；已知 type 的 payload 为 tagged union，未知 type 保留原文

| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 协议版本；同一 major 内顶层字段冻结，minor 只允许扩展 ext 或新增记录类型 |
| `event_id` | string | 是 |  |
| `scope` | enum(search | report) | 是 |  |
| `type` | string | 是 |  |
| `ts` | utc | 是 |  |
| `run_id` | string | null | 是 |  |
| `payload` | object | 是 | 已知 type 为 tagged union；未知 type 视为 open |
| `ext` | object | 是 | 扩展点；v1 读者保留未知键，不参与核心判定 |

### `proposal`

提案；越域或出现第四维即非法候选

| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | string | 是 | 协议版本；同一 major 内顶层字段冻结，minor 只允许扩展 ext 或新增记录类型 |
| `proposal_id` | string | 是 |  |
| `scenario_version` | string | 是 |  |
| `proposer_id` | string | 是 |  |
| `parent_id` | string | 是 |  |
| `switches` | switches | 是 |  |
| `rationale` | string | 是 |  |
| `created_at` | utc | 是 |  |
| `cost_usd` | number | 是 |  |
| `ext` | object | 是 | 扩展点；v1 读者保留未知键，不参与核心判定 |
