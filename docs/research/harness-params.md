# Harness 参数候选表（第一组：上下文、工具、模型）

本文件是调研结论，不是实测数据。所有默认值取自本机 Pi 1.4.0 的 `docs/` 与 `~/.pi/agent/models-store.json`；预期效应是估计，须由 `molt run` 验证。

## 0. 口径

- 环境：Pi 1.4.0，DeepSeek `deepseek-flash`（input 0.3 / output 1.2 / cacheRead 0.006 USD per 1M token）。
- 目标：成功率不降的前提下最小化 **cost**，tokens 与 wall_ms 作副指标。
- 准入：参数必须能在隔离 workdir 内表达（项目 `.pi/settings.json`、`.pi/sol-pi.json` 或 CLI flag），不改全局配置。
- 状态：`--no-session` 会丢掉 session 账本，禁止使用。

## 1. 候选表

| # | 参数 | 落点 | 取值 | 当前值 | 机制 | 预期效应 | 风险 | 进 v0 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 主模型 | `--model provider/id` | `deepseek-flash` / `llama-cpp/qwen3.5-9B` | deepseek-flash | 单价与能力同时变 | 最大 | 本地 9B 只有 32k 窗口，长任务做不完，成功率会塌 | 单独一组对比，不进主矩阵 |
| 2 | thinking level | `--thinking` | `off` / `low` / `high` / `max` | off | 思考 token 按输出价计费 | 大，双向 | `high` 可能靠多花 30–50% 换成功率，字典序下会必赢 | 是 |
| 3 | 压缩触发点 | `compaction.reserveTokens` | `16384`（默认）/ `400000` | 16384 | 触发条件 `context > window − reserve`；window = 1M | 中，仅长任务 | 默认值在 DeepSeek 上等于永不触发 | 是（与 4 联动） |
| 4 | 压缩保留量 | `compaction.keepRecentTokens` | `20000`（默认）/ `8000` | 20000 | 摘要后保留的尾部 token | 中 | 保留太少会丢刚读过的代码 | 是 |
| 5 | 压缩开关 | `compaction.enabled` | `true` / z`false` | true | 关掉只能靠窗口硬扛 | 小 | 长任务直接失败 | 否，固定 true |
| 6 | 工具集 | `defaultTools` | `read,bash,edit,write` / `bash,edit` / `read,bash,edit,write,grep,find,ls` | 前四项 | 每个工具的 schema 与描述都在每轮前缀里 | 小–中 | 砍 `read` 可能让模型改用 `cat`，反而更贵 | 是（三档） |
| 7 | 上下文注入 | `--no-context-files` / `--no-skills` / `--no-prompt-templates` | 全开 / 全关 | 全开 | AGENTS.md 3.5KB + skills 索引进前缀 | 小 | 丢掉项目约定，编辑风格可能退化 | 是（两档） |
| 8 | 缓存保温 | `cacheWarming` | `off` / `streaming` / `idle` | streaming | 缓存读比 input 便宜 50× | 中 | **只能全局设置**，按 workdir 渲染会破坏隔离 | 否，排除 |
| 9 | 重试 | `retry.maxRetries` | `3` / `0` | 3 | 传输层重试，不是能力升级 | 小 | 重试会把 wall_ms 抬高 | 否，固定 |
| 10 | 工具输出截断 | 无设置项（bash 2000 行 / 50KB 硬编码） | — | — | 观测体积 | 中 | 只能写扩展 | 否，第二期 |
| 11 | 并发 | molt runner | `1/2/4/8` | 4 | 只影响墙钟 | — | 不是质量维度 | 否，作资源约束 |
| 12 | 失败升级 | 无原生；`registerVirtualModel` 的 `route(request.failed)` | — | — | 便宜模型兜底、失败换强模型 | 大 | 需要写 TS 扩展 | 否，第二期 |

## 2. 建议的 v0 搜索空间

三维，12 个组合：

```text
thinking      ∈ {off, low, high}
compaction    ∈ {默认(16384, 20000), 强制(400000, 8000)}
context       ∈ {全开, 精简(--no-skills --no-context-files)}
```

