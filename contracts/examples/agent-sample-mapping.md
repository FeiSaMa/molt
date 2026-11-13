# agent.sample.json → canonical API 字段对照

`web/agent.sample.json` 是**示意样例**（`contract_status: illustrative`），不是契约。
canonical API 的字段级事实源是 `contracts/schemas/*.schema.json` 与
`molt/agent/probe.py` 的投影；本文件逐项说明样例字段如何落到 canonical 字段，
以及哪些样例值**不**被沿用。

## 1. session

| 样例字段 | canonical 字段 | 说明 |
| --- | --- | --- |
| `session.session_id` | `session_id` | 同义；canonical 形如 `sess-<16 hex>`，进程重启即失效 |
| `session.status` | `status` | 样例只有 `idle`；canonical 为 `idle` / `in_progress`（turn 执行期间） |
| `session.provider` | `provider` | 样例 `sample` 不是合法值；canonical 只有 `mock` / `openai-compatible`（服务启动参数决定） |
| `session.created_at` | `created_at` | ISO 8601 UTC |
| `session.last_event_id` | `last_event_id` | 无事件时为 `0`；不是事件缓冲长度 |
| `session.messages[].message_id` | `messages[].message_id` | 同义 |
| `session.messages[].created_at` | `messages[].created_at` | 同义 |
| `session.messages[].role` / `content` | 同名字段 | 历史只保留成功 turn 的 user/assistant 对 |
| （样例缺） | `messages[].turn_id` | canonical 必填；一轮 user 与 assistant 共用同一 `turn_id` |
| `session.messages[].experiment_refs` | `experiment_refs` | 可选；仅当本轮工具返回了已存在的 experiment 时出现 |
| `experiments.revision` | `revision` | 样例 `sample-revision-001` 是占位；canonical 是 64 位 sha256 |
| `experiments.comparability_note` | — | canonical 不返回该自由文本；`comparability_key` 全为 `null`，跨实验比较在文档里禁止 |

## 2. turn_events → SSE

| 样例字段 | canonical | 说明 |
| --- | --- | --- |
| `turn_events[].event_id` | SSE `id:` | 每 session 从 1 递增；`since` 正数排他 |
| `turn_events[].turn_id` | `data.turn_id` | 除 `done` 外所有事件都带 |
| `turn_events[].type = "status"` | `event: status` | canonical 追加 `state ∈ {thinking, tool, responding}` |
| `turn_events[].type = "tool_call"` | `event: tool.call` | `tool_call_id → call_id`；`tool: "experiments.get" → "experiments_get"`（下划线，点号非法） |
| `turn_events[].type = "tool_result"` | `event: tool.result` | `ok: true → status: "ok"`，`ok: false → status: "error"`（另允许 `denied`）；`result_ref → result`（canonical 直接给投影对象，不再用字符串引用） |
| `turn_events[].type = "message"` | `event: message.completed` | canonical 另有 `message.delta`（40 字符窗口）事件 |
| `turn_events[].type = "done"` | `event: turn.completed` **+** `event: done` | canonical 把「turn 完成」与「流结束」拆成两个事件；`done.data` 固定 `{}` |
| `turn_events[].status = "completed"` | `turn.completed.data.turn_count` | canonical 用成功 turn 计数（含本轮），不是枚举 status |
| （样例缺） | `event: error` | SSE-only 错误码：`upstream_*`、`tool_denied`、`too_many_tool_calls`；`error` 后连接关闭 |

## 3. experiments

canonical 投影的形状是冻结契约：**版本字段扁平、指标嵌在 `metrics`、证据计数与完整性分开**。

