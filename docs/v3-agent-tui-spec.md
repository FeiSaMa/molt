# molt v3 · Agent + TUI 架构 Spec

> 本文件是**评审稿（Draft v1）**，不是 Accepted 架构决定。文中「必须」在用户确认 §18 的开放问题、且 ADR-0020…0025 被接受后才对实现具有约束力；冲突时以已接受 ADR 为准。

## 1. 元信息

| 项 | 值 |
|---|---|
| 标题 | molt v3 · Agent + TUI 架构 Spec |
| 版本 | Draft v1（评审稿） |
| 日期 | 2026-11-21 |
| 基线 | `a5ce76dbbe60`（`main`，`pyproject` 版本 `1.2.0rc8`） |
| 状态 | Draft，待用户确认后进入实现 |
| 修订 | 2026-11-27（基线 `6198139`）：A 批文档处置；现行/目标分离、门禁分类、0024 归档入向标记；见 §23 |
| 决策 | 2026-11-21 用户确认 0020 X1 / 0021 X1 / 0022 X1（见文末决策记录） |
| 作者 | Kaguya（编排）/ 用户（决策） |
| 相关 ADR | 新增 0020–0025；修订说明 0015 / 0018 / 0019 |
| 本轮交付 | 初稿轮（2026-11-21）：spec + TUI mock 原型 + logo；不含完整 agent/kernel 集成。现行 TUI 进度见 §5.0 / §21 |
| 文档性质 | 架构 spec；不替代 ADR，不包含完整 DDL 与 OpenAPI 正文 |

## 2. 目录