模型维度单独做一次 A/B（4 个配置对比 1 个），因为它会改变整个成本分布，混进主矩阵会污染归因。

理由：这三维都能在 workdir 内表达、都不需要装扩展、都能用同一套题集直接比较。`tool_granularity` 用 `defaultTools` 近似后效应太小，放到第二轮。

## 3. 度量口径

不要用事件流里的 `message_update.usage` 累加：它是"该响应最新的累计值"，流式时可能长时间为 0。

正确做法是读 **session JSONL 账本**：

```text
--session-id <run_id> --session-dir <workdir>/sessions
→ sessions/*_<run_id>.jsonl
```

三类 entry 都要计入成本：

| entry | 何时产生 | 是否计费 |
| --- | --- | --- |
| `message`（role=assistant） | 每次模型响应 | 是，`message.usage` |
| `usage`（kind=`cache_warm`） | 缓存保温 | 是，单独 entry |
| compaction entry | 摘要请求 | 是，entry 自带 `usage` |

汇总 `usage.input` / `output` / `cacheRead` / `cacheWrite` / `totalTokens` / `cost.total`。

因此 `docs/contracts.md` 的 `run.json` 至少还缺四个字段，否则结果无法归因：`model`、`thinking_level`、`cost_usd`、`cache_read_tokens`。

## 4. 坑

1. **项目必须 trusted**：Pi 只在项目被信任后才读 `.pi/sol-pi.json` 与项目 `.pi/settings.json`。runner 必须带 `--approve`，否则渲染出来的配置全部静默失效。
2. **`cacheWarming` 是全局设置**，项目级渲染无效，别放进搜索空间。
3. **DeepSeek 的 thinking 档位不全**：`models-store.json` 里 `minimal` 与 `medium` 映射为 `null`，可选只有 `off/low/high/max`。
4. **`reserveTokens` 是"离窗口上限还剩多少就压"**，不是"上下文占多少比例就压"。`compaction_ratio = 0.35` 这种语义在上游不存在。
5. **SoL-Pi 对未知键直接拒绝加载**，`sol-pi.json` 必须严格匹配 schema version 1。
6. **版本兼容**：SoL-Pi 测过 Pi 0.84.2 / 1.0.4 / 1.3.0，本机是 1.4.0，会触发兼容警告。

## 5. 最小实验矩阵与预算

- 冒烟：3 题 × 2 配置，先确认管线通、账本读得出来。
- 正式：12 配置 × 20 题 = 240 次运行。
- 粗估：单次运行 0.05–0.2 USD（输入占大头，缓存命中率决定实际值），总计约 12–48 USD。先跑一次拿真实数再乘。

## 6. 门禁警告

字典序下 `thinking=high` 只要多通过**一道题**就赢，而单题通过与否受采样噪声支配。建议：

- 同题同 seed 跑候选与基线，逐题配对比较；
- 门禁改成"劣化不超过 k 道题"，而不是 `success_rate >= prev`。

## 7. 本期不做的

| 项 | 原因 |
| --- | --- |
| `actionFusion` / `evidencePreservingReducer` / `observationPack` | 需安装 SoL-Pi；Pi 1.4.0 未测；属"harness 组件"档，优先级最低 |
| `cacheWriteReadRatio` | 是 SoL-Pi 内部的经济决策比例，不是上下文压缩比例；语义与 `compaction_ratio` 不同 |
| 工具输出截断 | Pi 无设置项，需扩展 |
| 失败升级 | 需 `registerVirtualModel` 扩展或外层重试，第二期 |

## 8. 成本结构：先搞清楚钱花在哪

单轮成本大致是：

```text
cost_turn ≈ (new_input × 0.3 + cached_prefix × 0.006 + output × 1.2) / 1e6
```

DeepSeek 是自动前缀缓存：上一轮的完整上下文就是这一轮的前缀，命中部分按 0.006 计，比 0.3 便宜 50 倍；`cacheWrite` 单价为 0，写入不收钱。

三个直接推论：

