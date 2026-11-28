# ADR-0025：统一配置模型、配置树与自动路由

| 项 | 值 |
|---|---|
| 状态 | Draft |
| 日期 | 2026-11-21 |
| 基线 | `a5ce76dbbe60`（`main`，`pyproject` 版本 `1.2.0rc8`） |
| 实现状态 | 未交付：无 config DAG / 自动路由；Settings 仅 mock（2026-11-27，`6198139`） |
| 关联 | spec §6.5 / §12；前置 0018 / 0019 / 0021 / 0022 |
| 决策者 | 用户（评审中） |

Spec: `docs/v3-agent-tui-spec.md`

## 1. 背景

v1 的 genome 解决评测候选可比性：`switches`（thinking / context / tools）、`prompt_ref`、`tools_ref`、`runtime`、`budget`、`artifacts`、`scenario_version`，`genome_id=sha256(canonical manifest)`。它不解决「当前会话用哪套配置」。

v3 的配置要覆盖 model / provider / thinking / tools / MCP / permissions / prompt，且要能试、能对比、能回退、能按任务自动选。若另造一套与 genome 并列的 id，评测与运行会对不上；若把配置塞进 kernel 版本图，业务语义会污染 kernel。

本 ADR 固定统一配置字段、genome 投影、配置 DAG 与自动路由。

## 2. 决策

### D1 统一配置字段

- 顶层：`schema_version` / `label` / `parents[]` / `scenario_version`；
- `model{provider, id, base_url?, params}`；`thinking{level, budget_tokens?}`；`tools{allow[], deny[], max_parallel}`；`mcp{servers[]}`；
- `permissions{mode, rules[]}`（规则形状见 0023 D1）；`prompt{system, project_doc, append[]}`；`memory{inject, budget_tokens, scopes[]}`；
- `budget{wall_ms, turns, tokens, cost_micros}`；`routing{mode, pin?, candidates[]}`；`ext{}`。

### D2 genome 是子集投影

| genome 字段 | v3 来源 |
|---|---|
| `thinking` | `thinking.level` |
| `context` | `project_doc` + `memory.inject` → `full` / `minimal` |
| `tools` | `allow` 含 `exec` → `bash` |
| `prompt_ref` / `tools_ref` | managed 快照 |
| `budget` | 同名 |
| `artifacts` | managed `path` → `sha256` |
| `runtime{runtime_id,version}` | `model.provider` → `runtime_id`；provider adapter / Node 运行时版本 → `version` |
| `scenario_version` | 配置顶层 `scenario_version` |
| `parent_id` | 取 `config_edges` 中 `kind=derive` 的主父（无父为 `null`）；**不沿用 v1 父 genome** |

`genome_id = "sha256:" + sha256(canonical(genome 去掉 genome_id))`（带 `sha256:` 前缀），实现沿用 `molt/core/genome.py`；跨 `scenario_version` 不可比。

**不沿用 v1 父 genome**：v1 genome 的 `parent_id` 只作为 golden 回放时的显式注入值，运行时不从 v1 state 推导；投影产物的 `parent_id` 固定取 `config_edges` 中 `kind=derive` 的主父，无父即 `null`（与 spec §12 一致）。

**golden 要求**：同一配置经 Python `molt/core/genome.py::compute_genome_id` 与 TS 投影分别计算，`genome_id` 必须**字节一致**（含 `sha256:` 前缀）；不一致时 **P2 不得上线**。

### D3 配置树是 DAG

`id='c_'+sha256(canonical(body))[:16]`；`parent_ids` 0 / 1 / 2 个；操作 `derive` / `merge` / `apply` / `rollback` / `diff`；`merge` 取双父共同祖先逐字段三方合并，冲突返回 409 `merge_conflict` 并保留双方候选；`apply` 要求 `scenario_version` 一致；`rollback` 以历史节点为父新建，不改历史。

### D4 自动路由默认 `suggest`

- `off`：只用 `pin`；
- `suggest`：显示建议 + 理由，用户一键采用；
- `auto`：满足门槛才切换，并写 `usage.route_decision`。

决策链：特征（`task_class_hash` / `tokens_est` / `needs_tools`）→ 规则过滤 top-k ≤4 → 按历史成功率 / 平均墙钟 / 平均成本 / 用户 pin 打分 → 1 个候选 + 理由。

### D5 auto 门槛与回滚

同 `task_class_hash` 连续 N 次成功（默认 5）；期间无 `deny` / `ask` 回滚；成本未超预算；连续 2 次失败或用户改 `pin` → 回落 `suggest` 并记录；只在同 `scenario_version` 内统计。

### D6 配置与版本

config 节点是应用层对象（SQLite `configs`），不直接作 kernel snapshot；需固化时由 broker 把 `prompt` / `tools` 落成 managed 快照，回填 `path → sha256` 到 `genome.artifacts`。

### D7 交付状态与边界（2026-11-27，基线 `6198139`）

