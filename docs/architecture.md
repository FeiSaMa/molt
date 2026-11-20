# molt 架构

molt 为使用 Agent 的本地工作流提供状态事务、版本管理与审计。项目 CLI 可以把配置、提示词和
工具文件保存为一组快照。新作业在启动时取得一棵只读文件树，按相对路径读取同一版文件。
kernel 持久化不透明状态，Python 应用层管理文件工作流与证据。

接入现有程序可以从[项目级 CLI](operations/version-cli.md#项目级-cli) 开始。直接提交状态的
应用见[外部集成契约](operations/external-integration.md)，维护 kernel 状态见 [kernel CLI](operations/kernel-cli.md)。
第三方接入、回归与公开分发的建议见[发展路线](roadmap.md)。

| 路径 | 职责 |
| --- | --- |
| `crates/molt-kernel/` | 管理不透明 CAS 对象、manifest、generation、持久日志、归档、检查点和 GC。通过 UDS 接受来自本地客户端的请求。 |
| `molt/project_cli.py` | 项目配置、独立编辑目录、预览与保存、固定快照的文件树，以及可选的 OpenCode 启动入口。 |
| `molt/version_cli.py`、`molt/versioning.py`、`molt/version_files.py` | 工作区与版本命令、broker、分支和审计索引、文件捕获与快照导出。 |
| `molt/` 其他模块 | 通用 Python CLI、UDS 客户端和共享文件操作。 |
| `schemas/v1/` | 冻结的历史 v1 schema。字段说明见生成的[协议参考](protocol.md)。 |
| `examples/` | 通用事务客户端和本地配置与工具版本工作流。版本示例包含受控提案、固定版本运行和可回链摘要。 |
| `packaging/` | Linux 用户服务、候选安装包构建与隔离验证。 |
| `tests/` | 事务、恢复、旧入口拒绝、隔离工作流和审计验收。 |

## 提交与恢复

调用者上传内容，组成 manifest，并以稳定 request ID 提交 generation。kernel 在单写者锁下验证对象和请求，写入持久日志，再更新可见指针。提交结果未知时先查询原 request ID，必要时按原参数恢复。读取者以同一个已提交 manifest 解析整套版本。

从断线或进程崩溃恢复见[事务语义](research/v2-transaction-state-machine.md)和 [CLI 参考](operations/kernel-cli.md)。可信主体可独立保管 checkpoint，用于核对日志前缀及状态完整性。

## 配置与工具版本

项目级 `begin` 从指定分支头建立可写编辑目录，记录 generation 和分支头。`preview` 列出
受管文件的增删改路径。`save` 校验编辑开始时的前置条件，并把文件内容和操作者给出的理由交给
broker。遇到过期基线或结果未知时，编辑目录仍可用于检查。结果未知使用原 request ID 恢复。

broker 将快照及其父节点、分支引用、活动快照和证据索引纳入一次事务。回退切换活动快照，
保留分支头和历史。受管文件读取以已提交的活动快照为起点，导出完整的只读目录供一个新作业
使用。旧读者可以继续读取此前固定的目录。编辑目录有自己的生命周期，回退后若要从目标
快照继续开发，可从该快照建分支。

普通保存记录操作者提供的理由与来源。隔离提案入口另外记录生成主体、判断、校验结果和
批准来源。v3 摘要区分原始事件、可重算的快照投影和未验证的模型推断，旧 v2 摘要保留
回读与输入哈希复核路径。摘要生成失败时原始证据仍可读取。
[摘要指南](operations/version-cli.md#离线提案与摘要) · [设计](decisions/0018-agent-snapshots-and-audit-digests.md)

## 兼容性与边界

ADR-0019 将 v1 语义审计与迁移留在对应的历史发行归档。kernel 仅处理不透明对象和事务。外部业务结果由外部系统确认。本地状态回退的效力止于受管状态。

每个工作区由同一本机用户独占。程序可直接消费 `molt project read-tree` 返回的目录。
可选的 OpenCode 启动入口会为新作业设置临时 HOME 和 XDG 目录。需要限制网络或宿主文件访问时，
使用单独的隔离启动器。
