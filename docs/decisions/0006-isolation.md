# ADR 0006：Pi 1.5.0 隔离、trust 与账本

- 状态：已决定
- 日期：2026-11-10
- 关联：`molt/adapters/pi/`、`shells/code-repair/harness.py`、
  [ADR-0002](0002-protocol-v1.md)、[ADR-0003](0003-scoring.md)

## 1. argv 契约

一次 run 固定：

```
pi -p --mode json --approve --session-id <safe> --session-dir <abs> \
   --model <provider/model> --thinking <off|low|high> \
   --tools bash <prompt>
```

禁止 `--no-session`、`--continue`、`--resume`；禁止 no-tools 旗标。
argv 固定带 `--no-mcp` 与 `--no-extensions`，避免 MCP/扩展绕过 `--tools bash` 注册额外工具。
`--session-id` 按 Pi 规则规范化（首尾字母/数字）。

B2b 后 `tools` 锁定为单值 `bash`；`harness.py::tools_argv_for("bash")` 产出
`["--tools", "bash"]`，adapter 只透传。read/grep/find/ls/write/edit 全部不启用，
agent 的所有读写都必须走已 sandbox 的 bash；搜索空间因此是
`thinking × context = 3 × 2 = 6` 组，tools 不再是搜索维度。

## 2. bash 隔离：`.pi/settings.json` → shellPath → bwrap

每个 run 在 workdir 写：

```json
{"shellPath": "/abs/path/molt/adapters/pi/sandbox_bash.sh"}
```

wrapper 只做一件事：清理 proxy 环境变量后 `exec bwrap --unshare-net ...`。它：

- `--ro-bind /usr /bin /lib /lib64 /etc`，`--tmpfs /home /root /tmp`，`--bind "$WORKDIR"`，
  `--chdir "$WORKDIR"`，`/bin/bash "$@"`；
- 因此 bash 子进程无网络、无 proxy、看不到仓库 `bench/` / `web/` / `evidence/`；
- `WORKDIR` 与 `MOLT_SHELL_LOG` 由 adapter 注入，wrapper 在进入 sandbox 前记录调用；
- Pi 主进程本身不在 bwrap 内，用于模型客户端；**没有进程级 egress allowlist**，
  `provider_only` 是语义声明，不是已强制的网络策略。

`--approve` 只是一次性 trust 覆盖，不证明项目设置已加载。`trust.py` 必须用一次真实
`pi -p --mode json --approve` 让 bash 工具经 shellPath 执行一个无害的
`printf molt-trust-ok > preflight.txt`，并同时检查：

1. `preflight.txt` 存在；
2. `.molt/shell-path.log` 非空（证明 wrapper 被调用，而不是默认 bash）。

两者缺一即 `trust_missing`，不产生可采纳 run。缓存键 `(shell_version, settings_hash)`，
成功预检的 usage/cost 记录在 `run.ext.runtime.trust`。

## 3. 为什么只留 bash（实测，不是假设）

`read` / `grep` / `find` / `ls` 不经过 `shellPath`，bwrap 不覆盖它们。2026-11-10 实测：

```bash
pi -p --mode json --approve --session-id molt-read-probe-1 \
  --session-dir /tmp/pi-read-probe/sessions \
  --model deepseek/deepseek-flash --thinking off --tools read \
  "Use the read tool exactly once to read \
   /home/feisama/dev/molt/bench/holdout/holdout-even/task.json. \
   Reply with the value of task_id only."
```

模型成功返回 `holdout-even`，usage `totalTokens=3110`、`cost.total=0.000107796`，
session 文件 `/tmp/pi-read-probe/sessions/...jsonl`。结论：读工具能读 holdout。
最终选择不是只排除读工具，而是用 `--tools bash` 把 read/grep/find/ls/write/edit 全部关掉，
agent 只能通过 sandboxed bash 读写。这样 claim 可以说「agent 工具只剩 bash」，
而不是「读工具单独有例外」。

## 4. 账本与预算

- 解析 session JSONL v3 的全部 usage-bearing entry：assistant message、`usage`、
  `compaction`、未知类型；按 entry id 去重；`compaction.usage` 计入总额。
- 成本链：`cost.totalFromProvider`（数值/true）→ `cost.total`（目录估算）→ 本地目录价重算；
  `cost_source` 记录 `provider|estimate|mixed|none`。
- 与 `--mode json` 的 assistant `message_end` 总计交叉校验；token 偏差 >1 或成本偏差
  >1e-5 ⇒ `ledger_mismatch`，run 记 invalid。
- 预算：wall 由 adapter 超时 kill（进程组 SIGKILL，记 invalid，账本成本照算）；
  turns 按 assistant message 计数；tokens 按账本总量；超限均 invalid。demo 并发 1。
- Verifier 的 before 指纹由 `Harness.run` 在 runtime 启动前调用 `Verifier.guard` 取得，
  agent 结束后只取 after；测试目录或题面被改写即 `matches=false`、run 判废。

## 5. Project trust 与降级

- 正常路径：`project_trust=cli_approve`（每次调用带 `--approve`）。
- 无 wrapper / bwrap 不可用：降级 `network=open` ⇒ `isolation.level=permissions-only`、
  `degraded=true`、`claim_marker="sandbox unavailable"`；页面与 claim 必须写明。
- wrapper 存在但 trust 预检失败：`project_trust=trust_missing`，run invalid，不启动 Pi。
- claim 口径：agent 只可用 sandboxed bash 读写；bash 无网、无 proxy、看不到
  repo/bench/web/evidence；Pi 主进程无进程级 egress allowlist；verifier before/after 哈希；
  tools 锁定 bash，搜索空间 6 组。

## 6. schema 映射

B1 的 `run` 顶层冻结，没有 `runtime` 字段；D2 的 `run.runtime.*` 一律写入
`run.ext.runtime`（`provenance=live`、`project_trust`、`isolation`、`session_path`、
`log_ref`、`stderr_ref`、`argv`、`ledger`、`budget`）。`run.usage` 仍是执行侧唯一成本来源。

## 7. 协议域收窄说明

B2b 裁决 A 把 `tools` 从 `{default, lean}` 收窄为单值 `{bash}`，因此搜索空间从 12 组
变为 6 组。顶层字段没有新增/删除，`schema_version` 仍为 1.0.0；该收窄随
`scenario_version=demo-4` 生效，禁止与 `demo-3` 及更早的 12 组结果跨版本比较。
