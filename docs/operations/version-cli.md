# Agent 项目工作区

`molt project` 把一组受管文件保存为快照。作业启动时调用一次 `read-tree`，
从返回的目录读取整组文件，就能固定本次使用的版本。下面的入门示例用 Bash 和 jq 作读者。
`molt version` 用于初始化、分支与合并、审计、导出和低阶恢复。两者通过同一个 broker
管理 Linux x86_64 上的单用户工作区。每个工作区有自己的 kernel state、Unix socket 和
broker journal。工作区须由当前用户独占，路径要短到能容纳 `/state/kernel.sock`。

初次使用先安装并启动独立工作区，再到[项目级 CLI](#项目级-cli)保存与读取文件。
OpenCode 用户另见[隔离 OpenCode 项目](#隔离-opencode-项目)。如从候选包安装，按其来源
和校验和辨认具体的候选归档，并用 `molt project --help` 核对入口。

## 安装与首次运行

准备 Bash、uv、Python 3.12.13 和 jq。选下列一种安装方式，随后在同一 Bash 会话中启动工作区。
从候选包安装时，在解包目录执行以下命令。

```bash
set -e
bundle=$PWD
base=$(mktemp -d "${TMPDIR:-/tmp}/molt-version.XXXXXX")
uv venv --python 3.12.13 "$base/venv"
uv pip install --python "$base/venv/bin/python" "$bundle"/wheels/molt-*.whl
kernel="$bundle/bin/molt-kernel"
cli="$base/venv/bin/molt"
```

从源码试用时，在仓库根目录执行以下命令。需要 Rust 工具链。依赖、编译产物和工作区都放在临时目录。

```bash
set -e
base=$(mktemp -d "${TMPDIR:-/tmp}/molt-version.XXXXXX")
PYTHONDONTWRITEBYTECODE=1 UV_PROJECT_ENVIRONMENT="$base/venv" uv sync --locked --python 3.12.13
cargo build --locked -p molt-kernel --target-dir "$base/target"
cli="$base/venv/bin/molt"
kernel="$base/target/debug/molt-kernel"
```

启动仅服务于本例的工作区。下面的样例数据标为 `synthetic`。

```bash
workspace="$base/w"
v() { "$cli" version --workspace "$workspace" "$@"; }

v init --kernel "$kernel" --synthetic
"$cli" version --workspace "$workspace" serve --kernel "$kernel" >"$base/kernel.log" 2>&1 &
kernel_pid=$!
trap 'kill "$kernel_pid" 2>/dev/null || true; wait "$kernel_pid" || true' EXIT
ready=
for attempt in {1..100}; do
  if v status 2>/dev/null; then ready=1; break; fi
  kill -0 "$kernel_pid"
  sleep .05
done
test "$ready" = 1
```

管理普通文件时，直接继续[项目级 CLI](#项目级-cli)。要体验内置 echo 工具的配置校验与运行，可在空工作区走下面这条示例。

### 内置 echo 工具示例

```bash
printf '%s\n' '{"reply_format":"plain","prefix":"demo: "}' > "$base/config.json"
printf '%s\n' '{"tool":"echo","output_format":"plain"}' > "$base/tool.json"
v commit --config "$base/config.json" --tool "$base/tool.json" --expect-generation 0 > "$base/first.json"
first=$(jq -r .snapshot "$base/first.json")
event=$(jq -r .event "$base/first.json")
v run 'hello'
v history
v show "$first"
v audit "$event"
```

为项目的实际配置初始化工作区时省略 `--synthetic`。首个提交创建 `main` 并激活。
配置接受 `reply_format`（`plain` 或 `json`）及可选的 `prefix`（最多 120 个字符）。
工具接受 `tool: "echo"` 和 `output_format`（`plain`、`json` 或 `config`）。
固定格式必须与配置匹配，`config` 意味着随配置决定实际输出。
快照记录配置、工具描述与内置运行时依赖的内容身份，运行前验证这三项。
内置工具仅处理传入的文本。JSON 格式输出含 `echo` 键。

继续用这一工作区创建分支、合并，并核对跨回退的 pin。下列 generation
基于上一步完成后没有其他写入。

```bash
v branch topic "$first" --expect-generation 1
printf '%s\n' '{"tool":"echo","output_format":"config"}' > "$base/tool.json"
v commit --branch topic --config "$base/config.json" --tool "$base/tool.json" \
  --expect-generation 2 --expect-head "$first" > "$base/topic.json"
topic=$(jq -r .snapshot "$base/topic.json")
v merge main topic --expect-generation 3 --expect-target "$first" --expect-source "$topic" \
  --activate > "$base/merged.json"
v pin > "$base/pin.json"
v rollback "$first" --expect-generation 4
v run hello --pin "$base/pin.json"
```

使用 `status` 获取当前 generation 和各分支头。并发写入、失效的预期值或
合并冲突返回 3。输入、配对验证和工作区校验错误返回 2，运行错误返回 1。
`switch BRANCH --expect-generation N --expect-head SNAPSHOT` 激活该分支头。
回退只改变活动快照，分支头与已取得的运行 pin 保持原值。

## 项目级 CLI

在新工作区执行首节到 `test "$ready" = 1`，然后在同一个 Bash 会话运行以下命令。
这里的两份文件模拟作业配置与提示词。读者只接收一次导出的目录路径。

```bash
mkdir -m 700 "$base/edit" "$base/exports"
printf '%s\n' '{"workspace":"w","edits":"edit","exports":"exports","files":["settings.json","instructions.md"]}' > "$base/project.json"
p() { "$cli" project --config "$base/project.json" "$@"; }
edit=$(p begin | jq -er '.edit')
printf '%s\n' '{"format":"short"}' > "$edit/settings.json"
printf '%s\n' '写一段摘要' > "$edit/instructions.md"
p preview
first=$(p save --reason '先验证简短输出' | jq -er '.snapshot')

printf '%s\n' '{"format":"full"}' > "$edit/settings.json"
printf '%s\n' '写一份完整报告' > "$edit/instructions.md"
p save --reason '批处理作业需要完整报告' > "$base/second.json"
v audit "$(jq -er '.event' "$base/second.json")"
pinned=$(p read-tree | jq -er '.tree')
p rollback "$first"
current=$(p read-tree | jq -er '.tree')

read_job() { jq -r '.format' "$1/settings.json"; cat "$1/instructions.md"; }
read_job "$pinned"
read_job "$current"
```

第一次读取输出 `full` 和“写一份完整报告”，第二次输出 `short` 和“写一段摘要”。
接入自己的程序时，把同一个目录传给读取这些文件的进程。程序自行解释文件内容。
需要追溯一次运行时，可保存这次 `read-tree` 输出中的 `snapshot` 和 `tree`，以便按作业查回版本。

相对目录以项目配置文件所在目录为基准。

| 字段 | 用途 |
| --- | --- |
| `workspace` | 由 `molt version init` 创建的独立版本工作区 |
| `edits` | 权限 0700 的专用父目录，每次 `begin` 创建新的可写项目 |
| `exports` | 权限 0700 的专用父目录，每次 `read-tree` 或 `opencode` 固定一棵新树 |
| `files` | 允许纳入快照的相对文件路径，见[文件快照与导出](#文件快照与导出) |

`begin [--branch BRANCH]` 输出编辑目录，从指定分支头复制，默认分支为 `main`。
`preview` 输出增删改路径。结束对编辑目录的写入后，执行 `save --reason TEXT`。
可用 `--reason-source TEXT` 记录理由来源，默认 `local operator`。
清单内缺失路径视为删除，清单外的文件不纳入快照。保存成功后可在同目录继续编辑。
无变更的保存返回 `changed: false`。提交证据可用 `v audit EVENT` 查询。

`switch BRANCH` 和 `rollback SNAPSHOT` 修改活动快照。回退时以分支头作为后续
`begin` 的编辑基线，若要从回退点继续开发，可用 `v branch` 从该快照建立新分支，
然后 `begin --branch BRANCH`。旧导出和编辑目录都可继续查看。

返回码 3 表示编辑目录未保存、预期 generation 或分支头已变化，或项目锁正被占用。
检查 `preview` 与 `v status`。需要按新基线重新编辑时，用
`begin --keep-old [--branch BRANCH]` 保留旧目录并输出 `previous_edit`，再手动迁移所需改动。
程序不会更新已有编辑目录。

保存返回 4 或进程意外中断时，先运行 `p recover`。恢复通过 broker 查询原 request ID，
必要时原样重放。已提交时会将该批次推进为新编辑基线，并保留中断后的本地修改。
若进程在写入 broker 意图之前退出，查询确认 `not_found` 后才可重新保存。
低阶诊断使用 `v status`、`v request-status REQUEST_ID` 和本手册的恢复流程。

### 可选的 OpenCode 启动入口

若项目的受管清单包含 OpenCode 所需配置，可用
`p opencode -- debug config` 和 `p opencode -- debug agents` 读取固定快照。
对配置了可用 provider 的项目，用 `p opencode -- run '任务内容'` 启动 OpenCode v2。
使用 PATH 中的 `opencode`，在新只读导出目录中以 `--standalone` 前台运行。
固定的快照 ID 和目录写到 stderr，OpenCode 接管终端、标准输出和退出码。
每次作业使用新的 HOME 和 XDG 目录，可用 `run --help` 查看参数。连接已有服务、
复用已有会话和切换工作目录的参数不适用于此入口。新作业在启动时固定一棵树，
已启动的作业可继续读取旧导出目录。常驻 OpenCode 的热重载尚未验收。

该入口继承大部分进程环境，并为 OpenCode 重设配置与状态目录。如需限制网络或宿主
文件访问，可在 PATH 配置受控启动器。下节提供断网的读取示例。

## 隔离 OpenCode 项目

执行首节安装与启动命令，到 `test "$ready" = 1` 为止。用新的工作区，
从受管文件快照开始。现有 echo 工作区继续使用原命令。

```bash
edits="$base/edit"
exports="$base/exports"
mkdir -m 700 "$edits" "$exports"
mkdir -p "$edits/.opencode/agents"
printf '%s\n' '{"model":"isolated/demo"}' > "$edits/opencode.json"
cat > "$edits/.opencode/agents/reader.md" <<'MARKDOWN'
---
description: isolated demo reader
mode: subagent
---
Read the synthetic marker.
MARKDOWN
printf '%s\n' '["opencode.json",".opencode/agents/reader.md"]' > "$base/files.json"
batch() { v commit-files --root "$edits" --manifest "$base/files.json" "$@"; }
batch --expect-generation 0 --reason '初始化两份样例' --reason-source 'local operator' > "$base/initial.json"
initial=$(jq -er '.snapshot' "$base/initial.json")

# 编辑结束后，显式提交这一批文件。
printf '%s\n' '{"model":"isolated/edited"}' > "$edits/opencode.json"
batch --expect-generation 1 --expect-head "$initial" --activate \
  --reason '切换隔离样例配置' --reason-source 'local operator' > "$base/edited.json"
edited=$(jq -er '.snapshot' "$base/edited.json")
event=$(jq -er '.event' "$base/edited.json")
v audit "$event"

read_tree() { v read-tree --into "$exports" | jq -er '.tree'; }
pinned=$(read_tree)
v export "$initial" --into "$exports"
v rollback "$initial" --expect-generation 2
active=$(read_tree)
cmp "$pinned/opencode.json" "$edits/opencode.json"
jq -e '.model == "isolated/demo"' "$active/opencode.json"
test "$(jq -r '.branches.main' < <(v status))" = "$edited"
```

用 Arch Linux 上已安装的 OpenCode v2.3.2 读取刚固定的目录。每次调用以一棵树
为项目，配置与文本定义都从该目录读取。Bubblewrap 为该次调用提供临时的配置、
数据和状态目录。首次读取与切换后的读取各起一个进程。

```bash
opencode_read() {
  local tree=$1
  shift
  bwrap --unshare-all --unshare-user --disable-userns --die-with-parent --new-session \
    --cap-drop ALL --clearenv --setenv PATH /usr/bin --setenv HOME /tmp/home \
    --setenv XDG_CONFIG_HOME /tmp/config --setenv XDG_DATA_HOME /tmp/data \
    --setenv XDG_CACHE_HOME /tmp/cache --setenv XDG_STATE_HOME /tmp/state \
    --setenv OPENCODE_DISABLE_AUTOUPDATE true --setenv OPENCODE_DISABLE_MODELS_FETCH true \
    --ro-bind /usr/bin /usr/bin --ro-bind /usr/lib /usr/lib \
    --symlink usr/lib /lib --symlink usr/lib /lib64 --dev /dev --proc /proc \
    --tmpfs /tmp --dir /tmp/home --ro-bind "$tree" /project --chdir /project \
    -- /usr/bin/opencode debug "$@"
}
opencode_read "$pinned" config
opencode_read "$active" config
opencode_read "$active" agents > "$base/active-agents.json"
```

## 文件快照与导出

项目配置的 `files` 和低阶命令 `v commit-files --manifest` 使用相同的文件边界。清单是相对路径的 JSON
数组，最多 64 个常规文件，单个最多 256 KiB，合计最多 2 MiB。
导入按清单读取实际字节，不跟随符号链接，不接受硬链接和特殊文件。
停用编辑器和其他写入者后再捕获，不把凭据或敏感原文列入清单。
使用低阶 `commit-files` 时，从清单中移除路径可以提交该文件的删除，编辑目录中的原文件由操作者管理。
同一路径的不同修改、删除对修改及目录与文件重叠会导致合并冲突。
冲突退出码为 3，诊断在 stderr，历史中没有失败合并事件。

`read-tree` 从 kernel 已提交的索引固定一次活动版本。`export` 使用显式快照 ID。
两者都在专用 0700 父目录下准备新树，完成后发布 `project` 并返回路径。
失败可以重试，新调用使用新的目录。中断可能留下未发布的 `preparing`。
返回的树为只读权限，隔离调用额外挂载为只读。保留树直到对应读者结束。
切换和回退只改 kernel 的活动选择，新作业重新读取即可。已有进程和编辑目录
继续使用各自的文件。

批次必须给出预期 generation，已有分支再给出预期 head。
首次提交自动激活，后续通过 `--activate` 或显式 `switch` 激活。
文件与分支头相同时返回 `changed: false` 和空事件。需要激活时单独调用
`switch`。理由与来源一起提供，作为操作者陈述随事件提交。省略时审计显示理由缺失。
每次成功写入会推进 generation。摘要也是写入，`history` 只列版本事件。

## 离线提案与摘要

`v digest "$event"` 追加由原始记录渲染的摘要。若要生成推断，传
`--model /absolute/path/to/existing.gguf --model-id MODEL_VERSION`。
`propose BRANCH` 使用相同模型参数，并要求 `--expect-generation`、
`--expect-head`、`--task` 和本地操作者的 `--approval`。可用 `--sources`
提供受批准的短文本 JSON 对象。使用 `v propose --help` 查看命令。
提案通过校验和前置条件检查后成为新快照。模型判断保存为带生成主体的原始提案记录。
摘要引用标明原始事件或可重算投影。投影列出动作、目标快照、分支头是否变化、
理由有无及逐项改动。受管文件投影只含路径、大小和摘要，理由原文可从事件查看。
模型推断、冲突和缺口带 `verified: false`。`audit EVENT` 可读原始证据、
投影和历次摘要，历史 v2 摘要的输入可从 `legacy_projection_v2` 重算。

模型能力另需 `/usr/bin/bwrap`、可用的 Linux 用户 namespace、
`/usr/bin/llama-completion` 和已安装且兼容的 GGUF。现有提示模板面向
支持 ChatML 对话标记的模型。明确批准给本地模型的配置、任务及证据字段才应进入模型。
含凭据或敏感原文的内容应在投影前去除。模型、隔离或回链校验失败会停止此次写入，
原始记录仍可读取。引用符合字段约束并不证明模型推断正确。

## 断线查询与恢复

提交断线返回 4 时，使用 stderr 或 `status` 里的待恢复 ID。
恢复前确保已用 `v serve --kernel "$kernel"` 重新启动原工作区服务。

```bash
v status > "$base/status.json"
request=$(jq -er '.pending_request' "$base/status.json")
v request-status "$request"
v recover
v request-status "$request"
v status
```

查询为 `committed` 时 broker 核对原结果并完成 journal。仅在 kernel 明确允许
原 ID 重试且原 generation 仍匹配时，`recover` 重放已持久化的参数。
结果仍未知时保留原 intent，等状态恢复可判定后继续查询。

## 工作区备份与恢复

约定停止所有工作区写入者，再停止该工作区的 kernel。备份整个目录及权限，
包括 kernel state 的对象与日志和 `broker.json`。保护复制到工作区之外的 pin 文件。
`broker.lock` 可随目录复制，锁的持有状态由进程决定。`state/kernel.sock`
由服务重建，复制时排除该 socket。

使用 `molt project` 时，下面的 tar 只归档 `workspace`。项目配置还指定了独立的
`edits` 和 `exports` 目录。未保存的修改只在编辑目录中；仍有作业使用的旧导出也在
工作区外。需要保留这些内容时，在停止写入后另行备份项目配置及相应目录，保留权限。
已提交文件可以从对应快照重新导出。

工作区内的 `project-session.json` 保存当前编辑的绝对路径，会话还校验编辑目录、
导出目录的解析路径和受管文件清单。迁到新根目录后，即使 kernel 审计通过，也要单独
核对原编辑与导出是否可读，以及能否继续原项目会话。以下示例用 tar 将版本工作区恢复到新目录。

```bash
kill "$kernel_pid"
wait "$kernel_pid" || true
trap - EXIT
backup=$(mktemp -d "$base/backup.XXXXXX")
"$kernel" checkpoint export --state "$workspace/state" \
  --out "$backup/checkpoint.json" --created-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
tar -C "$workspace" --exclude=./state/kernel.sock -cpf "$backup/workspace.tar" .
restored=$(mktemp -d "$base/restored.XXXXXX")
tar -C "$restored" -xpf "$backup/workspace.tar"
"$kernel" audit --state "$restored/state" --checkpoint "$backup/checkpoint.json"
workspace=$restored
v serve --kernel "$kernel"
```

另一终端使用新工作区路径执行 `status`、`audit EVENT` 和 `run MESSAGE --pin FILE`。
崩溃时若离线检查需要 kernel 恢复，先复制原目录以保留 journal 和现场，
在恢复副本上依照 [kernel 备份与恢复](v2-backup-restore.md)处理。
用独立保存的最近可信 checkpoint 验证已提交前缀，再查原 ID 并完成 broker 恢复，
然后复查审计、状态、已有 pin 和未决事务。恢复副本与原目录具有相同 state ID，
只启用一个工作副本。checkpoint 和备份应分别保管。

### 旧编辑与导出路径不可访问

1. 保留完整备份。按上节恢复工作区，再分别恢复配置、编辑目录和导出目录。用各目录原来的相对层次
   查找已有导出和未保存文件。记录快照 ID、事件 ID 和原请求 ID。
2. 新的编辑或导出绝对路径与旧会话绑定不符时，`molt project` 会拒绝该会话。保留
   `project-session.json`、`broker.json` 和旧编辑目录。用新工作区的
   `molt version --workspace "$restored" status` 和 `audit EVENT` 核对已提交记录。
3. 若有未决请求，在恢复副本上用 `molt version --workspace "$restored" recover`
   从 broker journal 查询及恢复原 ID。用 `request-status ORIGINAL_ID` 核对收据，
   再按原事件 ID 核对保存理由和证据。若恢复失败或原 ID 仍不明确，保留完整副本及原请求并停止该工作区的写入。
4. 核对成功并停服后，另建一个工作副本。仅在复制时略过 `state/kernel.sock` 和
   `project-session.json`，保持备份原样，并把项目配置中的三个目录指向新副本。
   启动新工作区后用 `project read-tree` 核对活动快照，再在正确分支上 `project begin`。
   用旧会话的基线和恢复的分支头对照未保存的新增、修改和删除。把确认过的编辑应用到新编辑目录，
   用 `project preview` 复核后再保存。如分支头已改变，先人工合并。
5. 原路径失效时，重启普通作业并指向恢复的固定导出。按文件逐字节核对，或从对应快照重新导出。