- [1. 元信息](#1-元信息)
- [3. 核心思想与目标](#3-核心思想与目标)
- [4. 约束与不变量](#4-约束与不变量)
- [5. C4 分层](#5-c4-分层)
- [6. 关键数据流](#6-关键数据流)
- [7. 数据模型](#7-数据模型)
- [8. API 契约摘要](#8-api-契约摘要)
- [9. SSE 事件](#9-sse-事件)
- [10. 权限与沙箱](#10-权限与沙箱)
- [11. 记忆系统](#11-记忆系统)
- [12. 配置树与自动路由](#12-配置树与自动路由)
- [13. TUI 设计](#13-tui-设计)
- [14. 失败模式与恢复](#14-失败模式与恢复)
- [15. 安全与威胁模型](#15-安全与威胁模型)
- [16. 测试与验收](#16-测试与验收)
- [17. 分期路线](#17-分期路线)
- [18. 开放问题 / 假设](#18-开放问题--假设)
- [19. ADR 清单](#19-adr-清单)
- [20. 与现有 ADR 的冲突表](#20-与现有-adr-的冲突表)
- [21. 文件清单](#21-文件清单)
- [22. 决策记录](#22-决策记录)
- [23. 文档修订记录](#23-文档修订记录)

## 3. 核心思想与目标

### 3.1 核心思想

v2 已经把「版本 / 事务 / 审计」做扎实：Rust kernel 只存不透明字节、单写者提交、按原 request ID 恢复、checkpoint 审计；Python 层把配置、提示词、工具做成快照。用户真正要的是「让 agent 直接干活」，因此 v3 在 kernel 之外新增两层：一层是 TypeScript agent，负责会话树、记忆、权限与工具执行，自持 SQLite；另一层是 Rust TUI，作为性能优先的终端客户端，通过 loopback HTTP/SSE 与 agent 对话。

kernel **一行不改**，继续只回答「这棵树 / 这一代 / 这条日志是不是你当初提交的那一份」。一句话概括职责边界：**kernel 管证据，agent 管行为，TUI 管交互**。这样既保留 v2 的可审计性，又避免把 LLM 与网络能力塞进 kernel（INV-1、INV-2）。

### 3.2 目标（G1–G7）

| ID | 目标 | 可判定验收 |
|---|---|---|
| G1 | 真实任务闭环：提问 → 流式回答 → 工具 → 审批 → 结果 → 落库 | E2E-1 |
| G2 | 会话树 fork；可从任意节点只回会话，或同时回会话 + 配置 + 文件 | E2E-4 / E2E-5 |
| G3 | 所有工作区写入经单一 TS broker，语义与 Python `versioning.py` 的 managed 快照路径字节级对齐 | G-01…G-09 |
| G4 | 权限默认 ask/observe，网络默认关，Linux 使用 bwrap，密钥不进日志/事件/DB | P-01…P-12 + secret scan |
| G5 | 无参 `molt` 在 stdin 与 stdout 都是 TTY 且 `MOLT_NO_TUI != "1"` 时进入 TUI；非 TTY（或 `MOLT_NO_TUI="1"`）保持 help + `exit 0`（即使非 Linux）；`molt tui` 只受 `MOLT_NO_TUI="1"` 限制、不看 TTY；只有真正启动 TUI 的路径（TTY 无参 / 显式 `molt tui`）要求 Linux x86_64，否则 `exit 2` | C-01…C-04 |
| G6 | TUI 在 80×24 可用，`TERM=dumb` 不崩，`--selftest` 离线渲染全部屏幕 | T-01…T-09 |
| G7 | 无 agent 且未 `--mock` 时不阻塞启动、不静默假装有后端 | T-09 |

### 3.3 非目标

| 非目标 | 边界说明 |
|---|---|
| 不改 kernel | RPC、状态格式、依赖、网络、LLM 均不动 |
| 不重新引入 v1 | 不恢复 v1 读取 / 迁移 / 语义审计（ADR-0019） |
| 不复用 attic / v1-final 的 agent 代码 | 只取设计，不复制实现 |
| 不做多用户 / 服务端部署 / 远程 TUI | 仅 Linux x86_64 单机单 UID |
| 不做图片协议 | 不做 sixel / kitty / iTerm2 图片协议与像素宠物 atlas；P0 只做半块字符 |
| 不做插件运行时 | 不做 150+ 键位、不做 50 工具全量注入 |
| 不用 kernel 存会话或记忆 | 会话与记忆只在 agent 侧 SQLite / markdown |
| 不自动 git push / 发布 | 发布动作保持人工 |

理由：v3 是**叠加**而非替代。范围一旦扩散到多用户、远程或 kernel 改造，就会与 v2 的「单一职责 + 可审计」前提冲突。

### 3.4 与 v2 的关系

v2 的形态是「Rust kernel + Python `VersionSandbox` + 外部 OpenCode」。v3 的形态是「同一 kernel + TS broker（新，state 根隔离）+ TS agent + Rust TUI」。`molt project/version` 继续按 ADR-0019 工作，agent 不读也不写它们的 state。两套 state 根互斥（INV-7），因此 v3 可以在同一台机器上并存，但不能共用同一个 state 目录。

## 4. 约束与不变量

### 4.1 不变量（INV-1…INV-12）

| ID | 不变量 | 理由 |
|---|---|---|
| INV-1 | kernel 只处理不透明字节；依赖集合仍为 `{libc, serde_json, sha2}` | 防止 kernel 被业务逻辑与供应链污染 |
| INV-2 | kernel 无出站依赖；agent 无 kernel 直连（唯一例外是 broker 模块）；`node:net` 仅允许出现在 `src/broker/**` | 保证写路径唯一、可审计 |
| INV-3 | 所有写 kernel 的路径先经 broker 的 journal + 单写者锁 | 崩溃后可恢复，避免双写 |
| INV-4 | `request_id` 一旦持久化不可换新 ID；`unknown` 只查 / 恢复 | 幂等与审计的基石 |
| INV-5 | kernel commit ≠ 业务成功；digest / 记忆 / 模型输出无授权效力 | 防止把「已记录」误读为「已批准」 |
| INV-6 | 密钥只从 env / key file 读，永不落 DB / 事件 / 日志 / argv | 降低泄漏面 |
| INV-7 | agent state 根与 Python `molt project` state 根互斥；重叠即拒绝启动 | 避免同一 state 被两套事务写 |
| INV-8 | 只读命令零写入（以文件系统树快照判断，不看 `git status`） | 保证「只看不改」可验证 |
| INV-9 | 仅 Linux x86_64；其它平台 `exit 2` | 沙箱与 ABI 假设明确 |
| INV-10 | 回退不改写历史：文件回退 = 导出 + 前向 commit，不调用 kernel rollback | 历史只追加，审计可重放 |
| INV-11 | 会话树与配置 DAG 不共享 ID 空间（`n_` vs `c_`） | 两棵树语义不同，避免误引用 |
| INV-12 | 任何「看不到」的状态必须 fail closed（不静默 mock / v1 / 只读） | 防止把未知状态伪装成成功 |

### 4.2 硬约束

| 约束 | 当前值 / 规则 |
|---|---|
| 工具链 | Rust stable（`rust-toolchain.toml` 已锁 rustfmt/clippy）；Node 24 + pnpm 12，不依赖 Bun |
| kernel 传输 | UDS 权限 `0600`、`SO_PEERCRED` 同 UID、JSON-RPC 2.0、4 字节大端长度、16 MiB 上限 |
| manifest | root key 单段；路径禁绝对路径、`..`、反斜杠、`\0`、空段；单段 ≤255；总长 ≤4096；`type=file`；`mode ≤ 0o777`；文件不得是另一文件的祖先前缀 |
| Python 受管预算 | ≤64 文件 / 单文件 ≤256 KiB / 总 ≤2 MiB（`version_files.py`） |
| 时间 | ISO-8601 UTC；kernel ts 必须与重试完全一致 |
| 单 turn 预算 | 默认 wall 30 min / 工具 600 s / 模型 idle 120 s（可配） |
| 密钥来源 | env 或 `apiKeyHelper` stdout；禁 CLI flag、query string、配置树 |

## 5. C4 分层

### 5.0 现行路径（2026-11-27，基线 `6198139`）

本节的目标态（5.1–5.4）尚未整体交付。现行仓库路径是：

| 环节 | 现行实现 | 状态 |
|---|---|---|
| 入口 | `molt` 无参且 stdin/stdout 均为 TTY → `execv molt-tui`；`molt tui` 为显式子命令（`molt/cli.py`） | 已交付 |
| Chat | `molt-tui` 进程内读 `DEEPSEEK_API_KEY` / `MOLT_API_KEY_HELPER`，直连 DeepSeek provider（`crates/molt-tui/src/provider.rs`） | 已交付；C1 |
| 会话存储 | 本地 JSONL `$HOME/.local/share/molt/sessions/<id>.jsonl`（`session.rs`） | 已交付；不是后端服务，也不是 SQLite |
| Versions | `--version-workspace` → 子进程 `molt version` → Python `VersionSandbox` → kernel（`version_workspace.rs`） | 已交付；spec INV-2 保持 |
| Tree / Settings / Diff / Approval | 仍读 `MockData`；Live 模式下也不代表真实状态 | C4 / B1 待修 |
| TS agent / TS broker / SQLite / 权限引擎 / 工具执行器 | 根目录无 `agent/`，无 `.ts`、无 `package.json` | 未交付 |

现行路径的预算与门禁按现行实现判断：managed 快照仍是 64 文件 / 单文件 256 KiB / 总 2 MiB；5 000 文件 / 单文件 8 MiB / 总 256 MiB 只是 v3 workspace 目标（见 §7、§16）。

### 5.1 Context（v3 目标态，未交付）

```text
用户终端
  └─ molt-tui (Rust)
        │ HTTP/SSE（loopback + Bearer）
        ▼
     agent (Node 24 / TypeScript)
        ├─ SQLite（会话 / 事件 / 记忆）
        ├─ journal + 单写者锁
        ├─ providers / MCP / workspace
        └─ broker（TS 模块）
              │ UDS JSON-RPC 2.0
              ▼
           kernel (Rust，零改动)
```

### 5.2 Container（v3 目标态，未交付）

| 容器 | 技术栈 | 职责 | 明确不做 |
|---|---|---|---|
| `molt-tui` | Rust + ratatui 0.30.x + crossterm 0.29 | 渲染、键位、命令面板、审批 modal、diff、消费 SSE | 不连 kernel/broker、不写 DB、不持密钥（目标态；现行见 §5.0 / C1） |
| agent API | Node 24 + `node:http` | 路由、Bearer、SSE hub、幂等、错误映射 | 不执行工具、不直接写 kernel |
| agent runtime | TypeScript | turn 循环、上下文、模型适配、工具调度、取消、预算 | 不持有 UI 状态 |
| session store | SQLite WAL | 会话树、节点、事件、leaf、fork、迁移 | 不存密钥、不存 kernel 状态 |
| memory store | SQLite FTS5 + markdown | 抽取、去重、索引、检索、注入 | 不替代 compaction |
| permission engine | TypeScript | 规则、审批队列、saved rules、工具分级 | 不绕过 broker 写文件 |
| tool executor | TypeScript | 注册表、sandbox、`apply_patch`、输出截断、脱敏 | 不直接拼 shell 绕过 bwrap |
| broker | TypeScript 模块 | kernel 客户端、journal、index、单写者、恢复 | 不处理会话/记忆/权限 |
| kernel | Rust | 不透明字节、提交、恢复、审计 | 不变 |

### 5.3 Component（v3 目标态，未交付）

| 容器 | 关键文件 / 模块 |
|---|---|
| TUI | `app.rs`（状态机）、`api/{client,sse}.rs`、`ui/{header,sidebar,chat,tree,settings,diff,approval,footer,composer,palette}.rs`、theme/glyphs/logo、selftest/mock |
| agent HTTP | `routes.ts`、`auth.ts`、`sse.ts`、`errors.ts`、`idempotency.ts` |
| agent runtime | `loop.ts`、`context.ts`、`model.ts` |
| session store | `migrations/`、`sessions.ts`、`events.ts` |
| memory | `extract.ts`、`merge.ts`、`index.ts`、`search.ts` |
| permissions | `rules.ts`、`engine.ts`、`approvals.ts` |
| tools | `registry.ts`、`exec.ts`、`apply_patch.ts`、`fs.ts`、`mcp.ts`、`sandbox/bwrap.ts` |
| broker | `broker/{kernel,index,journal,publish,recover,guard}.ts` |

### 5.4 依赖方向与禁止（v3 目标态，未交付）

允许的方向是：`molt`（Python CLI）`--exec-->` `molt-tui` `--HTTP/SSE-->` agent；agent 内部再指向 runtime、store、permissions、tools、memory、config；agent 的 broker 模块 `--UDS-->` kernel。依赖必须单向，否则单写者与审计边界会被绕过。

| 禁止方向 | 原因 |
|---|---|
| TUI → kernel / broker | TUI 只是客户端，不能绕过 agent 的权限与幂等 |
| agent 非 broker 模块 → kernel socket | 保证 INV-2 与 INV-3 |
| broker → 会话 / 记忆 / 权限 | broker 只做提交与恢复，避免职责耦合 |
| kernel → 网络 / LLM | kernel 必须保持无出站依赖 |
| 任何层直接改 `state/` | 所有写入必须经 broker 的 journal + 锁 |

### 5.5 C1 凭据边界（现状与目标）

**现状（2026-11-27，基线 `6198139`）**：TUI 进程内读 `DEEPSEEK_API_KEY` / `MOLT_API_KEY_HELPER`，直接向 provider 发 `Authorization: Bearer`（`crates/molt-tui/src/provider.rs`）。provider key 存在于 TUI 进程内存，相对目标态扩大了凭据暴露面：进程转储、同 UID 进程读取、日志 / 错误串误带、崩溃转储都可能触及密钥。

**保留的硬约束**：

- **spec INV-6**：密钥只从 env / `MOLT_API_KEY_HELPER` 读，不进入 argv / 日志 / 错误串；`ProviderConfig` 的 `Debug` 必须打码；测试与错误路径不得打印密钥。
- **spec INV-2**：TUI 不直接接触 kernel socket。现行 Versions 路径经 CLI 子进程（`version_workspace.rs:134-146`），`crates/molt-tui/src` 无 `KernelClient` / `kernel.sock` 引用。

**目标（agent 模式，未交付）**：TUI 不持 provider key；provider 调用在 agent 侧、沙箱外，工具在 bwrap 内。目标态落地前，部署方应按「TUI 进程持有 provider key」评估信任边界，不得按目标态描述假定密钥只在 agent 侧。

### 5.6 待修项（C4 / C5，分别转 B1 / B2）

| 项 | 现状 | 待修与验收 |
|---|---|---|
| C4 | Live 模式下 Tree / Settings / Diff / Approval 仍读 `MockData`（`app.rs`、`tree.rs`、`settings.rs`、`approval.rs`），没有 fail closed | **B1 待修**：Live 下无真实数据源的四个面板不得展示为真实状态或接受动作；显式 Mock 模式保留演示。真实 Live 可用范围限定为已连接且有真实后端支持的 Chat、Versions 路径；会话存储是本地 JSONL，不属于后端。首轮自然态 PTY 验收后逐项验证 |
| C5 | B2 封堵已执行（2026-11-28）：TUI `b` 不再调用 `molt version rollback`，文件恢复动作明确不可用；前向恢复未实现，C5 仍待修 | **B2 剩余**：TUI 文件恢复改为导出目标状态 + 前向提交新 generation，不调用 kernel `rollback`；验收仍需覆盖操作前后内容、generation、历史与无 kernel rollback 调用，并运行既有 CLI rollback 回归，确认其仍只改变活动快照 |

C4 / C5 在 A 批只记录状态，不在本轮修复；不得把「四面板 mock」或「Versions 回退」描述为已符合目标契约。

## 6. 关键数据流

### 6.1 启动

1. 入口判定（`_tui_wanted`）：
   - 无参 `molt`：`_tui_wanted` = stdin 与 stdout 都是 TTY 且 `MOLT_NO_TUI != "1"`；为真 → `execv` `molt-tui`（`MOLT_TUI_BIN` 覆盖 TUI 路径），否则打印 help 并 `exit 0`（非 TTY 时即使非 Linux 也不报平台错误）。
   - `molt tui`：显式子命令，只受 `MOLT_NO_TUI="1"` 限制（不看 TTY，便于 CI / `--selftest`）；被禁用 → stderr + `exit 2`。
   - 平台门禁：只作用于真正要启动 TUI 的路径（TTY 无参 / 显式 `molt tui`）；非 Linux x86_64 → `exit 2`（INV-9）。
2. TUI 读 `$XDG_RUNTIME_DIR/molt/agent-<uid>.json` 获取 agent 地址与 token。
3. `GET /v1/health` 探活。
4. `GET /v1/sessions` 与当前会话的 `tree`。
5. `GET /v1/events?since=...` 取最近 200 条事件。
6. 渲染 Startup / Chat。
7. 失败时显示 Startup 错误卡，**不自动 mock**（INV-12）。

### 6.2 一轮对话

1. `POST /v1/sessions/{id}/messages`。
2. `turn.started`。
3. provider 流式返回 → `message.delta`。
4. 模型请求工具 → `tool.call`。
5. 权限判定为 ask → `tool.approval_required` → TUI 审批 modal。
6. 用户批准后执行 → `tool.result`。
7. `message.completed` → `turn.completed`。
8. 更新 `leaf_node_id` 并持久化事件。

### 6.3 fork

1. Tree 屏选中节点 N。
2. `POST /v1/sessions/{id}/fork`。
3. 新建 session，写入 `fork_from_session_id` 与 `fork_boundary_node_id`。
4. 复制 N 及其祖先（新 ID，保留 `origin_node_id`）。
5. 发 `fork.created`。
6. TUI 切到新会话。

### 6.4 rollback

1. 选中节点 N → 三复选（默认 session + config；禁止三目标全 false）。
2. **只有勾选 files 时**才二次确认：勾选 files 后进入确认子状态，第一次 Enter 只展示文件数、目标 checkpoint 与备份路径；第二次 Enter / `y` 才提交，`Esc` 回退到复选。仅回退 session / config 时第一次 Enter 即应用。
3. `POST /v1/sessions/{id}/rollback`；空回退返回 400 `invalid_request`。
4. 会话侧改 `leaf` 并写 `context_edit`；配置侧生成新 config 节点。
5. 文件侧找最近 checkpoint → 备份 → `broker.materialize` → 前向 commit（只恢复工作树文件，`.git/` 不恢复）。
6. 发 `rollback.applied`。
7. 无 checkpoint → `409 cannot_restore_files`。

### 6.5 配置应用

1. Settings 选中配置 C → `GET /v1/configs/{id}/diff`。
2. `POST /v1/configs/{id}/apply`。
3. 校验 `scenario_version`。
4. 生成新 config 节点 / 指针，并写 `config_change` 节点。
5. 发 `config.applied`。
6. 跨版本 → `409 scenario_mismatch`。

### 6.6 记忆写入

1. `turn.completed` 触发阶段一抽取（脱敏 + 证据）。
2. 写入 `memory_candidates`。
3. idle 60 s / 每 10 turn / 用户手动触发阶段二合并。
4. 加锁、去重、写 markdown、更新 FTS、刷新 `MEMORY.md`。
5. 发 `memory.written`。

### 6.7 SSE 断线 / 410

1. 客户端记录 `last_seq`。
2. 重连 `GET /v1/events?since=...`（或 `Last-Event-ID`）。
3. 正常：`200`，补发缺失事件 + `done`，转 live。
4. `since < min_retained`：`410 gone` + `min_seq`。
5. 收到 410 后全量重同步 `tree` + 最近 200 条事件。
6. 心跳 15 s；45 s 无帧则重连。

## 7. 数据模型

完整 DDL 将放在 `contracts/schema.sql`（W-06），正文只固定表清单、关键字段与不变量。所有时间戳为 ISO-8601 UTC；可能含敏感值的 JSON 字段在写入前按脱敏规则处理。

| 表 | 用途 | 关键字段 |
|---|---|---|
| `workspaces` | 工作区与 state 根 | `id`、`root`、`state_root`、`kernel_state_id`、`created_at`、`archived` |
| `sessions` | 会话元信息 | `id`、`workspace_id`、`title`、`root_node_id`、`leaf_node_id`、`active_config_id`、`fork_from_session_id`、`fork_boundary_node_id`、`generation`、`manifest_hash`、`workspace_snapshot_id`、`archived`、`meta_json` |
| `nodes` | 会话树节点 | `id`、`session_id`、`parent_id`、`origin_node_id`、`kind`、`seq`、`created_at`、`message_id`、`payload_json` |
| `messages` | 消息正文 | `id`、`node_id`、`role`、`content_json`、`model_id`、`provider` |
| `configs` | 配置 DAG 节点 | `id`、`parent_ids_json`、`label`、`body_json`、`genome_json`、`scenario_version` |
| `config_edges` | 配置 DAG 边 | `from_id`、`to_id`、`kind` |
| `events` | SSE 事件日志 | `seq`、`session_id`、`turn_id`、`type`、`payload_json`、`created_at`、`redacted` |
| `approvals` | 审批记录 | `id`、`session_id`、`turn_id`、`tool_call_id`、`action`、`resource`、`decision`（nullable；终态枚举 `once` / `always` / `reject` / `timeout` / `cancelled`）、`rule_id`、`requested_at`、`decided_at`、`decided_by` |
| `mcp_servers` | MCP 注册表 | `id`、`scope`、`name`、`transport`、`command`、`args_json`、`url`、`env_keys_json`、`enabled`、`status`、`last_ok_at`、`last_error` |
| `tool_calls` | 工具调用审计 | `id`、`session_id`、`node_id`、`turn_id`、`name`、`tier`、`input_json`、`status`、`exit_code`、`output_ref`、`output_bytes`、`truncated`、`approval_id`、`sandbox_mode`、`started_at`、`ended_at` |
| `usage` | 用量与路由 | `id`、`session_id`、`turn_id`、`provider`、`model_id`、`input_tokens`、`output_tokens`、`cached_tokens`、`cost_micros`、`wall_ms`、`route_decision`、`created_at` |
| `memories` | 记忆正文与证据 | `id`、`scope`、`workspace_id`、`path`、`title`、`body`、`tags_json`、`source_session_id`、`source_node_id`、`source_kind`、`evidence_json`、`verified`、`stale`、`created_at`、`review_by`、`hash` |
| `memories_fts` | FTS5 索引 | `title`、`body`、`tags`；trigram |
| 辅助表 | 幂等、候选、规则、迁移 | `requests`、`memory_candidates`、`saved_rules`、`schema_migrations` |

树查询依赖 SQLite 递归 CTE：叶→根用 `ORDER BY depth DESC`，子树查询带 `depth` 限制，兄弟数用 `GROUP BY parent_id`。`leaf_node_id` 是唯一活动叶；`root_node_id` 是起点；fork boundary 是被复制的最深节点；`fork_from_session_id` 保持可追溯。checkpoint 的 `payload_json` 固定包含 `{generation, manifest_hash, workspace_snapshot_id, archive_id?}`。

树与索引的不变量（DDL 细节由 W-06 定稿）：`nodes` 每个 session 只有一个根（`root_node_id`），`parent_id`、`leaf_node_id`、`fork_boundary_node_id` 必须与节点同 session；`(session_id, seq)` 唯一，禁止自引用与环（写入前用递归 CTE 校验）。`config_edges` 是配置 DAG 的唯一事实源，`configs.parent_ids_json` 只是只读投影，必须与 edges 一致。`memories_fts` 使用 FTS5 external content（`content='memories'`、`content_rowid='rowid'`），由 trigger 维护 insert / update / delete。`sessions.generation` 表示「该 session 最近 checkpoint 的 generation」；workspace 全局 current 只存在于 kernel（`current.generation`），F6 比较的是 workspace current，而不是 `sessions` 行。

kernel 映射关系如下：`state_root` 对应 `VERSION` / `STATE_ID` / `kernel.sock`；`kernel_state_id` 对应 handshake `state_id`；`generation` 对应 `current.generation`；`manifest_hash` 对应 `current.manifest_hash`；`workspace_snapshot_id` 对应 `index.json` 的 `snapshots[id]`。`broker.json` 固定为 `{contract:"molt-agent-broker/1", state_id, current, pending?}`。

state 根隔离采取**拒绝启动**而非自动修复：遇到 Python 合同 `broker.json`（`molt-synthetic-versions/1`）、`project-session.json` 或未知合同，一律 `exit 2`（INV-7）。

`SERVER_LOCK` 是 `molt-kernel serve` 的正常服务锁，**不是**冲突、也不得据此进入只读。broker 启动时先连接既有 serve（UDS + handshake 校验 `state_id`）；若 socket 不存在，agent 可启动 `molt-kernel serve` 子进程并等待 handshake 完成；超时或身份不符按 F7 / F12 报错。真正的写互斥是 `broker.lock` 与 kernel 侧 `LOCK`。

快照分两类。managed 快照的 root 为 `synthetic-versions`，遵守 ≤64 文件 / 256 KiB / 2 MiB 的预算，并产出 golden 对象；workspace 快照的 root 为 workspace，预算**已确认（2026-11-21，Q3）**：≤5 000 文件 / 单文件 ≤8 MiB / 总 ≤256 MiB。workspace 快照排除 `.git/`（整棵）、`.env*`、`.molt/memory/`、`.ssh/`、`.aws/`、`.netrc`、`.git-credentials`、`node_modules/`、`target/`、`.molt/tmp/`；拒绝 symlink。快照前强制 secret scan，命中密钥默认拒绝或要求显式确认。`.git` 整体排除且**不恢复**：文件回退只恢复工作树文件，不碰 git 元数据。

## 8. API 契约摘要

通用规则：

- 仅监听 `127.0.0.1` 动态端口；Bearer token 为 32 B base64url，放在 `0600` 的 runtime 文件中；禁止 query token。
- `POST` / `PATCH` 必须带 `request_id`（ULID）；`Idempotency-Key` 作为别名。同 ID 同 payload 返回原响应并标 `Idempotent-Replay`；同 ID 异 payload 返回 `409 request_conflict`。
- 错误体统一为 `{error:{code,message,details?,request_id?}}`。
- 错误码集合：`400`、`401`、`403`、`404`、`409`、`410`、`413`、`429`、`500`、`503`。
- 非流式请求超时 30 s；SSE 心跳 15 s；路径统一 `/v1` 前缀，不兼容变更走 `/v2`。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v1/health` | 探活 |
| GET / POST | `/v1/sessions` | 列出 / 新建会话 |
| GET / PATCH | `/v1/sessions/{id}` | 读取 / 更新会话元信息 |
| GET | `/v1/sessions/{id}/tree` | 读取会话树 |
| GET | `/v1/sessions/{id}/nodes/{nid}` | 读取节点详情 |
| POST | `/v1/sessions/{id}/fork` | 从节点 fork |
| POST | `/v1/sessions/{id}/leaf` | 切换活动叶 |
| POST | `/v1/sessions/{id}/rollback` | 会话 / 配置 / 文件回退 |
| POST | `/v1/sessions/{id}/messages` | 发起一轮对话 |
| POST | `/v1/turns/{tid}/cancel` | 取消 turn |
| GET | `/v1/events` | SSE / 补历史 |
| GET | `/v1/turns/{tid}` | 查询 turn 状态 |
| GET | `/v1/approvals` | 待审批列表 |
| POST | `/v1/approvals/{id}` | 批准 / 拒绝 |
| GET / POST | `/v1/configs` | 列出 / 新建配置 |
| GET | `/v1/configs/{id}` | 读取配置 |
| GET | `/v1/configs/{id}/diff` | 配置 diff |
| POST | `/v1/configs/{id}/apply` | 应用配置 |
| GET | `/v1/workspaces/{id}/versions` | workspace 快照列表 |
| POST | `/v1/workspaces/{id}/snapshots` | 创建快照 |
| POST | `/v1/workspaces/{id}/recover` | 恢复 / 重放 |
| GET | `/v1/memories` | 列出记忆 |
| GET | `/v1/memories/search` | 检索记忆 |
| POST | `/v1/memories/consolidate` | 触发阶段二合并 |
| GET | `/v1/mcp/servers` | MCP 列表 |
| PUT | `/v1/mcp/servers/{id}` | 新增 / 更新 MCP |
| POST | `/v1/mcp/servers/{id}/reconnect` | 重连 MCP |
| GET | `/v1/tools` | 工具清单 |
| GET | `/v1/permissions/rules` | 当前生效规则 |

审批写回：`POST /v1/approvals/{id}` 的请求 `decision ∈ {once, always, reject}`；`approvals.decision` 在 pending 时为 `NULL`（nullable），终态枚举 `once` / `always` / `reject` / `timeout` / `cancelled`。其它值返回 `400 invalid_request`。

## 9. SSE 事件

14 类事件中，标「持久化」的进入 `events` 表，可被 `since` 补发；流式增量不持久化，断线后以已落库的 message 重建。事件字段如下：

| 事件 | 关键字段 | 持久化 |
|---|---|---|
| `turn.started` | `turn_id`、`node_id`、`config_id` | 是 |
| `reasoning.delta` | `turn_id`、`index`、`text` | 否 |
| `message.delta` | `turn_id`、`message_id`、`index`、`text` | 否 |
| `message.completed` | `message_id`、`node_id`、`usage_id` | 是 |
| `tool.call` | `tool_call_id`、`name`、`tier`、`input_digest` | 是 |
| `tool.approval_required` | `approval_id`、`action`、`resource`、`preview`、`expires_at?` | 是 |
| `tool.result` | `tool_call_id`、`status`、`exit_code`、`output_preview`、`output_ref` | 是 |
| `turn.completed` | `turn_id`、`usage`、`node_id` | 是 |
| `turn.failed` | `turn_id`、`code`、`message` | 是 |
| `turn.cancelled` | `turn_id` | 是 |
| `fork.created` | `session_id`、`from_session_id`、`boundary_node_id` | 是 |
| `rollback.applied` | `targets`、`snapshot_id`、`generation`、`files_restored` | 是 |
| `config.applied` | `session_id`、`config_id`、`from_config_id` | 是 |
| `memory.written` | `scope`、`path`、`id`、`action` | 是 |

控制帧有三类：`ping`（15 s）、`done`（批次结束）、`reset.required{min_seq}`。帧格式为 `id` / `event` / `data` / `retry:3000`。`seq` 全局单调；`since` 按会话过滤，但序号不重置，因此客户端不能把「会话内连续」误当成「全局连续」。

`Last-Event-ID` 与 `since` 等价：优先用 header，缺失时用 query。若请求的 `since` 小于 `min_retained`，服务端返回 `410 gone` 并给出 `min_seq`；客户端必须丢弃增量假设，改走全量重同步（F3）。

## 10. 权限与沙箱

规则模型为 `{id, action, resource, effect, scope, order}`。`action` 取值 `fs.read|fs.write|proc.exec|net.fetch|mcp.call|agent.spawn|memory.write|config.apply|workspace.rollback`；`resource` 形如 `workspace:<rel>`、`fs:<abs>`、`cmd:<...>`、`net:<host>`、`mcp:<server>/<tool>`；`effect` 为 `allow|ask|deny`；`scope` 为 `global|project|session`；`order` 决定同层规则顺序。

判定采用 **last-match**：对每个资源取最后命中规则；多资源再按 `deny > ask > allow` 聚合，任一资源为 deny 则整体拒绝。理由是这样既能表达「默认拒绝 + 局部放行」，又能避免首匹配模式下宽规则遮蔽窄规则。`deny` 不可被 saved / always 覆盖；`always` 只把 `ask` 提升为 `allow`，不能把 `deny` 变成允许。

审批决定有三种：`once`（仅本次）、`always`（写入 `saved_rules`，scope 默认 session，可在编辑模式确认后修改）、`reject`（记 `denied`，turn 继续而不是整体失败）。审批超时 24 h 记为 `timeout` 且不执行；重连后丢失 pending 的审批转为 `cancelled`（F11）。

| 工具级别 | 工具 | 默认策略 |
|---|---|---|
| read | `read`、`grep`、`glob`、`list_dir` | allow，限 workspace |
| write | `apply_patch`（唯一写路径） | ask |
| exec | bwrap 内命令 | ask |
| network | `fetch` | deny |
| mcp | `mcp__*` | ask |
| subagent | 子 agent | ask |

bwrap 的基线命令（可执行形式，显式 src/dest；P0 只改文档，P1 必须补 bwrap 参数解析与真实执行测试）：

```bash
bwrap --die-with-parent --new-session \
  --unshare-pid --unshare-ipc --unshare-uts --unshare-cgroup \
  --unshare-net \
  --ro-bind /usr /usr --ro-bind /bin /bin --ro-bind /lib /lib \
  --ro-bind /lib64 /lib64 --ro-bind /etc /etc \
  --dev /dev --proc /proc --tmpfs /tmp \
  --bind "$WORKSPACE" /workspace \
  --ro-bind-try "$WORKSPACE/.git" /workspace/.git \
  --ro-bind-try "$WORKSPACE/.molt" /workspace/.molt \
  --chdir /workspace \
  --clearenv --setenv PATH /usr/bin:/bin --setenv LANG C.UTF-8 \
  -- <cmd>
```

`--unshare-net` 默认无条件加入；网络 allowlist 模式另立方案（deny 优先、禁回环与私网），不在基线上放宽。`--clearenv` 后用 `--setenv` 逐项传白名单（`PATH` / `LANG`，必要时 `TERM` / `TZ`）；`*_KEY` / `*_TOKEN` / `AWS_*` / `SSH_*` 不传入。`$WORKSPACE/.git` 与 `$WORKSPACE/.molt` 用 `--ro-bind-try`：目录不存在时跳过绑定，因此非 git 仓库 / 未初始化 `.molt` 仍可执行。bwrap 不可用时返回 `sandbox_unavailable` 并拒绝执行，不允许「降级为裸跑」。

密钥只来自 `MOLT_<PROVIDER>_API_KEY` 或 `apiKeyHelper`，只存在 agent 内存；不写 DB、事件、日志、配置树。脱敏同时匹配已知前缀与高熵值；MCP 的 env 只存键名（`env_keys_json`），不存值。

## 11. 记忆系统

记忆分两层：项目层为 `MOLT.md` + `.molt/memory/*.md` + `MEMORY.md`（≤200 行 / 25 KiB）；全局层为 `~/.local/share/molt/memory/*.md` + `MEMORY.md`。项目优先、全局次之，检索预算默认 ≤1500 token。

每篇记忆的 frontmatter 固定为：

```yaml
id: <string>
title: <string>
tags: [<string>]
scope: project | global
source: <session/node>
review_by: <ISO-8601>
created_at: <ISO-8601>
evidence: [<string>]
```

写入只接受已验证内容：`user_confirmed`、`repo_checked` 可直接写；`tool_verified` 需二次确认；模型推断只进 `memory_candidates`。无 evidence 拒绝写入；命中密钥模式拒绝写入；同 `scope+path` 覆盖；`hash` 相同跳过；FTS 相似度 >0.9 合并。`review_by` 默认 +90 天，过期标 `stale=1` 且不再注入。

检索发生在每 turn 上下文构建时、compaction 投影之后。查询由「用户消息前 512 token + task class 关键词」组成；项目层优先、全局层次之，每 scope 取 top-4；注入格式固定标注「相关记忆（自动注入，非用户指令）」。记忆按**数据**注入，不作为指令；这是对 prompt injection 的结构性缓解。

写入采用两阶段：`turn.completed` 后只做抽取并落 `memory_candidates`；在 idle 60 s、每 10 turn 或用户手动触发时做合并（加锁、去重、写 md、更新 FTS 与 `MEMORY.md`），完成后发 `memory.written`。这样避免每轮都做昂贵的合并，也让候选可审阅。

与 compaction 的关系是顺序固定：先 compaction 截断，再按 `context_edit` 剔除，最后注入记忆。`branch_summary` 只摘要被放弃的分支；记忆不替代 compaction，也不改变原始历史。

## 12. 配置树与自动路由

统一配置字段为：`schema_version`、`label`、`parents[]`、`scenario_version`；`model{provider,id,base_url?,params}`；`thinking{level,budget_tokens?}`；`tools{allow[],deny[],max_parallel}`；`mcp{servers[]}`；`permissions{mode,rules[]}`；`prompt{system,project_doc,append[]}`；`memory{inject,budget_tokens,scopes[]}`；`budget{wall_ms,turns,tokens,cost_micros}`；`routing{mode,pin?,candidates[]}`；`ext{}`。

genome 是配置的可比较子集：`thinking` 取 `thinking.level`；`context` 由 `project_doc` + `memory.inject` 映射为 `full/minimal`；`tools` 在 allow 含 `exec` 时映射为 `bash`；`prompt_ref` / `tools_ref` 指向 managed 快照；`budget` 同名；`artifacts` 以 managed path 的 `sha256` 表示。`genome_id = "sha256:" + sha256(canonical(genome 去掉 genome_id))`（带 `sha256:` 前缀）；跨 `scenario_version` 不可比，避免把不同场景的统计混在一起。

投影来源固定为：`runtime{runtime_id,version}` 取 `model.provider` 与 provider adapter 版本（`runtime_id` = provider 名，`version` = adapter / Node 运行时版本）；`scenario_version` 取配置顶层字段；`parent_id` 取 `config_edges` 中 `kind=derive` 的主父（无父为 `null`）。**不沿用 v1 父 genome**：v1 genome 的 `parent_id` 只作为 golden 回放时的显式注入值，运行时不从 v1 state 推导。golden 要求：同一配置经 Python `compute_genome_id` 与 TS 投影计算，`genome_id` 必须字节一致（含 `sha256:` 前缀）。

配置 DAG 的节点 ID 为 `id = 'c_' + sha256(canonical(body))[:16]`；`parent_ids` 允许 0/1/2 个；操作包括 `derive`、`merge`、`apply`、`rollback`、`diff`。merge 冲突返回 `409 merge_conflict`；apply 要求 `scenario_version` 一致，否则 `409 scenario_mismatch`。

自动路由有 `off` / `suggest`（默认）/ `auto` 三档。特征为 `task_class_hash`、`tokens_est`、`needs_tools`；先按规则过滤出 top-k ≤4 个候选，再按历史成功率、墙钟、成本与 pin 打分。`auto` 的门槛是 N=5（**待确认**，Q7）连续成功、无 deny/ask 回滚、未超预算；连续 2 次失败或用户改 pin 则回落到 `suggest`。统计只在同一 `scenario_version` 内进行。

## 13. TUI 设计

布局为：Header 1 行（显示 `v3 · mock · model`）；Sidebar 20–24 列（宽度 ≥72 显示；≥120 时 28–34 列；<72 隐藏；含会话树、MCP、todos）；Main 4 个 tab（Chat / Tree / Settings / Diff）；Footer 1 行（statusline、ctx / cost / gen、快捷键、瞬时错误）；Composer 1–8 行（多行输入、历史、粘贴 >800 字符折叠）。

| 屏幕 | 内容 |
|---|---|
| Startup | logo、最近会话、后端状态卡 |
| Chat | 虚拟滚动、tool 卡片、thinking 折叠 |
| Tree | 全屏树、分支、节点详情 |
| Settings | 配置 DAG、字段编辑、diff |
| Diff | 统一 / 并排、hunk、apply / restore |
| Approval | modal：action / resource / 预览 / 风险，`y` / `A` / `x` / `Esc` |

键位按动作分组共 26 组；内容包只给出「≤30」上限、未定义计数口径（按绑定组还是物理键），若按物理键计将超过 30，需在评审中确认并可能裁剪（**待确认**）。P0 只交付：`q` 退出、`Tab` 切屏、`?` 帮助、`a` apply、方向键移动、`PgUp/PgDn` 翻页，以及各屏局部键（Tree：`f` fork、`r` rollback、`Space` 标记；Diff：`n/N` hunk、`/` 搜索；Approval：`y/A/x`、`Esc` 关闭；Composer：`Enter` 发送、`Alt+Enter`/`Ctrl+J` 换行）。`Ctrl+C`：streaming 时取消当前 turn，非 streaming 时二次确认退出。`t` 启动 1.5 s mock stream，不做 mock/real 切换。P1：`Ctrl+P` 命令面板、`Ctrl+Q` 退出、`F2` sidebar、`F3` 主题、`Alt+1..4` 切屏、`Ctrl+T` 工具详情、`Ctrl+E` 全展开、`R` 重试、`Ctrl+Home/End` 顶底、`↑↓` 历史、`j/k/h/l` 树导航。其余动作进 `Ctrl+P`，避免继续膨胀键位表。

`/tree` 支持移动、选中、fork（标题可空）、rollback（三复选；**只有勾选 files 时才需二次确认**：勾选 files 后第一次 Enter 只进入确认子状态，展示文件数、目标 checkpoint 与备份路径，第二次 Enter / `y` 才应用，`Esc` 回退到复选；仅回退 session / config 时第一次 Enter 即应用，符合「一键回退会话 / 配置」；禁止三目标全 false，空回退返回 400 `invalid_request`）。边界节点有提示；无 checkpoint 时文件回退项置灰。审批 modal 打开期间禁止发新消息；`always` 会显示将要写入的规则并允许编辑；拒绝后 turn 继续。Diff 默认统一视图，宽度 ≥120 时支持并排；用 `diffy` 做 hunk，P0 只做简单关键字着色，不引 `syntect`；`n/N` 跳 hunk，`/` 搜索，可 apply / restore；restore 文案固定为「checkpoint（mock）」，不写 git HEAD；超过 2 MiB 或 10000 行只显示摘要并交给外部 `$PAGER`。

P0 的 `--mock` 是简化线性节点（无真实 fork / 回退）；所有 mock 状态文案统一带 `(mock)`；P1 才用 `parent_id` / `origin_node_id` / `n_` / `c_` 渲染真实会话树与配置 DAG。mock 结果不得当作真实回退或真实 tree。

logo / mascot 按 truecolor 半块 → 256 色半块 → 16 色半块单/双色（非 ASCII）→ 纯文本逐级降级；宽度 <80 时只画单行字标，不画 mascot。`TERM=dumb` 时打印提示并退出、不启 alt-screen；`NO_COLOR` 保留 TUI 但去色；非 TTY 不进入 TUI；modal 与流式输出期间冻结动画；tmux / Zellij 下使用静态 mascot；鼠标为 P1；探测失败不阻塞启动。帧文本用 `include_str!` 自绘，不引用 GPL 素材。

主题 token 包括 `bg`、`bg.panel`、`fg`、`fg.muted`、`accent`、`success`、`warn`、`danger`、`info`、`border`、`border.focus`、`diff.add`、`diff.del`、`diff.hunk`、`logo.a`、`logo.b`、`mascot.body`、`mascot.eye`；内置 `molt-night`、`molt-paper`、`molt-mono` 三套；用户主题在 `~/.config/molt/theme.toml`，解析失败回落到内置主题并提示；`--theme` 启动参数为 P1，P0 主题只走配置文件 / 默认主题。CJK 宽度用 `unicode-width`；ambiguous 默认窄、可配 wide；截断按显示宽度；关键布局不用 emoji。鼠标 P1：仅在 alt-screen 启用 SGR，支持滚轮与点击 tab，文本选择交还终端。可测试性要求：`--selftest` 用固定 fixture 渲染全部屏幕并 `exit 0`；P0 实现 `--width/--height`，仅与 `--selftest` 组合，默认 80×24 与 120×40 两档，非法值报错（`exit 2`）；`TestBackend` 缓冲快照写入 `tests/snapshots/*.txt`；`--mock` 不发网络；渲染不读时钟 / 环境，时间由 `State.now` 注入。

## 14. 失败模式与恢复

| ID | 失败 | 行为 |
|---|---|---|
| F1 | agent 崩溃 | 健康轮询 / SSE 断 → 指数退避重连 ≤30 s；仍失败提示重启；Header 显示 backend down，Composer 禁用 |
| F2 | SSE 断线 | 用 `Last-Event-ID` 重连补发；Footer 显示重连中；恢复不回滚视图 |
| F3 | 410 | 全量重同步 tree + 最近 200 条；提示但不清空会话 |
| F4 | SQLite busy / 约束 | 重试 3 次；仍失败拒绝该 turn 且不改 leaf；错误卡 `db_busy` |
| F5 | kernel unknown | 保留 pending；`POST recover` 原 ID 查 `request.status`；committed 结算；not_found 且基线一致则原参重放；界面显示「正在确认提交结果」 |
| F6 | stale generation | `409 stale_generation`；以 kernel workspace `current.generation` 为准重读基线（`sessions.generation` 仅是该 session 最近 checkpoint）；候选保留在 `candidates/`；提示「基线已变，已保留候选」 |
| F7 | 写冲突 / 后端不可用 | `broker.lock` / kernel `LOCK` 失败 → `writer_conflict`；合同或身份不符 → `exit 2`；服务暂不可用（socket 缺失且 spawn 失败 / handshake 超时）→ 重试并报错，**不得静默只读**。`SERVER_LOCK` 由 serve 正常持有，不算冲突 |
| F8 | 磁盘满 | ENOSPC 中止事务且不改 `current`；提示 GC dry-run |
| F9 | provider 超时 / 429 | 退避 ≤3 次 + jitter；仍失败 `turn.failed`，保留已产出文本，可重试 |
| F10 | MCP 崩溃 | stdio EOF / 5xx → `status=error`；工具返回 `mcp_unavailable`；可 reconnect；Sidebar 红点 |
| F11 | 审批丢失 | 重连后 approvals 无 pending → `tool_call` 置 `cancelled`；模型收到 `interrupted` |
| F12 | state 身份不符 | handshake `state_id` ≠ DB → 拒绝写，要求人工确认；阻塞式警告禁止继续 |

## 15. 安全与威胁模型

| 威胁 | 缓解 | 残余风险 |
|---|---|---|
| Prompt injection（MCP / 文件 / 粘贴） | 数据注入 + 免责声明 + 权限判定 + 高风险操作 ask | 模型仍可能被诱导选择危险但已允许的操作 |
| 工具越权 | 资源规范化 + workspace 前缀 + bwrap + 默认 ask | 规则配置过宽时仍可能放行 |
| 路径穿越 | 规范化并拒绝 `..` | 平台路径语义差异（本版本仅 Linux） |
| 符号链接 | `lstat` 拒绝 symlink；manifest `type=file` | 挂载点 / bind mount 边界需部署侧保证 |
| 密钥泄漏 | redact + 值匹配 + 测试扫描 + 不注入沙箱 env | 高熵非密钥文本可能被误判或漏判 |
| 恶意 hook / skill | P2 才引入，只在 bwrap 子进程，无进程内扩展 | 子进程仍可读 workspace 允许范围 |
| 供应链 | 依赖审查（MIT / Apache-2.0）+ 锁版本 | 上游漏洞需持续跟踪 |
| 本地同 UID 攻击 | Bearer token `0600` + loopback + kernel `SO_PEERCRED` | 同 UID 进程可读同一用户文件 |
| 会话 / 记忆伪造 | 无签名，仅保证本地完整性 | 不在本版威胁模型内 |
| 审计误读 | 文档与 UI 明确 commit ≠ 业务成功；digest / 记忆无授权效力 | 使用者仍需理解语义 |

## 16. 测试与验收

门禁分两类：**现行可执行**（本仓库当前可运行）与**未来门禁**（agent / golden 落地后才可执行）。两类不得混用；未落地的命令不得作为当前验收证据。Python 侧使用 uv 3.12.13。

### 16.1 现行可执行门禁（2026-11-27）

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo run -p molt-tui -- --selftest --width 80 --height 24
cargo run -p molt-tui -- --selftest --width 120 --height 40
uv run ruff check .
uv run pytest
```

P0 至少跑 Rust 门禁；Python 门禁按改动范围跑。

### 16.2 未来门禁（agent/ 与 golden 落地后；当前不可执行）

```bash
pnpm --dir agent install --frozen-lockfile
pnpm --dir agent typecheck
pnpm --dir agent lint
pnpm --dir agent test
pnpm --dir agent test:golden
```

当前 `test -d agent` → 0，仓库无 `.ts` / `package.json`，故 16.2 全部不可执行；G-01…G-09 的 golden 比对同样待 TS broker 落地。P1 / P2 在 agent/ 落地后补 16.2，不降低 16.1 的既有门禁。

`--width/--height` 保留在 P0 门禁（W-12b 实现）；上限 width ≤500、height ≤200；非法值（0、超限、缺一维、未配 `--selftest`）退出码 **2**。`--theme` 为 P1，不列入 P0 门禁。P1 必须新增 bwrap 参数解析与真实执行测试（扩展 P-09），并补「非 git workspace」「无 `.molt`」用例；P0 只做文档修正。

| 组 | 用例 | 覆盖 |
|---|---|---|
| C | C-01…C-04 | CLI：TTY / 非 TTY / 无参入口 / 找不到 TUI |
| T | T-01…T-09 | TUI：6 屏、降级、`--selftest`、`--mock`、无后端 |
| P | P-01…P-12 | 权限判定与审批 |
| S | S-01…S-04 | 单写者 |
| R | R-01…R-04 | 恢复 |
| G | G-01…G-09 | golden 字节对齐 |
| E2E | E2E-1…E2E-6 | 闭环、真实 kernel、断线恢复、fork、rollback、记忆 |
| Z | Z-01 | 只读命令零写入 |
| L | L-01 | 平台限制 |

E2E-1 为 mock provider 的读→写→答；E2E-2 用真实 kernel 与隔离 state 验证 managed 快照；E2E-3 验证提交后断线按原 ID 恢复；E2E-4 从中间节点 fork；E2E-5 验证会话 + 配置 + 文件回滚逐字节一致；E2E-6 验证记忆写入→检索→注入。

权限矩阵：P-01 空规则 → ask；P-02 allow → allow；P-03 allow + deny `.env` → deny；P-04 saved always `.env` → deny；P-05 ask all + allow git → `git status` allow；P-06 `git status; rm -rf /` → 逐子命令 ask/deny；P-07 多资源 → deny 聚合；P-08 deny net → deny；P-09 无 bwrap → 拒绝；P-10 session rule 新会话 → ask；P-11 global deny → deny；P-12 超时 24 h → timeout 不执行。

golden 只覆盖 managed 快照路径（`commit_file_contents` / `_record` / `_publish` / `recover` / 应用层 rollback）；`echo`、`activate`、`merge`、`propose`、`digest` 不在本轮。夹具位于 `tests/fixtures/golden/<case>/{files,sequence.json}`；`capture_python.py` 输出 `index.json`、`broker.json`、`generations`、`events`（保留原 `ts` / `request_id`）；TS 侧注入 clock 与 `nextRequestId` 顺序回放。逐字节比较 `index.json`、`broker.json`、generation、`manifest_hash`、`committed_at` 与事件路径；任一字节差异即失败，输出首个 JSON Pointer，禁止归一化。九个用例为：G-01 首次提交、G-02 二次提交、G-03 无变更、G-04 stale generation、G-05 stale head、G-06 journal 断线 committed、G-07 not_found 重放、G-08 外部改 current、G-09 rollback 前向提交。

依赖许可：ratatui、crossterm、unicode-width、serde、serde_json、ureq、diffy 为 MIT 或 MIT/Apache-2.0；`node:sqlite` 随 Node 许可（FTS5 / trigram 能力待实测，Q2）；TypeScript 为 Apache-2.0。禁止 GPL / AGPL、镜像 / 泄漏代码、未锁版本。

## 17. 分期路线

| 阶段 | 范围 | 出口 | 回滚 |
|---|---|---|---|
| P0（本轮） | spec + `molt-tui` mock（6 屏 + 键位 + 主题 + logo/mascot + selftest）+ `molt` 入口（无参 exec / `molt tui`） | T/C/Z 用例 + `cargo test/clippy/fmt` 全绿 + `test_zero_write` 不回归 | 删除 crate + 回退 `cli.py` |
| P1 | agent loop + SQLite + 树 + 工具 + MCP stdio + 权限/审批 + SSE + 真 TUI 接线 + bwrap 参数/执行测试 | E2E-1、S/P/R、F1/F2/F11 | 保留 mock，`--mock` 可用 |
| P2 | 记忆 + 配置树/路由 + kernel 快照/回退（managed + workspace）+ broker golden + 子 agent（仅上下文隔离） | G、E2E-2…E2E-6、Z 全绿；`molt project` 回归不受影响 | 关闭 `routing.auto` 与文件回退，broker 可切只读 |

纪律：每期更新 `docs/architecture.md` 与 ADR 状态；P1 未通过不得开启 P2 的 kernel 写入。这样可以把「UI 可用」「agent 闭环」「证据链对齐」分开验收，避免一次性引入不可回滚的复杂度。

## 18. 开放问题 / 假设

以下问题需用户逐项确认；标「推荐默认，待确认」的只是默认值，不是既成事实。

| ID | 问题 | 推荐默认 / 现状（待确认） |
|---|---|---|
| Q1 | agent 数据根与 token 路径 | 推荐默认（待确认）：`~/.local/share/molt/agent/`；token 放 `$XDG_RUNTIME_DIR/molt/agent-<uid>.json` |
| Q2 | `node:sqlite` 是否具备 FTS5 + trigram | 待实测；不满足则改用 `better-sqlite3` |
| Q3 | workspace 快照预算与排除项 | **已确认（2026-11-21，Q3）**：≤5 000 文件 / 单文件 ≤8 MiB / 总 ≤256 MiB + 排除项：`.git/`（整棵）、`.env*`、`.molt/memory/`、`.ssh/`、`.aws/`、`.netrc`、`.git-credentials`、`node_modules/`、`target/`、`.molt/tmp/`；拒绝 symlink；快照前 secret scan |
| Q4 | 是否确认文件回退禁用 kernel rollback，改为导出 + 前向 commit | **已确认（2026-11-21）**：导出 + 前向 commit，禁用 kernel rollback。X1 约束的是**文件回退**；公开 CLI `molt version rollback` 是只改变活动快照的运维命令，不属于文件回退，保持原语义（2026-11-27 解释，见 ADR-0022 D5.1） |
| Q5 | fork 复制行还是共享节点 | 推荐默认（待确认）：复制行 |
| Q6 | 记忆 `verified` 判定主体 | 推荐默认（待确认）：仅 `user_confirmed` + `repo_checked` 自动；`tool_verified` 二次确认 |
| Q7 | auto 路由 N 与成功定义 | 推荐默认（待确认）：N=5；成功 = turn 完成且无用户回退 |
| Q8 | provider 出网位置 / remote MCP 网络策略 | 推荐默认（待确认）：provider 在沙箱外；remote MCP 默认关，显式 allowlist |
| Q9 | `always` 作用域 / TTL | 推荐默认（待确认）：session + 24 h |
| Q10 | TUI 动效 / 鼠标默认 | 可后调；tmux 静态；鼠标 P1（默认关） |
| Q11 | 无参 `molt` 找不到 `molt-tui` 时 exit 2 还是 help | 推荐默认（待确认）：exit 2。**收口条件（2026-11-27）**：B 批自然态 PTY 验收覆盖双 TTY 启动与 `molt-tui` 缺失分支——双 TTY 缺失时 exit 2、非 TTY 时 help + exit 0；证据留存并注明日期后关闭。未收口前按推荐默认评审，不改实现 |
| Q12 | 单 agent 多 TUI / 多 workspace 并发级别 | 推荐默认（待确认）：单 TUI 多 workspace；多客户端 P2 |

| ID | 假设 | 影响 / 验证 |
|---|---|---|
| A1 | ratatui 0.30 + crossterm 0.29 可用 | 影响 TUI 栈；P0 编译验证 |
| A2 | Node 24 `node:sqlite` 有 FTS5 | 影响记忆实现；Q2 实测 |
| A3 | 单机单 UID | 影响威胁模型；与 INV-9 一致 |
| A4 | attic / v1-final 只作设计参考 | 不复制代码；许可证复核 |
| A5 | `test_project_cli.py` 可作 golden 夹具来源 | 影响 G 组；需确认可复用范围 |
| A6 | kernel manifest 允许额外 root 键且 Python 忽略未知 root | 影响兼容性；需回归验证 |

## 19. ADR 清单

ADR 0020–0025 正文已生成（W-04b / W-04c，2026-11-21），文件名为最终命名。0020–0023 与 0025 仍在评审，阻塞项必须在接受前关闭；0024 已于 2026-11-27 归档，不再作为待接受 ADR（重启条件见其头部）。

| ADR | 标题 | 范围 | 阻塞项 |
|---|---|---|---|
| [0020](decisions/0020-v3-layering.md) | v3 分层与进程边界 | TUI / agent / broker / kernel；`molt → molt-tui`；kernel 零改动；TUI 栈与降级 | Q1 / Q10 / Q11 / A1；Q11 / X2 收口条件见 §18 Q11 与 ADR-0020 X2（2026-11-27） |
| [0021](decisions/0021-ts-broker.md) | TS broker 与 kernel 事务契约 | 单写者 / journal / 原 ID 恢复 / state 根隔离 / golden 字节对齐 / managed vs workspace | Q2 / A5 / A6、0021 X1；TS broker 未交付，预算来源分列见 ADR-0021 D5 |
| [0022](decisions/0022-session-tree.md) | 会话树、fork 与回退 | `parent_id` 树 / leaf / boundary / checkpoint / 前向 commit / 两棵树分离 | Q4 / Q5、0022 X1；X1 只约束文件回退，CLI 运维 rollback 不在范围（ADR-0022 D5.1）；C5 待 B2 修 |
| [0023](decisions/0023-permissions-sandbox.md) | 权限、审批与沙箱 | last-match + deny>ask>allow / once/always/reject / 工具分级 / bwrap / 脱敏 | Q8 / Q9；权限引擎与 v3 工具沙箱未交付（ADR-0023 D8） |
| [0024](decisions/0024-memory-system.md) | 记忆系统 | 双层 / frontmatter / FTS5 / 两阶段 / 去重 / 过期 / 证据 / 与 compaction 关系 | Q2 / Q6；**0024 已归档（2026-11-27），重启条件见 ADR-0024 头部** |
| [0025](decisions/0025-config-dag-routing.md) | 统一配置模型、配置树与自动路由 | genome 子集 / DAG / suggest / auto / 跨版本不可比 | Q7 / A6；genome 目标保留，DAG / 路由未交付（ADR-0025 D7） |

## 20. 与现有 ADR 的冲突表

| 现有条目 | 关系 | 处理 |
|---|---|---|
| 0015 kernel 不透明字节 / 不跑 agent | 不冲突 | 0020 继承 |
| 0015 §M0「agent runtime 随 agent 归档」 | 已澄清：仅界定 v1 实验面，不禁止 v3 在 kernel 之外新建 agent | **已确认（2026-11-21，0020 X1）** |
| 0015 单写者 / 持久提交 / 原 ID / unknown | 继承 | 0021 落实 |
| 0015 socket `0600` / 同 UID | 继承 | 0021 落实 |
| 0018 版本图 = snapshot DAG + 分支 + 合并 | 部分重叠 | 0022 明确「会话树 / 配置 DAG」两棵树；0025 定义 config DAG |
| 0018「rollback 重发归档 manifest」 | 与文件回退方案冲突 | 0022 禁止用于文件回退，改为导出 + 前向 commit；**已确认（2026-11-21，0022 X1）** |
| 0018 单一 broker | 继承 | 0021 落实 |
| 0018 digest 无授权 | 继承并延伸 | 0024 延伸（0024 已归档，2026-11-27；重启条件见其头部） |
| 0018 受管预算 64 / 256 KiB / 2 MiB | managed 保留；workspace 另定 | **已确认（2026-11-21，0021 X1）**：≤5 000 文件 / 单文件 ≤8 MiB / 总 ≤256 MiB；排除 `.git/`（整棵）、`.env*`、`.molt/memory/`、`.ssh/`、`.aws/`、`.netrc`、`.git-credentials`、`node_modules/`、`target/`、`.molt/tmp/`；拒绝 symlink；快照前 secret scan |
| 0019 退出 v1 | 不冲突 | 0020 / 0021 / 0022 / 0024 不引入 v1 读取；0025 沿用 genome 字段但不读 v1 state（0024 已归档，2026-11-27） |
| AGENTS §二 不变量 | 对齐 | spec INV-1…INV-12 + 0021 / 0023 / 0024（0024 已归档，2026-11-27；重启条件见其头部） |
| AGENTS §七 验收入口 | 扩展 | 新增 pnpm / golden，不降低既有门禁；pnpm / golden 属 §16.2 未来门禁，当前不可执行 |

三处确认项已于 2026-11-21 由用户显式关闭：0020 X1（0015 §M0 不禁止 v3 在 kernel 之外新建 agent）、0022 X1（文件回退禁用 kernel rollback，改为导出 + 前向 commit）、0021 X1（workspace 预算 ≤5 000 文件 / 单文件 ≤8 MiB / 总 ≤256 MiB）。结论见 §22 决策记录。

0024 于 2026-11-27 归档；上表对 0024 的引用保留为历史设计关系，重启条件见 ADR-0024 头部；§11 正文未因归档改动。

## 21. 文件清单

| 路径 | 状态 | 说明 |
|---|---|---|
| `docs/v3-agent-tui-spec.md` | 新建 | 本评审稿 |
| 内部调研资料（目录索引） | 新建 | 目录索引与阅读顺序 |
| `docs/decisions/0020…0025-*.md` | 已生成（W-04b / W-04c） | 六份 ADR 正文（0020 / 0021 / 0022 由 W-04b，0023–0025 由 W-04c）；0024 已归档（2026-11-27） |
| `contracts/schema.sql` | 待补（W-06） | 完整 DDL |
| `contracts/openapi-v3.json` | 待补（W-06） | HTTP 契约 |
| `contracts/events.schema.json` | 待补（W-06） | SSE 事件 schema |
| `contracts/README.md` | 待补（W-06） | 契约目录说明 |
| `crates/molt-tui/` | 已交付（P0 原型 + 部分现行路径） | 入口、6 屏、selftest、logo；Chat 直连 provider、JSONL 会话、Versions 经 CLI 已接通；Tree / Settings / Diff / Approval 仍 mock（C4 / B1 待修）；B2 封堵已执行（2026-11-28）：TUI `b` 不再调用 CLI rollback，文件恢复动作明确不可用；前向恢复未实现，C5 / B2 仍待修；完整 P1 agent 闭环未开始 |

TUI 阶段口径与 `docs/architecture.md`「TUI 与版本工作区」一致：当前是已交付的 P0 原型，加上 Chat 直连、JSONL 会话与 Versions CLI 这些超出纯 mock 的现行路径；TS agent / TS broker / SQLite / 权限引擎 / 工具执行器均未交付，不因 TUI 可用而视为 P1 闭环。

本文档只记录已确认的架构方向与待确认项，不包含实现代码；任何偏离内容包的技术事实都应回到 ADR 评审中解决。

## 22. 决策记录

本节记录 2026-11-21 用户对三处阻塞项（§20）的确认结论；ADR-0020 / 0021 / 0022 正文中的对应条目同步标注「已确认（用户，2026-11-21）」。

| 决策 | 日期 | 结论 | 落点 |
|---|---|---|---|
| 0020 X1 | 2026-11-21 | ADR-0015 §M0「agent runtime 随 agent 归档」只界定 v1 实验面（agent / upper / skills / mcp / web / contracts 随 v1 归档），**不禁止** v3 在 kernel 之外新建 agent；kernel 与 agent 仍保持进程与数据边界分离。 | ADR-0020 §8 X1；§20 |
| 0021 X1 | 2026-11-21 | workspace 类快照预算：≤5 000 文件 / 单文件 ≤8 MiB / 总 ≤256 MiB；排除 `.git/`（整棵）、`.env*`、`.molt/memory/`、`.ssh/`、`.aws/`、`.netrc`、`.git-credentials`、`node_modules/`、`target/`、`.molt/tmp/`；拒绝 symlink；快照前 secret scan。managed 快照预算不变（≤64 文件 / 256 KiB / 2 MiB）。 | ADR-0021 §2 D5、§8 X1；§20 |
| 0022 X1 | 2026-11-21 | `/tree` 文件回退采用「导出 + 前向 commit」，**禁用** kernel `rollback`；历史只增不改，回退留下新 generation。 | ADR-0022 §2 D5、§8 X1；§20 |

三处确认项关闭后，0020 / 0021 / 0022 不再被该三项阻塞，可进入评审接受流程；其余开放项（0020 X2–X4、0021 X2–X4、0022 X2–X4，以及 §18 Q1–Q12 / A1–A6）仍按「待确认 / 待实测 / 假设」处理。本节只记录结论，不改写正文技术内容。

### 22.1 2026-11-27 ADR 处置（A 批）

| 项 | 日期 | 结论 | 落点 |
|---|---|---|---|
| 0024 | 2026-11-27 | 归档；原文件与正文保留；重启条件见头部 | ADR-0024 状态头 / §10；§19 / §20 |
| 0020–0023 / 0025 | 2026-11-27 | 保持 Draft；按对账修订现行 / 目标、交付状态、未决与延期条件 | 各 ADR §10 修订记录 |
| C1 | 2026-11-27 | 现状直连如实记录；spec INV-6 / INV-2 原约束保留；目标态 TUI 不持 provider key | §5.5；`architecture.md` |
| C4 / C5 | 2026-11-27 | 分别转 B1 / B2 待修，不在 A 批关闭 | §5.6；ADR-0022 D5.1 |
| 0020 X4 / Q11-X2 | 2026-11-27 | X4 本轮不拆 0026，记延期触发条件；Q11/X2 记双 TTY / 缺 binary 收口条件与日期 | ADR-0020 §8；§18 Q11 |

## 23. 文档修订记录

| 日期 | 基线 | 修订内容 | 来源 |
|---|---|---|---|
| 2026-11-21 | `a5ce76dbbe60` | Draft v1 初稿（W-04a）；§19 ADR 清单 | 本文档历史 |
| 2026-11-27 | `6198139` | A 批：§5.0 现行路径、§5.5 C1、§5.6 C4/C5；§16 门禁分类；§18 Q4/Q11；§19 / §20 状态同步与 0024 归档标记；§21 TUI 阶段；§19 六条链接核对为 `decisions/` | 2026-11-27 设计-实现对账（spec §18 Q1–Q12 / A1–A6、ADR §8 X1–X4；仓库静态核对） |
