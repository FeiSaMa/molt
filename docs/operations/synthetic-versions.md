# 合成配置与工具版本闭环

将自己的程序接入快照读取见[项目级 CLI](version-cli.md#项目级-cli)。本页使用合成配置和内置
echo 工具演示事务与回归测试。

[ADR-0018](../decisions/0018-agent-snapshots-and-audit-digests.md) 的首阶段示例使用固定的
合成配置和 echo 工具。`v1` 返回纯文本，`v2` 返回 JSON，`invalid` 的配置与工具不匹配。
`prefixed` 为纯文本添加前缀，`flexible` 的工具按配置选择输出格式，`json-config` 使用同一个
工具并将配置改为 JSON。
示例由仓库源码运行，调用现有 kernel UDS 接口。证据为 `synthetic`，摘要由固定模板生成。

## 本地运行

在仓库根执行。先将 `MOLT_KERNEL_BIN` 设为所用 kernel 二进制的绝对路径。使用较短的
仓库外路径，避免超过 Unix socket 路径长度上限。示例需要 `jq` 提取结果字段。

```bash
workspace=$(mktemp -d /tmp/molt-versions-XXXXXX)
demo="$workspace/v"
versions() {
  PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-sync --python 3.12.13 \
    python -m examples.synthetic_versions "$demo" "$@"
}
versions init "$MOLT_KERNEL_BIN"
"$MOLT_KERNEL_BIN" serve --state "$demo/state" >"$workspace/kernel.log" 2>&1 &
kernel_pid=$!
```

等 `"$MOLT_KERNEL_BIN" ping --socket "$demo/state/kernel.sock"` 成功，再执行：

```bash
first=$(versions activate v1 null)
old_pin=$(versions pin)
second=$(versions activate v2 "$(jq -c .current <<<"$first")")
new_pin=$(versions pin)
versions run "$old_pin" hello
versions run "$new_pin" hello
event=$(jq -r .event <<<"$second")
versions audit "$event"
summary=$(versions digest "$event")
versions audit "$event"
rolled=$(versions rollback "$(jq -r .snapshot <<<"$first")" "$(jq -c .current <<<"$summary")")
versions run "$new_pin" hello
versions gc-plan
```

`pin` 可保存到文件供任务重启使用。`read` 和 `run` 按原 request ID 核验已提交
generation，并使用该 generation 的配置与工具。回退追加一条证据并切换活动指针，
保留 `main` 的分支头及后续证据。`gc-plan` 只生成当前 state 的实际 kernel 清理计划，
会写入 `audit/gc-plan.json`。可从结果核实旧 committed manifest 中的文件仍可达。

## 命名分支和合并

在同一合成环境继续执行。两个新分支都从已提交的 `v1` 创建，分别更改配置和工具。

```bash
base=$(jq -r .snapshot <<<"$first")
draft=$(versions create-branch draft "$base" "$(jq -c .current <<<"$rolled")")
topic=$(versions create-branch topic "$base" "$(jq -c .current <<<"$draft")")
ours=$(versions advance draft prefixed "$(jq -c .current <<<"$topic")" "$base")
theirs=$(versions advance topic flexible "$(jq -c .current <<<"$ours")" "$base")
merged=$(versions merge draft topic "$(jq -c .current <<<"$theirs")" \
  "$(jq -r .snapshot <<<"$ours")" "$(jq -r .snapshot <<<"$theirs")")
versions status
versions audit "$(jq -r .event <<<"$merged")"
versions switch draft "$(jq -c .current <<<"$merged")" "$(jq -r .snapshot <<<"$merged")"
versions run "$(versions pin)" hello
versions run "$old_pin" hello
kill "$kernel_pid"
wait "$kernel_pid" || test "$?" -eq 143
```

`status` 返回当前 generation、活动 snapshot、各分支头和最近一次版本操作的证据 ID。
切换后新任务的 `run` 结果解码为 `synthetic: hello`，旧 pin 的结果仍是 `hello`。创建和推进分支时保留活动
snapshot。`merge --activate` 可在发布合并版本的同一次提交中显式激活。V02 的 `activate`
仍推进并激活 `main`，`rollback` 只切换活动 snapshot。

`advance`、`switch` 和 `merge` 在 broker 锁内核对完整旧 generation 和相关分支头。
过期的推进提案保存在私有 `candidates/<sha256>.json`，其中记录合成配置、工具和原基线。
核对时可用最新基线重新提出同一个 release，仍需经过组合校验。候选文件没有活动或分支引用。

合并需要两个不同的父 snapshot 和唯一最近的共同祖先。配置和工具分别按整个 JSON 对象
做三方合并。同一对象两边有不同改动时报告冲突。自动合并的配置和工具仍需一起通过
兼容性校验。冲突和组合校验失败都保留原 generation、分支头及活动版本，来源快照仍可追溯。
成功时证据记录目标和来源分支、按目标到来源排列的父版本、共同祖先、实际校验结果及激活选择。

提交报错且出现待恢复 request ID 时，先恢复原服务，再用 `versions recover`。
恢复先查询原 ID。已提交则补齐 broker 回执，未提交且基线仍一致时按原参数重发。
若基线变化、状态身份不符或暂存材料缺失，保留当时的 journal 供核对。
写入或生成 GC 计划前必须先解决待恢复请求。恢复后可用 `status` 的 `last_event` 查阅操作证据。

## 受控提案与审计摘要

在同一 `versions` 入口，由可信操作者选定分支、当前 generation 和旧分支头。以下示例使用已安装的 Qwen GGUF 和 `llama-completion`。变量 `expected` 保存 `status` 的完整 `current`，`head` 保存目标分支头，`model_id` 由操作者填入模型版本或文件摘要。

```bash
versions propose main "$expected" "$head" --activate \
    --task "Set prefix to 'lab: ' and retain the current reply and tool formats." \
    --sources '{"spec":"Add the exact prefix lab: followed by a space."}' \
    --approval "Isolated synthetic example" --model "$model" --model-id "$model_id"
versions digest "$event" --model "$model" --model-id "$model_id"
versions audit "$event"
```

`event` 取自提案结果。也可用 `--script /absolute/path/to/worker.py` 启动隔离 Python Agent。
脚本从 stdin 读单个 JSON 对象，提案输入包括 `purpose`、`task`、`config`、`tool`、`evidence`，
输出完整的 `config`、`tool` 和 `judgment`，判断含 `reason`、`evidence` 引用列表及 `assumptions`。
受管工具是内置 echo，依赖 `molt.synthetic-echo` 版本 1，和配置及工具一起写入版本。
外部命令和路径会被校验拒绝。省略 `--activate` 时先推进分支，再用 `switch` 激活。

摘要 worker 收到 `evidence` 投影，返回 `claims`，每条含 `kind`、`text` 和字段引用列表
`evidence`。`inference` 与 `missing` 至少引用一项，`conflict` 至少引用两项。
`audit` 返回原始事件、投影及历次摘要。沿摘要每条 `references` 或 `evidence` 中的
`event` 和 `field` 找到投影值。记录事实由 broker 渲染，模型文字需据所引字段人工复核。
模型失败或引用不合规时，既有事件及摘要仍可读。无模型的 `digest` 及脚本替身验收都记录为 synthetic，本地模型生成另记 `generation_mode: local_model`。

## 权限与验收边界

专属目录权限为 `0700`。提案和摘要 worker 在 Linux Bubblewrap 新命名空间中执行，网络隔离，
只挂载系统运行时及操作者指定的脚本或模型。broker 进程持有 UDS 权限，批准来源是可信本地操作者的调用及 UID。
请用合成输入并独占目录和 UDS。能在隔离外以 broker 同 UID 执行的进程具有该宿主权限。
正式部署的身份配置、证据权限和维护边界另行决策。

集成测试使用本次构建的 kernel，通过实际 UDS 发布与查询 generation，
并覆盖调用边界断线与真实进程中断。一次性测试 state 仅对测试生成的游离对象执行串行 GC。
Rust kernel 的故障注入矩阵独立覆盖
journal、日志、HEAD 和 current 的跨进程崩溃恢复。证据链完整性与业务真实性各有独立边界。
