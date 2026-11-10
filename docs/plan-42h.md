# 42 小时落地方案（最终取舍）

> 2026-11-10 标注：**superseded**。实现与契约以
> [`docs/protocol.md`](../protocol.md)、[`schemas/v1/`](../../schemas/v1)、
> [ADR-0002](../decisions/0002-protocol-v1.md)、[ADR-0003](../decisions/0003-scoring.md)、
> [ADR-0004](../decisions/0004-shell-and-dims.md) 为准。本文件保留作取舍记录；
> 其中「明确禁止装回 SoL-Pi」的结论仍有效。

目标不是"做出自进化 agent"，而是交付一个**能被检验的结论**：在固定题集上，某个 harness 配置在成功率不降的前提下把单题成本降了多少，且每个改动都能回退、能复现。

## 1. 取舍

| 项 | 决定 | 理由 |
| --- | --- | --- |
| harness 底层参数 | **做** | 第一档，便宜、可测、可归因 |
| SoL-Pi（reducer / action fusion / observation pack） | **砍** | 需安装；本机 Pi 1.4.0 超出其测过的 1.3.0；未知键直接拒绝加载；属最后一档 |
| 记忆库 / 向量检索 | **砍** | 第二档；10 道题的固定集没有跨任务复用，收益无法体现 |
| skill evolving | **砍** | 第三档；需要 family 题集真的同族，42 小时做不出可信证据 |
| agent 模块设计（subagent / 多 agent） | **砍** | 证据显示拓扑不是主变量；Pi 已有的模块不改 |
| 路由 / 失败升级 | **暂缓（stretch）** | 唯一值得做的模块，但会拖慢主实验；H30 前有时间再上 |
| 模型维度 | **砍** | 本机只配了 deepseek 一个 provider；本地 9B 需起 server 且能力分布完全不同 |
| compaction 维度 | **砍** | DeepSeek 1M 窗口 + reserve 16384，跑到 984k 才触发，这道题上不存在的维度 |
| `escalation_threshold` / `tool_granularity` / `compaction_ratio` | **删** | 与上游语义不符或已在别处表达 |

## 2. 唯一的 claim

> 在 10 道固定题上，配置 X 与基线成功率相同，单题成本降低 N%；每轮运行都有独立测试当裁判，配置、提示、工具集进同一个快照，可原子回退。

不要写"agent 自己进化"。这句话在 2026 年既没有区分度，也无法在 42 小时内证明。

## 3. 新的开关定义

只搜三维，12 组：

```text
thinking_level   off | low | high        # --thinking
context_mode     full | minimal          # minimal = --no-skills --no-context-files
tools_mode       default | lean          # lean = 去掉 write（逼模型用 edit）
```

固定不搜：`max_concurrency = 4`、`retry.maxRetries = 3`、`compaction` 默认。

可选的第四维（stretch）：`escalation = off | on`，on 表示首次失败后用 `thinking=high` 重跑一次，两次成本都计入。

删除 `config.py` 里的 `reducer_enabled`、`compaction_ratio`、`action_fusion`、`tool_granularity`、`escalation_threshold`。`configs/v0-baseline.json` 同步改。字段改名属于破坏性变更，但现在是骨架阶段，没有历史数据要迁移。

## 4. 题集

题数由两件事决定，不是拍一个整数：

- **成功率**是二值指标，配对比较（McNemar）下 10 道题测不出 1–2 题差异：b=2 时 p=0.5，b=4 时 p=0.125。想声称"成功率提升"至少要 30 道以上。所以本项目**不声称提升，只声称不劣**。
- **成本**是连续量，且同题配对后差值方差远小于绝对值方差，10 道足够分辨 20–30% 的差距。

决策规则：

| 题数 | 适用 |
| --- | --- |
| < 8 | 不可用，一道 flaky 就占 12% 以上 |
| 10 | 下限，够看成本差 |
| 12–15 | 推荐，留出 1–2 道题的容错 |
| > 15 | 只有在"同仓库多 bug"摊薄了出题成本时才值得 |

holdout 5 道就够——它只用于汇报，不参与选择，不需要统计功效。

