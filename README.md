# molt

molt 为本地 Agent 工作流提供持久状态、可回退的版本提交和审计证据。Rust kernel 存储内容寻址对象和版本历史，并通过 Unix socket 提供事务接口。Python 客户端负责接入工作流。

## 从源码启动

下面的步骤在 Linux 上启动一个独立实例。需要 [uv](https://docs.astral.sh/uv/) 和 [Rust](https://rustup.rs/)。

在仓库根目录运行：

```bash
PYTHONDONTWRITEBYTECODE=1 uv sync --locked --python 3.12.13
cargo build --locked -p molt-kernel
molt_workspace=$(mktemp -d /tmp/molt-XXXXXX)
target/debug/molt-kernel init --state "$molt_workspace/state"
printf 'Socket: %s\n' "$molt_workspace/state/kernel.sock"
target/debug/molt-kernel serve --state "$molt_workspace/state"
```

保留服务终端，在另一个终端从同一源码目录运行，使用上一条命令显示的 socket 路径：

```bash
export MOLT_KERNEL_SOCKET=/tmp/molt-XXXXXX/state/kernel.sock
PYTHONDONTWRITEBYTECODE=1 uv run --locked molt inspect
PYTHONDONTWRITEBYTECODE=1 uv run --locked molt current
PYTHONDONTWRITEBYTECODE=1 uv run --locked molt audit
```

要尝试一次完整的上传、提交和查询，跟随[事务示例](docs/operations/external-integration.md)。提交结果未知时，先用 `molt request-status` 查询原 request ID，恢复时复用原 ID 和参数。

## 版本管理与模型审计

[配置和工具版本示例](docs/operations/synthetic-versions.md) 演示受控提案、配置和工具成组激活、命名分支、合并、回退，以及在途任务固定到启动时的版本。审计保留变更当时的判断和证据，离线模型可以生成有出处的摘要。示例从源码运行，支持随仓库提供的 echo 工具。接入其他工作流需要对应的配置校验、工具运行入口和权限配置。

## 使用与维护

| 目标 | 文档 |
| --- | --- |
| 查询、提交、审计和事务恢复 | [CLI 参考](docs/operations/kernel-cli.md) |
| 安装并常驻运行 | [构建 Linux 安装包](docs/operations/v2-release-checklist.md) · [用户服务](docs/operations/molt-kernel-systemd.md) |
| 备份、校验以及恢复到新目录 | [备份与恢复](docs/operations/v2-backup-restore.md) |
| 读取旧状态或显式迁移 | [v1 兼容与迁移](docs/operations/kernel-cli.md#v1-只读兼容与显式迁移) |
| 了解后端模块与边界 | [架构](docs/architecture.md) |
| 构建候选安装包与本地验证 | [贡献指南](CONTRIBUTING.md) |

`1.1.x` 保留 v1 状态的只读审计与显式迁移。外部业务写入有自己的事务和生命周期。molt 的回退仅切换本地状态。

## 开发

开发与审阅约定见 [AGENTS.md](AGENTS.md) 和 [贡献指南](CONTRIBUTING.md)。
