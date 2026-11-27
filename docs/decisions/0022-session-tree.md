# ADR-0022：会话树、fork 与回退语义

| 项 | 值 |
|---|---|
| 状态 | Draft |
| 日期 | 2026-11-21 |
| 基线 | `a5ce76dbbe60`（`main`，`pyproject` 版本 `1.2.0rc8`） |
| 实现状态 | 未交付：会话树与 TUI 文件恢复未按本 ADR 实现；C5 待修（2026-11-27，`6198139`） |
| 关联 | spec §5.3 / §5.4 / §6.2–6.4 / §6.7 / §12 F6；前置 0018 / 0021 |
| 决策者 | 用户（X1 于 2026-11-21 确认） |

Spec: `docs/v3-agent-tui-spec.md`

## 1. 背景

用户对「回退」有三种不同需求：

1. 尝试一条路，失败后回退再试；
2. 只回对话，不动文件；
3. 连文件一起回。

业界分别由 Pi 的会话树、opencode 的 boundary、Claude 的 checkpoint 承载——它们是**不同层**的机制，不应混成一个。v2 的 `session_snapshot` 是线性 revision 链，且已归档。molt 已有配置版本图（0018）；若把会话树塞进同一结构，会出现 `snapshot_id` 指向消息的语义污染。

本 ADR 固定：树怎么长、fork 复制什么、checkpoint 落何时、文件回退怎么提交、两棵树如何分离。

## 2. 决策

### D1 会话树：`parent_id` + 不可变节点

`nodes(id, session_id, parent_id, seq, kind, payload_json)`；`kind ∈ {message, compaction, branch_summary, context_edit, model_change, config_change, checkpoint, label, turn_error}`。节点只增不改；删除用 `context_edit{replacement:null}` 表达。路径查询用 SQLite 递归 CTE，不物化闭包表。

### D2 leaf 指针是唯一「当前位置」

`sessions.leaf_node_id`。回退对话 = 改 `leaf_node_id` + 追加 `context_edit`；不删节点、不改父链。这样「回退」在审计上是一等公民，而不是让历史消失。

### D3 fork = 复制到边界

`POST /v1/sessions/{id}/fork` 带 `fork_from_session_id` 与 `fork_boundary_node_id`。复制边界节点及其祖先（新 ID，保留 `origin_node_id`），**不复制**边界之后的枝。理由：外键完整、事件序号独立、两侧可独立编辑。

### D4 checkpoint 的三个时机

- `turn.started` 之前；
- 写工具批次成功之后；
- 回退之前。

`payload_json` 固定为 `{generation, manifest_hash, workspace_snapshot_id, archive_id?}`。

### D5 文件回退 = 导出 + 前向 commit（禁用 kernel `rollback`）

步骤：找最近 checkpoint → 备份当前工作区 → 用 `broker.materialize` 物化 snapshot → 以当前 head 为父提交新 generation。

**已确认（用户，2026-11-21）**：采用导出 + 前向 commit，禁用 kernel `rollback`。原因：kernel rollback 会把归档 manifest 整份提交（`state.rs:392-419`），把 `index.json` 的 branches / active / events / digests 一起倒退（0018 V01 已记录）。历史只增不改，审计可重放（INV-10）。

#### D5.1 对话回退、TUI 文件恢复与 CLI 运维回退的边界（2026-11-27 解释；不改写 2026-11-21 决议）

| 操作 | 入口 | 语义 | 与 X1 的关系 |
|---|---|---|---|
| 对话回退 | TUI Tree / `POST /v1/sessions/{id}/rollback` 的 `targets.session` | 改 `sessions.leaf_node_id` + 追加 `context_edit`；不删节点、不改父链（D2） | 不受 X1 约束；不涉及文件与 kernel |
| TUI 文件恢复 | `/tree` 勾选 `targets.files` / 后续 API 文件分支 | 找最近 checkpoint → 备份当前工作区 → `broker.materialize` 导出目标状态 → 以当前 head 为父提交新 generation；旧历史继续可读 | **X1 的约束对象**：禁用 kernel `rollback`，只能前向提交 |
| CLI 运维回退 | `molt version rollback SNAPSHOT --expect-generation N` | 只改变活动快照（kernel 的活动选择）；分支头与已取得运行 pin 保持原值；旧导出与编辑目录仍可读（`docs/operations/version-cli.md`） | **不属于 X1 的文件回退**；保持原语义，不因 X1 改成导出 + 前向 commit |

**范围解释（决定，2026-11-27）**：X1（2026-11-21 已确认）中的「文件回退」只指上表第二行——把 checkpoint 的文件状态恢复到工作区并形成新 generation。公开 CLI `molt version rollback` 是只改变活动快照的运维命令，不是文件恢复，不在 X1 范围内；本 ADR 不缩窄、不改写 2026-11-21 的 X1 原文与 D5 正文，只补充术语边界。此段是解释 / 决定，不是 2026-11-21 的原文事实。

**实现状态（C5，B2 待修）**：TUI Versions 的 `rollback_last` 当前调用 `molt version rollback`（`crates/molt-tui/src/version_workspace.rs:571-591`），该调用既不是上表的「TUI 文件恢复」，也不能把 CLI 运维回退当作文件恢复。C5 列为 **B2 待修**：TUI 文件恢复改为导出 + 前向 commit，不调用 kernel `rollback`（也不借用会触发 kernel rollback 的 CLI 运维命令来冒充文件恢复）；B2 同时验证操作前后内容、generation、历史与无 kernel rollback 调用，并运行既有 CLI rollback 回归，确认它仍只改变活动快照。

### D6 两棵树分离

