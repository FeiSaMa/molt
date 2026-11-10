# ADR 0003：评分与门禁口径

- 状态：已决定
- 日期：2026-11-10
- 关联：[`docs/architecture.md`](../architecture.md)、`molt/core/policy.py`、`molt/core/metrics.py`

## 背景

v0 有三套互不兼容的口径：字典序 `(-success_rate, tokens_total, wall_ms_total)`、
`cost_per_solved = 全部成本 / 通过题数`、以及"掉 1 题时重复 3 次判噪"。同一候选在不同口径下
可能得到相反结论；且"全部成本"没有说明是否含提案开销与失败运行。

## 决定

1. **唯一指标**：
   `cost_per_solved = 该 genome 在题集上全部执行 run 的消耗 / 有效通过题数`。
   分子含 invalid、超时、超预算、判废与重复 attempt；分母只认首轮通过（attempt=1）的题。
   分母为 0 ⇒ `cost_per_solved = null` ⇒ `incomparable`。
2. **提案成本单列**：`cost_per_solved` 不含任何提案 / 搜索开销。预算拆两个 cap：
   `budget.execution_budget_usd` 与 `budget.search_overhead_budget_usd`；任一超限即
   `budget_exceeded`，`budget_check` 用 `execution_exceeded` / `overhead_exceeded` 两个布尔
   并写明 reason。二者不得互相抵消。
3. **门禁统计量**：首轮 pass@1 `pass_at_1_first_attempt`。重复 run 只写 `diagnostic.*`，
   不改变判定。四项执行上限（tokens / wall_ms / turns / attempts）必须逐项完全相同。
4. **判定顺序**（唯一实现：`molt/core/policy.py::decide`）：
   `incomparable`（上限不等 / 分母 0）→ `unstable`（任一侧 invalid 比例超阈）→
   `budget_exceeded` → 非劣 `solved_c >= solved_b - k` → 单题执行成本不升 → `adopt`。
5. **结论结构**：`gate_decision` 必带逐题配对表 `paired[]`（每题两侧 passed 与成本）；
   聚合数字不得单独出现。配对表与 scores 必须互相可推导。
6. **双分数**：`result.scope=report` 必须含搜索集与 holdout 分数；holdout 不进搜索回路。
   `web/` 只导聚合分，不导逐题明细。
7. **claim 措辞**：在 10 题搜索集上"未观察到通过题数下降"（n=10，描述性，seed 不可控），
   且单题执行成本不升；搜索开销单列报告。不得把小样本描述性结论写成统计显著性。

## 后果

- 字典序打分退役；G1–G8 夹具逐字段锁定行为（含比字典序更严的"降本掉题 = reject"）。
- 任何门禁阈值只出现在 `scenario.policy`；shell manifest 出现 `tolerance` / `cost_threshold` /
  `invalid_ratio` / `repeat_policy` 即校验失败。
- 审计可从 run + ledger 重算全部指标，并与已存 result / decision 比对。
