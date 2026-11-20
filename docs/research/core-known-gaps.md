# v1 审计的历史修复与边界

以下记录适用于 v1 只读审计。字段和语义以[协议](../protocol.md)为准。

## K5 配对表曾被当作可信输入（本轮修复）

- 历史缺口：`audit_state` 只校验 `decision.paired` 与 `decision.scores` 的自洽
  （`types.py` 的 solved/total/cost 等式），从未与 run 证据对账；两侧同时伪造也能通过。
- 本轮修复：从 `decision.ext.inputs` 引用的两个 result 取 run，先按 `run.genome_id` 与
  `decision.scores` 两侧 `genome_id` 分侧，再只用 `scope == "search"` 的 run 经
  `paired_rows_from_runs` 重算配对表，逐行核对任务集合/顺序、两侧 passed 与 cost（容差 1e-9）。
  两侧 `invalid_ratio` 也从同一分侧结果重算，不再使用 result 的 union refs；report scope 的
  run 不再可能进入配对成本或 invalid_ratio 分母；同一真实 run 被两个 result 共同引用会去重，
  不重复计费。顶层 `baseline_genome_id` / `candidate_genome_id` 必须与 `scores` 同侧
  `genome_id` 一致，否则跳过该 decision 的 budget / paired / gate 重放。
- 剩余边界：`invalid_ratio` 的分母是该侧全部 `scope == "search"` runs（含合法重试），
  不按 scenario search task 集二次过滤（与 `metrics.py:74-75` 的 `invalid / len(runs)` 一致）。
  分侧失败（两侧 genome 相同、引用第三 genome、目标侧没有对应 search runs、同一 result
  重复引用同一 run ID）时，paired 与 invalid_ratio 均跳过重算，不回退到 union refs
  （重复引用由类型层在装载时先拒绝）；audit 只证明记录间一致，不证明 verifier 源码真值。
- 验证：`tests/test_audit_hardening.py` 的正例（combined result、report result、共享 run、
  合法重试、per-side invalid_ratio）与负例（paired 成本/通过/任务集合/顺序伪造、
  union ratio 稀释、阈值篡改、第三 genome、同 genome、无 search run、缺失与重复引用、
  顶层 genome ID 错配）。

## K6 result / run 比较域曾无独立核验（本轮修复）

- 历史缺口：`run.scenario_version` / `run.shell_version` / `run.verifier_hash` 只被写入，
  审计从未核对；`result.scenario_version` 也没有校验。版本漂移可漏过审计。
- 本轮修复：每个 result（无论是否被 decision 引用）校验自身 `scenario_version`，其引用的
  每个现存 run 校验 `scenario_version` / `shell_version`；存在 decision 时，
  `decision.scenario_version` / `shell_version` 也分别对齐 scenario，且两侧 result 引用的
  每个 run 的 `verifier_hash` 与 decision 一致（不限进入 K5 的 run）。
- 剩余边界：只证明记录间一致，不证明 verifier 源码真值；没有 decision 时 `verifier_hash`
  缺少独立权威值，不据此宣称已完成核验（也不因缺少 decision 判错）。
- 验证：`tests/test_evidence_audit.py` 的有/无 decision 版本篡改矩阵与错误聚合用例。

## 兼容性

v1 状态的审计与显式迁移见 [CLI 参考](../operations/kernel-cli.md#v1-只读兼容与显式迁移)。
