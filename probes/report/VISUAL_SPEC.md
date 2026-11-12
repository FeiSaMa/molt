# Probe 可视化规格（v1）

> **目标不是展示通过率，是让观众看见「它改了什么 / 它学到了什么 / 它会不会学坏」。**

这份规格面向写 HTML 的队友：你只消费 `web/data/probe/state.json`（live）或
`web/data/probe/example.json`（仓库内样例），不要读 `web/data` 的五个 Tier 1 文件，
不要重新计算任何指标。所有数字、状态、代码、case 都来自数据；页面只负责把它们排成
一个 16:9 的投影画面。

数据契约的机器可读版本是 [`probe_state_schema.json`](probe_state_schema.json)；
样例是 [`../../web/data/probe/example.json`](../../web/data/probe/example.json)。
字段有冲突时以 schema 为准；schema 没写的键一律忽略（向前兼容），不要因为多一个键就报错。

---

## 0. 先读这一节：页面在讲什么

四个画面不是四张独立页面，而是**同一个 16:9 画面上的四个视觉区**：

| 区域 | 讲的问题 | 观众应该带走的一句话 |
| --- | --- | --- |
| ① 左 40% Run 1 | 它改了什么？哪里没改？ | baseline 在 x5/x6/x7 上失败；x5 的 4 次都没动文件，x6/x7 改错；x8 本来就会做 |
| ② 中 20% Memory | 它学到了什么？ | 5 条规则，来自 2 个 source、6 个 counterexample、1 次 reflection |
| ③ 右 40% Run 2 | 迁移到同一个函数上了吗？ | 同一个函数的 before/after：baseline 的写法 → memory 的写法；4 题各 4/4 |
| ④ 负迁移（可选） | 它会不会学坏？ | x8 先被带偏（签名漂移、0/2），gen4 恢复（4/4，3/4 保持原签名） |

**不要**把「16/16」做成一个大号百分比或胜利横幅。verdict 按数据分三档：

| verdict | 含义 |
| --- | --- |
| `v3_stable_all` | v3 在全题集（x5–x8）上稳定通过 |
| `v3_stable_headroom` | 只有 headroom 题（x5–x7）稳定通过，x8 不计入 |
| `partial_transfer` | 迁移只部分成立（2c 主实验 v1 口径） |

example 是 v3 数据，verdict 为 `v3_stable_all`，但 `verdict_note` 必须同时在场：
x8 的 baseline 本来 2/2，不构成记忆提升；签名漂移仍然存在；主 2c（v1）的口径是
`partial_transfer`。页面的语气是「把证据摊开」，不是「庆祝模型变强」。

---

## 1. 画布、栅格与视觉 token

### 1.1 画布

- 设计基准 **1920 × 1080（16:9）**，投影时浏览器全屏、无页面滚动。
- 页面 padding **48px**；顶部代际线 **72px**；底部状态行 **56px**；
  主区域 = 1080 − 48×2 − 72 − 56 − 24×2 = **808px**。
- 三列用内容宽度（1824px）的 **40% / 20% / 40%**，列间距 **24px**：
  1920 下约为 **710 / 355 / 710**。
- 1600 × 900 时改为 **38% / 24% / 38%**（中间列至少 300px）；
  低于 1400px 宽改为两行（Run1 + Run2 在上，Memory 全宽在下），但投影验收只认 16:9。
- 所有面板共用 8px 间距网格；卡片圆角 8px，边框 1px；不要大面积阴影。

### 1.2 字体

| 用途 | 字体 | 尺寸 / 行高 |
| --- | --- | --- |
| 正文、规则、说明 | `Inter, "Noto Sans SC", system-ui, sans-serif` | 17–18px / 1.65 |
| 小标签、代际线 | 同上 | 11–12px / 1.3，字距 0.02em |
| 代码、case 值、数字 | `"JetBrains Mono", ui-monospace, monospace` | 13–14px / 1.55 |
| 页面主标题 | 同上 sans | 22px / 1.2，600 |
| 卡片标题 | 同上 sans | 15px / 1.3，600 |

投影环境最小字号 **13px**；不要用 10px 以下的字。

### 1.3 颜色 token（浅色底，投影更稳）

| token | 值 | 用途 |
| --- | --- | --- |
| `--bg` | `#F7F4EF` | 页面底色（暖白，避免纯白刺眼） |
| `--panel` | `#FFFDF9` | 卡片底 |
| `--ink` | `#16181D` | 主文字（对比度 ≈ 15:1） |
| `--muted` | `#5C626B` | 次要文字（≥ 4.5:1） |
| `--rule` | `#8A8F98` | 分隔线、代际线、连线（1px，35–45% 透明度） |
| `--fail` | `#A3231B` | 失败边框 / 图标（不是纯红） |
| `--fail-bg` | `#FDF1EF` | 失败卡片 10–12%  tint |
| `--pass` | `#1B5E3F` | 通过 |
| `--pass-bg` | `#EDF6F0` | 通过 tint |
| `--warn` | `#7A4A00` | 负迁移 / 漂移 / partial（琥珀） |
| `--warn-bg` | `#FFF6E5` | 琥珀 tint |
| `--focus` | `#1F5FBF` | 键盘焦点环 |
| `--LIVE` | `#1B5E3F` | LIVE 点（仅在 live 且新鲜时） |

