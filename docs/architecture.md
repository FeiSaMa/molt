# 架构

molt 把"搜索候选 → 执行题集 → 判分 → 门禁 → 采纳 / 回退"做成一条可复算的流水线。
本文件描述 B1 + B2a 的实际分层；字段级契约以 [`protocol.md`](protocol.md) 与 `schemas/v1/` 为准。

## 一、三层与依赖方向

```
molt/core/**        ← stdlib only
   ↑          ↑
shell        adapter
(Task/Harness/Verifier)  (Runtime)
```

| 层 | 组件 | 职责 | 不负责 |
| --- | --- | --- | --- |
| core | `molt/core/` | 11 类协议记录、门禁、指标、快照事务、事件 / 账本、审计、脱敏、提案核心（`proposer.py`） | 调模型、跑 pytest、读题面 |
| shell | `shells/code-repair/` | 题集加载、prompt 渲染、调用 Runtime、运行外部 Verifier、注入 LLM/grid 提案策略 | 定义门禁阈值、写 `scenario.policy` |
| adapter | `molt/adapters/` | `fixture/` 执行入口；`llm_proposer.py` 负责 DeepSeek transport | 判分、采纳、写证据链 |

`molt/core/**` 只 import stdlib 与 `molt.core.*`（I11）。`python -m molt doctor --deps` 做 AST 检查；
`tests/test_boundary.py` 还会屏蔽第三方 importer 后 import core。

## 二、七端口

| 端口 | owner | B1 实现 |
| --- | --- | --- |
| TaskProvider | shell | `shells/code-repair/task_provider.py` |
| Harness | shell | `shells/code-repair/harness.py` |
| Verifier | shell | `shells/code-repair/verifier.py` |
| Runtime | adapter | `molt/adapters/fixture/runtime.py` |
| Gate | core 默认 | `molt/core/policy.py` |
| Archive | core 默认 | `molt/core/archive.py` |
| Ledger | core 默认 | `molt/core/ledger.py` |

门禁阈值唯一 owner 是 `scenario.policy`。shell manifest 只能声明能力与上限
（switch 域、单 run 预算、artifact roots、shell 版本），不得声明容差、成本门槛、
invalid 比例或重复策略。

## 三、一次搜索的数据流

```
scenario.json ──policy──┐
genome v0 ──switches────┤
                        ├─ Engine.run_task ─→ Harness ─→ Runtime
task.json ──workspace───┘                         │
                                                  ├─ Verifier（沙箱外只读 pytest）
                                                  ↓
                              run.json + ledger/<run_id>.jsonl + events/search/<run_id>.jsonl
                                                  ↓
                              evaluate_search → result.json（首轮 pass@1、cost_per_solved）
                                                  ↓
                              policy.decide → gate_decision.json（逐题配对表）
                                                  ↓
                              adopt：快照 → generation → snapshot/archive → pointer
```

报告阶段单独走 `scope=report`：先写 `holdout.accessed` 事件，再跑留出题，
`result.scope=report` 必须同时含 `scores.search` 与 `scores.holdout`。
`SearchHistory` 与 `ReportArchive` 是两个类型，`Harness.propose` 只接受前者。

提案分支（B2a）：`SearchHistory` → `Engine.propose` → shell 注入的 LLM 策略或
`molt.core.proposer` 网格 → `Proposal`（扩展字段全在 `ext`）→
`proposals/<id>.json` + `ledger/<prop-id>.jsonl`（`kind=overhead`）+
`proposal.generated` 事件。提案成本不进入 `cost_per_solved`。

## 四、门禁口径

判定顺序固定：

1. `incomparable`：四项执行上限不全等，或任一侧 `cost_per_solved` 分母为 0；
2. `unstable`：任一侧 invalid 比例超过 `scenario.policy.invalid_ratio_max`；
3. `budget_exceeded`：`execution_budget_usd` 或 `search_overhead_budget_usd` 任一超限；
4. `reject/solved_dropped`：通过题数低于 `baseline - k`；
5. `reject/cost_not_lower`：`cost_per_solved_candidate > cost_per_solved_baseline`；
6. 否则 `adopt`（`lower_cost_per_solved` 或 `non_inferior`）。

`cost_per_solved` 的分母是有效通过题数，分子是全部执行 run（含 invalid / 超时 / 超预算 /
判废）的成本；提案成本只进 overhead cap。结论必须携带逐题配对表。

