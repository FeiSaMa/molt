# ADR-0020：v3 分层架构与进程边界（TUI / agent / broker / kernel）

| 项 | 值 |
|---|---|
| 状态 | Draft |
| 日期 | 2026-11-21 |
| 基线 | `a5ce76dbbe60`（`main`，`pyproject` 版本 `1.2.0rc8`） |
| 关联 | spec §2 / §4 / §5.1 / §11 / §15；ADR-0015 / 0018 / 0019；0021 / 0022 同批评审 |
| 决策者 | 用户（X1 于 2026-11-21 确认） |

Spec: `docs/v3-agent-tui-spec.md`

## 1. 背景

v2 只负责「版本 + 审计」，交互交给外部程序：`molt project opencode -- run`（`project_cli.py:204-230`）拉起外部 agent 并复用其会话，molt 自身不持有 agent 运行时。v3 要在 kernel 之外新增两层——行为层（agent）与交互层（TUI）——同时不把 LLM、网络、会话语义塞进 kernel。

约束来自已冻结的方向：

- TUI 用 Rust + Ratatui；
- agent 用 TypeScript（Node 24 + pnpm 12，不依赖 Bun）；
- kernel 不跑 agent；会话 / 记忆归 agent 侧 SQLite；kernel 只管工作区快照、版本、回退、审计。

需要先固定的是「谁调谁、谁能碰 kernel socket、进程如何被发现」；否则 0021（事务契约）与 0022（会话树）没有稳定地基。

## 2. 决策

### D1 四层单向依赖

- 方向：`molt`（Python CLI）`--exec-->` `molt-tui`（Rust）`--HTTP/SSE-->` agent（TS）`-->` broker（TS 模块）`--UDS-->` kernel（Rust）。反向与跨层依赖一律禁止。
- TUI 不连 kernel / broker、不写 DB、不持密钥。
- broker 是唯一接触 kernel socket 的模块；`node:net` 仅允许出现在 `src/broker/**`（INV-2）。
- kernel 一行不改（INV-1、D6）。

### D2 入口行为

| 场景 | 行为 |
|---|---|
| `molt` 无参 + stdin 与 stdout 都是 TTY 且 `MOLT_NO_TUI != "1"` | `_tui_wanted=true` → `execv` `molt-tui`（`MOLT_TUI_BIN` 可覆盖路径） |
| `molt` 无参 + stdin 或 stdout 非 TTY，或 `MOLT_NO_TUI="1"` | 打印 help，`exit 0`，零写入（INV-8）；**即使非 Linux 也一样**，不触发平台门禁 |
| `molt tui`（显式子命令） | 只受 `MOLT_NO_TUI="1"` 限制，**不看 TTY**（便于 CI / `--selftest`）；被禁用 → stderr 提示 + `exit 2`；可带 `--mock` / `--width` / `--height`（`--width/--height` 仅与 `--selftest` 组合，P0）；`--theme` 为 P1 |
| `molt-tui` 缺失 | `exit 2` + 可操作提示；不回落 help、不静默 mock（INV-12） |
| 平台门禁 | 只作用于**真正要启动 TUI** 的路径（TTY 无参 / 显式 `molt tui`）：非 Linux x86_64 → `exit 2`（INV-9）；非 TTY 无参仍是 help + `exit 0` |

`--width/--height` 仅与 `--selftest` 组合；上限 width ≤500、height ≤200；非法值（0、超限、缺一维、未配 `--selftest`）→ `exit 2`。

### D3 后端发现

agent 启动时写 `$XDG_RUNTIME_DIR/molt/agent-<uid>.json`，内容 `{pid, addr, token, state_root, version}`；目录 `0700`、文件 `0600`。TUI 读该文件获得地址与 token。pid 已死或 `GET /v1/health` 失败 → 显示 Startup 错误卡 + 重试，不自动 mock（fail closed，INV-12）。

### D4 TUI 技术栈

`ratatui` 0.30.x + `crossterm` 0.29 + `unicode-width`。`ureq`（阻塞 HTTP 放后台线程，经 `mpsc` 回传 UI 线程）与 `diffy` 为 **P1**（P0 未引入）。P0 不引 `syntect`；不引 sixel / kitty / iTerm2 图片协议。

### D5 视觉降级

truecolor 半块 → 256 色半块 → 16 色半块单/双色（非 ASCII）→ 纯文本。`TERM=dumb` 打印提示并退出、不启 alt-screen；`NO_COLOR` 保留 TUI 但去色；非 TTY 不进入 TUI；宽度 <80 时只画单行字标、不画 mascot；tmux / Zellij 使用静态 mascot；鼠标 P1；探测失败不阻塞启动。mascot 用自绘文本帧 `include_str!` 嵌入，不引 GPL 素材。