| 样例字段 | canonical 字段 | 说明 |
| --- | --- | --- |
| `items[].experiment_id` | `experiment_id` | ID 对照见 §5 |
| `items[].title` | `title` | 同名同义 |
| `items[].study_kind` | `study_kind` | 同名同义（闭集） |
| `items[].status` | `status` | 与 `stage` **分开**：`status` 是完成状态，`stage` 是研究阶段（`scan` / `validation` / `gate` / `reflection` / `rehearsal`）；样例没有 `stage` |
| `items[].scenario_version` / `shell_version` | `scenario_version` / `shell_version` | 扁平字段，不再包一层；缺失时为 `null`，并配 caveat |
| （样例缺） | `verifier_hash` | 扁平字段；2c 使用 `b194ce32…` 完整哈希，缺失时为 `null` |
| `provenance.kind/basis` | `provenance.kind` / `provenance.basis` | 同名；canonical 额外有 `mixed` |
| `provenance.coverage.recorded_runs` / `total_runs` | `provenance.coverage.records_with_evidence` / `records_total` | 改名；语义是「带可复算证据的记录数 / 总记录数」。**2c main 样例写 16/16，canonical 为 20/20**：主实验 16 行 + control 4 行 = 20 行，源 `expected_runs=20`、`missing_runs=0`，gate 的成本也按 main+control 计 |
| `metrics.verdict` | — | **verdict 不进 API**（避免把源判定当成能力结论）；canonical 只给 `metrics.passed` / `metrics.total` / `metrics.pass_rate` |
| `metrics.cost_total_usd` | `metrics.cost.usd` | 改名；并补 `metrics.cost.scope` / `includes_control` / `includes_reflection` / `includes_retries` / `estimated` |
| `metrics.wall_ms` | `metrics.wall_ms.value` | 并补 `metrics.wall_ms.scope` / `includes_reflection` |
| 自由 metrics（如 `passed_runs`、`family_solved`、`candidate_all_four`、`headroom_three`、`baseline_x8`） | `metrics.passed` / `metrics.total` / `metrics.pass_rate` + `comparison` | 只保留 canonical 标量；**矩阵样例的 `passed_runs: 20` 不沿用**：canonical 的 `0/16` 是 family run 口径（4 档 × 2 题 × 2 rep = 16 行全部未通过），36 条 recorded 含 control 16 + oracle 4 |
| `metrics.passed_runs` / `recorded_runs`（v2/v3） | `metrics.passed` / `metrics.total` / `metrics.recorded_runs` / `evidence.rows_count` / `evidence.verdict_runs_count` | v2：3/4（task 级，x7 的 1/2 计通过）、rows_count 12、verdict_runs_count 6；v3：4/4、rows_count 18、verdict_runs_count 10 |
| （样例没有口径字段） | `metrics.score_scope` | 计分口径：`run` / `task` / `cell`；旧口径字段名已废弃 |
| （样例没有任务范围） | `task_scope: {count, reps, cells}` | 题数 / 每题重复数 / 判别单元数；`count` 是题数字段的改名 |
| `evidence.completeness`（字符串） | `evidence.completeness`（对象） | 固定四键 `rows` / `verdict` / `reflection` / `traces`，值 ∈ `present` / `absent` / `partial` / `not_applicable`；不再用 `{level, complete}` |
| `evidence.source_ref` | `evidence.files` | 单个字符串 → 仓库相对路径数组；不带原始 report 文本 |
| `caveats`（自由文本） | `caveats`（enum 闭集） | 16 项闭集；中文文案在 `probes/probe_experiments_manifest.json.caveat_catalog`，API 只返回枚举值 |
| `comparability_key` | `comparability_key` | 本批固定 `null` |

## 4. 8 条 canonical 的范围

canonical 索引固定 8 条，含样例中的 fixture 5 条 + 现场 3 条：

| # | experiment_id | 来源 |
| --- | --- | --- |
| 1 | `matrix-core-v3` | fixture（样例 `probe-matrix-c1`） |
| 2 | `validity-green` | fixture（样例 `probe-1-validity`） |
| 3 | `2c-main` | fixture（样例 `probe-4-main-2c`） |
| 4 | `2c-memory-v2` | fixture（样例 `probe-4-memory-v2`） |
| 5 | `2c-memory-v3` | fixture（样例 `probe-4-memory-v3`） |
| 6 | `rehearsal-run1` | 现场彩排 run1 |
| 7 | `rehearsal-run2` | 现场彩排 run2 |
| 8 | `batch1` | 判分器 cell 判别验证 |

## 5. ID 对照

| 样例 ID | canonical ID |
| --- | --- |
| `probe-4-main-2c` | `2c-main` |
| `probe-4-memory-v2` | `2c-memory-v2` |
| `probe-4-memory-v3` | `2c-memory-v3` |
| `probe-matrix-c1` | `matrix-core-v3` |
| `probe-1-validity` | `validity-green` |
| （样例无） | `rehearsal-run1` / `rehearsal-run2` / `batch1` |

## 6. 明确不沿用的样例值

- `schema_version: "agent-web-sample/0.1.0"` → canonical `1.0.0`，并新增
  `generated_at`、`revision`、`excluded`。
- 样例 `provenance.kind: "mixed"`（v2/v3）：canonical 依源记录 `command` 覆盖判为
  `live` + caveat `partial_provenance`（v2 6/12、v3 10/18）；`mixed` 只用于 batch1。
- 样例 `messages` 没有 `turn_id`；canonical 必填。
- 样例的事件类型是驼峰/点号写法；canonical 一律 `noun.verb`，工具名用下划线。