会话树 `nodes`（`n_` 前缀）；配置 DAG `configs` + `config_edges`（`c_` 前缀）。两棵树不得互相引用；唯一跨树引用是 `sessions.active_config_id`（INV-11）。

### D7 回退三复选

`targets{session, config, files}`；默认 `session=true, config=true, files=false`；**禁止三目标全 false**，空回退返回 400 `invalid_request`。**只有勾选 `files` 时才需要第二次确认**：勾选 `files` 后第一次 Enter 只进入确认子状态（展示文件数、目标 checkpoint、备份路径），第二次 Enter / `y` 才应用；`Esc` 回退到复选。仅回退 session / config 时第一次 Enter 即应用（一键回退会话 / 配置），不进入确认子状态。无 checkpoint → `409 cannot_restore_files`。

## 3. 备选方案

| 方案 | 内容 | 否决理由 |
|---|---|---|
| A | 复用 kernel rollback / archive | 会把 index 的 branches / active / events / digests 一起倒退 |
| B | git 快照 + revert | 引入 `.git` 依赖与独立 object DB，与「kernel 是唯一事务后端」冲突 |
| C | 只支持「只回会话」 | 不满足已冻结的「会话 + 配置 + 文件」回退需求 |
| D | fork 共享节点 | 事件序号 / leaf / 归档跨会话协调，删除会牵连两侧 |
| E | 会话树进 kernel | 业务语义进 kernel，违反 0015 |
| F | 本方案（不可变树 + 前向 commit） | 采纳 |

## 4. 理由

- 语义分层：会话树、配置 DAG、文件快照是三件事，各自模型最简；
- 前向 commit 可审计：历史不倒退，任何回退都留下新 generation；
- checkpoint 对应真实写入点，能精确回答「回到哪一个磁盘状态」；
- 对 `exec` 只承诺 checkpoint 时刻的整体状态，不假装能追踪任意写。

## 5. 影响

- `nodes` 随会话线性增长；fork 复制会增加行数。
- checkpoint 产生 CAS 对象，依赖 kernel GC。
- Tree 屏需显示 checkpoint 与 compaction 边界。
- `exec` 在两次 checkpoint 之间的写不会被单独记录（残余风险，UI 需说明）。

## 6. 失败场景 / 如果不这么做会怎样

| 若违反 | 直接后果 | 长期后果 |
|---|---|---|
| 用 kernel rollback 做文件回退 | 分支消失、事件索引倒退 | 审计链断裂，需人工修 state |
| 不分离两棵树 | 配置节点被当会话节点 fork | merge 失效，配置语义被污染 |
| 不做 checkpoint | 文件回退只能靠猜 | 恢复到错误状态，且无证据 |
| 文件回退只按一次 Enter 就应用 | 误覆盖工作区 | 需再回退一次，审计多出一次 generation |
| fork 共享可变节点 | 一侧编辑污染另一侧 | 两侧历史都不再可信 |
| 只改 leaf 不写 `context_edit` | 审计分不清「回退」与「从未发生」 | 复盘失败 |

## 7. 回滚 / 撤销

| 动作 | 步骤 | 结果 |
|---|---|---|
| 关闭文件回退 | 设 `targets.files` 不可用（UI 置灰 + API 409） | 只保留会话 / 配置回退 |
| 撤销一次会话回退 | 把 `leaf_node_id` 指回原节点 | 对话位置恢复 |
| 撤销一次文件回退 | 回退到「回退前」那个 checkpoint | 工作区回到操作前状态 |
| 下线端点 | 移除 `/rollback` 的文件分支 | 已有会话仍可读，历史不受影响 |

## 8. 开放问题

| ID | 问题 | 状态 |
|---|---|---|
| X1 | 文件回退是否禁用 kernel rollback | **已确认（用户，2026-11-21）**：采用导出 + 前向 commit，禁用 kernel rollback |
| X2 | fork 复制行还是共享节点 | 待确认（建议复制行，见 D3）。**延期条件**：会话树存储实现前确认；未确认前按复制行设计 |
| X3 | checkpoint 是否覆盖外部进程改动 | 建议不覆盖，UI 标注「外部改动未纳入」。**收口条件**：checkpoint UI 实现前确认标注文案与判定 |
| X4 | `exec` 批量写的 checkpoint 节流 | 待评估。**延期条件**：exec 接入 checkpoint 前评估节流阈值 |

## 9. 与现有 ADR 的关系

| ADR | 关系 | 说明 |
|---|---|---|
| 0018 | 补充并收窄 | 版本图继续管配置 / 工具快照；会话树是另一层；禁用 kernel rollback 做文件回退；CLI 运维 rollback 不属于文件回退，保持只改变活动快照（D5.1） |
| 0020 | 上游 | 回退由 agent 执行，TUI 只确认 / 展示 |
| 0021 | 依赖 | 使用 broker 的 generation / manifest / snapshot |
| 0015 | 继承 | 会话数据不进 kernel |

## 10. 修订记录

| 日期 | 基线 | 修订内容 | 来源 |
|---|---|---|---|
| 2026-11-21 | `a5ce76dbbe60` | 初稿（W-04b）：会话树、leaf、fork、checkpoint、前向 commit、两棵树分离 | spec §19 |
| 2026-11-27 | `6198139` | 补 D5.1：分开对话回退、TUI 文件恢复与 CLI 运维 rollback；明确 X1 只约束文件回退，CLI 保持只改变活动快照；C5 标 B2 待修；X2–X4 写延期/收口条件；X1 与 D5 原文保留，状态保持 Draft | 2026-11-27 设计-实现对账（C5；spec §18 Q4/Q5；`docs/operations/version-cli.md` 的 rollback 语义；`crates/molt-tui/src/version_workspace.rs` 静态核对） |
