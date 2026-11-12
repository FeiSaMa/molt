# 8 分钟路演（molt probe）

> 台词按时间轴写；每节末尾的 `[]` 是数字出处，不是念出来的内容。
> 现场入口：`uv run python -m probes.run_demo --profile fast`（fast：Run1 只跑 `probe-order-2`，Run2 跑 `x5`/`x6`）。
> 画面规格以 `VISUAL_SPEC.md` 为准；口径以 `FINAL.md` 与 `docs/research/headroom-probe.md` 为准。

## 0:00–0:30 一句话

**【画面】** 只有页头代际线和一句话，不切任何 diff。

**【说】**

> 同一个 agent，第一次跑失败，把失败归纳成规则，第二次跑同族的新题，过了。

不要开场讲架构。三层、七端口、gate 这些留到有人问。

## 0:30–1:30 Run1

**【画面】** 左栏 Run1：失败卡（`probe-order-2`，三行 case，`✗` 保留）+ 左 stub / 右 run1 的 split diff。

**【说】**

> 它没改对，也没改错——它按默认写了 `sorted(set(names))`。可见示例全过，doctest 也过；隐藏用例挂，因为这道题的真实规则是欠定的：大小写不敏感的归类 + 保留编码最小代表 + 按长度升序。
>
> 注意失败卡里给的不是规则，只有 `case_id / expected / actual`。它对失败的具体原因只有反例，没有答案。

**【关键句】**

> 失败不是因为笨，是因为信息不在工作区里。

出处：Run1 现场 `probes/report/rehearsal/run1/state.json`（`run1.solved=0`）；默认写法 `sorted(set())` 见 `web/data/probe/example.json` 的 `run1.tasks[probe-order-x5].code_after`；欠定设计与隐藏用例见 `docs/research/headroom-probe.md` §2。

## 1:30–3:00 Memory

**【画面】** 中栏 MEMORY · RULES：规则逐行浮现（最多 5 行，不足按实际行数；每行右侧连线到 `order-2` / `x3` 的 case chip），底部计数 `2 sources · 6 counterexamples · 1 reflection`。

**【说】**

> 这栏是它自己的产物：两个 source 文件、六条反例、一次反思调用。规则不是我们写的——prompt 只允许它写分组 / 去重 / 排序的规则，不许提参数名、返回类型、函数名，不许写代码。它把反例压成了几条规则，离线那份是这样五条：
>
> 忽略大小写归类，每类只留一个；同组多写法时留编码最小者；归类后按书写长度升序；长度相同再按不区分大小写的编码升序；需要截前若干项时先排完再截。

**【现场提示】** 现场反思只吃 `probe-order-2` 的失败用例，彩排两次都产出 **3 行**（`probes/report/rehearsal/run{1,2}/state.json` 的 `memory.lines`），措辞也不同。台词按屏幕实际行数走，**不要念“五条”**；要用五条的说法时，先说明“这是离线的 gen4”。

**【关键句】**

> 记忆不是把失败倒回去，是归纳到正确的抽象层级。

出处：`shells/probe/memory/gen4.md`（五条规则原文）；反思成本 $0.02970132、六条反例与 prompt 原文见 `probes/report/2c/memory-v3/reflection.json`；页面计数口径见 `VISUAL_SPEC.md` §4.4。

## 3:00–5:00 Run2

**【画面】** 右栏 Run2：同一个函数的 before/after split diff（默认写法 → 记忆写法），x5 / x6 两个 tab，徽章 `passes/runs`。

**【说】**

> Run2 换的是同族的新题：`unique` 和 `dedupe_merge`。记忆里没有这两个函数的任何用例，连函数名都没有——记忆里只有规则。
>
> 现场两次都过：2/2。离线四题各四次：16/16。

**【关键句】**

> 它迁移的不是答案，是规则。

出处：现场 `probes/report/rehearsal/run1|run2/run.json`（`run2` 阶段 x5、x6 各 passed，`verdict=v3_stable_headroom`）；离线 16/16 见 `probes/report/2c/memory-v3/verdict.json`。

## 5:00–6:00 方法与数字

**【画面】** 页头代际线 gen2 → gen3 → gen4，下面一行数字；不画柱状图、不画通过率曲线。

**【说】**

> 我们是迭代出来的，不是一次设计对的：
>
> - v1 回放：把失败用例原文塞回 prompt。x5 4/4，x6 3/4，x7 1/4，x8 0/4，签名漂移一塌糊涂。
> - v2 反思：让模型自己写规则，但没约束抽象层级。x5–x7 都到 4/4，x8 仍 0/2，签名漂移 10/12。
> - v3 约束反思：只许写分组 / 去重 / 排序。16/16，签名漂移降到 5/16。
>
> 关键数字这一行：离线 16/16；有 headroom 的三题 12/12，基线 0/10；控制题 2/2（说明探针不是“模型不会做题”）；反思稳定性 6/6（pro 3/3 + flash 3/3 都写成“长度升序”，没人漂到频次或大写数量）。
>
> 现场彩排两次：98 秒 / $0.05，89 秒 / $0.07，都落在 360s / $0.6 的预算里。

出处：方法演进与签名漂移见 `docs/research/headroom-probe.md` §「方法演进」；16/16 与 12/12 vs 0/10、控制 2/2 见 `probes/report/2c/memory-v3/verdict.json`（`baseline_comparison`）；稳定性 6/6 见 `probes/report/stability/SUMMARY.md`；彩排 `wall_ms=98219` / `cost_usd=0.050723` 与 `wall_ms=88773` / `cost_usd=0.072286` 见 `probes/report/rehearsal/run{1,2}/run.json`（98s、89s 为取整）。

