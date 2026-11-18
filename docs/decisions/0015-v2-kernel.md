# ADR 0015：v2 存储内核与迁移边界

- 状态：已接受；默认切换未获批准
- 审阅日期：2026-11-16
- 关联：ADR 0004、0009、0011；[范围清单](../research/v2-scope.md)

## 背景与决定

v1 的 Python 文件事务、普通 JSONL 与整树快照承载闭环；保留 v1 协议及证据，不在原地变更其语义。分阶段引入单机、单写者 Rust 内核，Python 保留场景、适配器、判分、门禁和语义审计。内核只处理不透明字节与引用，不运行 agent、理解业务 schema 或联网。v2 通过 parity 后才默认；v1 保留一个 release 的只读能力。此决定取代 ADR 0009 的“不删除现有代码”范围决定，但不改变 v1 协议。

### 维护者审阅结论（2026-11-16）

接受本 ADR 的持久化与事务模型、外部检查点威胁边界、Python/kernel 职责分层及 M0–M3 迁移语义，作为 v2 的架构基线。接受不表示已证明抵御可同时改写本地 state 与检查点的攻击者；保障仍仅相对于独立保存且验证方知道最新值的外部检查点。

**不批准将 v2 设为生产默认。** 默认仍为 v1，`MOLT_KERNEL_BACKEND` 缺省值保持 `v1`。M3 功能和回归通过不等于发布运行条件已就绪：kernel primary 仍需调用方配置并启动 kernel 服务。仓库现提供 Linux systemd user service 示例、服务单实例锁、stale socket 安全恢复和握手就绪探针；该路径仍需人工安装配置，不构成跨平台安装包或发布监督方案。全部 release gate 完成并另行批准前，不得改变默认值或宣称 v2 已可默认发布。

若未来单独批准首个默认使用 kernel 的正式版本，记为 **R**。R 是 v1 state 只读兼容窗口的首版：整个 R 发布周期保留 v1 读取、审计和显式迁移；最早只能在后继正式版本移除，且移除前必须另作决定并随版本发布说明。R 中所有 v1 state 写入口（含恢复、采纳、回退和间接存储 adapter）必须在持久写入前拒绝。回滚旧版并恢复迁移时点的原始 v1 快照，只会回到该时点；它不会自动包含之后写入 v2 的数据，运维必须另行保留和核对 v2 备份/导出。

首个默认 kernel 版本的候选安装支持面可限定为经验收的 **Linux x86_64**。其它平台继续运行 v1，或明确拒绝安装/启动 kernel；不能把 CI 构建 artifact 当作安装验收。

## 持久化与事务

CAS 以 SHA-256 寻址，写临时文件、fsync、rename 与目录 fsync，已有对象先验证，读取重算哈希。日志以连续 seq 和前项哈希链接，payload 先入 CAS；只允许恢复未确认的部分尾记录，已确认记录缺失或损坏须 fail closed。manifest 自身为不可变对象，逐项引用对象及大小、模式和类型，拒绝绝对路径、`..`、符号链接及超长路径。generation 与 archive 不可变，current 是可验证的原子引用；rollback 追加新 generation 并引用旧 manifest，不改历史。

事务持非阻塞 flock，持久化 journal、对象、manifest 和提交日志，最后原子替换 current 并 fsync 目录；恢复幂等，读者只见旧或新 generation。日志已落盘但 pointer 未切换时的提交判定、幂等 request ID 与恢复步骤必须在 M1 实现前以状态机和崩溃测试细化，不能假设日志追加与指针替换是同一原子操作。materialize 若实现，仅承诺每个 root 独立替换与 journal 恢复，不承诺多路径单调用原子性。GC 以 current、所有 archive 和日志为根，先记录计划，禁止删除可达对象。

布局使用 `VERSION`、`LOCK`、`kernel.sock`、`log/`、`objects/sha256/`、`manifests/`、`refs/`、`tx/` 和 `audit/`；projection 仅为可重建缓存。文件系统须支持同目录 rename、flock 与 fsync；不承诺网络文件系统行为。

