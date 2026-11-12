# Headroom probe：跨运行记忆的可测边界

> 本报告只使用已有证据；每个实验数字在括号内标注来源路径。
> 结论是**描述性**的，不进入对外 claim，不声称通用自进化。

## 1. 问题

demo-6 的成功率已经饱和（`AGENTS.md` §一记录真实基线 10/10），剩下可测的只有成本与墙钟 delta。
本探针要回答：在**成功率不降**的约束下，跨运行记忆是否能带来可复现的通过数提升？
如果不能，负结果本身也是结论：哪些“学习”只是题目默认偏置、哪些是记忆负迁移。

## 2. 设计

- 题源：欠定约定族 **C1b**（casefold 去重、保留 C1a 下 raw 最小代表）。
- 默认偏置：`sorted(set(names))`（大小写敏感去重）；它在互异小写示例上与 C1b 相同，但在大小写混合用例上失败。
- 隐藏用例：每道题至少 1 个默认必败的判别用例；checker 只输出 `case_id / expected / actual / passed`，trace 只给反例，不给规则全文。
- 控制题：`sign_label`、`chunk`，规格完整、无隐藏约定；用于判断探针是否“只是模型不会做题”。
- oracle：pro-max + 注入 C1 全文；用于确认题目可解、harness 正常。
- 2 道族题：`probe-order-2`（`dedupe`）、`probe-order-x3`（`dedupe_top_k`）；4 道迁移题：`x5..x8`（`unique`、`dedupe_merge`、`dedupe_min`、`distinct_count`）。
- 题目与 facet 登记在 `probes/design.json`；当前 12 道题、probe-6（主证据为 probe-4；probe-6 只用于现场 demo）、`shell_version=0.3.0`。

## 3. 仪器

- probe shell：`render_prompt` 从 `task.ext.probe.function` 参数化函数名；`ext.memory_ref` 存在时读取记忆文件，缺失 fail closed。
- 脱敏 verifier：只回 `case_id / expected / actual / passed`；不保存 pytest 原始 stdout/stderr、测试路径或测试名。
- state 隔离：所有 probe run 显式 `--state <probe-root>/...`；demo state 不参与。
- 决策表：`probes/decision-table.json` 冻结 **81 + 9** 行；sha256
  `6adee4dea3b59f0e0bb2dbf3455a93d0e142cf2fc494b36ef0bf1e7509fe84d6`
  （`probes/report/matrix/decision-meta.json`）。
- oracle/control：矩阵中 oracle 4/4、控制 K=2/2（`probes/report/matrix/verdict.json`）。

## 4. 实验序列（含失败）

### v1/v2：SWE-CC 式策略合规

两次 spike 的 reference 全过（4/4）、stub 3/3 策略失败，但 blind pro-max 的 3 条策略**全部通过**（0/3 失败），无法形成失败信号：
`probes/report/preflight/spike.json`、`probes/report/preflight/spike-v2.json`。
结论：策略合规题被 pro-max 直接读懂，放弃该题源。

### v3：C1a

`probes/report/matrix/verdict-v2-t1-t2.json`：

- `verdict=max_degradation`，`F(pro,max)=0 < F(pro,high)=1`；
- 唯一来源是 pro-high 在 T1 上 1/2；它写出了 `(len, s)` 这类“半猜中”实现；
- `F` = `{pro-max:0, pro-high:1, flash-max:0, flash-high:0}`，`K` 全 2；oracle 4/4；
- 成本 $0.83013787。

结论：T1/C1a facet 太容易被半猜中，退出族题，重锚 C1b。

### v4：C1b

`probes/report/matrix/verdict.json`：

- `verdict=memory_trigger`；
- `F={pro-max:0, pro-high:0, flash-max:0, flash-high:0}`，`K` 全 2；oracle task-level 2/2、run-level 4/4；
- 成本 $0.99972634；table sha 同上；
- 但 `function_check.all_ok=false`：`pro-high/probe-order-x3` 两次没有改目标函数（r2 wall/tokens invalid）。触发格 pro-max 是干净的。

### 2c：记忆迁移

`probes/report/2c/verdict.json`、`probes/report/2c/control-rows.json`：

- 主 verdict：**`partial_transfer`**（未升级为 `memory_works`）；
- memory **3/4**、baseline **1/4**、control **2/2**；
- gate：`adopt / lower_cost_per_solved`（`probes/report/2c/gate.json`）；
- 主实验 + 控制题成本 **$2.30024424**（main $2.25976688 + control $0.04047736）。