**禁止纯红 `#FF0000`、纯绿 `#00FF00`**。失败用低饱和砖红 + 浅 tint + 2px 边框 +
`✗` 图标 + `FAIL` 文本；通过同理用 `✓` + `PASS`。三重编码见 §10。

### 1.4 动效预算

- 首次进入：Run1 面板 0–400ms 淡入；Memory 每行 160ms 依次浮现（总 ≤ 1.2s）；
  Run2 400ms 淡入；负迁移条 300ms 淡入。之后**静止**，不循环。
- 自动轮播（仅 REPLAY/EXAMPLE）：Run1 三个失败卡每 7s 切换；Run2 四个 tab 每 7s 切换。
  用户点击后**钉住**，不再自动切；`LIVE` 模式不自动轮播（跟随最新数据）。
- 数字不做 count-up 动画；不要从 0 滚到 16/16。数字必须稳定可读。
- `prefers-reduced-motion: reduce`：所有 stagger、轮播、脉冲关闭，内容一次性显示。

---

## 2. 数据契约（页面只认这些路径）

### 2.1 顶层

| 字段 | 类型 | 页面用途 |
| --- | --- | --- |
| `schema_version` | `"1.0.0"` | 版本守卫；不认识的 major 显示 `DATA VERSION` 警告，不渲染 |
| `live` | bool | `false` → 一律 `REPLAY`；不得把样例标成 live |
| `phase` | `idle\|run1\|reflection\|run2\|done\|partial\|timeout` | 面板骨架 / 阶段 chip |
| `started_at` / `updated_at` | ISO 8601 | 120s / 300s 降级判定 |
| `revision` | 64 位小写 hex | 刷新信号；变化时更新，不做缓存键 |
| `lineage` | array | 顶部代际线，见 §7 |
| `run1` / `run2` | object | 左右两栏，见 §2.2 |
| `memory` | object | 中间栏，见 §2.3 |
| `negative_transfer` | object，可选 | 画面 4；缺失时整块隐藏 |
| `verdict` | `partial_transfer\|v3_stable_all\|v3_stable_headroom\|""` | 页脚主 verdict；照抄，不改写。三档含义见 §0：`partial_transfer` = 迁移只部分成立（2c 主实验 v1 口径）；`v3_stable_all` = v3 全题集稳定通过；`v3_stable_headroom` = 仅 headroom 题（x5–x7）稳定通过 |
| `verdict_note` | string，可选 | 与 `verdict` 同排显示的小字口径注记（如「x8 基线 2/2，不计入 headroom」「主 2c 为 partial_transfer」）；**不得**只显示 verdict 关键字 |
| `cost_usd` / `wall_ms` | number / int | 页脚成本与墙钟 |
| `note` | string | 页脚 tooltip |

### 2.2 `run1` / `run2`

```json
{
  "solved": 1,
  "total": 4,
  "headroom_solved": 0,
  "cost_usd": 1.59086813,
  "wall_ms": 3068945,
  "tasks": [ /* task[] */ ]
}
```

`headroom_solved` 可选：只计 x5–x7 的通过数，x8（`baseline-solvable`）不计入；
页面不得用它替换 `solved`，只用于说明「不含 x8」的口径。

`task` 字段：

| 字段 | 类型 | 页面用途 |
| --- | --- | --- |
| `id` | string | 卡片标题，如 `probe-order-x5` |
| `run_id` | string，可选 | 卡片 tooltip 的追溯 id；**不要**拼 state 路径 |
| `function` | string | 函数名，如 `unique`；Run2 左右两侧必须相同 |
| `signature` | string | 签名 chip，如 `(names) -> int` |
| `passed` | bool | 卡片状态底色 / 图标 |
| `passes` / `runs` | int，可选 | 卡片右上角 `0/4`、`4/4` |
| `note` | string，可选 | 卡片 tooltip（如 x5 的「含 2 次 gen3 续跑」） |
| `cases` | array | `input` / `expected` / `actual` 三行，见 §3.3 |
| `code_before` | string | split diff 左栏完整源码 |
| `code_after` | string | split diff 右栏完整源码 |
| `diff_lines` | array | 行级差异流：`{type: "same\|add\|del", text}` |

`case` 字段：`case_id`、`input`、`expected`、`actual`（后三者允许任意 JSON 值：
数组 / 对象 / 标量 / `null`）、`passed`。

### 2.3 `memory`

| 字段 | 类型 | 页面用途 |
| --- | --- | --- |
| `lines` | string[] | 5 行规则；逐行浮现 |
| `line_cases` | string[][]，可选 | 与 `lines` 等长；第 k 行 → 它解释的 case id |
| `source_cases` | string[] | 去重后的 case chip（建议 `task/case` 限定） |
| `sources` | string[]，可选 | 来源文件（仓库相对路径）；页脚 `N sources` |
| `attempts` | int，可选 | 反思次数；页脚 `1 reflection` |
| `reflection_model` | string | reflection 阶段显示模型名 |
| `cost_usd` | number | memory 产出成本（页脚 tooltip 可拆） |

### 2.4 `negative_transfer`

```json
{
  "task_id": "probe-order-x8",
  "baseline_signature": "(names) -> int",
  "drifted_signature": "(names, k) -> list[str]",
  "restored_signature": "(names) -> int",
  "status": "restored",
  "baseline_passes": 2, "baseline_runs": 2,
  "drifted_passes": 0, "drifted_runs": 2,
  "restored_passes": 4, "restored_runs": 4,
  "restored_signature_runs": 3,
  "note": "…"
}
```

