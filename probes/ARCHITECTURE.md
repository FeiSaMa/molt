# probes 架构：数据、比较域与证据链

这份文档只描述 probe 仪器**怎么流动**、**哪些数字能放一起**、**每个 sha256 在哪**。
题目约定与判定口径见 `probes/design.json`、`probes/decision-table.json`；
现场页面契约见 `probes/report/VISUAL_SPEC.md`。

## 1. 数据流

```
bench/probe/<task>/
  task.json                 ext.probe.function 指认要修的函数
  workspace/solution.py     stub（默认实现，可见示例能过）
  tests/check_cases.py      隐藏用例（expected 真值）
        │
        │  bench hash = 题目文件树摘要
        ▼
scenarios/probe/scenario.json
  scenario_version=probe-6，task_sets.search=8；policy.k / budget
        │
        ▼
shells/probe/genomes/*.json
  v0-* / oracle / gen2 / gen3 / gen4
  genome_id = sha256(canonical genome，含 scenario_version)
  artifacts{path → sha256}，ext.memory_ref 指向记忆文件
        │
        ▼
uv run python -m molt search \
    --scenario probe --runtime pi --genome <g> --state /tmp/molt-probe/<...>
        │
        │  shell/harness 装载 workspace；verifier 跑 check_cases.py；pi 产出代码
        ├──> <state>/runs/<run_id>.json       Run：outcome / usage / verification / artifacts / ext
        └──> <state>/results/<result_id>.json Result：scores.search（solved / cost / invalid_ratio）
        │
        ▼
gate / decision
  probes/run_2c.py → molt.core.policy.decide(...)
        │
        ▼
probes/report/**/*.json  ── probes/build_probe_example.py ──> web/data/probe/example.json
                         └─ probes/run_demo.py ─────────────> web/data/probe/state.json
                                                              web/data/probe/run.json
```

要点：

- **state 隔离**：所有 probe run 的 `--state` 必须在 `/tmp/molt-probe/**`
  （`probes/pilot-commands.json` 是白名单，`accept.py --stage isolation` 检查）。
  这样 `molt search` 的证据不会写进仓库，也不会混进 demo state。
- **一次 run 只做一件事**：每个 state 目录对应一题一次尝试；脚本自己汇总 `rows.json`，
  不依赖 `StateStore` 的跨题聚合。
- **报告不是数据源**：`report/**.json` 是落盘证据；页面只读 `web/data/probe/**`，
  demo 页面只读五个 Tier 1 文件。三者不互相回写。

## 2. 三个比较域（互不可比）

| 域 | `scenario_version` | 入口 | 主要证据 | 它能回答的问题 |
| --- | --- | --- | --- | --- |
| C1b：`probe-4/5/6` | `probe-4`（主证据）、`probe-5`/`probe-6`（重锚、现场） | `scenarios/probe` + `shells/probe` | `report/matrix/`、`report/2c/`、`report/2c/memory-v*` | 跨运行记忆能否迁移到同族新题 |
| C2：`c2-1` | `c2-1` | `scenarios/probe-c2` + `shells/probe-c2` | `report/c2/` | 第二个约定族是否复现「欠定 + 可失败」；结果是 `c2_invalid`（族题太容易被全部解掉） |
| demo：`demo-6` | `demo-6` | `scenarios/demo` + `shells/code-repair` | `web/data/*.json`、`evidence/` | 成功率不降时的成本/墙钟下降；与 probe 无关 |

为什么不能混：

- `genome_id` 覆盖 canonical genome（含 `scenario_version`、`parent_id`、`artifacts` 等），
  任一字段变都会换 id。例：`memory-v3/verdict.json` 记的 gen4 是 `sha256:be19bd9f…`
  （probe-4 提交里的文件），当前 `shells/probe/genomes/gen4.json` 是 `sha256:749d1f6f…`；
  artifacts 一致，差异来自 `scenario_version` / `parent_id`。`2c/verdict.json` 记的 candidate
  gen2 是 `f0ed8eef…`，也不等于当前 `gen2.json` 的 `44f6b2c1…`。引用证据时要带文件名和版本，
  不能只拿 id 前缀当「同一个实验」。
- `bench_hash`、任务集、判别用例都可能变：`probe-5 → probe-6` 只把 `probe-order-2` 的
  `case_04` 从 `["bb","bb","aaa"]` 改成 `["aaa","bb","bb"]`，就让 baseline 从「意外通过」
  变成「必败」。这种改动会使前后通过率不可直接相减。
- C2 用的是另一套 bench（`bench/probe-c2`）和另一套 shell（`probe-c2`），
  题面约定是舍入族，不是 C1b。
