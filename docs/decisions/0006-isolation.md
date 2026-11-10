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
   [--exclude-tools <list>] <prompt>
```

禁止 `--no-session`、`--continue`、`--resume`；禁止 no-tools 旗标。
`--session-id` 按 Pi 规则规范化（首尾字母/数字）。`tools_mode` 映射由 shell 产出：

| mode | harness 产出 |
| --- | --- |
| `default` | 无旗标 |
| `lean` | `--exclude-tools write` |

`--exclude-tools` 在 Pi 1.5.0 是「逗号列表、后一次覆盖前一次」，因此 adapter 把 lean 的
`write` 与内建读工具排除合并成**一次** `--exclude-tools`：

- default：`--exclude-tools read,grep,find,ls`
- lean：`--exclude-tools write,read,grep,find,ls`

这样 tools 维度真实生效，且不产生任何 no-tools 旗标。

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
- Pi 主进程本身不在 bwrap 内，保留 `provider_only` 网络以调用模型。

`--approve` 只是一次性 trust 覆盖，不证明项目设置已加载。`trust.py` 必须用一次真实
`pi -p --mode json --approve` 让 bash 工具经 shellPath 执行一个无害的
`printf molt-trust-ok > preflight.txt`，并同时检查：

1. `preflight.txt` 存在；
2. `.molt/shell-path.log` 非空（证明 wrapper 被调用，而不是默认 bash）。

两者缺一即 `trust_missing`，不产生可采纳 run。缓存键 `(shell_version, settings_hash)`，
成功预检的 usage/cost 记录在 `run.ext.runtime.trust`。

## 3. 内建读工具例外（实测，不是假设）

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
session 文件 `/tmp/pi-read-probe/sessions/...jsonl`。结论：读工具能读 holdout，
必须在 argv 排除。最终实现固定 `--exclude-tools read,grep,find,ls`（lean 再加 `write`），
读操作改走已 sandbox 的 bash。该例外影响 claim：受保护的是 bash 与网络边界，不是
「所有工具都经过 bwrap」。

## 4. 账本与预算

- 解析 session JSONL v3 的全部 usage-bearing entry：assistant message、`usage`、
  `compaction`、未知类型；按 entry id 去重；`compaction.usage` 计入总额。
- 成本链：`cost.totalFromProvider`（数值/true）→ `cost.total`（目录估算）→ 本地目录价重算；
  `cost_source` 记录 `provider|estimate|mixed|none`。
- 与 `--mode json` 的 assistant `message_end` 总计交叉校验；token 偏差 >1 或成本偏差
  >1e-5 ⇒ `ledger_mismatch`，run 记 invalid。
- 预算：wall 由 adapter 超时 kill（进程组 SIGKILL，记 invalid，账本成本照算）；
  turns 按 assistant message 计数；tokens 按账本总量；超限均 invalid。demo 并发 1。

## 5. Project trust 与降级

- 正常路径：`project_trust=cli_approve`（每次调用带 `--approve`）。
- 无 wrapper / bwrap 不可用：降级 `network=open` ⇒ `isolation.level=permissions-only`、
  `degraded=true`、`claim_marker="sandbox unavailable"`；页面与 claim 必须写明。
- wrapper 存在但 trust 预检失败：`project_trust=trust_missing`，run invalid，不启动 Pi。

## 6. schema 映射

B1 的 `run` 顶层冻结，没有 `runtime` 字段；D2 的 `run.runtime.*` 一律写入
`run.ext.runtime`（`provenance=live`、`project_trust`、`isolation`、`session_path`、
`log_ref`、`stderr_ref`、`argv`、`ledger`、`budget`）。`run.usage` 仍是执行侧唯一成本来源。