## IPC 与审计

Unix socket 权限 0600，同 UID 校验；JSON-RPC 2.0，四字节大端长度，16 MiB 上限，超大对象分块。握手固定 `molt-kernel/1` 与 state ID；写请求有持久化幂等 ID，错误结构化且不返回堆栈。断线后的提交状态必须可查询，不能把未知结果误报为未提交。客户端不得直接写 v2 state，启动/锁/socket 生命周期需定义竞争与故障处理。

内核先校验链、对象、引用和 generation，Python 再重算 run/result/gate、配对、版本、holdout 与脱敏；两层均通过才成功。报告稳定排序且不将 v1 float 比较结果写成 v2 整数结论。v1 保留现有 float 语义；整数微美元和舍入报告属于另行 ADR 的 v2 domain 变更。提交前 Python 负责 redaction/secret scan，内核要求显式 `redaction_ok`；标记不是内容无秘密的密码学证明。审计不自动修复证据。

威胁边界：哈希链能检出相对可信头部的篡改，但攻击者若能重写全部 state 和头部，可重建整条链；M1 支持导出外部检查点，默认离线且不强制；审计可验证当前链头等于检查点或从其延伸，较旧或分叉均失败。保证仅为 tamper-evident relative to an external checkpoint。验证方必须知道最新检查点，且其存储不能被攻击者回滚；单个本地文件不是可信锚点。签名仅为可选扩展，同机同攻击者控制的私钥不能提供独立信任。M1 不证明进程真的执行，不保证 live 可复现、verifier 真值或跨机器二进制可复现；独立 judge 与内核观察执行留待 M4。

## 编码与依赖

内核 envelope、manifest、报告的 canonical JSON 需要明确数值范围、Unicode、重复键和非法数值规则，使用跨语言 golden vectors；业务 payload 按原字节存储。v1 `json.dumps` 浮点行为不能未经验证当作跨语言规范。拟选 `serde`/`serde_json`（解析和编码）、`sha2`（SHA-256）、`libc`（Unix 锁及凭据）；命令行解析可先用标准库。每项依赖及精确版本、平台和安全审查在 M1 PR 定稿，不视为已安装。锁文件纳入版本控制。

## 迁移及风险

M0 记录基线并按已确认范围物理归档实验面、断开入口和同步测试/构建，M1 在独立 PR 建立内核，M2 feature flag 双路径并做 fixture/replay parity，M3 dry-run、幂等迁移和默认切换。v1 原样保留，迁移记录来源与哈希，报告舍入；不跨 scenario_version 比较。每阶段可回滚代码，v2 数据不可用旧写入器修改。主要风险为断电顺序、日志尾部与提交判定、无外部锚点的完整性边界、Python 隐藏写路径、跨语言编码差异、secret 扫描漏报及实验模块对 CLI 的依赖。必须用崩溃/篡改矩阵及端到端测试验证，不能以文档替代。

## 检查点与发布

`molt-kernel checkpoint export --out <path>` 输出 canonical JSON，包含 `protocol`（`molt-kernel-checkpoint/1`）、`state_id`、`generation`、`log_seq`、`log_hash`、`manifest_hash`、`created_at`、`kernel_version`。`audit --checkpoint <path>` 校验同一 state 及检查点处链和 generation/manifest，拒绝较旧头部或分叉。无需网络透明日志或在线服务。adopt / 发布命令可选 `--require-checkpoint`。外部文件与本地 state 无共同原子事务：本地提交确认后若检查点写入失败，保留已确认提交，明确报告检查点未完成，并使用同一 request ID 幂等重试发布；断线或持久化结果不明时报告 unknown，先查询或恢复，不能声称未提交。此决定由管理员确认。

### 默认切换 Release Gate

本 ADR 接受架构，不构成默认切换授权。切换须由单独发布决定批准，并同时满足以下条件：

