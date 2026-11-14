# Molt deck · 时效性核对与演示节奏调整

> 核对时间：2026-11-15。对象：`molt-deck.pdf`（26 页）与当前 web / `/srv/molt`。
> 结论：demo-6 的三层数字仍然成立；P21、P23 有三处线上数据已过时；PPT 开场应按 web 的“同题前后对照”重排。

## 1. 数据核对

| Deck 页 | 原口径 | 当前值 | 状态 | 依据 |
|---|---|---|---|---|
| P05 | NVIDIA 82.2% / 78.2%，1.1M / 2.2M token，~28 / 66 次调用，2026-07-27，arXiv:2607.20709 | 与原文一致 | ✅ 时效内 | NVIDIA Technical Blog 2026-07-27；arXiv 2607.20709 §4.2 |
| P10–P11 | gate $0.035560 → $0.010684；cps $0.003556 → $0.001068；10/10；10 组配对 | 与 `web/data/gate.json` 一致 | ✅ demo-6 录制 | gate.json `generated_at=2026-11-10T17:05:02Z`，pairs 10，verdict=adopt |
| P12–P13 | claim $0.009978 / 41.7s；tracked $0.010684 / 42.6s；gate 级；holdout 5/5；负结果 $0.011894（+19.2%） | 与 ADR-0008 / web/data 一致 | ✅ 但必须保留三层标签 | `docs/decisions/0008-demo.md`；`web/data/runs.json` |
| P21 | 线上图谱 2 分支 / 5 节点 / 17 资产 | **4 分支 / 5 节点 / 25 资产，revision 25** | ❌ 过时 | `/api/upper/graph` + `/api/upper/assets`，2026-11-15 |
| P21 | 189 条 = 153 + 20 + 10 + 1 + 5；公开 41 / 内部 39 / 受限 109 | 一致 | ✅ | `scale-graph.json` / manifest |
| P21 | 图谱测试 23 passed | **706 collected：693 passed / 5 failed / 8 skipped** | ❌ 过时 | `/srv/molt` 全量 pytest，2026-11-15 |
| P22 | BM25 P@1 1.0、P@3 0.759、P@5 0.744 | P@1 已 committed；**P@3/P@5 只在文档表格，未进 committed 指标** | ⚠️ 需加口径 | `molt-upper-eval/docs/upper-eval-results.md`；`content.json` staticeval 块 |
| P23 | binding revision 2 → 3 | **revision 6** | ❌ 过时 | `/api/runtime/status` |
| P23 | approved 与 approval_source 为空 | **project_kb approved=true / approval_source=registry / approved_by=automatic-evolution-policy-v1** | ❌ 过时 | `/api/mcp/tools` |
| P23 | 统一回归 239 passed | **693 passed / 5 failed / 8 skipped（706 collected）** | ❌ 过时 | `/srv/molt` pytest，2026-11-15 |
| P23 | 5.87s / n=1 / 665 字符 | 仍是唯一 live 证据；没有更新的一手 live 记录 | ⚠️ 只能标“历史 n=1” | 生产验收记录 2026-11-14 11:48 |
| P14–P19 | probe / memory-v3 数字 | 仍对应已落盘报告 | ✅ 离线/REPLAY 口径 | `probes/report/2c/`、`probes/report/validity/` |

## 2. 必须替换的文案

### P21

原：

> 同时维护线上图谱 **2 分支 / 5 节点 / 17 资产**。……图谱测试 **23 passed**。

改为：

> 同时维护线上图谱 **4 分支 / 5 节点 / 25 资产（revision 25）**。……全量回归 **706 collected：693 passed / 5 failed / 8 skipped**（2026-11-15）。

### P23

原：

> 审批字段 approved 与 approval_source 仍为空。……binding revision 从 2 迁到 3……统一回归 **239 passed**。

改为：

> 当前 binding **revision 6**；project_kb **approved=true / approval_source=registry**（approved_by=automatic-evolution-policy-v1）；仍不是模型评测或人工审批通过。
> 全量回归 **706 collected：693 passed / 5 failed / 8 skipped**（2026-11-15）；5.87s 是 **历史 n=1**，不是当前新增证据。

### P22

在“BM25 P@1 1.0、P@3 0.759、P@5 0.744”后加一句：

> P@3 / P@5 仅出现在文档表格，未进入 committed 指标；对外只把 P@1 当作已提交口径。

## 3. 按 web 节奏重排

Web 现场的顺序是：

```text
同一道题 → 基线 4/8 → 进化 8/8 → 规则来源（分支）→ 门禁 → 189 树
```

Deck 建议改成：

1. 封面
2. 地图（P02）
3. 四种进化（P03）
4. 问题（P04）
5. **新增：同一道题 4/8 vs 8/8**
6. **新增：8 项硬检查**
7. **新增：规则从哪来（r1 分叉）**
8. **新增：规则怎么生效（422 / active 不变）**
9. NVIDIA harness（P05）
10. 两条回路（P06）
11. 枚举 genome（P07）
12. 度量（P08）
13. 六步门禁（P09）
14. demo-6 基线（P10）
15. demo-6 候选（P11）
16. 三层数字（P12–P13 合并）
17. probe / memory-v3（P14–P19 选 2–3 页）
18. 静态评测（P22，补 P@3/P@5 口径）
19. Skill / MCP runtime（P23，按上面替换）
20. 隐藏规则 / 负迁移（P24–P25）
21. **189 条资产（P21，更新 4/5/25 与 693/5/8）**
22. 收尾（P26）

原则：

- 前 8 页先让观众看到“同题前后差距 + 规则来源 + 门禁”，不要先讲 gate 成本。
- demo-6 的三层数字放在技术证据段，不要与 live 的 4/8 记分卡混成一屏。
- 189 树放最后，作为规模收束；不要放在开场。
- P10–P13 必须保留“1 live + 20 replay”“2026-11-10 录制”的标签。

## 4. 可直接插入的开场页

已生成 5 页 PDF：

```text
/home/feisama/Downloads/molt-demo-insert.pdf
```

内容：

1. 同一道题：4/8 vs 8/8
2. 8 项硬检查
3. 规则从哪来：r1 分叉
4. 规则怎么生效：422 门禁
5. 189 条候选（建议放最后，作为规模收束）

建议把第 1–4 页插在 P04 之后；第 5 页放 P21 的位置。

## 5. 一句话讲法

> 基线不是不会判断，它把结论说对了；但在合规审计里，不能引用规则的结论不算完成。进化补的是可审计性：4/8 → 8/8，差的 4 分全是规则引用与检索记录。

这句话放在 P05 之前，观众就不会再把“进化”理解成“模型变聪明”。
