# ADR-0021：TS broker 与 kernel 的事务契约及 golden 对齐

| 项 | 值 |
|---|---|
| 状态 | Draft |
| 日期 | 2026-11-21 |
| 基线 | `a5ce76dbbe60`（`main`，`pyproject` 版本 `1.2.0rc8`） |
| 实现状态 | 未交付：无 TypeScript broker；Python `VersionSandbox` 仍为唯一 broker（2026-11-27，`6198139`） |
| 关联 | spec §3 / §5.4 / §6.5–6.7 / §12 F5-F7 / §14.4；前置 0020 / 0015 / 0018 |
| 决策者 | 用户（X1 于 2026-11-21 确认） |

Spec: `docs/v3-agent-tui-spec.md`

## 1. 背景

Python `versioning.py` 已跑通 broker 语义（`versioning.py:110-267`）：`index.json` 的 active / branches / snapshots / events / digests，`broker.json` 的 pending，`broker.lock` 单写者，以及崩溃后按原 request ID 恢复。kernel 侧是 journal → 日志提交 → current。

v3 用 TypeScript 重写 broker。重写不是重新设计：语义一旦漂移，已写入的 index 与 generation 链可能不可读。另一个风险是 agent state 根与 Python `molt project` 的 state 根重叠，两套 journal 互相覆盖。本 ADR 固定「逐条复刻 + 字节级 golden + 启动门禁」。

## 2. 决策

### D1 逐条复刻 Python 语义

- `_session`：flock 非等待，拿不到即 `writer_conflict`；`_load` / `_save` 用 canonical 序列化 + 原子写（tmp + fsync + rename + 目录 fsync）。
- `_idle_current`：有 pending 即拒写；`current != journal.current` 视为外部修改，拒写。
- `_publish`：先 `put index` → `manifest.create({ROOT: files})` → 写 `pending{expected, params}` → `generation.commit`。
- `_complete`：校验 `manifest_hash`、`committed_at`、`generation == expected + 1`。
- `recover`：按原 ID 查 `request.status`；committed 则结算；not_found 且基线一致则原参重放；基线已变则保留意图并报冲突。
- `_index`：`current=null` 返回空骨架，不报错。

### D2 合同与布局

- state 根：`<agent-home>/workspaces/<ws>/state`，由 kernel init 生成。
- `broker.json`：`{contract:"molt-agent-broker/1", state_id, current, pending?}`。
- `broker.lock`（单写者锁）与 `candidates/`（stale 候选保留目录）。
- `sessions.db` 在 agent-home，**不在** state 内（会话数据不进 kernel 视野）。
- index root 沿用 `synthetic-versions`（golden 可与 Python 比对）；workspace root 只用于工作区文件树。

### D3 state 根隔离（拒绝启动）

遇到以下任一情况 `exit 2`，**不降级只读**：

- `broker.json.contract = molt-synthetic-versions/1`（Python 合同）；
- root 或其父目录存在 `project-session.json`；
- 未知合同。

`SERVER_LOCK` 是 `molt-kernel serve` 的正常服务锁，**不构成冲突**，也不得据此只读。

### D3a broker 生命周期（两种启动方式）

1. **连接既有 serve**：broker 打开 `kernel.sock`，完成 JSON-RPC handshake 并校验 `state_id`；`SERVER_LOCK` 由 serve 持有属正常状态。
2. **serve 未运行（socket 不存在）**：agent 可启动 `molt-kernel serve` 子进程，等待 socket 出现与 handshake 完成；handshake 超时或失败按 F7 重试 / 报错，**不得静默只读**。

写互斥由 `broker.lock`（进程级 flock）与 kernel 侧 `LOCK` 保证；两者失败才报 `writer_conflict`。

### D4 原 ID 纪律

- pending 落盘后不可换 ID；恢复分支禁止生成新 `uuid`。
- TS 新建请求用 `agent-<ulid>`；golden 回放注入 Python 侧原字符串。

### D5 两类快照共用提交路径（预算分列，来源不同）