逐题稳定性（`probes/report/2c/verdict.json:stability`、`probes/report/2c/followup.json`）：

| 题 | memory 通过 | baseline 通过 | 判断 |
|---|---|---|---|
| x5 | 4/4 | 0/4 | **stable_pass** |
| x6 | 3/4 | 0/4 | jitter |
| x7 | 1/4 | 0/2 | jitter |
| x8 | 0/4 | 2/2 | **negative_transfer** |

x8 的归因见 `probes/report/2c/x8-analysis.md`：baseline 用 `len({casefold})` 正常返回 int；memory 把签名改成
`-> list[str]` 并加 `k`，返回列表，`actual != expected`。疑似由 task-b 的 `k=2` 列表用例诱发，标 `[unverified]`。

### followup

- x8 签名约束：`no-memory 0/2`、`memory 0/2`，结果 `not_explained_by_signature_constraint`；memory 组仍改了签名，no-memory 组未改函数体，实验被 no-op 混淆（`probes/report/2c/followup.json`）。
- x7 复现：原 1/2 + 新 0/2 = **1/4 jitter**（同上）。
- x5/x6 复现：x5 新增 2/2（合计 **4/4**），x6 新增 1/2（合计 **3/4**）；baseline 均为 0/4（`probes/report/2c/followup.json`）。

### memory v2（反思式）

- 一次 `pi` 反思：只输入 sources，不读 `design.json`；model `deepseek-v4-pro`、thinking `max`、成本 **$0.03685308**（`probes/report/2c/memory-v2/reflection.json`）。
- 生成 `shells/probe/memory/gen3.md`（4 条规则），gen3 genome 用 0.2.0 prompt、`memory_ref=gen3.md`。
- 对比（v1 / v2 / baseline；v2 的 x5 含 2 次 gen3 补齐，合计 4/4）：

| 题 | v1 memory | v2 memory | baseline |
|---|---|---|---|
| x5 | 4/4 | **4/4**（含 gen3 补齐 2 次） | 0/4 |
| x6 | 3/4 | **4/4** | 0/4 |
| x7 | 1/4 | **4/4** | 0/2 |
| x8 | 0/4 | **0/2** | 2/2 |

- v2 判定：`reflection_better`（通过率：x5 持平 1.00；x6 1.00 > 0.75；x7 1.00 > 0.25；`probes/report/2c/memory-v2/verdict.json`）。
- **x8 仍是 `x8_still_negative`（0/2）**：反思记忆没有修掉 x8 的计数函数被改成 list/k 的问题。
- **签名漂移**：10/12 v2 runs 改了签名（x5 2/2、x6 4/4、x7 2/4、x8 2/2）；按预注册只记 caveat，不改判定。
- 成本：reflection + 12 runs = **$0.53273915**（`probes/report/2c/memory-v2/verdict.json`）。
- 结论按实际数字：v2 的 x5/x6/x7 都达到 4/4（x5 含 gen3 补齐），x8 仍是负迁移。

### memory v3（规则型反思）

- 固定 prompt 只允许描述分组/去重/排序规则；模型 `deepseek-v4-pro`、thinking `max`、成本 **$0.02970132**（`probes/report/2c/memory-v3/reflection.json`）。
- 输出 5 行、无参数名/返回值类型/函数名；gen4 genome 用 0.2.0 prompt、`memory_ref=gen4.md`。
- v3 4 reps 结果（`probes/report/2c/memory-v3/verdict.json`）：

| 题 | v1 | v2 | v3 | baseline |
|---|---|---|---|---|
| x5 | 4/4 | 4/4（含 gen3 补齐） | **4/4** | 0/4 |
| x6 | 3/4 | 4/4 | **4/4** | 0/4 |
| x7 | 1/4 | 4/4 | **4/4** | 0/2 |
| x8 | 0/4 | 0/2 | **4/4** | 2/2 |

- v3 判定：**`v3_stable_all`**（16/16）。x8 从 0/2 修到 4/4；但 x8 baseline 2/2，所以这不是相对 baseline 的提升。
- all-four：v3 **16/16** vs baseline **2/12**；headroom-three：v3 **12/12** vs baseline **0/10**。
- 签名漂移：v2 **10/12**，v3 **5/16**（x5-r2、x5-r3、x6-r1、x6-r2、x8-r3）；x7 全部干净。
- 成本：reflection + 16 gen4 + 2 gen3 x5 补齐 = **$0.4589434**；未触发 $1.0 stop。

