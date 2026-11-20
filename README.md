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

安装后使用 `molt version` 初始化个人工作区、提交自己的配置与内置 echo 工具描述、切换或合并分支、回退活动版本。运行可以固定到已提交的快照。审计保留原始记录，已安装的离线模型可追加引用原始字段的摘要。独立安装、首次提交和断线恢复命令见[个人工作区手册](docs/operations/version-cli.md)。[源码示例](docs/operations/synthetic-versions.md)使用同一 broker。

### OpenCode 项目快照

先按[安装与首次运行](docs/operations/version-cli.md#安装与首次运行)在 Bash 中安装候选包并启动一个新工作区。执行到 `test "$ready" = 1` 为止，在同一终端继续。下面用合成配置演示保存、回退与审计。

```bash
edits="$base/edit"
exports="$base/exports"
mkdir -m 700 "$edits" "$exports"
printf '%s\n' '["opencode.json"]' > "$base/files.json"
printf '%s\n' '{"model":"isolated/demo"}' > "$edits/opencode.json"

batch() { v commit-files --root "$edits" --manifest "$base/files.json" "$@"; }
batch --expect-generation 0 --reason '建立试用基线' --reason-source 'local operator' > "$base/initial.json"
initial=$(jq -er '.snapshot' "$base/initial.json")

# 日常用法是在隔离 OpenCode 项目中编辑该文件，结束写入后提交。
printf '%s\n' '{"model":"isolated/edited"}' > "$edits/opencode.json"
batch --expect-generation 1 --expect-head "$initial" --activate \
  --reason '更新样例配置' --reason-source 'local operator' > "$base/edited.json"
v audit "$(jq -er '.event' "$base/edited.json")"

v rollback "$initial" --expect-generation 2
tree=$(v read-tree --into "$exports" | jq -er '.tree')
cat "$tree/opencode.json"
```

最后一条命令应显示 `{"model":"isolated/demo"}`。新 OpenCode 作业从 `$tree` 读取固定版本，后续编辑仍在 `$edits` 完成。清单可加入相对路径形式的 Agent 定义和文本工具，选用不含敏感信息的文件。示例 generation 对应连续执行的三次写入，实际操作前用 `v status` 查看当前值和分支头。隔离 OpenCode 读取、分支、合并和离线摘要见[个人工作区手册](docs/operations/version-cli.md#隔离-opencode-项目)。

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