- **genome 目标保留**：D2 的 genome 子集投影、`sha256:` 前缀与 Python / TS 字节一致校验仍是目标；现行 `molt/core/genome.py` 只覆盖 v1 genome，v3 投影与 golden 校验未实现。
- **配置 DAG 未实现**：无 `configs` / `config_edges` 表；`crates/molt-tui/src/settings.rs` 的 `apply_config` 只改 `mock.active_config`，不是真实配置应用。
- **路由未实现**：`off` / `suggest` / `auto` 仅存在于文档；无 `usage.route_decision` 写入，无成功 / 失败统计。
- 本 ADR 保持 **Draft**。D2 的 genome 子集可作为独立目标先行；D3–D5 的 DAG / 路由在 agent + SQLite 落地后重启。**重启条件**：v3 agent 存储与 Settings 接线排期启动时，先定稿 X1–X4 并补 config DAG 验收，再实现路由。Q7（auto 的 N 与成功定义）对应 X1；A6（kernel manifest 允许额外 root 键）随 TS broker / golden 回归验证。

## 3. 备选方案

| 方案 | 内容 | 否决理由 |
|---|---|---|
| A | 单一全局配置 | 无法试两套，无法回答「上次为何好」 |
| B | 配置进 kernel 版本图 | 业务语义进 kernel，且频率高 |
| C | 默认 `auto` | 用户不知情被换模型 / 成本，失败无法归因 |
| D | 只用 `pin`，不路由 | 多模型靠手工切换，无依据 |
| E | 直接复用 genome | 缺 permissions / MCP / prompt 正文 |
| F | 本方案（DAG + 投影 + suggest） | 采纳 |

## 4. 理由

- 兼容：genome 仍可比，v3 是其超集，`genome_id` 公式沿用 v1 并统一加 `sha256:` 前缀；
- 可解释：`suggest` 给理由，用户可拒绝；
- 可回退：节点不可变，`apply` 旧节点即可；
- 隔离：跨 `scenario_version` 不比较，避免错误归因。

## 5. 影响

- 新增 `configs` / `config_edges` 表；节点随实验增长，需要归档策略；需要离线路由夹具；
- Settings 屏需要 DAG + diff + apply；`usage` 需记录 `route_decision` 与理由。

## 6. 失败场景 / 如果不这么做

| 若违反 | 直接后果 | 长期后果 |
|---|---|---|
| 无配置树 | 无法试两套，回退靠手改文件 | 不可复现 |
| genome 与配置两套 id | 评测与运行对不上 | 结果无法归因 |
| 跨 `scenario_version` 比较 | 旧场景选最省钱模型，新场景崩 | 归因错误 |
| 默认 `auto` | 成本 / 行为不可预测 | 用户失去信任 |
| auto 无回滚 | 坏选择持续被选中 | 长期退化 |

## 7. 回滚 / 撤销

| 动作 | 步骤 | 结果 |
|---|---|---|
| 路由选错 | 用户 `pin`（`route_decision=pinned`） | 该 `task_class` 停用 auto |
| 配置节点有问题 | `apply` 上一节点 | 新指针，不改历史 |
| 整期撤销 | `routing.mode=off` + 固定 `pin` | 回到单配置 |
| 保留证据 | `configs` 只读保留 | 可审计，不删 |

## 8. 开放问题

| ID | 问题 | 状态 |
|---|---|---|
| X1 | auto 的 N 与成功定义 | 待确认（建议 N=5；成功 = turn 完成且无用户回退）。**延期条件**：auto 路由实现前确认；未确认前默认 `suggest` |
| X2 | `switches.context` 的 `full` / `minimal` 投影规则 | 待定稿。**延期条件**：上下文投影实现前定稿 |
| X3 | 配置节点 TTL / 归档 | 待评估。**延期条件**：config DAG 存储实现前评估归档策略 |
| X4 | 是否从 `.molt/config.json` 导入 | 待评估。**延期条件**：配置导入实现前评估 |

## 9. 与现有 ADR 的关系

| ADR | 关系 | 说明 |
|---|---|---|
| 0018 | 对接 | snapshot DAG 管内容版本；config DAG 管参数演化，经 `artifacts` / managed 快照挂钩 |
| 0019 | 不引入 | 不读 v1 state；genome 字段沿用 v1 schema |
| 0021 | 依赖 | broker 落 prompt / tools 快照并回填 hash |
| 0022 | 分离 | 两棵树：`c_` vs `n_` |
| 0023 | 引用 | `permissions` 规则复用 0023 D1 |

## 10. 修订记录

| 日期 | 基线 | 修订内容 | 来源 |
|---|---|---|---|
| 2026-11-21 | `a5ce76dbbe60` | 初稿（W-04c）：统一配置、genome 投影、config DAG、suggest / auto 路由 | spec §19 |
| 2026-11-27 | `6198139` | 补 D7：分开 genome 目标与未实现的 DAG / 路由；X1–X4 写延期条件；状态保持 Draft | 2026-11-27 设计-实现对账（spec §18 Q7/A6，本文件 §8；`crates/molt-tui/src/settings.rs` 静态核对） |
