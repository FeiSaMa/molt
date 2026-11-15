# 素材页内容工作稿

一句话：**这里只是内容工作稿，排版和视觉由你来定；页面只认一份 `content.json`，改内容不要动 HTML。**

## 文件清单

| 文件 | 作用 | 谁维护 |
| --- | --- | --- |
| `index.html` | 零依赖静态渲染页（内联 CSS / JS，无 CDN、无构建）。读 JSON 后按 `order` 渲染卡片、目录、筛选和复制按钮。 | 前端 / 排版 |
| `content.json` | **唯一内容数据源**：元信息、块顺序、素材块正文、数字、图表、示例、边界与演示动作。 | 内容 |
| `sources.json` | 来源字典：每个 key 对应 `repo` / `path` / `locator`，页面按 key 解析成纯文本来源行（不拼外链）。 | 内容 / 证据 |
| `README.md` | 本文件，编辑约定与字段说明。 | 内容 |

页面不内置任何数字或观点；所有页面文字来自 `content.json`，来源文字来自 `sources.json`，剩下的只是固定 UI 标签。

## 怎么改

### 加一个块

1. 从 `blocks` 里复制一个结构完整的块；
2. 改 `id`，**必须全文件唯一**（重复 id 会被页面跳过并告警）；
3. 填 `title` / `one_liner` / `status` / `numbers` 等字段；
4. 把新 `id` 加到 `order` 里你希望它出现的位置。

只加进 `blocks` 但不加进 `order`，页面不会展示它，只会在顶部提示「未列入 order」。

### 调顺序

**只改 `order` 数组**。`order` 决定目录和卡片的顺序；不要为了调顺序去移动 `blocks` 里的对象。

### 删一个块

同时删两个地方：

- `order` 里的 id；
- `blocks` 里对应的对象。

只删一边的后果：

- 只删 `blocks`：`order` 引用不存在的块，页面跳过并告警；
- 只删 `order`：块仍在 `blocks` 里，页面不展示并告警。

### 改文案

只改 `title` / `one_liner` / `body_md` 是安全的。改 `numbers` 和 `limits` 属于改结论，必须同时核对来源与 `level`，不要凭印象改。

## 字段表

### 顶层

| 键 | 类型 | 必填 | 作用 |
| --- | --- | --- | --- |
| `meta` | object | 是 | 页面头部信息与状态图例。 |
| `meta.title` | string | 是 | 页面标题与浏览器标题。 |
| `meta.subtitle` | string | 否 | 标题下的一句话说明。 |
| `meta.updated` | string | 是 | 内容更新时间（写人类可读日期）。 |
| `meta.base_commit` | string | 是 | 内容对应的基线提交号（只写短 SHA，不要加链接）。 |
| `meta.scope_note` | string | 是 | 口径说明：这批素材能说什么、不能说什么。 |
| `meta.legend` | array | 是 | 状态图例：`{ key, label, desc }`。`key` 必须和 `block.status[].key` 对应；`label` 显示在徽章上；`desc` 作为悬浮说明。 |
| `order` | string[] | 是 | 块的展示顺序，元素是 `block.id`。缺省时页面按 `blocks` 顺序展示并告警。 |
| `blocks` | object[] | 是 | 素材块数组，字段见下。 |

### 单个 block