### 方法演进

| 方法 | x5 | x6 | x7 | x8 | 签名漂移 | x8 结果 |
|---|---|---|---|---|---|---|
| v1 回放（原始反例） | 4/4 | 3/4 | 1/4 | 0/4 | —（未系统统计） | negative |
| v2 反思（gen3） | 4/4 | 4/4 | 4/4 | 0/2 | 10/12 | negative |
| v3 约束反思（gen4） | 4/4 | 4/4 | 4/4 | 4/4 | 5/16 | fixed，但 baseline 2/2 |

### 演示稳定性（probe-6）

probe-4 的 T2 只有三个失败用例，长度升序与频次升序都能拟合：case_01/02/03 的期望在两个排序规则下相同，因此 fast profile（Run1 只跑 T2、Run2 跑 x5/x6）的反思会在两种规则之间摆动。实跑 5 轮 fast：3 轮 `v3_stable_headroom`，2 轮不是（1 轮 `partial_transfer`，x5 被频次规则带偏；1 轮反思调用超时中止）。这不是题面通过率变化，而是演示记忆的稳定性问题。

probe-5 的 `case_04` 用 `["bb", "bb", "aaa"]`，但它被 baseline 的 `dict.fromkeys` 保序去重意外通过，未进入反思素材。probe-6 把它改为 `["aaa", "bb", "bb"]`：长度排序给 `["bb", "aaa"]`；频次排序、`sorted(set())` 与 `dict.fromkeys` 都给 `["aaa", "bb"]`，baseline 必败，因此该用例会进入反思。

主证据仍是 probe-4（`probes/report/2c/**`、`web/data/probe/example.json`）；probe-6 只用于现场 live demo，不与 probe-4 的数字混算。

反思稳定性（2026-11-12）：对同一组 T2 失败用例（含 case_04）独立反思 6 次，pro 3/3、flash 3/3 都把排序规则描述为“长度升序”，0/6 漂移到频次或大写数量，0/6 提及 k/limit/数量上限。样本仅 6 次、单一 prompt，属描述性结果。证据：`probes/report/stability/reflections.json`。

## 5. 结果表

矩阵 F/K（`probes/report/matrix/verdict.json`）：

| cell | F | K |
|---|---|---|
| pro-max | 0 | 2 |
| pro-high | 0 | 2 |
| flash-max | 0 | 2 |
| flash-high | 0 | 2 |

2c 逐题与控制（`probes/report/2c/verdict.json` / `control-rows.json`）：

| 项 | memory | baseline |
|---|---|---|
| x5 | 4/4 | 0/4 |
| x6 | 3/4 | 0/4 |
| x7 | 1/4 | 0/2 |
| x8 | 0/4 | 2/2 |
| ctl-a | 2/2 | — |
| ctl-b | 2/2 | — |
| 总通过 | 3/4（主口径） | 1/4（主口径） |

成本：

- v4 矩阵：$0.99972634（`probes/report/matrix/verdict.json`）
- 2c 主实验 + 控制：$2.30024424（`probes/report/2c/verdict.json`）
- x8/x7 followup：$0.66560745；x5/x6 新增 8 runs：$0.89713394；followup 累计 $1.56274139（`probes/report/2c/followup.json`）

## 6. C2 舍入族（轻量）

目标：只验证“欠定约定族 + 默认偏置”在 C2 上是否成立；不写迁移题、不做反思、不跑 Run2。

设计：

- C2a：`format_percent(p: Decimal) -> str`，保留两位小数，half-up（0.5 远离零），负数同向远离零。
- C2b：`round_ratio(x: Decimal) -> Decimal`，同一舍入规则。
- 默认实现：`f"{p:.2f}"` / `x.quantize(Decimal("0.01"))`，即 Decimal 上下文的 half-even。
- 隐藏用例：`12.125 → 12.13`、`-12.125 → -12.13`；默认 half-even 给 `12.12` / `-12.12`。
- 控制：`c2-ctl-a`（`sign_label`）、`c2-ctl-b`（`chunk`），规格完整、无隐藏约定。
- 题集：4 题（2 族 + 2 控制），`scenario_version=c2-1`。probe shell 的 task_provider 硬编码
  `bench/probe`；为保住 probe-6 的 artifact/genome，C2 使用同构隔离 shell `probe-c2`
  （`task_provider` 只读 `bench/probe-c2`），scenario 的 `base_shell` 记为 `probe`。

