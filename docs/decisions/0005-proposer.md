# ADR 0005：LLM proposer 与 proposal.ext 契约

- 状态：已决定
- 日期：2026-11-10
- 关联：[`docs/protocol.md`](../protocol.md)、`molt/core/proposer.py`、
  `molt/adapters/llm_proposer.py`、`shells/code-repair/harness.py`

## 背景

B1 的 `proposal` 顶层只有 10 个字段且 `additionalProperties:false`。B2-D1 要求记录
diagnosis、模型、prompt/response 哈希、usage、成本来源、fallback 与 source，
同时不得破坏 v1 顶层冻结。

## 决定

### 1. 顶层保持冻结，扩展字段全部进 `ext`

| B2-D1 字段 | 落点 |
| --- | --- |
| `base_genome` | 顶层 `parent_id` |
| `candidate_switches` | 顶层 `switches` |
| `diagnosis` | `ext.diagnosis`；顶层 `rationale` 保留一句话理由 |
| `model` | `ext.model`（如 `deepseek-flash`；grid 为 `grid`） |
| `prompt_sha256` | `ext.prompt_sha256` |
| `response_sha256` | `ext.response_sha256` |
| `usage` | `ext.usage{input, output, cache_read, cache_write, reasoning, total}` |
| `cost{value, source}` | 顶层 `cost_usd` + `ext.cost_source ∈ provider/estimate/none` |
| `counted_as` | `ext.counted_as = "search_overhead"` |
| `fallback{used, reason}` | `ext.fallback` |
| `source` | `ext.source ∈ llm/grid`；`proposer_id` 为 `llm:<model>` 或 `grid` |

`ext.history` 额外记录 `{scenario_version, entries, excluded}`：`excluded>0` 表示旧版本
history 已被过滤，旧条目从未进入 prompt。

### 2. 输入只能是当前版本的 SearchHistory

- `Harness.propose` 首行硬拒非 `SearchHistory`（含 `ReportArchive`），版本不一致抛
  `ValueError`。
- `molt.core.proposer.filter_history` 在构造 prompt 前按 `parent.scenario_version` 过滤；
  旧版本 history 整体丢弃，`ext.history.excluded` 记录条数。
- prompt 只含 `SearchHistory.to_dict()`、当前 `switches`、允许的三维枚举；
  不含 holdout、report 分数、gate 阈值、bench 路径。

### 3. 提案成本单列

- 顶层 `cost_usd` 是该次提案的实际成本（provider 实报或按目录价估算）。
- `Ledger.append("prop-<id>", {kind:"overhead", cost_usd})`；audit 对 `prop-*` 的
  `kind=overhead` 全量对账。
- `metrics_from_runs` / `cost_per_solved` 不读取 ledger 的 overhead 条目；提案成本只影响
  `budget.search_overhead_budget_usd` 的 `overhead_exceeded`，不得与执行成本互相抵消。

### 4. 失败一律回落网格，仍留 Proposal

可识别的 fallback reason：

| reason | 触发 |
| --- | --- |
| `invalid_json` | 响应 content 不是 JSON（最多重试 1 次） |
| `empty_candidate` | `switches` 缺失或空对象 |
| `switch_out_of_domain` | 缺失维度、第四维、值越域 |
| `invalid_response` | 缺 diagnosis/reason 或结构不是 object |
| `missing_api_key` | 无 `MOLT_PROPOSER_API_KEY` / `DEEPSEEK_API_KEY` |
| `transport_error` | 网络 / HTTP / provider 异常 |
| `proposal_over_budget` | 实际成本超过 `search_overhead_budget_usd` |
| `llm_error` | shell 层兜底：注入的 proposer 抛异常 |

回落时 `source=grid`、`fallback.used=true`、`fallback.reason=<上表>`，但仍写入
`proposals/<id>.json` 与 overhead ledger；失败尝试的 prompt/response 哈希、usage、成本
一并保留，供审计归因。

### 5. 网格回退是通用的

`molt.core.proposer` 只做三维笛卡尔枚举，不硬编码 `code-repair` 业务名。
shell 通过构造参数传入候选域与排序函数（`rank`），默认排序按“改动维度数 → 值距离 →
固定枚举序”。默认 grid 是主动选择，`fallback.used=false`；只有 LLM 失败才标记 fallback。

### 6. 适配器与 transport

`molt/adapters/llm_proposer.py` 只依赖 stdlib；`Transport = Callable[[dict], dict]` 由构造注入。
默认 transport 调 DeepSeek OpenAI-compatible `/chat/completions`，模型 `deepseek-flash`，
base_url `https://api.deepseek.com`，key 从环境读取。测试注入纯函数 fake transport，不触网。

### 7. 后果

- 每个被评估候选都可追到 Proposal：`ext.source=llm|grid`，`proposal_id` 进 ledger 与事件。
- proposal 顶层 schema 与七端口白名单不变；`policy.decide` 判定顺序不变。
- `harness.py` 改动触发 `scenario_version` 升级；B2a 时 demo 从 `demo-1` 升到 `demo-2`，
  B2b（bash-only）继续升到 `demo-4`，`bench_hash` 与 v0 genome artifact 哈希同步重算。
