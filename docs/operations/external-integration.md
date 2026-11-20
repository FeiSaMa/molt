# 最小外部集成契约

本契约组合现有 kernel API；未新增冻结记录、业务 RPC、身份服务或 Agent 框架。
客户端见 [opaque_client.py](../../examples/opaque_client.py)，CLI 见 [kernel-cli.md](kernel-cli.md)。
例子仅为 `synthetic`，kernel 提交与外部业务成功必须分别核验。

## 接口盘点与信任边界

当前实现来源为 `molt/kernel_client.py`、`crates/molt-kernel/src/main.rs` 的 dispatch
以及 `state.rs::request_status/commit`。支持 Linux 本地 UDS。

| 可复用入口 | 实际语义 |
| --- | --- |
| `KernelClient(path, timeout)` | 建立 UDS 并握手 `molt-kernel/1`；handshake 返回 state_id，集成方应固定预期 state_id |
| `upload_object` / `get_object` | 内容寻址不透明字节；分块上传，断开后可重新上传相同内容；不验证来源真实性 |
| `create_manifest` / `get_manifest` | 不透明树；hash、大小、路径与 mode 校验；不含业务授权 |
| `call("generation.commit", {request_id, manifest_hash, ts})` | 单写者持久事务；同 ID 同参数幂等，不同参数冲突 |
| `request_status(id)` | 先恢复再审计，返回 committed/result 或 not_found/retry_with_same_request_id；可能写恢复状态 |
| `current` / `inspect` / `call("audit")` | 当前指针、内部一致性与状态检查；不证明真实执行 |
| 离线 checkpoint/recover/备份 | 用现有 CLI；先停止对应独立 state 的服务，checkpoint 不含对象备份 |

服务只以同 UID peer 检查和文件/socket 权限约束本机访问，同 UID 不是不同业务用户的身份隔离。
request ID、JSON-RPC id 和业务 ID 不同：JSON-RPC id 只关联一次连接中的响应；
request ID 稳定关联一次 kernel 写入；业务 ID 由外部服务分配，kernel 不解释。

| 错误 | 调用方动作 |
| --- | --- |
| invalid_request / request_conflict | 停止，修复输入或人工核对；不换 ID 遮掩冲突 |
| permission_denied / integrity_error | 拒绝执行，检查权限或恢复完整性；不静默降级 v1 |
| tx_locked | 未知结果先查原 ID；等待单写者可用后按原参数处理 |
| io_error / timeout / disconnect / malformed response | 若可能已发 commit，结果 unknown；恢复连接后查原 ID，不凭错误断言失败 |

## 关联、恢复与人工接管

调用者在执行前持久保存 task_ref、kernel request_id、不可变 payload 摘要和原 ts。
每个阶段用不同但稳定的 request ID（例如 intent/result），不要把业务重试变成新操作。
外部业务操作另有 operation_id 和 service-assigned business_ref；关联表由集成层保存。
调用者主体必须来自受信入口/身份代理，不能来自模型参数或业务工具返回的 self-reported user。
`opaque_client` 只展示存储 API，不承担生产身份和授权检查。

断线时先恢复 kernel，再 `request_status`：committed 取原结果，核对原 manifest；
not_found 且明确允许同 ID 重试时，使用原 payload、ts；查询不可用继续 unknown。
业务结果未知时先向业务系统按 operation_id 查询；只有业务明确支持相同 ID 去重时才允许同 ID 重发。
不能查询且不去重则停止自动重试，保留 operation_id、动作摘要、最后已知阶段供人工核对。
业务成功、kernel 记录失败属于“业务已核验、记录待恢复”，不能再执行业务动作。
取消是独立授权、独立 operation_id 的业务动作，必须查询取消结果；generation 回退不等于取消。
本地与业务系统不具备共同原子事务，也不承诺业务恰好执行一次。

## 最小记录、隐私与留存

以下是集成层自定义对象内容约定，不是 v1 schema 扩展，也不改变 kernel manifest 语义。
最小摘要包含 provenance=synthetic、无敏感 task_ref/operation_id、阶段、业务结果来源类别与受控证据引用。
kernel commit 只证明这些字节被保存；业务系统查询是独立信任来源，工具任意文本不能升级为事实。
不保存会话令牌、账号原文、姓名、完整预约原文、prompt 或工具输出；采用允许字段投影。
哈希不是加密或匿名化，低熵身份/座位的 hash 仍可能被猜出，不用 hash 代替隐私设计。

示例只保存假数据；状态目录仅测试用户可读，不进入仓库。测试证据只含操作摘要和断言。
生产接入前由数据责任方确定访问控制、受控引用解析方、留存期限、删除和备份保留政策。
不可变 CAS 不能承诺单条即时物理删除；必要时用可撤销的外部受控引用并单独管理内容密钥。
当前示例无自动清理/GC；测试结束是否删除独立目录也由用户确认，不自动触碰用户 state。

## 运行与限制

先安装独立候选 wheel、启动测试 kernel（均见 [kernel-cli.md](kernel-cli.md)）。
将示例复制到仓库外，用隔离环境的解释器执行，明确 socket 与原 request ID：

```bash
PYTHONDONTWRITEBYTECODE=1 /absolute/test-venv/bin/python -I /absolute/examples/opaque_client.py \
  --socket /absolute/test-state/kernel.sock --request-id synthetic-example-1
PYTHONDONTWRITEBYTECODE=1 /absolute/test-venv/bin/python -I /absolute/examples/opaque_client.py \
  --socket /absolute/test-state/kernel.sock --request-id synthetic-example-1 --query
```

示例 payload/ts 固定，因此重复调用始终使用原参数。真实调用方需要先持久化完整意图，
并固定期望 state_id；不能把此演示直接当多租户生产 SDK。
已有接口足以支撑有限模拟，不需修改冻结协议；缺少的是外部身份、权限、可靠业务查询、去重与运维责任，
身份验证、权限与业务对账由集成方负责。