- demo-6 的题、shell、gate 口径都独立；probe 数据进入 `web/data` 五个 Tier 1 文件
  属于违规（见 `AGENTS.md` §web/data 契约）。

允许的比较只有一种：**同一个 `scenario_version` 内，baseline 与 candidate 的配对比较**。
跨版本只能并列陈述，不能画同一条曲线，也不能相减。

## 3. 记忆生命周期与 sha256 位置

```
probes/report/matrix/runs.json + probes/report/2c/memory-traces/*.json
        │  build_memory.generate_sources（确定性）
        ▼
shells/probe/memory/sources/pro-max-probe-order-{2,x3}.md
        │
        ├── build_memory（确定性拼接） ──────────────> shells/probe/memory/gen2.md
        │                                                 （gen2：失败用例回放，无反思）
        │
        └── pi 反思（≤5 行规则，thinking=max）
                    ├──> shells/probe/memory/gen3.md   （gen3：一次普通反思）
                    └──> shells/probe/memory/gen4.md   （gen4：约束反思，禁止参数名/类型/函数名）
        │
        ▼
genome.artifacts{path → sha256} + ext.memory_ref
        │  molt search（Run2）从 memory_ref 读文件；文件缺失 fail closed
        ▼
gate.json：paired baseline/candidate、cost_per_solved、verdict
```

哈希分别落在哪：

| 对象 | 位置 | 口径 |
| --- | --- | --- |
| `sources/*.md` | `shells/probe/genomes/gen2.json` 的 `artifacts` | 文件字节 sha256（`9a6667…`、`c2e5e4…`） |
| `gen2.md` | `gen2.json` 的 `artifacts`；`report/2c/proposal.json` 的 `ext.memory_sha256`；`report/2c/verdict.json` 的 `memory_sha256` | 文件字节 sha256（`c00636…`） |
| `gen3.md` | `gen3.json` 的 `artifacts`；`report/2c/memory-v2/verdict.json` 的 `memory_sha256` | artifacts 是文件字节（`b08f39…`）；verdict 记的是反思**响应文本**的 sha256（`78da84…`，无结尾换行） |
| `gen4.md` | `gen4.json` 的 `artifacts`；`report/2c/memory-v3/verdict.json` 的 `memory_sha256` | artifacts 是文件字节（`c660b1…`）；verdict 记响应文本（`b73d64…`） |
| genome 整体 | `genome_id` | `sha256:` + canonical genome 的 content hash（含 `scenario_version`） |
| 题目文件 | `scenario.bench_hash`；genome `artifacts` 里的 `shells/probe/*` | 文件/文件树 sha256（artifacts 逐路径取文件字节） |
| run 产物 | `run.artifacts["solution.py"]`；`run.verification.tests_hash_{before,after}` | 文件字节 sha256 |
| verifier | `run.verifier_hash`、`gate.verifier_hash` | `shells/probe/verifier.py` 字节 sha256（`b194ce…`） |
| 页面样例 | `web/data/probe/example.json` 的 `revision` | payload 去掉 `revision` 后的 content hash |
| 现场状态 | `web/data/probe/state.json` 的 `revision` | 同上；只作刷新信号，不是兼容性信号 |

注意两种「记忆 sha256」不要混：`genome.artifacts` 对**文件字节**取哈希；
`memory-v2/v3` 的 `verdict.memory_sha256` 来自 `reflection.json` 的
`gen3_sha256`/`gen4_sha256`，是对**模型响应文本**取哈希。两者只差一个结尾换行，
但值不同。校验文件完整性用 `uv run python -m molt genome verify <genome.json>`。

`gen2` 是确定性回放，没有反思成本；`gen3`/`gen4` 各多一次 `pi` 反思调用
（证据分别在 `report/2c/memory-v{2,3}/reflection.json`，含 prompt、原始响应、usage）。
`report/2c/memory-v3` 记录的 18 次 run 是 16 次 gen4（x5–x8 × 4 reps）加 2 次
x5 的 gen3 续跑；当前脚本一次调用只跑其中 10 次（每格 2 reps + x5 gen3 两次），
`rows.json` 里多出的 gen4 reps 3–4 不是这一次调用的产物。

## 4. 现场状态机

```
        （schema 里的 idle 只是契约占位；run_demo 的第一份 state 就是 run1）
                                  │
                                  ▼
   run1 ──每完成一题写 state──> reflection ──> run2 ──每完成一题写 state──> done
     │                            │              │
     └──────────── DemoStop(partial / timeout) ─┘
                                  │
                                  ▼
                     state.json / run.json（phase=partial|timeout，exit=2）
```

