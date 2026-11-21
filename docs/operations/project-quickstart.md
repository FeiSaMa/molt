# Project workspace quickstart / 项目工作区快速开始

Run this example on Linux x86_64 with Bash, jq, [uv](https://docs.astral.sh/uv/),
and [Rust](https://rustup.rs/). The build uses Python 3.12.13 and the repository's
pinned Rust toolchain. The example starts its own kernel and uses synthetic
files. It requires no model account or provider configuration.

在 Linux x86_64 上准备 Bash、jq、uv 和 Rust。构建使用 Python 3.12.13 和仓库固定的 Rust
工具链。示例启动独立 kernel 并使用合成文件，无需模型账号或 provider 配置。

## Run the example / 运行示例

Clone the repository and run the following in Bash. 克隆仓库后在 Bash 中执行以下命令。

```bash
git clone https://github.com/FeiSaMa/molt.git
cd molt
(
  set -e
  build=$(mktemp -d /tmp/molt-build.XXXXXX)
  UV_PROJECT_ENVIRONMENT="$build/venv" uv sync --locked --python 3.12.13
  cargo build --locked -p molt-kernel --target-dir "$build/target"
  TMPDIR=/tmp bash examples/project-workspace.sh \
    "$build/venv/bin/molt" "$build/target/debug/molt-kernel"
)
```

The script prints two results after rollback. The previously pinned job reads
`full` and `Write a full report`. The new job reads `short` and
`Write a summary`. The audit output includes `Use the full report format`.

回退后，已固定快照的作业读取 `full` 和 `Write a full report`，新作业读取
`short` 和 `Write a summary`。审计输出会显示第二次保存的理由
`Use the full report format`。

The script stops its kernel on exit. The printed demo directory holds the save
receipts, the two pinned exports, the audit result, and `kernel.log`. Choose a
short temporary path if you change `TMPDIR`, as the kernel uses a Unix socket.

脚本退出时停止本次启动的 kernel。输出中的示例目录保留提交回执、两棵固定导出树、
审计结果和 `kernel.log`。如需自定义 `TMPDIR`，请使用短路径以容纳 Unix socket。

## Connect your own reader / 接入自己的读者

List the relative paths of your config, prompts, or tool definitions in the
project's `files` array. After a save, run
`molt project --config project.json read-tree` once when the job starts. Pass
the resulting `tree` path to the job and record `snapshot` with the job result.
Keep reading from that tree for the entire run.
[Project CLI guide](version-cli.md#项目级-cli)

在项目配置的 `files` 中列出配置、提示词或工具定义的相对路径。提交后，每个作业启动时
调用一次 `read-tree`，把返回的 `tree` 传给作业，并将 `snapshot` 和结果一并保存。
同一次运行始终使用这一棵树。[项目级 CLI 手册](version-cli.md#项目级-cli)

If a save is interrupted and the result is unknown, run
`molt project --config project.json recover` to query or recover the original
request. Rollback switches the active local snapshot. External side effects
follow the external system's own recovery process.
[Recovery guide](version-cli.md#断线查询与恢复)

提交中断且结果未知时，先运行 `recover` 查询或恢复原请求。回退切换本地活动快照，
外部操作的恢复由外部系统处理。[断线查询与恢复](version-cli.md#断线查询与恢复)

Share what worked, where you got stuck, and the version you tried.
[Independent trial template](independent-trial.md) · [Issues](https://github.com/FeiSaMa/molt/issues)
