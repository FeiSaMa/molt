# probes — 跨运行记忆的测量仪器

## 它是什么 / 不是什么

- **是什么**：用欠定约定族（同一函数有多种都说得通的实现，工作区只给部分示例）+ 隐藏用例 +
  oracle/控制题，测「失败 → 反思 → 记忆 → 迁移」这条链路有没有可测效果。
  仪器本身只负责出题、隔离 state、跑真实 `pi`、把每一轮落成证据；判定靠预注册规则，不靠现场解释。
- **不是什么**：不是 core，不参与 demo-6 的对外 claim。probe 数据**不进** `web/data` 的五个
  Tier 1 文件（`index.json`、`scenario.json`、`runs.json`、`gate.json`、`variants.json`）；
  `web/data/probe/` 是独立演示数据面。probe 的数字也不能拿来证明 demo-6 的降本结论。

## 目录

- `probes/*.py`：仪器脚本（见下表）。
- `probes/decision-table.json`：81 + 9 行预注册真值表；F 向量 → verdict 的映射在跑实验前冻结。
- `probes/design.json`：每题约定（C1/C1b/C2）、默认实现、项目实现、facet、判别用例说明。
- `probes/pilot-commands.json`：全部 probe 命令与 state 白名单（所有 state 必须在 `/tmp/molt-probe/**`）。
- `probes/report/`：证据（`preflight/`、`batch1/`、`validity/`、`matrix/`、`2c/`、`c2/`、
  `stability/`、`rehearsal/`，以及 `FINAL.md`、`PITCH.md`、`SUMMARY.md`、`VISUAL_SPEC.md`）。

## 脚本表

「是否调模型」指是否发起真实 `pi` 调用；成本取自仓库内证据，不是估算上限。

| 脚本 | 作用 | 是否调模型 | 典型成本 |
| --- | --- | --- | --- |
| `accept.py` | Batch 1 验收：12 个 `--stage` 用真实文件/命令判定 | 否（只跑 `pi --version`） | $0，秒级 |
| `run_validity.py` | 2a 有效性：oracle/控制/族题，16 runs | 是 | ≈ $0.80 |
| `run_matrix.py` | 2b 矩阵：4 格 × (2 控制 + 2 族题) × 2 reps + oracle 4 = 36 runs | 是 | ≈ $1.00（上限 $2.00） |
| `build_memory.py` | 从失败 trace 生成 `sources/*.md`、`gen2.md` 和 memory proposal | 否（确定性） | $0，秒级 |
| `run_2c.py` | 2c 主实验：baseline vs gen2 在 x5–x8，含控制与 gate | 是 | ≈ $2.30（上限 $2.50） |
| `run_memory_v2.py` | gen3 反思记忆在 x5/x6/x7 上对比 v1 | 是 | ≈ $0.53（含反思） |
| `run_memory_v3.py` | gen4 约束反思在 x5–x8，另加 x5 gen3 两次续跑 | 是 | ≈ $0.46（证据 18 runs；单次调用 10 runs 更低） |
| `run_c2.py` | C2 舍入族有效性门：16 runs | 是 | ≈ $0.73（上限 $1.50） |
| `run_demo.py` | 现场演示：Run1 → 反思 → Run2，增量写 state/run | 是 | fast ≈ $0.05–0.07 / 98s、89s（彩排两次，见 `probes/report/rehearsal/`）；full 上限 $1.20 / 420s |
| `build_probe_example.py` | 从 2c 证据生成可视化样例 `example.json` | 否（确定性） | $0，秒级 |
| `check_matrix_functions.py` | 检查矩阵 run 是否真的改动了 `ext.probe.function` 指向的函数 | 否（AST） | $0，秒级 |
| `compare.py` | 只读冻结决策表，提供 `lookup` / `verdict_for` | 否 | $0 |
| `build_decision_table.py` | 一次性生成 81+9 决策表；运行时不用 | 否 | $0 |

矩阵 v3 有已知 `function_check.all_ok=false`：`pro-high/probe-order-x3` 有 run 没改目标函数
（见 `probes/report/matrix/function-check.json`）。触发格 `pro-max` 是干净的。

## 四条主流程（外加一个可视化样例）

1. **有效性**：`uv run python probes/run_validity.py` → `probes/report/validity/verdict.json`
   （16 runs；`green` 表示族题可失败、控制/oracle 正常）。
   注意：这份证据是 `probe-1` 的探索口径，probe-2 的权威测量在矩阵；不要把 2a 的 16 runs
   当成主结论。
