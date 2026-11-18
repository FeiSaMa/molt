# M1 事务状态机与恢复契约

本文件细化 ADR 0015，供实现和崩溃测试评审。以下持久化顺序假设本地文件系统正确实现文件及目录 fsync、同目录 rename 和 flock；不承诺网络文件系统的行为。

## 当前验收证据（2026-11-16）

`crates/molt-kernel/tests/ipc_e2e.rs` 启动独立内核进程，通过真实 Unix socket 验证握手、
对象写入、记录追加、generation 提交、request ID 幂等及冲突、current 和审计；并经 CLI
验证 archive、rollback、checkpoint 成功导出及拒绝覆盖、旧 checkpoint 验证后续链、
GC dry-run 列出不可达对象且保留原有审计结果。

独立进程故障矩阵在 journal durable、log fsync、HEAD rename/durable、current rename/durable
六处停驻后由父进程发送 SIGKILL。恢复两次后，journal/log 阶段保持旧 current；HEAD rename
及之后恢复为完整新 current；已确认 request ID 重试返回原结果。该矩阵验证进程崩溃，不模拟
断电或文件系统丢失未 fsync 数据。

`tests/test_kernel_client.py` 用 Python `KernelClient` 连接真实服务端，覆盖握手、CAS 对象与
manifest、提交/current、request status、相同 ID 幂等及冲突。`state.rs` 的并发测试让 4 个
reader 与 20 次提交交错；锁竞争返回 `WouldBlock` 时重试，所有成功读取都验证 generation
引用完整。CAS 测试将 bucket 父目录替换为符号链接，读写均拒绝且目标目录无新增内容。

Rust 验收命令为 `cargo test --workspace --features molt-kernel/fault-injection`，格式与静态
检查分别为 `cargo fmt --all --check` 和
`cargo clippy --workspace --all-targets --features molt-kernel/fault-injection -- -D warnings`。
Python E2E 命令需先构建二进制，再设置 `MOLT_KERNEL_BIN` 并运行
`PYTHONDONTWRITEBYTECODE=1 uv run pytest -q tests/test_kernel_client.py`。

## 规模基准

`cargo run --release -p molt-kernel --example scale_bench -- 10000 0` 测量 10k 条同步记录追加；
`cargo run --release -p molt-kernel --example scale_bench -- 0 1073741824` 测量本地 CAS 1 GiB
写入及读回哈希校验。两项分开运行以免大对象内存占用干扰记录测试。IPC 帧仍限制为 16 MiB，
该 1 GiB 结果不代表 IPC 大对象分块已实现或验证。当前机器的 release 结果：CAS 写 1334 ms
（767.6 MiB/s），读回校验 1311 ms（781.1 MiB/s）；10k 记录追加 1,485,893 ms
（6.73 records/s）。记录结果来自去除 `Log::append` 中重复前缀扫描之前的构建；之后已改为
复用恢复时读取的已确认记录后重测：10k 条追加 `1,376,016 ms`（`7.267 records/s`）。此前单次结果为 `1,485,893 ms`（`6.73 records/s`）；新结果约快 8.0%，但都是单次本机描述性测量，不能视作稳定回归或跨设备性能承诺。dirfd CAS 改造后的 1 GiB 测量：写 `1,344 ms`（`761.9 MiB/s`），读回并校验 `1,144 ms`（`895.1 MiB/s`）。这是本机单次结果，不是跨设备性能承诺。

## CAS 路径安全与 IPC 大对象

Unix CAS 读写从已打开的 state 目录句柄开始，逐级使用 `openat(O_NOFOLLOW | O_DIRECTORY)`；缺失目录使用 `mkdirat`，对象临时文件使用 `openat(O_CREAT | O_EXCL | O_NOFOLLOW)`，最终以同文件系统 `linkat` 不覆盖发布。验证与后续操作共享已打开的目录 inode，路径替换只能导致操作落到原目录或失败，不能重定向到替换后的符号链接。并发 bucket rename/symlink 测试确认外部目标保持空目录。CAS 审计遍历仍有路径式目录枚举，审计不写数据；读写对象路径的安全边界不依赖该遍历。

IPC 保持 16 MiB JSON-RPC frame 上限；大对象走 `object.upload.begin/chunk/finish/abort`。begin 声明总字节数和 SHA-256，连接上最多一个会话；chunk 是非空、至多 1 MiB 的 byte array，offset 必须等于已收字节数且不能超过声明长度。finish 要求长度与摘要完全匹配，随后才以 CAS 不覆盖发布语义硬链接入对象库并 fsync 目标目录。请求 ID 不适用于临时上传会话：断线后临时文件由 Drop 删除，调用方用同内容重开上传；若 finish 响应丢失，按摘要重传会命中并验证已有不可变对象。异常进程退出留下的 `.upload-*` 在服务成功绑定唯一 socket 后、开始 accept 前清理并 fsync state 目录。E2E 覆盖多块 2 MiB 对象、偏移错误、摘要错误、断线清理、启动清理及 Python client 上传。

