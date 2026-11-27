# Project workspace quickstart / 项目工作区快速开始

Run this example on Linux x86_64 with Bash, jq, [uv](https://docs.astral.sh/uv/),
and Python 3.12.13. The example starts its own kernel and uses synthetic files.
It requires no model account or provider configuration.

在 Linux x86_64 上准备 Bash、jq、uv 和 Python 3.12.13。
示例启动独立 kernel 并使用合成文件，无需模型账号或 provider 配置。

## Run the example / 运行示例

### From a Linux bundle / 使用 Linux 安装包

Verify the archive against its checksum, unpack it, and run from the extracted directory.
Use a bundle that contains `share/molt/examples/project-workspace.sh` and
`share/molt/examples/project-reader.mjs`. For the published v1.2.0rc9 archive,
use the source instructions below for this walkthrough.
核对归档的 SHA-256 后解包，在解包目录执行以下命令。
请选择包含上述两个示例文件的候选包。若拿到的是公开发布的 v1.2.0rc9 归档，
请用下方的源码方式完成本演练。

```bash
(
  set -e
  demo_env=$(mktemp -d /tmp/molt-demo-env.XXXXXX)
  export PYTHONDONTWRITEBYTECODE=1
  uv venv --python 3.12.13 "$demo_env/venv"
  uv pip install --no-index --python "$demo_env/venv/bin/python" ./wheels/molt-*.whl
  TMPDIR=/tmp bash ./share/molt/examples/project-workspace.sh \
    "$demo_env/venv/bin/molt" ./bin/molt-kernel
)
```

### From source / 使用源码

Install [Rust](https://rustup.rs/) to build with the repository's pinned toolchain.
Clone the repository and run the following in Bash.
安装 Rust 后克隆仓库，在 Bash 中执行以下命令。

```bash
git clone https://github.com/FeiSaMa/molt.git
cd molt
(
  set -e
  build=$(mktemp -d /tmp/molt-build.XXXXXX)
  export PYTHONDONTWRITEBYTECODE=1
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

### Use a Node.js reader / 接入 Node.js 作业

The CLI contract also works from Node.js without a Molt package dependency.
Start your project's kernel and save a version before running the reader.
Pass your Molt executable, project config and the UTF-8 files you want to read.
In the extracted bundle, use `share/molt/examples/project-reader.mjs`.

先启动项目的 kernel 并保存一个版本，再传入 Molt 可执行文件、项目配置和要读取的 UTF-8
文件。解包后请使用 `share/molt/examples/project-reader.mjs`。

```bash
# From the source checkout / 在源码目录运行
node examples/project-reader.mjs /path/to/molt ./project.json settings.json prompt.txt

# From the extracted candidate / 在解包后的候选包目录运行
node share/molt/examples/project-reader.mjs /path/to/molt ./project.json settings.json prompt.txt
```

Replace the executable, config path and file names with those for your project.
The quickstart stops its demo kernel when it exits.
请替换为自己项目的可执行文件、配置路径和受管文件名。快速开始示例退出时会停止演示 kernel。

An application can import the function and parse its own file formats.
应用可以导入函数并按自己的格式解析文件。

```js
import { readPinnedProject } from "./examples/project-reader.mjs";

const pin = readPinnedProject(process.env.MOLT_BIN, "./project.json");
const settings = JSON.parse(pin.files["settings.json"]);
startJob({ settings, instructions: pin.files["prompt.txt"], snapshot: pin.snapshot });
```

The example calls `read-tree` once, reads the selected paths from the
`project.json` file list and returns the snapshot ID with the contents.
Store that ID with the job record.

示例只调用一次 `read-tree`，从固定目录读取 `project.json` 清单中选定的路径。
返回的快照 ID 可随作业结果保存。

If a save is interrupted and the result is unknown, run
`molt project --config project.json recover` to query or recover the original
request. Rollback switches the active local snapshot. External side effects
follow the external system's own recovery process.
[Recovery guide](version-cli.md#断线查询与恢复)

提交中断且结果未知时，先运行 `recover` 查询或恢复原请求。回退切换本地活动快照，
外部操作的恢复由外部系统处理。[断线查询与恢复](version-cli.md#断线查询与恢复)

Share what worked, where you got stuck, and the version you tried.
[Independent trial template](independent-trial.md) · [Issues](https://github.com/FeiSaMa/molt/issues)
