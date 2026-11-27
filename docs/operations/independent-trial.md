# 独立试用记录

这份模板用于记录一位未参与实现的开发者，是否能只看公开手册把自己的本地程序接入 molt。默认使用合成或脱敏文件，不使用真实账号、校园系统、付费模型或正式服务。

先按[工作区快速开始](project-quickstart.md)选择源码或 Linux 安装包路径，运行双文件示例。示例会打印 `Demo directory`，目录中保留每步的 JSON 回执、固定读取结果和审计结果。再按快速开始的 Node.js reader 示例或[项目级 CLI 手册](version-cli.md#项目级-cli)，用自己的程序和可共享的测试文件复现固定读取。记录命令、输出和接入时遇到的问题。

## 试用元数据

- 试用编号：
- 日期与时区：
- 操作者与实现关系：未参与实现 / 参与实现（若为后者说明原因）
- 收到的源码提交（如含未提交内容，另记源码清单 SHA-256）或候选包文件名：
- 收到的制品 SHA-256 与本机校验结果（如使用包）：
- 操作系统、架构、libc、Python、`uv`、`jq` 版本（如使用 Node.js，另记其版本）：
- 工作区、状态目录和证据目录：
- 输入标记：`synthetic` / `redacted`

## 验收步骤

| 步骤 | 命令或操作 | 预期 | 实际结果与证据 |
| --- | --- | --- | --- |
| 初始化 | 按[快速开始](project-quickstart.md)运行源码或安装包示例。保存终端输出中的 `Demo directory` 路径。 | 示例创建独立工作区并完成 kernel socket 握手。 |  |
| 保存 A | 检查 `$demo/a.json`，记录 `.snapshot`。 | 两份受管文件以理由提交，回执包含快照 ID。 |  |
| 保存 B | 检查 `$demo/b.json`，记录 `.snapshot` 与 `.event`。 | 第二个快照包含修改后的完整文件，回执包含事件 ID。 |  |
| 固定读取 | 检查 `$demo/pinned-b.json` 和 `$demo/old-job.txt`。 | B 的 `{snapshot, tree}` 固定，旧读者读到 `full` 与 `Write a full report`。 |  |
| 回退 | 检查 `$demo/rollback.json`、`$demo/pinned-a.json` 和 `$demo/new-job.txt`。 | 活动版本切到 A；新读者读到 `short` 与 `Write a summary`，已固定的旧读者仍读 B。 |  |
| 审计 | 检查 `$demo/audit-b.json` 中 `.evidence.reason.text`。 | B 的事件可查到理由 `Use the full report format`。 |  |
| 接入自己的读者 | 按[快速开始中的 Node.js reader 示例](project-quickstart.md#use-a-nodejs-reader--接入-nodejs-作业)选择与候选匹配的命令，或记录自己的 CLI 调用。 | 作业启动时只读取一次并固定 `{snapshot, tree}`，结果记录快照 ID。 |  |
| 恢复 | 只有隔离工作区出现未知提交结果时，按[恢复手册](version-cli.md#断线查询与恢复)用原 request ID 查询或恢复；普通演示未出现时记 `N/A`。 | 不确定结果不换新 ID 重提；确认原请求的最终状态后再继续写入。 |  |

`demo` 是快速开始脚本打印的示例目录路径。将它设为实际输出路径后，可逐项核对快照、文件内容和理由：

```bash
demo=/path/from/the/Demo-directory/output
jq -r '.snapshot, .event // empty' "$demo/a.json" "$demo/b.json"
cat "$demo/old-job.txt" "$demo/new-job.txt"
jq -r '.evidence.reason.text' "$demo/audit-b.json"
```

Linux 安装包中脚本位于 `share/molt/examples/project-workspace.sh`。恢复演练只在可丢弃的隔离副本中进行。

## 体验记录

- 首次成功读回耗时：
- 首个阻塞（步骤、实际输出和退出码，如无则填无）：
- 为继续试用收到的帮助及结果：
- 试用者原始反馈：
- 无需口头解释即可完成的步骤：
- 必须口头解释的术语或命令：
- 实际回答了什么问题：
- 哪个步骤最容易误用：
- 建议改进的文档或 CLI 入口：

## 结论

- 是否完成首次接入：是 / 否
- 是否满足旧读者读 B、新读者读 A：是 / 否
- 是否能按事件查到 B 的理由：是 / 否
- 未完成项与解除条件：

记录完整命令、原始退出码和日志路径。不要把试用者的自报结果、模型输出或工具返回自动写成可信事实。正式环境验收另用专项清单。