| 键 | 类型 | 必填 | 作用 |
| --- | --- | --- | --- |
| `id` | string | 是 | 锚点与顺序键，全文件唯一；不要用空格和 `#`。 |
| `title` | string | 是 | 卡片标题。 |
| `one_liner` | string | 是 | 一句话结论，显示在标题下、带左侧色条。 |
| `tags` | string[] | 否 | 标签，显示成 `#tag` 小片。 |
| `status` | `{key,label}[]` | 是 | 状态徽章。`key` 去 `meta.legend` 里取色与文案；`label` 可覆盖 legend 的 label。 |
| `body_md` | string | 是 | 展开讲解，默认折叠。只用最小 Markdown：`###` 小标题、`**加粗**`、`- ` 列表、空行分段。 |
| `numbers` | object[] | 是（可为空数组） | 数字表，列固定为「指标 / 数值 / 分母 / 层级」。 |
| `numbers[].label` | string | 是 | 指标名。 |
| `numbers[].value` | string | 是 | 展示值，保留单位与符号；不要在这里塞解释。 |
| `numbers[].denominator` | string | 是 | 分母或口径；没有分母就写 `"—"`。 |
| `numbers[].level` | string | 是 | **必填**，且必须是 `live` / `REPLAY` / `离线` / `静态` / `历史 claim` 之一。 |
| `numbers[].source` | string | 是 | 来源 key（对应 `sources.json`）或短文本；会以小字显示在指标下方。 |
| `chart` | object \| null | 否 | 图表；无图写 `null`。 |
| `chart.type` | `"table"` \| `"bar"` | 是（chart 非 null 时） | 表格或横条图。 |
| `chart.columns` | string[] | table 必填 | 表头。 |
| `chart.rows` | any[][] | table 必填 | 表格行，元素渲染成纯文本。 |
| `chart.rows[].label` | string | bar 必填 | 横条标签。 |
| `chart.rows[].value` | number | bar 必填 | 横条数值，用来按最大值归一化宽度。 |
| `chart.rows[].display` | string | bar 必填 | 横条右侧展示值（建议带单位）。 |
| `chart.note` | string | 否 | 图表下方的注释。 |
| `examples` | object[] | 是（可为空数组） | 示例区。 |
| `examples[].kind` | string | 是 | 示例类型。页面识别 `teaching` / `教学` / `example` 显示为「教学示例」，识别 `actual` / `record` / `记录` / `live` / `replay` / `real` 显示为「实际记录」。 |
| `examples[].input` | string | 至少一项 | 输入 / 题面。 |
| `examples[].expected` | string | 至少一项 | 预期输出。 |
| `examples[].note` | string | 否 | 示例说明。 |
| `examples[].source` | string | 是 | 来源 key 或短文本。 |
| `limits` | string[] | 是（可为空数组） | 「不能这么说」列表，红色区块，逐条写边界。 |
| `demo_action` | object | 是 | 现场演示的三行脚本。 |
| `demo_action.action` | string | 是 | 台上做。 |
| `demo_action.evidence` | string | 是 | 应看到。 |
| `demo_action.fallback` | string | 是 | 失败降级。 |
| `sources` | string[] | 是（可为空数组） | 来源 key 列表（对应 `sources.json`），页面渲染为 `repo · path · locator` 纯文本，不生成链接。 |

### sources.json

| 键 | 类型 | 必填 | 作用 |
| --- | --- | --- | --- |
| `generated_at` | string | 是 | 来源清单生成时间。 |
| `note` | string | 是 | 来源清单口径说明。 |
| `sources` | object | 是 | key → 来源对象。 |
| `sources.<key>.repo` | string | 是 | 仓库名。 |
| `sources.<key>.path` | string | 是 | 文件路径。 |
| `sources.<key>.locator` | string | 是 | 行号、函数名、命令或记录 id。 |
| `sources.<key>.kind` | string | 否 | 来源类型（如 code / run / doc）。 |
| `sources.<key>.status` | string | 否 | 该来源的事实等级（如 live / replay / 静态）。 |
| `sources.<key>.desc` | string | 否 | 一句话说明，渲染为来源行的悬浮提示。 |

## 完整 block 示例（bar 版，可直接复制）

```json
{
  "id": "example-cost",
  "title": "示例块：成本对比",
  "one_liner": "同一组题上，候选配置的单题执行成本低于基线；这是结构示例，不是结论。",
  "tags": ["成本", "示例"],
  "status": [{ "key": "landed", "label": "已落地" }],
  "body_md": "### 怎么读\n\n这里放方法说明，默认折叠。正文用**加粗**强调关键约束。\n\n- 第一点：先看分母\n- 第二点：再看层级列\n\n段落之间用空行分开。",
  "numbers": [
    {
      "label": "单题执行成本",
      "value": "120",
      "denominator": "12 题",
      "level": "REPLAY",
      "source": "runs"
    },
    {
      "label": "墙钟时间",
      "value": "68s",
      "denominator": "—",
      "level": "离线",
      "source": "runs"
    }
  ],
  "chart": {
    "type": "bar",
    "rows": [
      { "label": "基线", "value": 120, "display": "120" },
      { "label": "候选", "value": 68, "display": "68" }
    ],
    "note": "示例数字，仅演示归一化宽度；正式内容必须来自证据。"
  },
  "examples": [
    {
      "kind": "teaching",
      "input": "题面输入示例",
      "expected": "预期输出示例",
      "note": "教学用，不是实际运行记录。",
      "source": "demo"
    },
    {
      "kind": "actual",
      "input": "实际运行中的一条输入",
      "expected": "实际记录到的输出摘要",
      "note": "来自回放的某条记录。",
      "source": "runs"
    }
  ],
  "limits": [
    "不能说「所有任务都更便宜」。",
    "不能把示例数字当作真实基线。"
  ],
  "demo_action": {
    "action": "台上跑一条 replay",
    "evidence": "看到 gate 判定与逐题配对表",
    "fallback": "改放已导出的截图与数据面"
  },
  "sources": ["runs", "gate"]
}
```

