# ADR-0023：权限、审批与沙箱

| 项 | 值 |
|---|---|
| 状态 | Draft |
| 日期 | 2026-11-21 |
| 基线 | `a5ce76dbbe60`（`main`，`pyproject` 版本 `1.2.0rc8`） |
| 实现状态 | 未交付：无权限引擎、无 v3 工具执行器；仅有 mock 审批 UI（2026-11-27，`6198139`） |
| 关联 | spec §10 / §15；前置 0015 / 0020；被 0021（工具调用）与 0025（`permissions` 字段）引用 |
| 决策者 | 用户（评审中） |

Spec: `docs/v3-agent-tui-spec.md`

## 1. 背景

v2 没有 agent；v3 的 agent 会读写文件、执行命令、访问 MCP、调用模型，必须先回答三问：谁判定一次调用？点过「always」是否等于永久放行？规则被绕过后还剩什么安全边界？

官方结论：**shell 规则不是安全边界**。`git status; rm -rf ~` 可以绕过任何基于命令字符串前缀的 allow 规则；规则层只能表达可解释的意图，真正的隔离必须叠加沙箱。

## 2. 决策

### D1 规则模型

`{id, action, resource, effect, scope, order}`：

- `action ∈ {fs.read, fs.write, proc.exec, net.fetch, mcp.call, agent.spawn, memory.write, config.apply, workspace.rollback}`；
- `resource ∈ {workspace:<rel>, fs:<abs>, cmd:<...>, net:<host>, mcp:<server>/<tool>}`；
- `effect ∈ {allow, ask, deny}`；`scope ∈ {global, project, session}`；`order` 单调递增；
- 匹配语义 **last-match-wins**：后写规则覆盖先写规则。

### D2 多资源聚合：deny > ask > allow

一次调用可能触及多个资源：逐资源取最后命中，再按 `deny > ask > allow` 聚合；任一资源命中 `deny`，整体拒绝。

### D3 deny 不可被覆盖

`always` 只把 `ask` 提升为 `allow`，**永不覆盖 `deny`**（含 glob 命中）。`decide` 先跑 deny 扫描；`saved_rules` 与显式 `deny` 冲突时 `deny` 胜。

### D4 审批三态

`once` 仅本次；`always` 写 `saved_rules`，`scope` 默认 `session`，确认前可编辑资源模式；`reject` 记 `denied`，turn 继续，`tool_calls.status=denied`。

超时 `24h` → `timeout`，不执行；重连丢失 pending → `cancelled`，模型收到 `interrupted`。

### D5 工具分级

`read` / `grep` / `glob` / `list_dir`：allow，限 `workspace`；`apply_patch`（唯一写路径）：ask；exec（bwrap 内）：ask；`fetch`：deny；MCP：ask；subagent：ask。

不做每工具独立审批流，也不做 50 工具全量注入。

### D6 沙箱 bwrap

基线命令（可执行形式，显式 src/dest 成对）：

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

- `--unshare-net` 默认**无条件**加入；网络 allowlist 模式另立方案（deny 优先、禁回环与私网），不在基线上放宽。
- `--clearenv` 必须配 `--setenv` 逐项传白名单（`PATH` / `LANG`，必要时 `TERM` / `TZ`），否则子进程无环境。
- `--ro-bind` / `--bind` 必须 src + dest 成对；省略 dest 的旧写法（`--ro-bind /usr /bin /lib …`）不可执行。
- `$WORKSPACE/.git` 与 `$WORKSPACE/.molt` 用 `--ro-bind-try`：目录不存在时跳过绑定，因此**非 git 仓库 / 未初始化 `.molt` 仍可执行**。
- `*_KEY` / `*_TOKEN` / `AWS_*` / `SSH_*` 不传入；bwrap 不可用 → `sandbox_unavailable`，拒绝执行，**不降级**。
- 测试归属：**P0 只做文档修正**；**P1 必须新增 bwrap 参数解析与真实执行测试**（扩展 P-09），并补「非 git workspace」「无 `.molt`」两个目录缺失用例。

### D7 密钥

来源为 env `MOLT_<PROVIDER>_API_KEY` 或 `apiKeyHelper`；只在 agent 内存，不写 DB / 事件 / 日志 / 配置树；redact 已知前缀 + 高熵值；`mcp_servers.env_keys_json` 只存键名；provider 调用在沙箱外；工具需凭据时用继承 fd 或外部 helper。

### D8 交付状态与延期条件（2026-11-27，基线 `6198139`）