1. **成本主要取决于"一共往上下文里追加了多少 token"**，而不是"上下文有多长"。追加 = 工具输出 + 思考 + 助手正文。
2. **压缩省不了多少钱**。被压掉的历史本来就在缓存里，按 0.006 计价；压缩反而要额外付一次摘要请求（输入近乎整个上下文）。压缩的价值是防 context rot 和防溢出，不是省钱。
3. **思考 token 是双倍计费**：按输出价生成一次，之后作为助手消息永久留在上下文里（`requiresReasoningContentOnAssistantMessages: true`），每轮都要重新送。所以 `thinking=high` 的成本增长不是线性的。

## 9. 调优顺序

按这个顺序做，前一步没做对不要进下一步。

**Step 0 量基线。** 5 道题，记录每题的 `turns / cost / cacheRead 占比 / 最大上下文`。没有这四个数，后面所有判断都是猜。

**Step 1 修缓存。** 算 `cacheRead / (input + cacheRead)`。低于 50% 就先找前缀污染源：system prompt 里有没有时间戳、每轮变化的 todo/计划、工具集是否中途变化、AGENTS.md 是否被动态改写。修好这一步通常是 2–5 倍降本，且不影响成功率，是所有调优里性价比最高的。

**Step 2 调 thinking。** `off → low → high`。这是唯一一个既改成本又改成功率的参数，也是最容易看出差别的。看两个数：成功率、每次成功任务的成本（不是总成本——失败的运行也要付钱）。

**Step 3 砍工具面。** 先砍 `write`（整文件重写最贵，逼模型用 `edit`），再考虑砍 `read`（砍了模型会改用 `cat`，可能更贵，必须实测）。`--no-skills` 只在任务不需要技能时开。

**Step 4 动压缩。** 只有出现下列任一情况才动：单题最大上下文超过 200k、长任务因窗口失败、或需要复现 SoL-Pi 的机制。动的时候是**降 reserve**（让压缩更早触发），例如 `reserveTokens = 850000` 表示 150k 就压。别指望它省钱。

**Step 5 换模型。** 最后做，且只在成功率有余量时做。降级一个模型可能省 10 倍钱，也可能多跑三倍轮次——后者往往更贵。

**终止条件**：成功率持平、成本降幅 < 5% 就停。两轮演示到这一步够了。

## 10. 症状 → 动作

| 症状 | 大概率原因 | 动作 |
| --- | --- | --- |
| 成本高，但轮次少、成功率高 | 每轮输出/思考太长 | 降 thinking；限制解释性输出 |
| 成本高，轮次多 | 探索效率低 | **提高** thinking 或换强模型，通常总成本反而降 |
| `cacheRead` 占比低 | 前缀不稳定 | 固定 system prompt 与工具集，去掉动态注入 |
| 单题上下文 > 200k 后开始犯错 | context rot | 降 `reserveTokens` 强制压缩，或拆分任务 |
| 成功率只掉一题，成本掉一半 | 很可能是噪声 | 同题同 seed 复跑，别直接采纳 |
| 工具调用很多但文件改不动 | 接口/粒度问题 | 换 `edit` 粒度，或加编辑后的 lint 反馈 |
| 同一题多次运行方差极大 | 采样温度或任务本身不稳定 | 先降温度/固定 seed，再谈调参 |

## 11. 计分口径修正

字典序（成功率 > token > 耗时）在这种调优里会误导：它不允许"多花 30% 换稳一题"，也不惩罚失败运行的成本。

建议改成：

```text
cost_per_solved = total_cost / solved_count
约束：solved_count >= baseline_solved_count
```

在满足约束的候选里按 `cost_per_solved` 升序。这样失败运行的成本自然被计入，也允许更强的配置靠少走弯路赢回来。

## 参考

- Pi 1.4.0 文档：`/usr/lib/pi/docs/{settings,compaction,json,session-format,cli-integration}.md`
- SoL-Pi 配置 schema：`NVlabs/SoL-Pi` → `docs/configuration.md`
- SWE-agent ACI：arXiv 2405.15793；`swe-agent.com/latest/background/aci/`
- Context rot：Chroma Research，2025-07
- Harness 参数化：`strands-labs/harness-optimizer`、`neosigmaai/auto-harness`、`canvas-org/meta-agent`
