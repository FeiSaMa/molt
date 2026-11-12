# core 已知缺口（2026-11-12）

编号用 K 前缀，避免与门禁夹具 G1–G8 混淆。

状态：已知、暂缓。BoHack 2026（11/13–15）前冻结 core，赛后按 ADR 统一处理。
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

## 为什么现在不修

- K1/K2 动 core，会让现有 run/result/gate 证据链失效；
- K4 动冻结面，触发 `scenario_version` 升版与基线重跑；
- 四者都不影响 BoHack 的"现场两次运行"演示。