`status ∈ {none, active, restored}`。`none` 或字段缺失 → 整个画面 4 隐藏；
`active` → 第三段仍为琥珀「仍在漂移」；`restored` → 第三段可显示恢复，但整条仍标
`NEGATIVE TRANSFER`，**不要**变成绿色成功条。

### 2.5 `diff_lines` 是线性流，不是左右两栏

`diff_lines` 按 `difflib` 的 opcode 顺序存：`same` 两边都有；`del` 只在左；
`add` 只在右；替换块先 `del` 后 `add`。**页面负责重建 split 视图**，不要假设
数组里已经分好左右。算法：

```js
const left = [], right = [];
for (const line of diffLines) {
  if (line.type === "same") {
    left.push({ text: line.text, kind: "same" });
    right.push({ text: line.text, kind: "same" });
  } else if (line.type === "del") {
    left.push({ text: line.text, kind: "del" });
    right.push({ text: "", kind: "pad" });
  } else if (line.type === "add") {
    left.push({ text: "", kind: "pad" });
    right.push({ text: line.text, kind: "add" });
  }
}
```

行号只对非 `pad` 行递增（左右各自计数）。同一替换块里 `del` 与 `add` 行数不等时，
短的一侧补 `pad`，不要错位。`pad` 行显示为空、无行号、无背景。

---

## 3. 画面 ①：Run 1（失败 / 对照）

### 3.1 这栏回答什么

baseline（v0）在 x5/x6/x7 上失败，x8 通过。三种失败形态必须在同一栏里可见：

1. **没动**：x5 的 `code_before` 与 `code_after` 完全相同（`diff_lines` 全是 `same`）；
2. **改错**：x6/x7 的 diff 有真实增删，但 case 仍失败；
3. **本来就会**：x8 通过（`baseline-solvable`），是画面 4 的对照，不能藏。

### 3.2 布局

左栏 710 × 808。**四张卡纵向排列**，默认只展开一张，其余折叠为 44px 高的标题行：

```
┌──────────────────────────────────────────┐
│ ● x5  unique()       FAIL 0/4   ✗✓✓     │  ← 折叠标题（44px）
├──────────────────────────────────────────┤
│ ● x6  dedupe_merge() FAIL 0/4   ✗✓✓     │
├──────────────────────────────────────────┤
│ ● x7  dedupe_min()   FAIL 0/2   ✗✓✓     │  ← 当前展开（约 520px）
│   input    […]                           │
│   expected […]                           │
│   actual   […]                           │
│   ┌─ stub ─────────┬─ run1 ───────────┐  │
│   │ …              │ …                │  │
│   └────────────────┴──────────────────┘  │
├──────────────────────────────────────────┤
│ ◆ x8  distinct_count() PASS 2/2  ✓✓✓    │  ← CONTROL，始终可见
└──────────────────────────────────────────┘
```

- 三张失败卡按 7s 自动轮播（REPLAY/EXAMPLE）；点击标题钉住；再次点击继续。
- x8 默认折叠，但**必须在首屏可见**：标题行用中性色 + `◆` + `CONTROL · baseline-solvable`，
  不要用绿色大成功块；展开后可看它的 diff 与 case。判定 control 不要硬编码所有通过题：
  用 `run1.tasks[i].id === negative_transfer?.task_id`（样例即 x8），
  或唯一 `passed === true` 的 run1 题。
- 标题行永远显示：task id、`function()`、状态 chip、`passes/runs`、三个 case 的
  `✓/✗` 小点（顺序与 `cases` 一致）。这样即使折叠也能读出「3 个 case 里哪个挂了」。

### 3.3 三行 case

展开卡上半部分是**三条固定行**，每条左侧 84px 标签，右侧等宽字体：

```
input      ["Delta", "ee", "DELTA", "delta"]
expected   ["ee", "DELTA"]
actual     ["DELTA", "Delta", "delta", "ee"]
```

规则：

- 值用 `JSON.stringify(value)`（紧凑、无多余空格）；字符串用双引号；`null` 显示 `null`。
- 长值允许换行（`overflow-wrap: anywhere`），但保持 `input/expected/actual` 三行标签对齐。
- `expected === actual` 时右侧加 `✓`；否则加 `✗`，并把 `actual` 行整行加浅红底
  （`--fail-bg`）+ `✗` 图标 + `aria-label="actual does not match expected"`。
- 只显示该 run 的 3 个 case；不要补造 case。

### 3.4 split diff（左 stub，右 run1）

- 两栏等宽（各 50%），栏头：`stub（工作区初始）` / `run1（baseline 改写后）`。
- 行号在各自栏的内侧；`same` 无背景；`del` 左栏浅红底 + `−` gutter；
  `add` 右栏浅绿底 + `+` gutter。颜色只是辅助，gutter 符号与 `aria-label` 必须存在。
- **长 same 段折叠**：连续 `same` 超过 4 行时，只显示首 2 行 + 中间
  `⋯ N 行未变`（11px muted）+ 末 2 行。折叠不得吞掉任何 `add/del`。
