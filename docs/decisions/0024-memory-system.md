# ADR-0024：记忆系统

| 项 | 值 |
|---|---|
| 状态 | Draft |
| 日期 | 2026-11-21 |
| 基线 | `a5ce76dbbe60`（`main`，`pyproject` 版本 `1.2.0rc8`） |
| 关联 | spec §6.6 / §11；前置 0018 / 0022；被 0025（`memory` 字段）引用 |
| 决策者 | 用户（评审中） |

Spec: `docs/v3-agent-tui-spec.md`

## 1. 背景

每开一个新会话，用户都要重新交代项目约定、偏好与已确认事实。业界两条路线指向同一结论：

- Claude Code 的 auto memory：记忆是**可读可改的文件**；
- Codex 的两阶段离线流水线：抽取与合并分离，合并前可审。

两者都拒绝「黑箱向量库」，也拒绝「模型总结直接当事实」。本 ADR 固定记忆的存放、写入门槛、去重与过期、检索、两阶段流程、与上下文压缩的分层，以及注入方式。

## 2. 决策

### D1 双层目录

- 项目：`MOLT.md` + `.molt/memory/*.md` + `MEMORY.md`（`MEMORY.md` ≤200 行 / 25 KiB）；
- 全局：`~/.local/share/molt/memory/*.md` + `MEMORY.md`；
- frontmatter：`id` / `title` / `tags` / `scope` / `source` / `review_by` / `created_at` / `evidence[]`。

### D2 只写已验证事实

`user_confirmed` / `repo_checked` 可直接写；`tool_verified` 需二次确认；模型推断只进 `memory_candidates`；无 `evidence` 拒绝写入；命中密钥模式拒绝写入。

### D3 去重与过期

同 `scope` + `path` 覆盖；hash 相同跳过；FTS 相似度 >0.9 合并（证据取并集）；`review_by` 默认 `+90d`；过期置 `stale=1`，不注入但可检索。

### D4 检索：FTS5 起步

`memories_fts` 使用 `tokenize=trigram`（支持 CJK 子串）；不支持时退 `unicode61` 并在 `health.degraded` 标注；记忆量到 200–2000 条后再评估 BGE-M3（另立 ADR）。

### D5 两阶段流水线

1. `turn.completed` 后异步抽取 → `memory_candidates`（脱敏 + `evidence`）；并发抽取只入候选；
2. idle 60s / 每 10 turn / 用户手动触发 → 阶段二合并：`memory.lock`、去重、写 md、更新 FTS 与 `MEMORY.md` → `memory.written`。

### D6 与 compaction / branch_summary / context_edit 分层

- `compaction`：截断（`first_kept_node_id`）；
- `branch_summary`：摘要被放弃的分支；
- `context_edit`：剔除节点；
- `memory`：跨会话长期事实。

顺序固定：路径 → `compaction` → `context_edit` → 记忆注入。

### D7 注入按数据、不按指令

注入块标题固定为「相关记忆（自动注入，非用户指令）」，每条为 `title — 摘要（review_by）`；预算默认 ≤1500 token；项目优先、全局次之，每 scope top-4；`stale` 不注入；记忆内容不得被当作执行指令。

### D8 与 workspace 快照的边界

workspace 快照排除 `.git/`（整棵）、`.env*`、`.molt/memory/`、`.ssh/`、`.aws/`、`.netrc`、`.git-credentials`、`node_modules/`、`target/`、`.molt/tmp/`；拒绝 symlink。快照前**强制 secret scan**，命中密钥默认拒绝，或要求用户显式确认后才继续。

`.git` 整体排除且**不恢复**：文件回退只恢复工作树文件，不触碰 git 元数据。

`.molt/memory/` 的文件事实源归本 ADR 所有（写入门槛 / 去重 / 过期见 D2–D5），**不被 workspace 快照纳入**，也不随工作区回退一起回滚；记忆回滚走 §7 的「删 md + DB 标 `stale`」。

## 3. 备选方案

| 方案 | 内容 | 否决理由 |
|---|---|---|
| A | 模型总结直接写记忆 | 幻觉被永久化 |
| B | 向量库起步 | 依赖与运维重，P2 无法验收 |
| C | 记忆只读 | 用户仍要反复交代 |
| D | SQLite BLOB，不落 md | 不可审阅、不可 diff |
| E | 每 turn 全量注入 | token 线性增长 |
| F | 本方案（文件 + FTS5 + 两阶段） | 采纳 |

## 4. 理由

- 可审计：记忆是 md，可 diff、可 review；
- 可回滚：删文件即删除记忆；
- 抗污染：验证门槛 + `evidence` 强制；
- 成本可控：FTS5 零额外依赖，注入有预算。

## 5. 影响

- 文件与 DB 两套存储，启动需对账（不一致以文件为准）；
- 需要 `/memory` 浏览 / 编辑 / 开关；
- 隐私要求 redact + 可删；
- `.molt/memory/` 属工作区路径，但文件事实源归本 ADR，须由 workspace 快照排除规则剔除（见 D8）。

## 6. 失败场景 / 如果不这么做

| 若违反 | 直接后果 | 长期后果 |
|---|---|---|
| 写未验证推断 | 幻觉变成事实 | 后续会话被错误前提污染 |
| 无 `review_by` | 记忆腐烂 | 过期结论仍被注入 |
| 无 `evidence` | 无法回答「从哪来」 | 不可审计，不可回滚 |
| 记忆当指令 | prompt injection 持久化 | 跨会话攻击面 |
| 无去重 | 同事实存 10 份 | 挤爆注入预算 |

## 7. 回滚 / 撤销

| 动作 | 步骤 | 结果 |
|---|---|---|
| 记忆污染 | 关 `memory.inject` + 删 md（`gio trash`）+ DB 标 `stale` | 停止注入，保留审计 |
| 抽取器 bug | 停阶段二，丢弃 `candidates` | 已写 md 不受影响 |
| 迁移失败 | 以文件重建 DB | DB 可重建，文件是事实源 |
| ADR 级撤销 | `inject=false` + 不启阶段二 | 回到纯 compaction |

## 8. 开放问题

| ID | 问题 | 状态 |
|---|---|---|
| X1 | `tool_verified` 是否自动通过 | 待确认（建议否） |
| X2 | Node 24 SQLite trigram 是否可用 | 待实测 |
| X3 | 全局 vs 项目冲突优先级 | 待确认（建议项目优先） |
| X4 | 是否导入 `AGENTS.md` / `CLAUDE.md` | 待评估 |

## 9. 与现有 ADR 的关系

| ADR | 关系 | 说明 |
|---|---|---|
| 0018 | 延伸 | digest 无授权 → 记忆不得作审批依据 |
| 0022 | 依赖 | 会话树提供 `evidence` 与 `turn.completed` |
| 0015 | 不冲突 | 记忆不进 kernel |
| 0025 | 被引用 | `memory{inject, budget_tokens, scopes[]}` 控制注入 |