16 次结果（`probes/report/c2/verdict.json`）：

| cell | task | passes/runs |
|---|---|---|
| family | `c2-percent` | 4/4 |
| family | `c2-ratio` | 4/4 |
| control | `c2-ctl-a` | 2/2 |
| control | `c2-ctl-b` | 2/2 |
| oracle | `c2-percent` | 2/2 |
| oracle | `c2-ratio` | 2/2 |

- 16/16 run 有有效 outcome，无缺样本；成本 **$0.73449113**，未触发 $1.3 stop。
- 判定：**`c2_invalid`**。族题 best-of-4 为 2/2（期望 0/2）；baseline 在 4 reps 内都直接猜到了
  `ROUND_HALF_UP`，默认 half-even 偏置没有形成必败门槛。
- 结论只覆盖有效性：C2 不能作为“默认偏置有效性”的第二个正面样本；不声称记忆迁移，也不生成
  可视化样例。

## 7. 负结果

- **prompt 硬编码 `top_k`**：v1 矩阵的 T2 全部无效；`probes/report/matrix/verdict-v1-invalid.json` 的
  `invalid_reason="probe prompt hardcoded top_k; T2 runs invalid"`，成本 $1.37784319。
- **导出层过滤误删 demo live run**：加 `probe` ext 过滤会丢掉所有 pi live；已回滚，`molt/export.py` 当前零 diff；
  `tests/test_probe_export_isolation.py` 只验证“导出不过滤、隔离靠 state”。
- **T1/C1a 半猜中**：`verdict-v2-t1-t2.json` 的 `F(pro-high)=1`，来自 T1 的 `(len, s)` 半猜中。
- **x8 记忆负迁移**：v1 memory 0/4 vs baseline 2/2；v2 仍 0/2（`x8_still_negative`）；签名被改成 `-> list[str]` 并加 `k`（`probes/report/2c/x8-analysis.md`、`memory-v2/verdict.json`）。
- **x7 抖动（v1）**：v1 4 次仅 1 次通过（`followup.json`）；v2 达到 4/4，但 2/4 runs 仍有签名漂移。

## 8. 局限

- 样本小：每格 2 reps，2c 每题最多 4 reps；没有统计显著性。
- 单一模型对：pro/flash × high/max；没有多模型、多 seed。
- 单一约定：C1b；迁移题只有 4 道，x8 还出现负迁移。
- probe shell 是独立 shell；probe 结果不进入 demo 的 `bench_hash`，也不进入对外 claim。
- 2c 的 gate 使用 $2.5 execution cap，`scenario.policy` 仍是 $1.0；两者不同层，报告只把 gate 作为附加信号。

## 9. 复算命令

```bash
uv run pytest -q
uv run python probes/accept.py --stage memory
uv run python probes/accept.py --stage isolation
uv run python -m probes.accept --stage history
uv run python -m molt scenario validate --scenario scenarios/probe/scenario.json
uv run python -m molt scenario validate --scenario scenarios/demo/scenario.json
uv run python -m molt genome verify shells/probe/genomes/gen2.json
uv run python -m molt genome verify shells/probe/genomes/gen2-sig.json
```

`--stage history` 只读 tracked JSON 重算 v3/headroom/控制/稳定性；`run_memory_v3.py`
的 8 次 gen4 + 2 次 gen3 是新实验入口，不等于报告里的 16+2 条累计证据，重跑会覆盖
`probes/report/2c/memory-v3/rows.json` 与 `verdict.json`。

2c 的 verdict/gate 来自：

- `probes/report/2c/verdict.json`
- `probes/report/2c/gate.json`
- `probes/report/2c/rows.json`（主 16 runs）
- `probes/report/2c/control-rows.json`（控制 4 runs）
- `probes/report/2c/followup.json`（x8/x7/x5/x6 复现）
- `probes/report/2c/memory-v2/verdict.json`（反思式 memory v2：x5/x6/x7/x8）
- `probes/report/2c/memory-v3/verdict.json`（规则型 memory v3：x5/x6/x7/x8）

## 10. 结论

**v3 在 4 道迁移题、4 次重复下 16/16 全部通过（`v3_stable_all`）；x8 从 0/2 修到 4/4，但 x8 baseline 2/2，属于基线本就可解，不构成提升；签名漂移 v2 10/12 → v3 5/16；主 verdict 仍 `partial_transfer`，不声称通用自进化。**