**现行 managed-file 预算（当前生效；来源是 Python 实现，不是本 ADR 新增）**：

| 类型 | root | 预算 / 规则 | 用途 | 来源 |
|---|---|---|---|---|
| managed | `synthetic-versions` | ≤64 文件 / 单文件 ≤256 KiB / 总 ≤2 MiB；路径禁绝对 / `..` / `.git` / `.env*` | golden 对象 | `molt/version_files.py:13-15`（HEAD `6198139` 现行实现） |

**v3 workspace 目标预算（已确认目标，尚未实现）**：

| 类型 | root | 预算 / 规则 | 用途 | 来源 |
|---|---|---|---|---|
| workspace | workspace | ≤5 000 文件 / 单文件 ≤8 MiB / 总 ≤256 MiB；排除 `.git/`（整棵）、`.env*`、`.molt/memory/`、`.ssh/`、`.aws/`、`.netrc`、`.git-credentials`、`node_modules/`、`target/`、`.molt/tmp/`；拒绝 symlink；快照前强制 secret scan，命中密钥默认拒绝或要求显式确认 | 工作区文件树 | ADR-0021 X1 / spec §18 Q3（**已确认（用户，2026-11-21）**） |

**两张表不得混用**：现行 CLI / managed 路径仍按 64 文件 / 单文件 256 KiB / 总 2 MiB 执行；5 000 文件 / 单文件 8 MiB / 总 256 MiB 只是 v3 workspace 快照的目标预算，当前没有代码路径执行它。引用任一数字时必须带「现行 managed」或「v3 workspace 目标」限定。

workspace 快照整体排除 `.git/` 且**不恢复**：文件回退只恢复工作树文件，不碰 git 元数据。

### D6 golden 只覆盖 managed

覆盖 `commit_file_contents` / `_record` / `_publish` / `recover` / 应用层 rollback；`echo` / `activate` / `merge` / `propose` / `digest` 不在本轮。

### D7 字节级比对

唯一非确定源是 `datetime.now(UTC)` 与 `uuid4`（`versioning.py:227,234`）。TS 暴露 `clock()` / `nextRequestId()` 注入点；回放后 `index.json`、`broker.json`、generation、`manifest_hash`、`committed_at`、事件对象路径必须逐字节相同；**禁止归一化**（不做字段排序、不补默认值、不省略空格）。

### D8 交付状态与重启条件（2026-11-27，基线 `6198139`）

- **TS broker 未交付**：仓库根无 `agent/`，无 `.ts`、无 `package.json`；Python `VersionSandbox`（`molt/versioning.py:110-267`）仍是唯一 broker。D1–D7 是未来 TS 实现必须满足的契约，不是已实现证据。
- **golden 未落地**：无 `agent/tests/golden`、无 `tests/fixtures/golden`、无 `contracts/`；G-01…G-09 与字节级比对全部待 TS broker 落地后执行。
- 本 ADR 保持 **Draft**。**重启条件**：v3 agent / TS broker 排期启动时，先按本 ADR 跑通 G-01…G-09 与 state 根隔离门禁，再进入实现；若 TS agent 被明确延期或归档，本 ADR 随之转归档评审，不得被引用为「已实现」或「现行预算」。在重启前，现行 `molt version` / `molt project` 路径继续使用 Python `VersionSandbox`。

## 3. 备选方案

| 方案 | 内容 | 否决理由 |
|---|---|---|
| A | TS 子进程调 Python `VersionSandbox` | 依赖 Python、每次多进程、仍共享 state 风险 |
| B | TS 直调 kernel，不实现 journal | 丢失 unknown 语义，违反 0015 |
| C | 新增 kernel 条件提交 | 改 kernel，0020 D6 禁止 |
| D | 用 SQLite 存 index | 破坏 kernel 作为唯一事务后端 |
| E | 逐条复刻 + golden（本方案） | 采纳 |

## 4. 理由