如果时间紧，**优先加题而不是加重复**：重复测的是噪声，题目测的是泛化，演示里后者才是故事。

- 全部来自 3 个小型 Python 仓库的真实 bug：把修复回退，配一个修复前失败、修复后通过的 pytest。
- 每题一个 workdir 模板，测试文件不在模板内，由 runner 只读挂载/复制到工作区外执行。
- 难度校准：基线（thinking=off，默认工具）应当通过 6–8 道。全过或全不过都说明题目没设计好。
- 时间成本最高的一环，预计 6–8 小时。先把 3 道做成样板，再批量复制。

## 5. 度量与门禁

主指标：

```text
cost_per_solved = 全部运行成本 / 通过题数
约束：通过题数 >= 基线通过题数
```

副作用指标全部落 `run.json`（契约要加字段）：`model`、`thinking_level`、`cost_usd`、`cache_read_tokens`、`turns`、`max_context_tokens`。

门禁：候选通过题数不得少于基线；只掉 1 题时用 3 次重复判断是不是噪声。`result.json` 同时写 search 与 holdout 两组分数，holdout 不参与选择。

## 6. 42 小时排期

| 时段 | 内容 | 出口条件（不满足就降级） |
| --- | --- | --- |
| H0–2 | 冻结范围；`git init` + 首次提交；`doctor` 检查 pi 版本、`--approve`、session 账本路径 | 手工跑一次 `pi -p --mode json`，能从 session JSONL 读出 cost |
| H2–8 | `build_pi_argv`、`run_task`、账本解析、`store` 读写 | 1 道题端到端产出完整 `run.json` |
| H8–18 | 题集：3 道样板 → 15 道正式；`run_suite` 并发 | 基线通过 ≥ 6/10 |
| H18–24 | `score` + 门禁 + `result.json`；启动 12 组扫描 | 12 组跑完，账本无缺失 |
| H24–30 | 快照 + `revert`；holdout 跑基线与冠军；基线/冠军各重复 3 次 | 回退能复现指标 |
| H30–36 | 报告、README 重写、演示脚本 | 3 分钟能演示完：基线 → 冠军 → 回退 |
| H36–42 | 缓冲、补跑、录屏 | — |

扫参在后台跑，期间写报告，不要串行等。

## 7. 风险与止损

| 风险 | 概率 | 止损 |
| --- | --- | --- |
| 题目太难，基线 0–2/10 | 高 | H8 试点后立即降难度；宁可题简单也不要没信号 |
| 账本解析出错 | 中 | 回退到事件流 `message_end` 累加，精度差但能用 |
| 单题超时 / DeepSeek 抖动 | 中 | 超时 300s，失败重试 1 次，仍失败记 `timeout` 并保留 |
| 扫描跑不完 | 中 | 砍到 8 组（thinking 三档 × context 两档，去掉 tools 维度） |
| 想把 SoL-Pi 装回来 | 高 | 明确禁止；装了会引入未知键报错和版本不兼容 |
| 想加记忆库 | 中 | 明确禁止；42 小时里它是纯负债 |

## 8. 交付物

1. CLI 四个子命令可用：`run` / `score` / `revert` / `doctor`。
2. `runs/` 下约 150 条 run、对应 `result.json`，`git tag v0` 与 `v1`。
3. 一页结论：12 组配置的成功率、单题成本、cache 命中率、token；一句话结论。
4. 3 分钟演示：跑基线 → 跑冠军 → 展示成本差 → `revert` 回 v0。
5. README 重写，只保留"参数扫描 + 外挂裁判 + 原子回退"这条叙事。

## 9. 预算

约 150 次运行，按单次 0.05–0.15 USD 估，总计 8–25 USD。先跑 3 次拿真实单次成本，再决定要不要加重复次数。

## 10. 现在就该做的第一件事

不是写 runner，是**手工跑一次**：

```bash
cd /tmp/opencode/scratch && pi -p --approve --mode json \
  --session-id probe --session-dir ./sessions \
  "列出当前目录的文件，然后回复 DONE"
```

然后从 `sessions/*_probe.jsonl` 里读出 `usage`。这一步不通，后面全是空中楼阁。
