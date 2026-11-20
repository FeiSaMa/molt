# molt 架构

molt 为 Agent 变更提供版本状态、事务恢复和审计。状态持久化与工作流校验分别由 kernel 和应用侧承担。

| 路径 | 职责 |
| --- | --- |
| `crates/molt-kernel/` | 管理不透明 CAS 对象、manifest、generation、持久日志、归档、检查点和 GC。通过 UDS 接受来自本地客户端的请求。 |
| `molt/` | Python CLI、UDS 客户端、显式迁移和 v1 只读审计。 |
| `schemas/v1/` | 冻结的 v1 兼容 schema。字段说明见生成的[协议参考](protocol.md)。 |
| `examples/` | 通用事务客户端和本地配置与工具版本工作流。版本示例包含受控提案、固定版本运行和可回链摘要。 |
| `packaging/` | Linux 用户服务、候选安装包构建与隔离验证。 |
| `tests/` | 事务、恢复、协议兼容、隔离工作流和审计验收。 |

## 提交与恢复

调用者上传内容，组成 manifest，并以稳定 request ID 提交 generation。kernel 在单写者锁下验证对象和请求，写入持久日志，再更新可见指针。提交结果未知时先查询原 request ID，必要时按原参数恢复。读取者以同一个已提交 manifest 解析整套版本。

从断线或进程崩溃恢复见[事务语义](research/v2-transaction-state-machine.md)和 [CLI 参考](operations/kernel-cli.md)。可信主体可独立保管 checkpoint，用于核对日志前缀及状态完整性。

## 配置与工具版本

应用侧 broker 校验暂存的配置和工具，将候选版本、分支引用、活动版本及证据索引写入同一次提交。回退追加活动版本切换和证据，保留分支与审计历史。在途任务通过已提交版本的 pin 读取原配置、工具和受管依赖。

Agent 通过隔离的提案入口提交候选。证据记录变更当时的判断、校验结果和批准来源。摘要生成使用固定事件的获准证据，区分记录、模型推断和证据缺口。摘要生成失败时原始证据仍可读。[版本工作流](operations/synthetic-versions.md) · [设计](decisions/0018-agent-snapshots-and-audit-digests.md)

## 兼容性与边界

`1.1.x` 保持 v1 冻结字段、只读审计和源状态零写入的显式迁移。kernel 仅处理不透明对象和事务。外部业务结果由外部系统确认。本地状态回退的效力止于受管状态。