- 代码宽度不够时用 `white-space: pre-wrap; overflow-wrap: anywhere`，不要横向滚动条
  （投影时滚动条不可见且难操作）。换行行在行号后加 `↳` 细标记。
- **空 diff 状态**（x5；以及任何 `diff_lines` 中没有 `add`/`del` 的卡）：右栏不画空框，显示一条居中的：
  `NO EDIT — 模型返回了 stub，0 行变更`，仍保留红框 + `✗` + `FAIL · NO EDIT`。
  这是信息，不是渲染失败。
- **有改动的失败**（x6/x7）：照常画 diff；上方状态 chip 是 `FAIL`，不要因为「有 diff」
  就标成通过。
- 代码区只渲染 `code_before` / `code_after` 与 `diff_lines`；不要从别处抓代码。

### 3.5 状态与颜色

| 状态 | 边框 | 底 | 图标 | 文本 |
| --- | --- | --- | --- | --- |
| 失败 | 2px `--fail` | `--fail-bg` 10% | `✗`（SVG） | `FAIL` |
| 失败且未改 | 2px `--fail`（虚线内框） | `--fail-bg` 8% | `⊘` | `FAIL · NO EDIT` |
| 通过 / 对照 | 1px `--ink` 25% | `--panel` | `◆` | `CONTROL · baseline-solvable` |
| 数据缺失 | 1px `--rule` 虚线 | 透明 | `…` | `WAITING` |

**不要**把 x8 画成绿色大成功卡；它是 control，语气是「baseline 本来就会做」。

### 3.6 数据绑定

| 元素 | 路径 |
| --- | --- |
| 栏头 `1/4` | `run1.solved` / `run1.total` |
| 卡片 id / 函数 / 签名 | `run1.tasks[i].id` / `.function` / `.signature` |
| 状态 | `run1.tasks[i].passed` |
| `0/4` 徽章 | `.passes` / `.runs` |
| 三个 ✓/✗ 小点 | `.cases[].passed` |
| 三行值 | `.cases[].input` / `.expected` / `.actual` |
| 左/右代码 | `.code_before` / `.code_after` |
| diff | `.diff_lines`（§2.5 重建） |
| 追溯 | `.run_id`（tooltip） |

---

## 4. 画面 ②：Memory（它学到了什么）

### 4.1 布局

中栏 355 × 808。上半部分是规则列表，右侧贴边是 case chip 列，底部是计数行。

```
┌────────────────────────────────────┐
│ MEMORY · gen4 · deepseek-v4-pro    │  ← 22px 标题 + 12px 副标题
│                                    │
│ R1  先忽略大小写进行归类，每类只…  │──┐   ┌─────────┐
│ R2  每类若有多种写法，只保留字符…  │──┼──▶│ order-2 │
│ R3  归类后按书写长度从短到长排列。 │──┤   │ c1 c2 c3│
│ R4  长度相同时，按不区分大小写的…  │──┤   ├─────────┤
│ R5  若只需保留前若干项，则先完成…  │──┘   │ x3      │
│                                    │      │ c1 c2 c3│
│ 2 sources · 6 counterexamples · 1 reflection │
└────────────────────────────────────┘
```

- 规则行：17px / 1.65，左侧 24px 的 `R1…R5` 编号（11px mono muted）。
  数据里的 `"1. "` 前缀只用于排序；渲染时去掉，用 `R1` 替代，**文本内容不得改写**。
- 每行右侧留 64px 给连线出口；行与行之间 14px。
- 底部计数行：12px mono，`--muted`；数字可点击高亮对应 chip。

### 4.2 case chip 与连线

- chip 列宽 78px，按 `source_cases` 顺序分组（`order-2` / `x3`），组标题 10px uppercase；
  chip 文本缩写为 `c1`、`c2`…，`title` / `aria-label` 放完整 id
  （如 `probe-order-2/case_01`）。
- 每行 k 的连线段数 = `memory.line_cases[k].length`。一条 1px 贝塞尔曲线从规则行右端
  `(x=行右缘, y=行中线)` 到对应 chip 左端 `(x=chip 左缘, y=chip 中线)`：
  - `stroke: var(--rule)`，`stroke-opacity: .35`，`fill: none`，`vector-effect: non-scaling-stroke`；
  - 不画箭头；不要正交折线；允许少量交叉，不做复杂路由；
  - hover/focus 某行：该行连线 `opacity: .9`、`stroke-width: 1.5`，其他线降到 `.12`；
  - 点击某行：钉住该行的连线；点击空白处取消。
- `line_cases` 缺失、长度不等于 `lines.length`、或引用了 `source_cases` 之外的 id：
  **不画连线**，chip 仍按列显示，加一行 11px 说明 `connectors unavailable in this state`。
  不要猜映射。

### 4.3 逐行浮现

- 首次进入：R1 0ms、R2 +160ms、R3 +320ms、R4 +480ms、R5 +640ms；
  连线在该行出现后 240ms 内 `stroke-dashoffset` 绘制（或淡入）。
- `reflection` 阶段且 `lines` 为空：显示 `reflecting…` + `reflection_model`，
  **不要**画假进度条或假行。
- `idle` / `run1` 阶段：显示 5 个 44px 的灰色骨架槽，标题 `waiting for run 1 failures`。
- `done` 但 `lines` 为空：显示 `no memory produced`（中性文案，不是错误）。

### 4.4 页脚计数

