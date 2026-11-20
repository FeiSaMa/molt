# 通用 kernel CLI

这是当前源码的入口说明；旧 `1.1.0` 发行包的命令与证据不能替代本次源码或新包验收。
CLI 不运行 Agent。比赛专用 `search/propose/calibrate/demo/genome/scenario/gate/adopt/revert`
已退出命令注册；比赛资产的实际删除仍等待完整清单确认。

## 连接、对象与提交

在独立测试目录用 `molt-kernel init --state /absolute/new/state` 初始化，
再用 `molt-kernel serve --state /absolute/new/state` 启动服务。
Python 命令使用 `PYTHONDONTWRITEBYTECODE=1 uv run --python 3.12.13 python -m molt`；
下表用 `molt` 简写。socket 可由全局 `--socket PATH` 或 `MOLT_KERNEL_SOCKET` 提供。
显式参数优先；socket 对应的实例决定 state，通用 UDS 命令不接受逻辑 `--state`。

| 命令 | 行为 |
| --- | --- |
| `molt inspect` / `molt current` | 查看 state 身份 / 当前 generation |
| `molt object put /path/to/file` | 分块上传不透明对象，返回 hash；不执行文件内容 |
| `molt object get HASH --out /path/to/new/file` | 读取到新文件，拒绝覆盖已有路径 |
| `molt manifest create /path/to/roots.json` | roots JSON 直接交给 kernel 校验，返回 manifest hash |
| `molt manifest get HASH` | 查询已存 manifest |
| `molt commit --manifest HASH --request-id ID --at RFC3339` | 提交 generation；ID 和时间由调用者显式固定 |
| `molt request-status ID` | 按原 ID 查询持久提交状态 |
| `molt audit` | kernel 对象、日志和指针的完整性审计 |

`roots.json` 的示例（hash、size 必须匹配实际上传对象）：

```json
{"workspace":{"synthetic.json":{"type":"file","mode":384,"size":2,"sha256":"sha256:<64 hex>"}}}
```

CLI 输出 JSON；参数或前置条件不满足返回 2，运行失败返回非零。
`commit` 的同 ID、同 manifest、同时间重试返回原 generation；改变参数产生 `request_conflict`。
连接中断、超时或 I/O 错误可能发生在持久提交之后，CLI 报 `commit outcome unknown`，不会自动重试。
先用原 ID 查询；`committed` 带原结果，`not_found` 带 `retry_with_same_request_id=true`。
查询不可用时继续保持结果未知，恢复服务后再查；不能换 ID 盲重试。
`request-status` 的 kernel 实现会先执行持久事务恢复，因此它不是磁盘取证意义的零写入接口。

kernel 幂等不等于外部业务恰好执行一次；本地 generation 回退不等于外部操作取消。
本地哈希链和 audit 只检查内部一致性，不证明执行真实性；不声称 tamper-proof。

## 离线恢复与 checkpoint

以下命令调用现有 Rust 二进制，路径来自 `MOLT_KERNEL_BIN` 或 PATH 中的 `molt-kernel`。
`--state` 是实际磁盘路径。恢复和备份前停止该测试 state 的服务，不修改正式服务。

```bash
molt checkpoint export --state /absolute/state --out /absolute/new/checkpoint.json \
  --created-at 2026-11-19T00:00:00Z
molt audit --state /absolute/restored-state --checkpoint /absolute/checkpoint.json
molt recover --state /absolute/restored-state
```

checkpoint 导出拒绝覆盖；checkpoint 本身不包含对象数据。备份需在服务停止后复制完整 state，
恢复到独立空目录并审计；详细步骤见 [备份与恢复](v2-backup-restore.md)。
外部 checkpoint 的保管者和可信来源必须独立确认，不能把本地导出等同外部可信见证。

## v1 只读兼容与显式迁移

整个 `1.1.x` 保留 v1 只读语义重算与显式迁移，不原地改冻结 schema。
审计必须提供既有 state 和 scenario JSON；旧 scenario id/`--shell` 别名仅在对应配置仍存在时可解析，
不加载 shell、verifier 或 runtime，也不自动生成任何审计输入。

```bash
MOLT_KERNEL_BACKEND=v1 molt audit --state /path/to/v1-state --scenario /path/to/scenario.json
molt migrate --state /path/to/v1-state
molt migrate --state /path/to/v1-state --apply --migration-id stable-import-id
```

迁移连接目标 socket，缺省仅 dry-run；`--apply` 必须有稳定 migration ID。
预览和迁移均不修改源 state；同 ID 不能用于不同源快照。v1 模式拒绝恢复、提交及其他写命令，
服务失败时绝不静默回写 v1。迁移不改变历史 `fixture/replay/live` 标签或 schema 语义。
