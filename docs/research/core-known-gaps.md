# core 已知缺口（2026-11-12）

编号用 K 前缀，避免与门禁夹具 G1–G8 混淆。

状态：K1–K4 已知、暂缓；K5/K6 已在本轮修复（仅 `molt/core/audit.py`，不动协议、不重跑证据）。
BoHack 2026（11/13–15）前冻结 core，赛后按 ADR 统一处理。
范围：`molt/core/**` 与承重的 shell verifier；不包含 probe 实验的已知局限（见 `headroom-probe.md`）。

## K1 成本以 float 累加与比较

- 位置：`molt/core/policy.py`（`SideEval.cost_total_usd`、`decide` 的 `1e-12` 比较）、`molt/core/types.py`（`ScoreBlock` / `Budget`）。
- 证据：`d585d2f` 的 CI 记录——3.11 因 CPython 3.12 的 `sum()` 补偿求和导致 gate 浮点夹具漂移，unit 作业因此只跑 3.12。
- 影响：同一 `state/` 在不同解释器上 `audit` 可能得到不同 `verdict`；"证据可复算"退化为"在特定 Python 版本上可复算"。
- 建议：成本改整数微美元（`int`，1e-6 USD），比较用整数，展示层除 1e6；`schemas/v2` + ADR。
- 时机：赛后。

## K2 `non_inferior` 也返回 `adopt`

- 位置：`molt/core/policy.py` 判定阶梯最后分支。
- 影响：成本完全相等的候选会推进 generation 并归档，archive 被零收益候选稀释；页面上出现"采纳成功、delta=0"。
- 建议：改为 `keep`（写决策、不归档），或要求严格更低成本才 `adopt`。
- 时机：赛后（需重跑 gate 夹具）。

## K3 `k=0` 与文档不一致

- 位置：`docs/plan-42h.md` §5 写"只掉 1 题时用 3 次重复判断是不是噪声"；`policy.k=0` 是一票否决，重复判定未实现。
- 影响：评审会问；该文件已标 superseded，但这句话仍误导。
- 建议：把该句改为"k=0 一票否决；重复判定未实现"，其余保留为取舍记录。
- 时机：现在（纯文档）。

## K4 verifier 的 `sitecustomize` 逃逸（shell 层，承重）

- 位置：`shells/code-repair/verifier.py`（`PYTHONPATH=str(workdir)` + `cwd=workdir` 跑 pytest）。probe shell 已脱敏，但同样没挡住这个入口。
- 机理：Python 启动时自动 import `sitecustomize`；agent 在 workdir 放一个 `sitecustomize.py` 即可在 pytest 之前执行任意代码，改断言或改结果。`bench_tree_hash` 只覆盖 `bench/**`，workdir 不在哈希内，所以 `matches=True` 仍会成立。
- 影响：agent 可以改裁判，而不是改被测代码。
- 建议：verify 前检查 `sitecustomize.py` / `usercustomize.py` / `conftest.py` / `pytest.ini` 并判废；加 `PYTHONNOUSERSITE=1`；考虑 `-c` 指向空配置。
- 时机：赛后；若赛前要动 `shells/code-repair/**`，需按冻结面升 `scenario_version` 并重跑基线。

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

## 为什么其余项现在不修

- K1/K2 动 core 协议语义，会让现有 run/result/gate 证据链失效；
- K4 动冻结面，触发 `scenario_version` 升版与基线重跑；
- K3 是纯文档措辞，可与文档批次一起改；
- K5/K6 已在本轮修复，只改审计读取路径，不升版本、不重跑证据。
  K1–K4 都不影响 BoHack 的"现场两次运行"演示。