- `N sources` = `memory.sources?.length`；缺失时 = `source_cases` 里不同 task 前缀数。
- `M counterexamples` = `memory.source_cases.length`。
- `1 reflection` = `memory.attempts ?? 1`；文案固定 `reflection`（单数）。
- 例：`2 sources · 6 counterexamples · 1 reflection`。
- 这些数字只来自 memory 块，不要用 `run1.tasks[].cases.length` 去算。

### 4.5 措辞

这栏是「规则」，不是「模型顿悟」。标题写 `MEMORY · RULES`，不要写
`WHAT IT LEARNED` 的大字断言；副标题可以写 `reflection over 6 failed cases`。
不要在 Memory 栏展示模型的完整思维链或 prompt 原文。

---

## 5. 画面 ③：Run 2（同一个函数的迁移）

### 5.1 这栏回答什么

**同一个函数**在 memory 介入前后的写法差异。左栏是 `run2.code_before`（即 run1
选中 baseline 的最终代码），右栏是 `run2.code_after`（memory v3 的最终代码）。
**不是**把左栏硬编码成 stub；x5 的 baseline 没改，所以它恰好等于 stub，但页面仍必须
读 `code_before`。也**不是**两个不同函数。两栏的函数名必须相同
（`run2.tasks[i].function`）；若解析结果不同，显示 `DATA ERROR · function mismatch`，
不画 diff。

### 5.2 布局

右栏 710 × 808：顶部 4 个 tab（44px），下面一个 50/50 split diff。

```
┌──────────────────────────────────────────────┐
│ x5 4/4 │ x6 4/4 │ x7 4/4 │ x8 4/4 !         │  ← tab bar，当前 x5
├──────────────────────────────────────────────┤
│ unique()   (names) -> list[str]              │  ← 函数名 + 签名 chip
│ ┌─ baseline ─────────┬─ memory v3 ─────────┐ │
│ │ …                  │ …                   │ │
│ │ − return sorted(…) │ + groups: dict…     │ │
│ │                    │ + …                 │ │
│ └────────────────────┴─────────────────────┘ │
└──────────────────────────────────────────────┘
```

- tab 标签固定 `x5 4/4` 格式；`4/4` 来自 `run2.tasks[i].passes/runs`。
- 默认选中 x5；REPLAY/EXAMPLE 下每 7s 自动切；点击钉住。
- x8 tab 右上角加琥珀 `!`，点击后画面 4 高亮（见 §6）。
- 面板标题：`Run 2 — memory v3`；右侧 `4/4` 总徽章来自 `run2.solved/total`。
- 函数名只出现一次（两栏共用），避免「两个函数」的误读。

### 5.3 diff 高亮

- 与 §2.5 相同的重建算法；左 `baseline`、右 `memory v3`。
- `del` 行左栏：浅红底 + `−` + 删除线可选（建议不加删除线，投影更清楚）；
  `add` 行右栏：浅绿底 + `+`；`same` 无背景。
- 变更块上下各留 2 行上下文；连续 `same` > 4 行折叠为 `⋯ N 行未变`。
- **不要**只高亮颜色：每行 gutter 必须有 `+` / `−`；整块变更的左上角加
  `2 行替换` / `5 行新增` 的小标签（11px muted）。
- 代码换行规则同 §3.4。

### 5.4 签名 chip 与漂移

- 函数名右侧显示 `task.signature`（mono 13px）。
- 如果 `run2.tasks[i].signature !== run1.tasks[i].signature` 且该题 `passed`：
  加琥珀 `SIGNATURE DRIFT` chip（`!` 图标），不要把它藏起来。v3 的 x5/x6 在完整
  4 次重复里有漂移；展示 run 选的是干净签名，但 chip 仍以数据为准。
- x8 tab 的 `!` 与 §6 的负迁移条联动；x8 的 `signature` 与 baseline 相同，
  所以 chip 本身不显示 drift，历史漂移由画面 4 讲。

### 5.5 `4/4` 徽章与注记

- 每题徽章：`passes/runs`，例如 `4/4`；不要写成 `100%`。
- x5 的 4/4 含 2 次 gen3 续跑（`task.note`）；徽章右上角加 `*`，tooltip 显示
  `task.note`。其他题没有 `*`。
- 如果 `passes < runs`，徽章用琥珀色 + `!`，不要用红色（不是失败，是抖动）。

### 5.6 数据绑定

| 元素 | 路径 |
| --- | --- |
| 总 `4/4` | `run2.solved` / `run2.total` |
| tab 标签 | `run2.tasks[i].id` + `.passes` / `.runs` |
| 函数名 | `.function`（左右共用） |
| 签名 | `.signature` |
| 左右代码 | `.code_before` / `.code_after` |
| diff | `.diff_lines` |
| x5 注记 | `.note` |
| 追溯 | `.run_id` |

---

## 6. 画面 ④：负迁移（可选）

### 6.1 何时显示

- `negative_transfer` 缺失或 `status === "none"` → **整块隐藏**，布局不留空洞
  （Run2 面板占满右栏）。
- `status === "active"` 或 `"restored"` → 在右下角显示一条 **琥珀色** 的
  三段式徽章条，或作为 x8 tab 的展开详情。不要覆盖主 diff；投影时默认放在
  Run2 面板底部，高约 120px。

### 6.2 三段式签名徽章