## 6:00–7:00 负结果

**【画面】** 右下角负迁移徽章条（琥珀色三段式），或展开 x8 tab 的详情。

**【说】**

> 三件必须主动讲的事：
>
> 第一，x8 被记忆改坏过。基线本来 2/2（它返回 int 计数），v1 记忆把它改成返回 list 并加了 k，0/4；v2 还是 0/2。是签名漂移带偏的。v3 修回 4/4——但基线本来就 2/2，所以这不算提升，只是没做坏。
>
> 第二，C2 舍入族没有 headroom。我们设计 half-up 对 half-even 的默认偏置，结果模型四次里直接就猜到了 `ROUND_HALF_UP`，判定 `c2_invalid`。所以约定必须是“项目特定且非领域常识”，否则模型本来就知道。
>
> 第三，我们有一整个矩阵因一条 prompt 里硬编码了 `top_k` 而作废，$1.38 打水漂。抓到它的是 oracle，不是我们自己看出来的。

出处：x8 归因见 `probes/report/2c/x8-analysis.md` 与 `docs/research/headroom-probe.md` §4（`0/4`、`0/2`、基线 `2/2`）；C2 见 `probes/report/c2/verdict.json`（`c2_invalid`，族题 best-of-4 2/2）；作废矩阵见 `probes/report/matrix/verdict-v1-invalid.json`（`invalid_reason="probe prompt hardcoded top_k; T2 runs invalid"`，成本 $1.37784319）。

## 7:00–8:00 局限与收尾

**【画面】** 页脚状态行：`verdict` + `verdict_note` + cost / wall + `LIVE` / `REPLAY`。

**【说】**

> 局限说清楚：单一约定 C1b、单一模型对（pro/flash × high/max）、每题 2–4 reps。x8 不算提升，主 2c 口径仍是 `partial_transfer`。我们不声称通用自进化。
>
> 我们交付的不是一个会自进化的 agent，是一套能证明它有没有进化的仪器：题集、反例接口、决策表、oracle、accept/audit 重算。

出处：局限见 `docs/research/headroom-probe.md` §8；页脚字段见 `VISUAL_SPEC.md` §8；`accept` / `audit` / `genome verify` 的复算命令见同文档 §9。

---

# Q&A（10 条）

1. **这算自进化吗？**
   算最小闭环：失败 → 反思 → 记忆 → 新题迁移。四条都在，且每一步有产物（`state.json` 的 `run1` / `memory` / `run2`、`gen4.md`、`verdict.json`）。不声称通用。

2. **样本太小？**
   是。4 题 × 4 次、单模型对、2–4 reps。所以报告只写“可测、可复算”，不写“统计显著”。

3. **会不会学坏？**
   会，已经学坏过两次。x8 在 v1 / v2 被记忆改成返回列表（签名 `-> list[str]` 并加 `k`），0/4 与 0/2。我们没删记录，留在 `probes/report/2c/x8-analysis.md`，并且 v3 的约束是照这条修出来的。

4. **为什么不让它改自己的代码？**
   改代码的验证成本高：要重跑测试套、要防回归、要处理依赖。42 小时做不出可信证据。改记忆能用同一条 oracle 判——同一批题、同一个 checker、同一份证据链。

5. **x8 为什么不算提升？**
   基线 2/2，v3 4/4 只是没做坏。headroom 三题才是 12/12 vs 0/10 的对照。把 x8 算进提升就是把“修 bug”说成“变强”。

6. **成本多少？**
   现场 fast 约 $0.05–0.07（彩排 0.0507 / 0.0723）。全部实验 $10 量级：可复算台账 $8.87，其中三次作废/负结果矩阵合计约 $3.0（v1 作废 $1.38、C1a $0.83、C2 $0.73）。

7. **现场失败怎么办？**
   切预录：读 `probes/report/rehearsal/run1/state.json`，页面按 `live=false` 标 `REPLAY`，并显示 `verdict_note`。不伪装 live。

8. **和 SWE-bench 比？**
   不比。那是能力基准（模型能不能做题），这是“跨运行学习”的仪器（同一模型两次运行之间有没有迁移）。两个问题不同，数字不可互换。

9. **max 是不是更好？**
   不一定。C1a 那一格 pro-high 有过一次降级（T1 上 1/2，写出 `(len, s)` 半猜中，F=1），pro-max 是 F=0；但那是单格 2 reps 的采样，不能推成“max 更稳”的规律。v4/C1b 四格 F 全 0，max 与 high 没分出差别。

10. **数据能复算吗？**
    能。`probes/accept.py` 九个 stage、`python -m molt audit` 只读重算、`python -m molt genome verify` 校验 genome；逐题行在 `probes/report/2c/rows.json` 与 `control-rows.json`，反思原文在 `2c/memory-v3/reflection.json`，彩排在 `probes/report/rehearsal/`。

---

# 不要说的话

- “通用自进化”
- “16/16 证明模型会学习”
- “x8 也是提升”

理由：`VISUAL_SPEC.md` §12.1 措辞禁区 + `docs/research/headroom-probe.md` §10 结论。

# 现场降级

- **网络 / 模型卡住**：切 `probes/report/rehearsal/run1/state.json`，页面标 `REPLAY`（`live=false` 或 120s stale 都会自动标）。
- **页面 404**：`web/data/probe/example.json` 兜底，显示 `REPLAY · EXAMPLE`。
- **超 360s**：页面显示 `PARTIAL · 360s cap`，保留最后数据，不补跑。