- 复刻已验证实现，风险最小；golden 字节级可回归，漂移立刻暴露。
- state 隔离把 Python / TS 两套 broker 的冲突挡在启动时；只用 kernel 现有能力，不需要内核新特性。

## 5. 影响

- 新增 `agent/src/broker/*` 与 `agent/tests/golden`（夹具 + capture + 回放，9 用例 G-01…G-09）。
- 每次提交 N 个对象 + 1 个 manifest + 1 次 commit，与 Python 同量级；Python `molt project` 不受影响，两套 broker 并存靠 D3 门禁。

## 6. 失败场景 / 如果不这么做会怎样

| 若违反 | 直接后果 | 长期后果 |
|---|---|---|
| 不做 golden | index 字段顺序 / 默认值差一点 | 写出不可读的 index，恢复失败 |
| 不注入 clock / id | hash 链差异被掩盖 | 无法定位漂移，审计失真 |
| 不隔离 state | 两个 journal 互相覆盖 | current 跳代、数据不可恢复 |
| 恢复时换 ID | 重复 generation | 幂等失效 |
| TUI 直连 kernel | 绕过锁与 journal | 见 0020 |

## 7. 回滚 / 撤销

| 动作 | 步骤 | 结果 |
|---|---|---|
| 停写并核对 | 停 agent 进程；kernel audit 核对链 | 不再产生新 generation；确认已提交内容 |
| 已提交数据 | 不回滚（历史只增） | 通过导出只读树保留证据 |
| 彻底弃用 | 归档 agent-home；合同升级只能新建 state | 不影响 Python state；不得原地改 `contract` 字符串 |

## 8. 开放问题

| ID | 问题 | 状态 |
|---|---|---|
| X1 | workspace 快照预算与排除项 | **已确认（用户，2026-11-21）**：≤5 000 文件 / 单文件 ≤8 MiB / 总 ≤256 MiB；排除 `.git/`（整棵）、`.env*`、`.molt/memory/`、`.ssh/`、`.aws/`、`.netrc`、`.git-credentials`、`node_modules/`、`target/`、`.molt/tmp/`；拒绝 symlink；快照前强制 secret scan，命中密钥默认拒绝或要求显式确认 |
| X2 | `node:sqlite` 是否具备 FTS5 + trigram | 待实测；不满足改用 `better-sqlite3`。**延期条件**：session / memory store 选型前完成实测；未实测不得把 SQLite 写成已选定实现 |
| X3 | golden 夹具能否复用 `test_project_cli.py` | 待验证（A5）。**延期条件**：golden 夹具首次落地时与 A5 一并验证；未验证前 G 组只算设计 |
| X4 | 大 workspace 是否打成 tar 单对象 | 待评估。**延期条件**：v3 workspace 快照实现前评估对象数与性能 |

## 9. 与现有 ADR 的关系

| ADR | 关系 | 说明 |
|---|---|---|
| 0015 | 继承 | 单写者 / 持久提交 / 原 ID / unknown |
| 0018 | 落实 | 隔离 state + 单一 broker |
| 0019 | 不涉及 | 不引入 v1 读取 |
| 0020 | 上游 | broker 是唯一接触 kernel socket 的模块 |
| 0022 | 下游 | checkpoint 引用 generation / manifest / snapshot |

## 10. 修订记录

| 日期 | 基线 | 修订内容 | 来源 |
|---|---|---|---|
| 2026-11-21 | `a5ce76dbbe60` | 初稿（W-04b）：逐条复刻、state 隔离、原 ID、golden、managed/workspace 预算 | spec §19 |
| 2026-11-27 | `6198139` | 拆分两张预算表并标来源（现行 managed 64/256 KiB/2 MiB；v3 workspace 目标 5 000/8 MiB/256 MiB），明确不得混用；补 D8 交付状态与重启条件；X2–X4 写延期条件；状态保持 Draft | 2026-11-27 设计-实现对账（spec §18 Q2/Q3/A5，本文件 §8 X1–X4；`molt/version_files.py` 静态核对） |