- **权限引擎未交付**：仓库根无 TypeScript agent / runtime（根目录 `agent/`、`.ts`、`package.json` 均不存在）；`crates/molt-tui/src/approval.rs` 只有 UI 状态与 `mock::ApprovalState`，不执行 D1–D3 的 last-match、多资源聚合或 deny 扫描。
- **v3 工具沙箱未交付**：无工具注册表、无 `apply_patch` / exec 执行器；bwrap 调用只出现在 v2 GGUF worker（`molt/version_workers.py:16`；`examples/` 下的同名文件只是 4 行 re-export shim），不是 v3 tool executor，不能作为 D6 的已实现证据。
- **C1 直连不替代 Q8**：现行 TUI Chat 直连 provider（key 在 TUI 进程内，见 ADR-0020 D1a），provider 出网不受 v3 bwrap 约束。这只说明现状，不回答 Q8「provider 出网位置 / remote MCP 网络策略」；Q8 仍是未决项，必须在 agent 模式落地前单独确认。
- 本 ADR 保持 **Draft**；D1–D7 是目标设计，不是现状描述。**重启条件**：v3 权限引擎 / 工具执行器排期启动时，先按 D1–D6 与 P-01…P-12 验收，再接线 TUI；未重启前，审批 modal 不得被当作真实权限判定。

## 3. 备选方案

| 方案 | 内容 | 否决理由 |
|---|---|---|
| A | 首匹配 | 多资源语义不清，无法表达「后写覆盖」 |
| B | 只有沙箱、无规则 | 不可解释，无法表达项目级允许 |
| C | 只有规则、无沙箱 | `;` / `$()` / 管道可绕过，等于没有边界 |
| D | `always` = 全局永久 | 一次误点导致长期扩权 |
| E | 默认 `acceptEdits` | 写路径必须逐次确认 |
| F | 本方案（规则 + 沙箱） | 采纳 |

## 4. 理由

- 可解释：每个决定能追溯到具体规则与 `order`；可撤销：`saved_rules` 可查可删，scope 有界；
- 纵深：规则负责意图，沙箱负责隔离；与 AGENTS「控制失效即拒绝执行」一致，`sandbox_unavailable` 直接拒绝。

## 5. 影响

- 审批 modal 高频；read 类在进程内只读；bwrap 启动约几十 ms；依赖 unprivileged user namespace，不可用时 exec 全拒；
- `saved_rules` 需 UI 可查可删（P2）；短命令需接受 bwrap 的固定启动成本。

## 6. 失败场景 / 如果不这么做

| 若违反 | 直接后果 | 长期后果 |
|---|---|---|
| `always` 覆盖 `deny` | 永久放行 | 审计无法解释，无法收敛 |
| 不做多资源聚合 | `git status; rm -rf /` 前半 allow 导致整体放行 | 规则形同虚设 |
| 不叠沙箱 | `;` / `$()` / 管道绕过 | 误以为有边界 |
| 密钥进沙箱 env | 任意脚本读走 | 凭据泄露 |
| bwrap 缺失时降级直跑 | 假沙箱 | 安全承诺失效 |
| 审批超时放行 | 无人值守扩权 | 后台任务越权 |

## 7. 回滚 / 撤销

| 动作 | 步骤 | 结果 |
|---|---|---|
| 规则误配 | `mode=ask` + 清 `saved_rules` | 回到逐次确认 |
| `always` 误点 | 删除对应 `saved_rules` 行 | 该资源恢复询问 |
| 沙箱不可用 | 全局禁 `exec` | 只保留 read / write |
| ADR 级撤销 | 关 auto，恢复全 ask | 权限系统降级为纯确认 |

## 8. 开放问题

| ID | 问题 | 状态 |
|---|---|---|
| X1 | `always` 作用域 / TTL | 待确认（建议 `session` + 24h）。**延期条件**：审批引擎实现前确定；影响 `saved_rules` 表与 UI |
| X2 | provider 出网 / remote MCP 网络 | 待确认。**延期条件**：与 Q8 同批，在 provider / remote MCP 接线前定稿；现行 C1 直连不构成答案 |
| X3 | 项目级规则文件随仓库分发 | 待评估（供应链风险）。**延期条件**：项目级规则文件随仓库分发前评估 |
| X4 | read 类是否需沙箱 | 待评估。**延期条件**：read 类工具执行器实现前评估 |

## 9. 与现有 ADR 的关系

| ADR | 关系 | 说明 |
|---|---|---|
| 0015 | 不冲突 | 规则与沙箱都在 kernel 外；kernel 不持凭据 |
| 0018 | 不冲突 | `approvals` / `saved_rules` 属 agent 私有状态，不进版本图 |
| 0025 | 被引用 | `permissions{mode, rules[]}` 复用 D1 的规则形状 |
| AGENTS §二 | 落实 | 「控制失效即拒绝执行」直接落为 `sandbox_unavailable` |

## 10. 修订记录

| 日期 | 基线 | 修订内容 | 来源 |
|---|---|---|---|
| 2026-11-21 | `a5ce76dbbe60` | 初稿（W-04c）：规则模型、聚合、审批、工具分级、bwrap、密钥 | spec §19 |
| 2026-11-27 | `6198139` | 补 D8：明确权限引擎与 v3 工具沙箱未交付；C1 直连不替代 Q8；X1–X4 写延期条件；状态保持 Draft | 2026-11-27 设计-实现对账（spec §18 Q8/Q9，本文件 §8；`crates/molt-tui/src/approval.rs`、`molt/version_workers.py` 静态核对） |