```
  (names) -> int          (names, k) -> list[str]          (names) -> int
  baseline 2/2     ───▶   gen2 0/2                 ───▶   gen4 4/4
                         SIGNATURE DRIFT                   RESTORED 3/4
  └───────────────────── NEGATIVE TRANSFER ─────────────────────┘
```

- 三段等宽，箭头 1px，中间段 2px 琥珀边框 + `!` 图标；整条左上角固定
  `! NEGATIVE TRANSFER`（11px uppercase 琥珀）。
- 签名用 mono 13px；如果太长，允许在 `->` 后换行，不要截断到不可读。
- 计数：`baseline_passes/baseline_runs`、`drifted_passes/drifted_runs`、
  `restored_passes/restored_runs`；第三段再加 `restored_signature_runs/restored_runs`
  的小字 `签名恢复 3/4`。
- `status=active`：第三段显示琥珀 `STILL DRIFTING`，不显示绿色 `RESTORED`；
  `status=restored`：第三段可以用 `✓` + `RESTORED`，但整条仍是琥珀底，
  **不要**变成「成功横幅」。
- 文案必须写：`x8 baseline 本来 2/2，不能算记忆提升`。这句话直接来自数据
  `baseline_passes/baseline_runs` 与 `note`，不要删。

### 6.3 不要

- 不要把 x8 画成「从 0 修到 4/4」的胜利故事；baseline 是 2/2。
- 不要隐藏 gen2 的 0/2；负迁移是这栏的主角。
- 不要用红色；负迁移是警告/学习风险，不是失败。用琥珀 + `!`。
- 不要在画面 4 里放柱状图或「漂移率」曲线；三段签名就够。

---

## 7. 代际线（页头）

### 7.1 结构

```
v0            gen2            gen3              gen4
0/10    ───▶  8/12    ───▶    12/12    ───▶     12/12
baseline      回放            反思              约束反思
```

- 位于页头 72px，横跨全宽；节点按四等分放置（或按 `lineage` 顺序等距）。
- 连接线 **1px `--rule`，45% 透明度**；节点圆点 6px；当前节点 2px `--ink` 环。
  **不要**用粗线、面积图、渐变、阴影或按通过数改变节点大小。
- 每个节点两行：第一行 `gen`（11px uppercase muted）；第二行 `passes/runs`
  （14px mono ink）；`label` 放节点下方 10px muted（或 tooltip）。
- `note` / `scope` 放 tooltip：`该节点覆盖 x5/x6/x7；分母与相邻节点可能不同`。
- 当前节点：`phase=run1|idle` → 第一个；`reflection|run2|done` → 最后一个；
  `partial|timeout` → 最后一个已完成的。当前节点只加环，不闪烁。
- `lineage` 为空：隐藏线，显示 12px muted `lineage pending`；不要画占位节点。

### 7.2 这不是图表

代际线是**实验时间线**，不是通过率曲线。禁止：

- 按通过数画 y 轴、面积、柱、折线斜率；
- 把 0/10 和 12/12 换算成百分比；
- 在不同分母的节点之间画「增长箭头」暗示同一口径。

节点只写 `passes/runs`；`scope` 与 `note` 说明分母差异。文案示例：
`v0 0/10（x5–x7）→ gen4 12/12（x5–x7；x8 另计）`，不要写 `0% → 100%`。

---

## 8. 页脚状态行

一行，横跨全宽，56px 高，顶部 1px `--rule`：

```
v3_stable_all  v3 16/16；x8 基线 2/2，不计入 headroom；主 2c（v1）为 partial_transfer   $2.050 · 60m 30s   [↺ REPLAY]
```

| 元素 | 数据 | 格式 |
| --- | --- | --- |
| verdict | `verdict` | 原样文本，mono 14px 600；空串显示 `—` |
| verdict_note | `verdict_note` | **必须**紧跟在 verdict 右侧，小字（11–12px `--muted`），同一行或直接下一行；不允许只显示 verdict 关键字。`verdict_note` 缺失时才省略，并在 tooltip 里写 `no verdict note` |
| cost | `cost_usd` | `$` + 3 位小数（`$2.050`）；tooltip 拆 `run1.cost_usd + run2.cost_usd` |
| wall | `wall_ms` | `Xm Ys`（`3629891` → `60m 30s`）；tooltip 说明「live 为会话墙钟，replay/example 为展示 run 之和」 |
| badge | `live` / `updated_at` / `phase` | 见 §9 |

- `note` 放 verdict 的 `title`/tooltip；不要在主行展开长文。
- 右侧 badge 与左侧 verdict 之间用弹性空白隔开；badge 永远靠右。
- 不要在这一行放图表、进度条、百分比。

---

## 9. 刷新与降级

### 9.1 取数

- 页面从 `data/probe/state.json` 取（相对于 `web/` 根）；每 **5s** 轮询一次，
  加 `?ts=${Date.now()}` 破缓存。
- `revision` 未变 → 不重渲染；变了 → 只更新变化的面板，保留当前 tab / 钉住状态。
- `state.json` 404 / 网络失败 / JSON 解析失败 → 取一次 `data/probe/example.json`，
  标 `REPLAY · EXAMPLE`；两者都失败 → 全屏错误卡 + `RETRY`，不要空白页。
