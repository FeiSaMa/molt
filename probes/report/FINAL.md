# molt probe — 最终一页

## 一句话

规则型记忆 v3 在 4 道迁移题 × 4 次重复上 16/16；有 headroom 的三题 12/12 vs 基线 0/10；x8 基线本就可解，不计入提升。

## 现场入口

```bash
uv run python -m probes.run_demo --profile fast
```

- 实测 140.8s / $0.045 / `v3_stable_headroom`（`web/data/probe/run.json`：`wall_ms=140797`、`cost_usd=0.045428`）
- 写 `web/data/probe/state.json`；页面轮询 `revision`（`AGENTS.md`「`web/data` 契约冻结」）
- 降级：抽到 `partial_transfer` 切预录，页面标 `REPLAY`（`probes/report/VISUAL_SPEC.md`）

## 关键数字（各带出处）

- v3 16/16：`probes/report/2c/memory-v3/verdict.json`（`v3_stable_all`，4 题 × 4 reps）
- headroom 12/12 vs 0/10：同上 + `probes/report/2c/rows.json`（汇总见 `docs/research/headroom-probe.md` §5）
- 控制 2/2：`probes/report/2c/control-rows.json`（ctl-a 2/2、ctl-b 2/2）
- 矩阵 F/K：`probes/report/matrix/verdict.json`（四格 F=0、K=2）
- 成本合计：`docs/research/headroom-probe.md` 的「成本」段（v4 矩阵 $0.99972634 + 2c 主实验与控制 $2.30024424 + followup 累计 $1.56274139）

## 三轮方法

v1 回放 → v2 反思 → v3 约束反思；签名漂移 10/12 → 5/16；x8 从 0/4 修到 4/4（`docs/research/headroom-probe.md` §「方法演进」）。

## 负结果（主动讲）

- prompt 硬编码 `top_k`，v1 矩阵作废（`probes/report/matrix/verdict-v1-invalid.json`）
- 导出过滤会误删 demo live run，已回滚（`docs/research/headroom-probe.md` §6）
- x8 曾负迁移（签名漂移）（`probes/report/2c/x8-analysis.md`）
- 反思是采样：T2 的三个用例无法区分长度/频次，probe-6 加 `case_04` 判别（`docs/research/headroom-probe.md` §「演示稳定性（probe-6）」）

## 已知局限

单约定 C1b、单模型对、2–4 reps、x8 不算提升、签名漂移 5/16、页面由前端实现。

## 文件地图

- 仪器：`probes/run_demo.py`、`probes/accept.py`、`probes/decision-table.json`
- 题集：`bench/probe/**`、`scenarios/probe/scenario.json`
- 记忆：`shells/probe/memory/{gen2,gen3,gen4}.md`
- 证据：`probes/report/{matrix,2c,validity,preflight}/`
- 页面规格：`probes/report/VISUAL_SPEC.md`
