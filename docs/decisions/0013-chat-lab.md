# ADR 0013：聊天执行工作台（隔离两轮 coding lab）

- 状态：**提案 + 本地实现（待评审，未合并）**
- 日期：2026-11-13
- 关联：`molt/agent/lab.py`、`molt/agent/workspace.py`、`molt/agent/lab_tasks.py`、
  `contracts/openapi-lab-v1.json`、`docs/api/chat-lab-api.md`、`web/chat/**`

## 背景

现有聊天 API（ADR-0012）只有 mock / openai-compatible 文本对话与两个只读实验工具；
`web/chat/` 只有实验档案和逐题对照。用户新授权三项普通 Agent 能力：

1. 真实 provider 的通用对话 / 代码分析；
2. 隔离 workspace 文件读写与运行代码测试；
3. 真实 round1 → evolution/patch → diff → round2。

同时服务器上的实际条件必须如实记录：

- 不存在 `MOLT_CHAT_MODEL` / `MOLT_CHAT_BASE_URL` / `MOLT_CHAT_API_KEY`；
- 服务器没有 `bwrap`，且 AppArmor 限制 unprivileged user namespace；
- 服务器上不能把任意 shell / 文件读取暴露给公网聊天。

## 决定

### 1. Provider 保持适配器边界

- 复用 `molt/agent/chat.py` 的 `openai-compatible` adapter；不新增第三方依赖。
- 没有 `MOLT_CHAT_MODEL` 时继续 mock；UI 必须显示 `MOCK`，mock 的 patch 标记
  `source=mock-deterministic`、`real_model=false`。
- 不把 mock 结果写成真实模型结果；不静默回退。

### 2. 新 lab API 与独立契约

- 新增 `/api/lab/**`：capabilities、tasks、runs、runs/{id}、events、cancel、openapi。
- 新契约 `contracts/openapi-lab-v1.json`；**不修改** `contracts/openapi-v1.json`
  的 11 条路由语义。
- 所有执行端点要求 Bearer；`/api/lab/openapi.json` 免鉴权。
- 新 API 的 HTML 消费方是 `web/chat/`；不改 `web/page/**`、`web/data/**`。

### 3. 隔离是上线执行端点的硬门槛

- 唯一执行后端是 `bwrap`：
  - `--unshare-all`、`--die-with-parent`、`--new-session`、`--cap-drop ALL`；
  - 只读 `/usr`、`/lib`、`/lib64`、`/bin`、`/sbin`；
  - 只写绑定本次 workspace；
  - `--clearenv`、固定 `PATH`、无网络命名空间；
  - 外层 `prlimit` + 命令超时。
- 启动自检必须验证：workspace 可写、`/srv/molt` 不可读、无网络、`/usr` 不可写。
- 自检失败 → `execution.available=false`，`POST /api/lab/runs` 返回
  503 `adapter_unavailable`（`details.reason=isolation_unavailable`），前端禁用按钮。
- 不提供任意 shell 参数；只运行固定的 `python3 -m unittest -q`。
- 文件工具只允许 workspace 内相对路径，拒绝 `..`、绝对路径、符号链接、NUL。

### 4. 两轮循环的口径

- 新 fixture `calc-v1` 与历史 demo/probe 数据分离；`provenance.kind=lab_fixture`、
  `historical=false`。
- 流程：seed → round1 真实测试 → patch（provider 或 mock 确定性）→ 真实 unified diff
  → round2 真实测试。
- 这不是引擎的 genome evolution，也不是“自进化”能力；页面和文档必须如此标注。
- 真实 provider patch 失败不降级为 mock；报告 `provider_error`。
- 结果 `adopted=round2.passed`；不声称统计显著性。

### 5. 并发、预算、取消

- 全局同时 1 个 lab run；忙 → 409 `conflict`（`details.reason=lab_busy`）。
- 每 run 最多 2 轮、2 次测试命令、60s 墙钟、单命令 15s、输出 16KB、
  单文件 64KB、总文件 256KB、最多 16 文件。
- `DELETE /api/lab/runs/{id}` 设置 cancel 并 kill 进程组；SSE 发
  `run.cancelled` + `done`。
- 完成后删除 workspace；结果保留在内存（最多 20 条摘要）。

### 6. 前端

- 左侧新增“两轮修复工作台”，显示 provider/isolation/evolution 真实状态、
  round1/round2、stdout 摘要、真实 diff、provenance。
- 视觉对齐线上 9 屏主页面：纸白、墨色、衬线标题、朱红/靖蓝、细星轨；
  不覆盖 `web/page/**`。
- 隔离不可用时按钮 disabled，显示准确阻断；不播放假 evolution 动画。

## 不做 / 当前阻断

- **服务器执行端点关闭**：服务器没有 `bwrap`，`unshare -Ur` 被 AppArmor 拒绝；
  因此公网只能看到 fail-closed 状态，不能执行代码。
- **真实 provider 未验收**：服务器没有 provider 配置；只能用 mock 做流程验证。
- 不实现任意 shell、任意文件读取、网络访问、多工具 ReAct、持久 workspace。
- 不实现引擎 genome evolution、adopt/revert、holdout 变更。
- 不修改 `molt/core/**`、`schemas/v1/**`、`bench/**`、`shells/**`、`web/data/**`。

## 评审与流程

- 新 API/契约属于契约域；按 `AGENTS.md` 需要 ADR；评审为建议，不设 approve 数量门禁。
- 本实现只在本地与部署分支存在，未 commit 到 `main`、未 push。
- 服务器部署保留 `/tmp/molt-chat-agent-backup-*` 回滚目录。

## 备选（未采用）

- 用 Docker/Podman 隔离：服务器无权限、无 rootless runtime。
- 用 systemd user `PrivateTmp` / `ProtectHome`：服务器实测被静默忽略。
- 用 proot 伪装隔离：不是安全边界，拒绝。
- 在 chat 里暴露任意 `bash` 工具：违反“不得向公网聊天暴露服务器任意 shell”。

## 2026-11-13 加固追加（不改上文决定）

- `UrllibTransport` 对上游响应体设 1 MiB 上限，`OpenAICompatibleAdapter` 对 content
  设 256 KiB 上限；lab provider patch 另设 64 KiB 上限。
- 单命令超时 / 墙钟超时直接 `run.failed`，不把超时当成普通测试失败继续 patch。
- 取消 kill 进程组；清理 workspace；SSE 断流后前端只以
  `GET /api/lab/runs/{id}` 的终止态解锁，避免重复执行。
- IME 组合输入期间 Enter 不触发发送。
- 以上均未新增 API path；`contracts/openapi-lab-v1.json` 仍为 6 条 path。