- 连续 3 次拉取失败 → badge `OFFLINE`（琥珀 + `⚠`），保留最后一次好数据；
  恢复后自动回到 LIVE/REPLAY。

### 9.2 徽章判定（按优先级）

```
if phase === "timeout"                      -> TIMEOUT chip (red + ⏱)
else if phase === "partial"                 -> PARTIAL chip (amber + ◐)
else if source is example                   -> REPLAY + EXAMPLE
else if live === false                      -> REPLAY (gray + ↺)
else if now - updated_at > 120_000          -> REPLAY + STALE (gray/amber + ↺)
else                                        -> LIVE (green + ●, subtle pulse)
if phase === "done"                         -> append DONE chip (ink, no pulse)
if live === true && now - started_at > 300_000
   && !["done","partial","timeout"].includes(phase)
                                            -> force PARTIAL (amber + ◐) + "300s cap"
```

- **120s**：`live=false` **或** `updated_at` 超过 120s 未更新 → `REPLAY`。
- **300s**：硬上限；仅对 `live=true` 且未 done 的阶段生效。到点后冻结自动轮播、
  停止 LIVE 脉冲、显示 `PARTIAL · 300s cap`；保留最后数据，不清屏。
- `phase=done` 后即使 `live=true` 也不显示 `PARTIAL`；显示 `LIVE`/`REPLAY` + `DONE`。
- `updated_at` 无法解析 → 当作 stale（`REPLAY`），tooltip 显示 `invalid updated_at`。
- 客户端时钟偏差 > 60s：badge 旁加 `?`，tooltip 提示先对时；不要因此把 live 标成 replay
  之外的第三种状态。

### 9.3 缺失字段

- 顶层必填字段缺失 → 视为 `partial`：保留上一帧好数据；没有上一帧则用 example；
  在页脚加 `PARTIAL · incomplete`，不要抛异常。
- `run1.tasks` / `run2.tasks` 为空 → 显示骨架卡（`WAITING`），不要伪造 case。
- `memory.lines` 为空且 `phase=reflection` → `reflecting…`；`phase=done` → `no memory produced`。
- `negative_transfer` 缺失 → 隐藏画面 4；不要用 `run1.tasks[x8]` 硬凑。

### 9.4 示例数据

`example.json` 的 `live=false`、`phase=done`、`started_at/updated_at` 是固定证据时间
（不是本次时钟）。页面应显示 `REPLAY · EXAMPLE`，不要因为 `updated_at` 旧就显示
`PARTIAL`（300s 规则只对 live 生效）。`note` 原文是
`example generated from 2c evidence; not a live run`。

`example.json` 固定为 probe-4 的离线证据；页面轮询的现场 `state.json` 是 probe-6
（T2 checker 的 `case_04` 改为 baseline 必败的判别用例）。不要拿 example 的 case 数校验 live，也不要把
两者算进同一个通过率。

---

## 10. 可访问性

### 10.1 三重编码

每个状态必须同时有 **颜色 + 图标 + 文本**，不允许只靠红绿：

| 状态 | 颜色 | 图标（inline SVG） | 文本 |
| --- | --- | --- | --- |
| 失败 | `--fail` | `✗` 叉 | `FAIL` |
| 失败且未改 | `--fail` | `⊘` 斜杠圆 | `FAIL · NO EDIT` |
| 通过 / 对照 | `--pass` / ink | `✓` / `◆` | `PASS` / `CONTROL` |
| 漂移 | `--warn` | `!` | `SIGNATURE DRIFT` |
| 恢复 | `--warn` + `--pass` | `↺` | `RESTORED` |
| LIVE | `--LIVE` | 实心圆 | `LIVE` |
| REPLAY | `--muted` | `↺` | `REPLAY` |
| PARTIAL | `--warn` | `◐` | `PARTIAL` |
| TIMEOUT | `--fail` | `⏱` | `TIMEOUT` |
| OFFLINE | `--warn` | `⚠` | `OFFLINE` |

图标用内联 SVG（16×16，`stroke: currentColor`，`stroke-width: 2`），不要 emoji；
装饰性图标 `aria-hidden="true"`，状态文本保留在 DOM 中。

### 10.2 对比度与焦点

- 正文 `--ink` on `--bg` ≥ 7:1；`--muted` ≥ 4.5:1；`--fail`/`--pass`/`--warn`
  在各自 tint 上 ≥ 4.5:1（用作文字时）。
- `--fail-bg` / `--pass-bg` 只做背景；不要把浅底当文字色。
- 键盘焦点：2px `--focus` 环 + 2px offset，`outline` 不被 `overflow` 裁掉。
- Tab 顺序：代际线（只读，可跳过）→ Run1 卡片 → Memory 规则行 → Run2 tabs →
  负迁移 → 页脚。折叠卡片的标题是 `<button>`，`aria-expanded` 同步。
- 点击目标 ≥ 44×44px；tab 高度 44px；chip 至少 28px 高。

### 10.3 屏幕阅读器

- 卡片：`role="button"` + `aria-label="Run 1, probe-order-x5, failed, 2 of 3 cases matched"`；
  展开内容用 `aria-controls` 关联。
- case 行：`<dl>` 语义（`input`/`expected`/`actual` 为 `<dt>`，值为 `<dd>`），
  不要只画 div。
- 代际线：用 `<ol>` + `<li>`，每个节点 `aria-label="gen4, 12 of 12 runs passed, scope x5 x6 x7"`；
  不要用 canvas 画文本。