### D6 kernel 零改动

不修改 kernel。未来确需 kernel 新能力（RPC、状态格式、依赖）必须另立 ADR，不得在本 ADR 或实现里夹带。

## 3. 备选方案

| 方案 | 内容 | 否决理由 |
|---|---|---|
| A | 全 TS 单进程（TUI 也用 TS） | 与「Rust TUI」冻结决策冲突；UI 卡顿会拖垮 agent |
| B | 复用 opencode / Pi runtime | 权限与证据语义不一致；上游破坏性变更不可控 |
| C | 沿 v2 把交互交外部 agent | 无法自有会话树 / 记忆 / 品牌 |
| D | Python agent + Rust TUI | 与 0015 M0 归档边界冲突更严重；性能与打包弱 |
| E | 本方案（四层单向 + TS broker） | 采纳 |

## 4. 理由

- 边界可测：用 `cargo tree` 断言 TUI 依赖集合，用 lint 规则断言 `node:net` 只在 `src/broker/**`。
- 故障隔离：TUI 崩溃不影响 agent 与 kernel；agent 重启不需要 TUI 重编。
- 渲染性能：Rust + Ratatui 保证 80×24 与大 diff 场景不卡顿。
- 可替换性：TUI 只依赖 HTTP/SSE，任意 HTTP 客户端都能替代，便于测试与脚本化。

## 5. 影响

- 新增 `crates/molt-tui`、`agent/`。
- 修改 `molt/cli.py`（新增 `tui` 子命令 + 无参 TTY 分支）、`Cargo.toml` members。
- 多一个常驻进程与 runtime 文件（`agent-<uid>.json`，退出时清理）；新增 TUI 快照测试与 `--selftest` 门禁（T-01…T-09）。
- 仅支持 Linux x86_64（INV-9）。

## 6. 失败场景 / 如果不这么做会怎样

| 若违反 | 直接后果 | 长期后果 |
|---|---|---|
| agent 进 kernel | kernel 依赖膨胀、出站网络出现 | 0015 破产，单测与审计变慢 |
| TUI 直连 kernel | 绕过 journal 与单写者锁 | 崩溃后无法按 request ID 恢复 |
| 入口不 fail closed | 无后端时静默 mock | 用户以为在真实后端上操作，数据落到错误位置 |
| 不锁 TUI 栈版本 | 上游 minor 回归 | 渲染错位、键位失效 |
| 不做视觉降级 | dumb / tmux / 窄终端乱码 | 不可用，且难定位 |
| 夹带 kernel 改动 | 破坏 INV-1 | 审计前提消失 |

## 7. 回滚 / 撤销

| 动作 | 步骤 | 结果 |
|---|---|---|
| 撤入口 | 单 commit 还原 `molt/cli.py` 的无参分支与 `tui` 子命令 | `molt` 回到 v2 help 行为 |
| 撤 TUI crate | 删除 `crates/molt-tui`，从 `Cargo.toml` members 移除 | workspace 恢复原状 |
| 撤 agent | 停 agent 进程，删 `$XDG_RUNTIME_DIR/molt/agent-<uid>.json`；P0 无持久数据 | TUI 启动报「无后端」，无残留 state |

## 8. 开放问题

| ID | 问题 | 状态 |
|---|---|---|
| X1 | ADR-0015 §M0「agent runtime 随 agent 归档」是否禁止 v3 agent | **已确认（用户，2026-11-21）**：M0 仅界定 v1 实验面，不禁止 v3 在 kernel 之外新建 agent |
| X2 | 找不到 `molt-tui` 时 `exit 2` 还是回落 help | 待确认（建议 `exit 2`，与 D2 一致） |
| X3 | 动效 / 鼠标默认值 | 可后调；不影响本 ADR 接受 |
| X4 | 是否拆出 0026 TUI 专篇 | 待评审（当前 TUI 细节留在 spec §13） |

## 9. 与现有 ADR 的关系

| ADR | 关系 | 说明 |
|---|---|---|
| 0015 | 不冲突 | agent 在 kernel 之外；X1 已确认 M0 不禁止 |
| 0018 | 延伸 | 「单一 broker」由 0021 承接并落到 TS 模块 |
| 0019 | 不涉及 | 本 ADR 不引入 v1 读取 |
| 0021 | 下游 | broker 契约与 golden 对齐 |
| 0022 | 下游 | 会话树与回退由 agent 执行，TUI 只确认 / 展示 |
| 0023–0025 | 同批 | 权限、记忆、配置；本 ADR 只定层与边界 |
