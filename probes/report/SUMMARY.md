# Headroom probe — Summary

**结论（一句话）**：规则型 memory v3 在 4 道迁移题 ×4 次重复上 **16/16** 全部通过（`v3_stable_all`）；x8 从 0/2 修到 4/4，但 x8 baseline 本身 2/2，不构成提升；签名漂移 v2 10/12 → v3 5/16；主 verdict 仍 `partial_transfer`，不声称通用自进化。

## 三个数字

1. **v3：x5/x6/x7/x8 各 4/4，合计 16/16**（`probes/report/2c/memory-v3/verdict.json`）。
2. **v3 vs baseline**：all-four 16/16 vs 2/12；headroom-three（x5/x6/x7）12/12 vs 0/10。
3. **v3 成本 $0.4589434**（reflection $0.02970132 + 18 runs）；签名漂移 v2 10/12 → v3 5/16。

## 三个负结果

- **prompt 硬编码 `top_k`**：v1 矩阵 T2 全部无效（`probes/report/matrix/verdict-v1-invalid.json`）。
- **x8 曾是负迁移**：v1 0/4、v2 0/2；v3 修到 4/4，但 baseline 2/2，不能算记忆提升（`probes/report/2c/memory-v3/verdict.json`）。
- **签名漂移仍在**：v3 5/16（x5-r2、x5-r3、x6-r1、x6-r2、x8-r3）；x7 全干净。

## 复算入口

```bash
uv run pytest -q
uv run python probes/accept.py --stage memory
uv run python -m molt scenario validate --scenario scenarios/probe/scenario.json
uv run python -m molt genome verify shells/probe/genomes/gen4.json
```

证据目录：`probes/report/matrix/`、`probes/report/2c/`（主实验、控制、followup、memory-v2、memory-v3）、`probes/decision-table.json`。