2. **矩阵**：`uv run python probes/run_matrix.py` → `probes/report/matrix/verdict.json`
   （36 runs；F/K 向量查冻结决策表，`memory_trigger` 才进入 2c）。
3. **记忆**：`uv run python probes/build_memory.py` → `uv run python probes/run_2c.py` → gate
   （`probes/report/2c/gate.json`；`molt.core.policy.decide` 给 adopt/reject）。
   反思代际另跑 `run_memory_v2.py`（gen3）与 `run_memory_v3.py`（gen4）。
4. **现场**：`uv run python -m probes.run_demo --profile fast` → `web/data/probe/state.json`
   （`--state` 必须在仓库外；生成物在 `.gitignore` 内）。
5. **可视化样例**：`uv run python probes/build_probe_example.py` → `web/data/probe/example.json`
   （从已记录证据确定性重放，不调模型；用于页面开发和离线展示）。

## 证据地图

| `report/<dir>` | 内容 | 复算命令 |
| --- | --- | --- |
| `preflight/` | `pi` 预检、blind/stub spike、prompt 修复前后的对照 | `uv run python -m probes.accept --stage max_preflight` |
| `batch1/` | 盲测/占位/oracle traces、`rows.json` | `uv run python -m probes.accept --stage blind`（`/tmp/molt-probe/batch1/blind` 缺失时自动读 tracked `blind-trace.json`） |
| `validity/` | 2a 16 runs、`verdict.json`（`green`） | `uv run python probes/run_validity.py`（真调模型，≈$0.80） |
| `matrix/` | 2b 36 runs、`verdict.json`、`decision-meta.json`、`function-check*.json`、作废的 v1/v2 结果 | `uv run python probes/run_matrix.py`；静态检查 `uv run python probes/check_matrix_functions.py`（已知 `all_ok=false`） |
| `2c/` | 主实验 rows/gate/verdict、`solutions/`、`memory-traces/`、`memory-v2/`、`memory-v3/`、`followup.json`、`x8-analysis.md` | `uv run python probes/run_2c.py`（≈$2.30）；`build_memory.py`；`run_memory_v2.py` / `run_memory_v3.py`；`uv run python -m probes.accept --stage memory` |
| `c2/` | C2 族 16 runs；`verdict=c2_invalid`（族题被 baseline 全解，属负结果） | `uv run python probes/run_c2.py`；`uv run python -m probes.accept --stage c2` |
| `stability/` | 同一组失败用例的 6 次独立反思采样与分类 | 无 runner；逐字输出见 `reflections.json`，prompt 见 `sources.md` |
| `rehearsal/` | 现场 demo 的 run1/run2 state 与 run 快照 | `uv run python -m probes.run_demo --validate --out probes/report/rehearsal/run2/state.json --run-out probes/report/rehearsal/run2/run.json` |
| `FINAL.md` / `PITCH.md` / `SUMMARY.md` | 一页结论、路演台词、给评委的摘要（数字都带出处） | 不适用（叙述文档，数字回指上面的 JSON） |
| `VISUAL_SPEC.md` / `probe_state_schema.json` | 页面数据契约与视觉规格 | `uv run python -m probes.accept --stage visual` |

## 复算入口

```bash
uv run pytest -q
uv run python -m probes.accept --stage <name>
uv run python -m probes.accept --stage history
uv run python -m molt scenario validate --scenario scenarios/probe/scenario.json
uv run python -m molt genome verify shells/probe/genomes/gen4.json
```

`history` 只读 tracked JSON 重算 v3/headroom/控制/稳定性；历史 runner（`run_2c.py`、
`run_memory_v3.py` 等）是新实验入口，不是累计报告的重建器，重跑会覆盖 `rows/verdict`。

`--stage` 可选：`max_preflight`、`freeze`、`isolation`、`ambiguity`、`leak`、`default_bias`、
`c2`、`family`、`blind`、`history`、`memory`、`visual`。

## 边界

- 不改 core / schema / demo-6；probe 只消费 `molt search`、`molt scenario validate` 等既有入口。
- 不把 probe 数据当 demo-6 证据；两边 `scenario_version`、`bench_hash`、genome id 都不同。
- 跨 `scenario_version` 不比较：`probe-4`（主证据）→ `probe-5`/`probe-6`（重锚与现场）会重算
  `bench_hash` 与 genome id；`c2-1`、`demo-6` 同理。