## 持久化边界

写操作取得非阻塞独占锁后执行。所有写请求携带调用方生成的 request ID；同一 ID 与不同请求内容冲突，同一 ID 与相同内容返回已记录的结果。请求内容以规范编码的摘要绑定。结果和 request ID 随确认日志持久化，不依赖易失的进程内缓存。

1. 写入 CAS 对象，逐个 fsync 文件、rename 并 fsync 所在目录。已有对象先验证哈希，不覆盖。
2. 写入并验证 manifest 对象及其全部引用，持久化 manifest 映射。
3. 持久化 journal，记录 request ID、请求摘要、旧 current、目标 generation、manifest 和预期日志位置；fsync journal 与目录。
4. 追加包含完整提交结果的日志记录，fsync 日志段。再以临时文件、fsync、rename、目录 fsync 更新 HEAD，HEAD 指向最后确认的 seq/hash。**HEAD 目录 fsync 完成是确认边界**；仅日志段 fsync 不表示已确认。HEAD 本身及日志记录的格式必须允许恢复验证。
5. 验证已确认提交后，以临时文件、fsync、rename、目录 fsync 更新 current；最后清理 journal 并 fsync 目录。成功响应只能在 current 持久化后发送。

HEAD 的原子替换可能已经发生而调用方尚未观察到目录 fsync 的结果；此时返回或断线结果是 unknown，不能宣称未提交。恢复时以磁盘上可验证的 HEAD 为准：若包含该提交则完成 current，否则丢弃 HEAD 之后的未确认尾部并保持旧 current。即使进程在确认边界之前被杀，HEAD 的 rename 也可能已持久化，所以允许恢复为旧态或新态。恢复不能截断 HEAD 指向的记录，也不能把已确认的损坏解释为未确认尾部。HEAD 和完整 state 都受攻击者控制时无法防止历史重建或回滚；独立保存且已知最新的外部 checkpoint 才能检测相对该锚点的变化。

## 状态与恢复

| 状态 | 持久事实 | 恢复及重试 |
| --- | --- | --- |
| idle | 无活动 journal | 新 ID 可开始；已确认 ID 返回原结果。 |
| preparing | 对象或 manifest 可能落盘；尚无完整 journal | current 不变；保留不可变对象，清除临时文件；同 ID 可重试。 |
| prepared | 完整 journal 已落盘，HEAD 尚未确认提交 | 校验 HEAD 和日志；未确认时截断 HEAD 之后的尾部、保持 current，记录或返回可安全重试的未提交状态。 |
| committed | HEAD 已确认提交 | 校验日志、对象、manifest 和 journal 的一致性；幂等完成 current 切换，同 ID 返回原 generation。 |
| aborted | 已确定 HEAD 未确认提交 | current 保持旧值；同一请求可按明确约定重试，不得产生重复确认记录。 |
| unknown | 客户端未收到可靠结果，或持久化操作结果不明 | 不报告未提交；以同一 ID 查询或重试，先在锁下恢复后判定。 |

恢复必须串行、幂等、fail closed：HEAD 所指记录缺失、哈希不匹配、journal 与确认记录冲突或 current 无法对应已确认历史时，拒绝写入并报告损坏，不自动修复证据。完整性审计只读，不执行恢复。普通读者只读取经验证的 current 所指不可变 generation；恢复进行期间允许读到旧或新 generation，不能读取 staging。

| 注入崩溃位置 | 恢复结果 |
| --- | --- |
| 对象落盘后 | 旧 current；孤立对象可由后续 GC 处理。 |
| manifest 落盘后 | 旧 current；孤立对象可由后续 GC 处理。 |
| journal 落盘后 | HEAD 未确认则旧 current；重试同一 ID 不重复提交。 |
| 日志段 fsync 后、HEAD 更新前 | 未确认尾部可截断，旧 current。 |
| HEAD 替换前后、current 替换前后 | HEAD 未确认则旧态；确认则恢复并发布新态。current 永远只指完整旧或完整新 generation。 |

追加普通记录与提交 generation 必须共用确认边界和连续序号，不允许通过独立写路径绕过 HEAD。并发 commit 的失败者返回 `tx_locked`；锁释放不代表先前请求未提交。日志段中间缺失、重排及已确认尾部截断均是完整性错误。

## 外部检查点与提交结果

外部检查点的写入与 state 的 HEAD/current 不能组成跨文件系统原子事务。可选要求检查点的发布操作必须在持久 journal 中记录目标与阶段；如果本地提交已确认但外部写入失败，结果为已提交但检查点未完成或 unknown，恢复和同 ID 重试应继续发布同一个检查点，不能报告整次提交未发生。若产品要求“外部写失败时整次提交必然未发生”，需要额外协调协议且与上述约束冲突，不能由实现静默声称满足。检查点只证明其记录的历史前缀；验证方还必须独立知道最新检查点。
