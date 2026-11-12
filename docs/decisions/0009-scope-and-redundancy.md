# ADR 0009：范围与冗余 —— 承重墙 vs 按需实现

- 状态：已决定
- 日期：2026-11-11
- 关联：`README.md`、`AGENTS.md`、`docs/architecture.md`、`web/README.md`、
  `molt/core/ports.py`、`shells/<id>/`、`molt/adapters/`

## 背景

真实 Pi 基线在 demo-6 的 10 道 search 上 **10/10**（$0.035560，106.9s），成功率维度已经饱和。
原 `docs/architecture.md` 按「基线 6–8/10」的假设设计了一批机制：6 开关搜索、SoL-Pi
（reducer / action fusion / observation pack / 压缩）、compaction 维度、双模型信号、
memory / RAG、skill evolving、multi-agent、模型维度、escalation、git tag 回滚等。

题目一旦饱和，这些机制大多**没有输入**：搜索空间再大也换不来成功率，记忆与多 agent
的成本也换不来通过数。继续把「已设计」当成「已实现/已证明」，会让 claim 失真，
也会把工程量浪费在没有信号的地方。

本 ADR 把系统分成两类：**砍了就不可信的承重墙**，与**按需实现的简单题冗余**。

## 承重墙（不可砍）

| 机制 | 为什么砍了 adopt 就不可信 |
|---|---|
| 外部 pytest + 全 `bench/**` 树哈希（guard/verify） | 判分不在 agent 进程内；agent 改测试、改题面、改其它题都会被全树哈希发现并判废。砍掉即等于让被测者自证。 |
| holdout 隔离（`scope=report`、search 流零 holdout id） | 搜索/报告必须用不同题集，search 事件与导出不得泄漏 holdout 逐题信息。砍掉后「没见过的题」不再成立，泛化分数没有意义。 |
| 原子快照 / 回退（含文件 root、单写者事务、journal + generation + tree hash） | adopt/revert 必须是可恢复的单事务；进程被杀也只能停在旧态或新态。砍掉后「采纳了什么」无法复算，回滚也不可信。 |
| audit 重算 / 脱敏 / secret scan | 指标、门禁结论、invalid 比例、账本成本必须从 run 重算；证据里不得出现 key/token。砍掉后数字只是自述，不是证据。 |
| 七端口与协议边界（core / shell / adapter） | 判分、采纳、写证据链的 owner 固定；core 只依赖 stdlib 与 `molt.core.*`。砍掉后任何 adapter 都能改结论，跨 shell 也不可比。 |
| bash-only bwrap + trust preflight + Pi 账本 | agent 只能通过 sandboxed bash 读写；trust 证明项目设置真的生效；账本给出真实成本与 session 归属。砍掉后「真实 Pi」与「fixture」无法区分。 |

这些是 adopt 可信的最小集合。它们不因题目简单而冗余，反而在演示里承担全部可信度。

## 简单题冗余（现在不实现，按需）

| 项 | 在简单题上的状态 | 何时按需实现 | 落点（端口 / adapter / shell） |
|---|---|---|---|
| 6 开关 / SoL-Pi（reducer / action fusion / observation pack / 压缩） | 已存在 6 组 `thinking × context`；SoL-Pi 无输入 | 题目需要多步推理/工具编排，单点开关不再解释差异时 | shell 的 `switches.schema.json` + `manifest.capabilities`；SoL-Pi 在 shell/harness 内 |
| compaction 维度 | 6 组已饱和，上下文远未接近窗口 | 上下文接近模型窗口、或压缩本身成为成本瓶颈时 | shell 侧预压缩 / adapter 内处理；若进协议需先 ADR + 新 major |
| 本地 9B signal / 双模型 | 单模型已 10/10，draft/verify 无增益 | 需要端侧离线（local model）或单模型错误率可测且双模型能降错时 | `molt/adapters/` 的 runtime/adapter；不进 core |
| LLM proposer 作为主路径 | 当前 grid 兜底已足够，LLM 成本高且无成功率增益 | grid 覆盖不了组合、或出现可标注的稳定失败族时 | `molt/adapters/llm_proposer.py` + `harness.propose` 的 fallback 路径 |
| memory / RAG | 每题独立，无跨题复用 | family 题集出现跨题复用（同族题重复出现）时 | shell 的 `task_provider` / `harness` 注入；core 只保留 artifact ref |
| skill evolving | 题目简单，无稳定 family | 出现稳定 family 且外部准入（评测/审核）就绪时 | shell 的 `skills_ref` + genome artifact |
| multi-agent / subagent | 单 agent 已饱和，协调只增成本 | 任务需要独立上下文或独立权限、单 agent 无法隔离时 | adapter/runtime 层的进程编排；不复用单 run 协议 |
| 模型维度 | 搜索空间只到 6 组，模型未成为变量 | 同一 shell 需要跨模型比较且预算允许时 | adapter/model registry + genome `runtime`/`ext` |
| escalation / routing | 失败成本低，路由无收益 | 失败成本高、或不同题族需要不同型号/预算时 | adapter/gateway 层；不改 core `policy.decide` |
| git tag 回滚 | 当前用 snapshot/archive 足够 | 进入发布流程、需要对外版本标签时 | release/CI 流程（docs/ops），不进 core 协议 |
| HTML 页面 | 仓库只交付 `web/data` 数据面 | 现场展示需要页面时 | 队友 C 消费 `web/data`；`web/page/` 已落地为零构建消费方页面（见文末追加节），导出流程仍不产 HTML |

触发条件按「有真实输入 + 有可测收益」判断，不以「设计上完备」为理由提前实现。

## 决定

1. **不删除现有代码与文档**：端口、adapter、shell 的既有实现保留；本 ADR 只界定范围。
2. **现在不实现上述冗余项**：包括不新增 compaction 维度、不把 LLM proposer 改为主路径、
   不引入 memory/skill/multi-agent/模型维度/escalation、不做 git tag 回滚、不做 HTML 页面。
3. **未来按需实现时只允许在现有端口 / adapter / shell 边界内扩展**：
   - shell：`TaskProvider` / `Harness` / `Verifier`、`switches.schema.json`、`manifest.entrypoints`；
   - adapter：新的 `Runtime` / proposer transport / model registry；
   - core：只在确有协议变更时动，且必须先写 ADR、按新 major 版本走（`schemas/vN/`）。
4. **claim 不包含冗余项**：没有真实 run、没有 pair 表、没有 gate 证据的机制，不得写进 claim。

## 后果

- core 保持小：只承担协议、门禁、事务、审计；复杂机制留在 shell/adapter。
- 现场 demo 只用一个 shell（code-repair）+ 两个配置点（`thinking`、`context`），
  足以展示「成功率不降、成本/墙钟下降」的 adopt 闭环。
- 简单题上的冗余项有明确触发条件与落点，不会因为「已经设计了」被误当成已实现；
  也不会为了演示而临时加机制。
- 未来实现任一冗余项时，必须带测试与证据（run/result/pair/gate 或对应 adapter 证据），
  不允许只改文档或只在 fixture 上自证。

## 后续（2026-11-11 追加，不改上文决定）

`web/page/` 已作为**零构建静态展示页**落地（`file://` 直开、零外部请求、只读 `web/data`、
`node web/page/check.mjs` 验收）。这是「消费数据面的页面」，不是导出流程的产物：
`molt export` / `molt demo` 仍然只写 `web/data`，不生成 HTML。
上文「按需」清单中「HTML 页面」一项的现状以此节为准；其余决定不变。