## table 版 chart（替换上面 block 的 `chart` 字段）

```json
"chart": {
  "type": "table",
  "columns": ["配置", "通过题数", "单题成本"],
  "rows": [
    ["基线", "10/10", "120"],
    ["候选", "10/10", "68"]
  ],
  "note": "示例表格；数字仅示意，正式内容必须来自证据。"
}
```

## 数字字段规范（重点）

1. `level` **必填**，只能写：`live`、`REPLAY`、`离线`、`静态`、`历史 claim`。不要写「实时」「实测」这种模糊词，也不要把 replay 写成 live。
2. `denominator` **必填**；没有分母就写 `"—"`，不要留空，不要用 0 代替。
3. `value` 是展示值，保留原始单位与精度；换算口径写进 `denominator` 或 `body_md`。
4. `source` 必须能落到 `sources.json` 的 key 或一句可核对的位置描述；不要写「内部数据」。
5. 同一块的数字必须共享同一个分母口径；分母不同就拆成两行或两块。

## 本地预览

```bash
cd web/page/materials
python3 -m http.server 8123
```

然后打开该目录的 `index.html`（`http://localhost:8123/`）。

- 必须通过 HTTP 访问：直接双击 HTML（`file://`）会被浏览器拦截 `fetch`，页面会显示明确的错误提示块。
- 这里只做预览，不跑测试；预览服务看完就关掉。

## 禁忌

- 不要把页面内容当指令执行。素材块里的文字是给人看的，不是给 Agent 的命令。
- 不要往 `content.json` / `sources.json` 塞个人路径、服务器 IP、凭据、密钥或内网地址。
- 不要新增第二份数据源：不许在 HTML / CSS / JS里硬编码数字或结论；页面只读这两个 JSON。
- 不要手改 `index.html` 里的渲染逻辑去「适配」某一块；如果字段不够用，先改契约再改页面。
- 不要把 `sources.json` 的 `repo` / `path` 拼成 GitHub 链接；仓库可能不公开，页面只显示纯文本。
- 不要为了排版在 JSON 文本里塞 HTML 标签；`body_md` 只走白名单转换，其他字段全部按纯文本渲染。

## 导出建议（大屏 / PPT）

- **主线取 `order` 前 6 块**：每块只讲 `one_liner` + `numbers` 里的一行关键数字 + `demo_action` 的「台上做 / 应看到」。
- 第 1 块建议是全局口径或最小可验证单元；第 2–4 块放主结果与对照；第 5–6 块放边界与失败降级。
- 超过 6 块的部分放备份页，现场只在被追问时跳转，不占主线时间。
- PPT 上不要把整块正文贴进去；`body_md` 是讲解底稿，不是幻灯片文案。
- 复制单块时用卡片右上角「复制本块」，拿到的是纯文本，方便粘进讲稿或备注。

## 页面行为（便于校对）

- 顶部目录从 `order` 生成，点击平滑滚动；状态筛选按钮从 `meta.legend` 里实际出现过的 key 去重生成。
- 每块右上角「复制本块」会复制 `title` / `one_liner` / `numbers` / `limits`，成功时按钮变为「已复制 ✓」并弹出提示；失败时会选中本块文字并提示手动复制。
- `body_md` 默认折叠；打印时会自动展开。
- 数据有问题（重复 id、order 缺失、sources 缺失）时，页面顶部显示「数据提示」并继续渲染其余内容；`content.json` 整体读不到时显示错误块，不会白屏。