门禁夹具的 `SIDE_KEYS` 不含 `scenario_version`；逐侧版本用
`baseline_scenario_version` / `candidate_scenario_version` 覆盖，缺省回落到 case 级版本。
`SideEval.scenario_version` 必须与 scenario 一致，否则 `policy.decide` 抛
`IncomparableError`（禁止跨版本比较）。

## 五、裁判隔离

- 测试目录在候选工作区之外，Verifier 运行前后取树哈希，不一致判废；
- `isolation.network` 三档写死映射：`none → sandboxed`、`provider_only → provider-sandboxed`、
  `open → permissions-only`；
- 无法建立沙箱时降级为 `permissions-only`，`degraded=true`，claim 必须写
  `sandbox unavailable`，不得静默降级；
- agent / bash 侧禁止读取 `bench/**`、`web/**`、`evidence/**`；bash 在 `sandboxed` /
  `provider-sandboxed` 下一律无网络；
- archive 只存 `result_refs[]`，不内联分数；`web/` 的 holdout 只导聚合分。

## 六、事务与证据

`state/` 布局：

```
state/
  lock                     # flock 独占；拿不到立即 tx_locked
  current.json             # 指针：generation + snapshot_id + tree_hash + archive_id
  journal/<generation>.json
  generations/<8位代际>/<root>/...
  snapshots/<snapshot_id>.json
  archive/<archive_id>.json
  runs/<run_id>.json
  results/<result_id>.json
  decisions/<decision_id>.json
  proposals/<proposal_id>.json
  genomes/<genome_id>.json
  events/search/<run_id>.jsonl
  events/report/<run_id>.jsonl
  events/search.jsonl      # 单写者物化
  events/report.jsonl
  ledger/<owner_id>.jsonl
```

采纳顺序固定：journal（`O_EXCL`）→ generation（临时目录 rename）→ snapshot → archive →
pointer（最后）。`adopt` / `revert` 入口会先自动调用锁内的 `_recover_locked`，把上次未完成
事务恢复到旧态或新态；`doctor --recover` 作为显式兜底，重复调用幂等。
读者校验「指针 + generation + tree hash」，有界重试 3 次，超限 `stale_read`。
快照覆盖 manifest 声明的全部 roots，`roots` 与 `files` 同键，缺一即拒绝。

事件按 `scope` 分流；已知 type 的 payload 是 tagged union，未知 type 保留原文。
`audit` 只重算 canonical evidence：从 run / ledger 重算 result，从配对表与 genome 上限重算
gate，并断言 search 流零 holdout id、report 流至少一条 `holdout.accessed`。

## 七、版本触发

必须升 `scenario_version` 并重跑基线：`harness.py`、`verifier.py`、`task_provider.py`、
`switches.schema.json`、`bench/**` 任一变更。
新增候选文件（`genomes/`、`prompts/`、`tools/` 中的新文件）只产生新 `genome_id`；
原地修改已被既有 genome 引用的文件视为破坏性变更。禁止跨 `scenario_version` 比较。

## 八、提案层（B2a）

- **LLM proposer 是一等公民**：输入只能是当前 `scenario_version` 的 `SearchHistory`；
  `parent` 的 `genome_id`、`switches`、允许的三维枚举进入 prompt，holdout / report /
  gate 阈值不进入。
- **proposal.ext 契约**：`diagnosis`、`model`、`prompt_sha256`、`response_sha256`、
  `usage{input,output,cache_read,cache_write,reasoning,total}`、`cost_source`、
  `counted_as="search_overhead"`、`fallback{used,reason}`、`source`、`history`。
  顶层字段保持 B1 冻结的 10 项（见 ADR-0005）。
- **失败回落**：越域 / 空候选 / 坏 JSON / 超预算 / 缺 key / 网络错误都回落到通用三维网格，
  `source=grid`、`fallback.used=true`，仍写 Proposal 与 overhead ledger。
- **成本归属**：`cost_usd` 只在 `ledger/<prop-id>.jsonl` 的 `kind=overhead` 中出现；
  `cost_per_solved` 与 `result.scores.*` 不含提案成本，audit 按 `prop-*` 全量对账。
- **可追责**：每个被评估候选的 `proposal_id` 可回到 `ext.source=llm|grid` 与
  `proposal.generated` 事件。