`state.json.phase` 的取值与触发条件：

| phase | 何时写入 |
| --- | --- |
| `idle` | schema 允许，但 `run_demo` 不产出；不要把它当 live 数据 |
| `run1` | `v0-pro-max` 跑 baseline 题，每完成一题增量写一次 |
| `reflection` | Run1 结束、反思调用进行中（写 state 时带已收集的失败用例） |
| `run2` | 用新 memory 构造 genome 后跑迁移题，每完成一题增量写一次 |
| `done` | Run2 结束，算 verdict / cost / wall，写最终 `state.json` 与 `run.json` |
| `partial` | 单题超过剩余墙钟预算（杀进程组，保留已完成题）；阶段边界成本超 `--max-cost`；反思调用失败/无可用文本；run1/run2 genome 校验失败；`memory_ref` 文件缺失；读不到 run 记录；`task.json` 缺 `ext.probe.function` |
| `timeout` | 阶段边界 `check_limits` 发现总墙钟耗尽，或剩余不足 20s；反思超时且剩余墙钟不足以再留 240s |

两档 profile 只改题集与默认预算，不改状态机：

- `fast`：Run1 只跑 `probe-order-2`，Run2 只跑 `probe-order-x5`/`x6`；默认 360s。
- `full`：Run1 跑 `probe-order-2`/`x3`，Run2 跑 `x5`–`x8`；默认 420s。

## 5. 证据链：run → result → gate → verdict

```
run.json
  outcome / usage{cost_usd,tokens,turns,wall_ms}
  verification{tests_hash_before,tests_hash_after,matches}
  artifacts{solution.py: sha256}
  ext.probe{mode,cell,convention_id,oracle}      ← 现场标记
        │
        ▼
result.json
  run_refs[]，scores.search{solved,total,cost_total_usd,cost_per_solved_usd,invalid_ratio}
        │
        ▼
gate.json（molt.core.policy.decide）
  paired[]：task_id / baseline_passed / candidate_passed / 两侧 cost
  scores：两侧 solved、cost_per_solved
  verifier_hash、limits_identical、invalid_ratio、verdict+reason
        │
        ▼
verdict.json（各批脚本自算）
  rows 汇总 → F/K 或 solved/cost_total → 预注册规则 → verdict
```

可重算（确定性）：

- 所有 sha256：`artifacts`、`verification.tests_hash_*`、`verifier_hash`、`genome_id`、
  `example.json` 的 `revision`；用 `sha256sum` 或 `molt genome verify`（记忆响应文本那种
  口径除外，见上一节的两种哈希）。
- 成本汇总：`usage.cost_usd` 求和；`cost_per_solved = cost_total / solved`。
- 配对表与 gate 输入：从 `run` 记录取 `solved` 与 `cost_usd`，按 `task_id` 配对；
  `decide()` 的输入是两边的 `TaskEval`、`limits`、`invalid_ratio`。
- 矩阵判定：`F` 向量查 `probes/decision-table.json`；表的 sha256 在
  `report/matrix/decision-meta.json`，脚本会重新校验。
- 有效性/C2：`verdict` 由 oracle/控制是否正常、族题是否可失败直接算出。

不可重算（必须重跑模型或读原始记录）：

- 模型实际写了什么代码、反思生成了哪五行——证据是 `run.artifacts["solution.py"]`、
  `run.ext.runtime.argv`（渲染后的 prompt/模型/thinking）、`reflection.json` 的
  `response_text`。重新调用不会逐字节复现（seed 不可控）。
- 因此「通过率」只能从已记录 run 汇总；不要用一次新 run 覆盖旧 verdict。

## 6. `run.ext.probe` 的定位

`run.ext.probe` 由 `molt/adapters/pi/runtime.py` 的 `_probe_payload` 写入：

```json
{"probe": {"mode": "max|high_only", "cell": "gen2", "convention_id": "C1", "oracle": false}}
```

它是**现场标记**，回答「这条 run 属于哪个 probe 格子、跑在哪个 thinking 档」。
它不是 demo-6 的字段：

- demo-6 的 genome 没有 `ext.probe`，`run.ext.probe` 不会出现；
- demo-6 的 claim、gate、页面都不读它；
- 反过来，不能拿 `run.ext.probe.cell` 去补 demo-6 的 `demo_group`/`provenance`。

现场 `run_demo.py` 会把 genome 的 `ext.probe.cell` 改成 `live-memory`，再写进 Run2 的 run；
这只是给现场证据留标记，不改变判定逻辑。