1. **服务生命周期**：定义受支持的安装/启动/监督/停止及升级方案，明确 state 目录、socket 权限、启动就绪探测、单实例锁冲突、崩溃重启和干净关闭行为。CLI 不得静默启动未配置的服务，也不得回退写入 v1；未连接、协议不兼容、锁冲突和服务退出均须以非零状态和可操作诊断呈现。
2. **故障与恢复**：发布检查覆盖启动失败、运行中断线、提交结果 unknown、重启恢复及磁盘/权限错误；确认错误输出不把 unknown 说成未提交，且同 request ID 查询/重试可恢复。服务不可用时不得自动切到 v1 或另一 state。
3. **v1 只读保留期**：发布清单明确 v1 只读兼容从哪个 v2 默认版本开始、至少持续一个完整 release 的具体版本范围/终止条件；该期间 v1 state 只允许读取、审计和显式迁移，所有写入口拒绝修改并有测试。不得让旧版写入器打开 v2 state。回滚仅切回代码/configuration 并使用未迁移的 v1 state，不把 v2 数据降级写回。
4. **迁移与兼容**：在隔离的真实服务进程上复验 dry-run 零写入、具名迁移幂等/冲突拒绝、源树不变、受支持 stateful 命令 parity、语义审计和 v1 只读限制；发布包须包含 kernel 可执行文件及兼容性检查。
5. **发布检查**：CI/release checklist 验证默认选择、安装包/服务配置、健康检查、不可用时的错误合同、数据备份/恢复说明和 v1 只读期；记录版本化验收证据。全部通过后由维护者另行明确批准默认切换，单改环境变量或代码默认值不构成批准。

当前状态：`1.1.0rc1` Linux x86_64 组合候选包已通过隔离安装和 persistent user unit 的启动、SIGKILL 自动恢复、停止及同 state 重启检查；修正后的候选包也已记录磁盘/权限失败与 unknown-request 恢复验收。证据见 [候选验收](../research/v2-candidate-evidence.md) 和[发布清单](../operations/v2-release-checklist.md)。该预发布版本不是默认版本 R。正式发布版本绑定的备份恢复说明和 R 的兼容窗口仍待补齐；默认切换仍未批准，默认仍为 v1。

## 事务状态机

| 状态 | 持久事实 | 恢复与请求语义 |
| --- | --- | --- |
| idle | 无活动 journal | 新 request ID 可开始；相同已完成 ID 返回原结果。 |
| preparing | journal 与 staging 可能部分存在，对象可能已落盘 | 不移动 current；清理未提交 staging，保留可达对象；同 ID 可安全重试。 |
| prepared | journal、对象和 manifest 已持久化，尚无提交日志 | current 保持旧值；中止或幂等重试，记录确定结果。 |
| committed | 提交日志已持久化 | 日志是提交判定点；恢复验证并完成 current 切换，重复 ID 返回同一 generation；损坏则 fail closed。 |
| aborted | 确认未写提交日志 | current 保持旧值，同 ID 返回中止结果。 |
| unknown | 客户端断线或写入结果不明 | 查询持久 request ID/恢复后判定；禁止声称未提交或用新 ID 盲目重试。 |

对象后、manifest 后、journal 后崩溃恢复旧态；提交日志后、pointer 替换前后恢复新态。日志追加和 fsync 边界必须区分未确认尾部与已确认记录，确认边界及故障矩阵见 [M1 事务状态机](../research/v2-transaction-state-machine.md)。先用 UDS 打通 init → object.put → record.append → generation.commit → current → audit，再完善恢复、回退、审计、GC 和检查点。

## M0 范围审阅结论（2026-11-15）

管理员确认 probe/probe-c2 整体属于实验面：shell、bench、scenario 与 probes、upper_scenarios 一起归档。
保留 code-repair/svc-config、search/holdout、demo/svc-config、core 与适配器。
agent runtime 依赖实验 API，不是通用 runtime，随 agent 归档；contracts 是实验 API 契约，不是 v1 协议事实源。
web/export 和实验模块物理移到 attic；先断开 CLI/demo 入口，再移动资产与对应测试，最后排除构建和检查。
不修改 harness/verifier 内容，不改变 v1 schema 或 gate。M0 完成仅表示 ready-for-review，不表示合并或允许开始 M1。
