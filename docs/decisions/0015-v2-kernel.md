# ADR 0015：v2 存储内核与迁移边界

- 状态：提议，待审阅
- 日期：2026-11-15
- 关联：ADR 0004、0009、0011；[范围清单](../research/v2-scope.md)

## 背景与决定

v1 的 Python 文件事务、普通 JSONL 与整树快照承载闭环；保留 v1 协议及证据，不在原地变更其语义。分阶段引入单机、单写者 Rust 内核，Python 保留场景、适配器、判分、门禁和语义审计。内核只处理不透明字节与引用，不运行 agent、理解业务 schema 或联网。v2 通过 parity 后才默认；v1 保留一个 release 的只读能力。此决定取代 ADR 0009 的“不删除现有代码”范围决定，但不改变 v1 协议。

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

M0 只记录基线和范围，M1 在独立 PR 建立内核，M2 feature flag 双路径并做 fixture/replay parity，M3 dry-run、幂等迁移和默认切换。v1 原样保留，迁移记录来源与哈希，报告舍入；不跨 scenario_version 比较。每阶段可回滚代码，v2 数据不可用旧写入器修改。主要风险为断电顺序、日志尾部与提交判定、无外部锚点的完整性边界、Python 隐藏写路径、跨语言编码差异、secret 扫描漏报及实验模块对 CLI 的依赖。必须用崩溃/篡改矩阵及端到端测试验证，不能以文档替代。

## 检查点与发布

`molt-kernel checkpoint export --out <path>` 输出 canonical JSON，包含 `protocol`（`molt-kernel-checkpoint/1`）、`state_id`、`generation`、`log_seq`、`log_hash`、`manifest_hash`、`created_at`、`kernel_version`。`audit --checkpoint <path>` 校验同一 state 及检查点处链和 generation/manifest，拒绝较旧头部或分叉。无需网络透明日志或在线服务。adopt / 发布命令可选 `--require-checkpoint`；要求成功持久化外部检查点才报告提交成功。外部文件与本地 state 无共同原子事务：在实现前必须明确失败及断线的 unknown 状态、恢复和幂等重试，不能承诺跨文件系统失败必然回滚已发布的检查点。

## 事务状态机草案

| 状态 | 持久事实 | 恢复与请求语义 |
| --- | --- | --- |
| idle | 无活动 journal | 新 request ID 可开始；相同已完成 ID 返回原结果。 |
| preparing | journal 与 staging 可能部分存在，对象可能已落盘 | 不移动 current；清理未提交 staging，保留可达对象；同 ID 可安全重试。 |
| prepared | journal、对象和 manifest 已持久化，尚无提交日志 | current 保持旧值；中止或幂等重试，记录确定结果。 |
| committed | 提交日志已持久化 | 日志是提交判定点；恢复验证并完成 current 切换，重复 ID 返回同一 generation；损坏则 fail closed。 |
| aborted | 确认未写提交日志 | current 保持旧值，同 ID 返回中止结果。 |
| unknown | 客户端断线或写入结果不明 | 查询持久 request ID/恢复后判定；禁止声称未提交或用新 ID 盲目重试。 |

对象后、manifest 后、journal 后崩溃恢复旧态；提交日志后、pointer 替换前后恢复新态。日志追加和 fsync 边界必须区分未确认尾部与已确认记录，具体确认标记和故障矩阵在 M1 定稿。先用 UDS 打通 init → object.put → record.append → generation.commit → current → audit，再完善恢复、回退、审计、GC 和检查点。