- 状态 badge：`role="status"` + `aria-live="polite"`；轮询更新不要每次都播报，
  只在 phase/badge 变化时更新文本。
- 连线是装饰：`aria-hidden="true"`；映射关系通过 `line_cases` 生成
  `aria-describedby` 文本（`R3 explains probe-order-x3/case_01`）。

### 10.4 动效与缩放

- 尊重 `prefers-reduced-motion: reduce`：关闭 stagger、轮播、LIVE 脉冲、连线绘制动画。
- 浏览器 125% 缩放下不重叠、不裁切；用 `rem`，不要写死 px 行高。
- 投影对比：避免 1px 以下细线、10px 以下文字、低对比灰字。

---

## 11. 验收清单（写完页面逐条勾）

- [ ] 1920×1080 无滚动；1600×900 不重叠；三列 40/20/40（或断点规则）。
- [ ] 页头代际线 4 节点，`v0 → gen2 → gen3 → gen4`，每节点 `passes/runs`；
      1px 线、45% 透明度，没有 y 轴/面积/通过率曲线。
- [ ] Run1 有 4 张卡；x5/x6/x7 失败、x8 `CONTROL` 可见；每张卡有
      input/expected/actual 三行；split diff 左 stub、右 run1；
      x5 显示 `NO EDIT`，x6/x7 显示真实增删。
- [ ] Memory 5 行逐行浮现；每行按 `line_cases` 连到 case chip；
      页脚 `2 sources · 6 counterexamples · 1 reflection`；`line_cases` 缺失时不画线。
- [ ] Run2 4 个 tab 各 `4/4`；同一个函数的 baseline vs memory split diff；
      变更行有 `+`/`−` gutter + 背景；x5 的 `*` 注记可见。
- [ ] 画面 4 在 `negative_transfer` 缺失时完全隐藏；存在时琥珀 + `!`；
      三段签名 `(names) -> int` → `(names, k) -> list[str]` → `(names) -> int`；
      文案包含「baseline 本来 2/2」。
- [ ] 页脚 `verdict` + 同排小字 `verdict_note` / cost / wall / LIVE|REPLAY；example 显示 `REPLAY · EXAMPLE`；
      `live=false` 或 120s stale → REPLAY；300s 未 done → PARTIAL；不允许只显示 verdict 关键字。
- [ ] 颜色 + 图标 + 文本三重编码；不单靠红绿；对比度达标；键盘可达。
- [ ] 主画面没有柱状图/饼图/通过率曲线/百分比大数字；附录也没有通过率曲线。
- [ ] 未知键忽略；缺字段不崩溃；`prefers-reduced-motion` 生效。
- [ ] 页面 DOM / 文本里没有本机绝对路径（家目录、临时目录、state 路径）。

---

## 12. 不要做（硬约束）

1. **不要画柱状图、饼图、通过率曲线**，也不要在主画面放「成功率」百分比；
   这些只能放附录，且附录不是投影主视觉。
2. **不要用纯红/纯绿**，不要只靠颜色表达状态；不要用 emoji 当状态图标。
3. **不要把 `example.json` / `replay` 标成 `LIVE`**；不要把 `fixture` 标成 live。
4. **不要隐藏 x8 的 baseline 2/2**；不要把 x8 写成「从 0 修到 4/4」。
5. **不要跨 `scenario_version` 比较**；`example.json` 是 probe-4 的离线证据，现场 live `state.json` 是 probe-6（T2 `case_04` 为 baseline 必败判别用例），两者不要混算。
6. **不要重算通过率**（`solved/total` 只用于显示计数）；不要用 `4/4` 画任何曲线。
7. **不要改写 verdict**，也不要省略 `verdict_note`；`v3_stable_all` 就是 `v3_stable_all`，
   2c 主口径的 `partial_transfer` 写在 note 里。
8. **不要展示 holdout / 其他 Tier 1 数据**；本页面只读 `probe/`。
9. **不要为了让画面好看而删掉失败的 case**；x5/x6/x7 的 `✗` 必须保留。
10. **不要用 3D、渐变数据色、发光、粒子、自动播放声音**。

### 12.1 措辞禁区

| ✅ 可以说 | ❌ 不要说 |
| --- | --- |
| v3 在 x5–x7 上 12/12；x8 4/4 | 「memory 提升了通过率」 |
| x8 baseline 2/2；gen2 0/2；gen4 4/4，3/4 恢复签名 | 「x8 从 0 修到 4/4」 |
| verdict `v3_stable_all` + note（x8 不计入；主 2c 为 `partial_transfer`） | 「16/16 证明通用自进化」 |
| 这是 4 题 ×4 次的描述性证据 | 「统计显著」 |
| gen2 的记忆是失败用例原文回放 | 「模型理解了规则」 |
| 同一函数的 before/after | 「两个不同函数的对比」 |

---

## 13. 附录（可选，不进主画面）

如果观众追问，附录可以放：

- 逐题配对表：`task_id / baseline passed / memory passed / baseline cost / memory cost`；
- 4 次重复的逐 run 表：`run_id / outcome / signature / signature_changed`；
- 成本拆分：`reflection + runs`。

**仍然不要**放通过率曲线、柱状图、饼图；表格足够。附录默认折叠，按 `A` 打开。
