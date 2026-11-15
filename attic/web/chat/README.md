# molt Agent 对话页

这是独立于 `web/page/` 路演数据页的真实对话客户端，使用浏览器原生 HTML/CSS/JS，不需要构建工具或 npm 依赖。

## 本地打开

可以通过静态 HTTP 服务访问（推荐；也支持直接打开 `index.html`）：

```bash
python3 -m http.server 8000 --directory web
```

然后打开 `http://127.0.0.1:8000/chat/`，点击“连接设置”，填写 Agent API 根地址和服务端 token。
API 根地址是 origin，例如 `http://127.0.0.1:45123`，不要在末尾加 `/api`。
本地开发时把 API 地址填成你的 Agent origin（例如 `http://127.0.0.1:8080`，页面与 `/api/` 同源）；不要把生产地址写进仓库。

API 默认 mock provider，连通后可以立即进行本地对话；提到实验时 mock 会调用只读实验工具，适合检查完整交互路径。页面不把 token 写入 localStorage/sessionStorage/cookie 或 URL；连接配置只留在当前页面内存中。

## 页面结构（现场演示前端）

左栏是常驻功能菜单（图标 + 标题），共 6 项，默认不选中任何项、功能面板收起：

| 菜单项 | 面板内容 | 说明 |
| --- | --- | --- |
| 项目经验/分支回退 | 当前执行图（上层上下文图谱、分支切换、资产目录、REPLAY 评估）+ 重建候选图（只读静态预览） | 默认界面；默认显示当前执行图 |
| 两轮修复工作台 | `/api/lab/**` 两轮修复工作台 | 隔离不可用时执行端点关闭；runtime 未部署时普通聊天仍可用 |
| 运行能力设置 | 自动工具模式开关 + provider / 运行字段技术信息 | 连接后读取 `/api/runtime/status`；能力未启用时开关禁用 |
| 技能 | 技能目录、可用性与原因 | 只展示公开字段，不代表已执行 |
| MCP 工具 | MCP server / tool 列表 + manual 调用 | 无工具或 sandbox 不可用时显示原因 |
| 实验档案 | 实验索引、逐题对照、证据流示意 | 真实计数，不做帧号动画指标 |

点击菜单项会展开右栏（`#side-panes` 内）并切换到对应功能界面，`is-active` 标记当前项；
菜单选中态只存页面内存，不写 storage，也不改 URL。

菜单顶部有 `#menu-mode-toggle`，在「图标 + 标题」与「仅图标」之间切换：仅图标模式把
`is-menu-icons` 加到 `.chat-layout` 上并隐藏 `.menu-label`。按钮始终居左，两种模式下位置不变。
切换状态写入 `localStorage` 的 `molt.chat.menu.mode`（`labels` / `icons`），刷新后保留。

聊天区在桌面右栏始终可见并保持整屏高度；菜单与功能面板各自滚动。桌面断点为 1101px 以上，
1920 与 1366 下左栏约占 46–50%，右栏为聊天。1100px 以下改为单列，聊天排在功能面板之前，
右上角提供「工具面板 ↓」一键跳到功能菜单。390px 宽下无横向溢出，发送按钮在快照预览展开时仍可见。

**资产目录可收起**：默认收起，标题行只显示「▶ 资产目录」与右侧计数
（「共 N 项 · 当前显示 M 项」，未连接时是「读取中…」/「不可用」）；点标题展开目录说明、
状态过滤和资产卡片，▶ 旋转为 ▼。用的是原生 `<details>`，无额外状态存储。

**无大标题与导语**：原「大标题 + 导语」区块已删除，面板直接从功能内容开始；
「对话只发送到你配置的 Agent 服务」隐私提示已全部删除，不再出现在功能面板内。

**品牌字标**：左上角不再用「圆圈 m + molt」文字组合，改为 `molt-brand.svg`
（`Molt.svg` 的黑色渲染，高 38px、按原始 1914×908 比例自适应宽度），链接行为不变。

**欢迎区垂直居中**：没有对话时欢迎块在消息区内垂直居中（`.messages:has(> #welcome)`，
flex 居中），不再用 `clamp()` 固定顶部留白；发出首条消息后回到普通流式布局。

**菜单常驻，功能面板可收起**：「收起」按钮（`#panel-collapse`）悬浮在功能面板右上角
（`.side-panes-toolbar` 为零高度 sticky 层，不单独占行），只收起功能面板（`#side-panes`），
菜单列保留并按当前菜单模式显示；同时清除菜单选中态、隐藏所有 pane。
展开不设按钮，点任一菜单项即可展开。
收起时功能面板从右往左擦除消失（`clip-path: inset(0 100% 0 0)`），展开时从左往右展开；
面板列宽（`@property --panel-w`）与对话框宽度同步过渡，两个动画同时进行；菜单栏不参与动画。
收起状态不写 storage：刷新后固定为「面板收起 + 菜单未选中」；
只有菜单模式（图标 / 图标 + 标题）会被保留。

**运行能力 / 技能 / MCP 面板**：去掉 `.runtime-panel` 顶部横线（`border-top: 0`），
面板内容从顶部直接开始。

**实验档案分割线**：`EVOLUTION / EVIDENCE` 上方加一条 1px 分割线
（`#pane-archive .evolution-panel`），把来源标记说明与实验档案内容分开。
证据流示意图的 canvas 由 `ResizeObserver` 监听真实尺寸，面板展开动画结束后按 594×112
重设位图，避免被拉伸成一条色带。

**对话头标题**：「研究对话 / 尚未连接」两行贴合显示（`gap: 0`、`line-height: 1.25`）。
输入区状态行未连接时显示「未连接」，字号 `.68rem`，比正文小一号。

**顶栏**：`RESEARCH CONSOLE` 与连接圆点（`#connection-dot`）作为一组水平居中显示；
「实验 Agent」文案与顶栏展开按钮已删除。

**功能面板字体**：标签与说明类小字（如「公开状态」「是否评测」、状态 chip、过滤按钮、
`14 个上下文批次…` 统计条、叙述性 note）使用 `"Microsoft YaHei Light"`；
哈希、节点 ID、diff 等代码/数据文本仍用等宽字体。
输入区「通用 Agent / 实验问答」使用 `"Microsoft YaHei"`；鼠标悬停只给**未选中**的模式按钮
加蓝色内描边（`box-shadow: inset 0 0 0 1px`），不填充背景；已选中项悬停保持原样、不加边框。

**两轮修复工作台图标**：能力卡片圆内的放大镜改为内联 SVG，在圆内精确居中。

**对话区清理**：已删除「理解 / 检索 / 回答」阶段条，以及 provider / isolation 徽标；
消息区只保留对话内容和工具徽章。

**模式切换**：`通用 Agent / 实验问答` 放在输入框底部、「Enter 发送」左侧
（`.composer-bottom .mode-switch`）；不再有模式说明文字，也没有「去两轮修复工作台」按钮。
切换只改变发送时的模式指令前缀（`【通用 Agent 模式】` / `【实验问答模式】`），不写 storage。

**输入区收起**：输入框右下角的向下箭头按钮把编辑器整个收起（同时隐藏免责声明，消息区
顺势增高）；收起/展开都有 0.22s 的高度+透明度动画，右下悬浮球随输入区高度一起移动。
收起后对话右下角出现一个 46px 白色圆形悬浮球（带阴影），里面是铅笔图标，
点一下恢复输入框并把焦点放回输入区。按钮悬停只把边框变蓝，不填充底色。
状态只存页面内存，刷新回到展开态。

**跳转最新**：消息区离开底部超过 120px 时，右下角出现向下箭头的白色悬浮球
（与铅笔球同列、竖排，距右侧 34px，距输入区约 23px），
点一下平滑滚到最底部；回到底部后自动隐藏。

**欢迎区快捷问题**：「可以试试」的两个快捷问题在聊天区欢迎块内（左栏不再放），
点一下把问题填进输入框；没有对话时显示，发出第一条消息后随欢迎块一起消失，
点「新对话」清空会话后会重新出现。

**演示缩放**：桌面端把根字号提到 20px（等效浏览器 125% 的阅读尺寸），并同步放大
消息气泡、欢迎块、输入区与对话头高度；1100px 以下不套用该缩放，保持原有单列布局。

## 消息 Markdown

对话消息（用户与 Agent 都含）按 Markdown 渲染，实现是 `chat-md.js` 里的自研轻量渲染器：
没有 npm 依赖、不联网、不引入第三方库。

- 支持：标题、段落与换行、粗体 / 斜体 / 删除线、行内代码、围栏代码块（带语言类名）、
  有序 / 无序列表（含嵌套）、引用、表格、分割线、链接与裸 URL、`<https://…>` 自动链接。
- 安全：先整体 HTML 转义再套用格式规则，只产出一组固定标签，服务端或用户返回的
  原始 HTML 不会被执行；链接只放行 `http(s)`、`mailto` 与相对路径，`javascript:`、
  `data:` 等一律降级为纯文本；外链带 `rel="noopener noreferrer"`。
- 流式兼容：`message.delta` 累积原文，按帧节流重渲染；未闭合的代码块或标记按纯文本
  容错显示，`message.completed` 到达后以完整内容重渲染。
- 样式：代码块与表格在气泡内独立滚动，窄屏不撑破布局。

## 分支语义与上下文

- 分支 `label` 为 `ops` / `dev` 时显示中文「运维」/「开发」，其它 label 原样显示；
  `branch_id`、tip、context hash 等技术信息放在小字或折叠区。
- 「当前上下文」摘要显示：分支中文名、active 节点短 hash、资产目录分组计数（来自
  `/api/upper/assets` 的 `counts`/`assets`）。资产目录**不等于**分支已采纳资产。
- 分支区改为配置分支图：节点 + 父子边 + 分支 chips；active tip 实心 indigo，选中链的边高亮；点节点会选中所属分支，点 chip 直接切分支。`is-active` 用蓝底高亮，`is-selected` 用分支色描边，避免「开发是 ACTIVE 但运维带蓝边」的误读。
- 配置分支图支持「打开大图」：全屏浅色演示板，拖动画布平移、滚轮/按钮缩放；右侧显示分支/节点详情，可直接切换分支或回退到上一个完整上下文。
  初次加载默认选中 active 分支（无 active 时回退到 `ops` / 第一个分支）。
- 选择非 active 分支后，按钮上方显示「切换：从「X」到「Y」· 整份上下文切换，历史保留」；
  已在目标分支时提示「当前已在「X」……」。切换成功后只显示操作结果
  「已从「X」切换到「Y」· 整份上下文切换，历史保留。」（`#upper-action-result`，
  位于切换按钮下方），不再重复「最近切换：…」，并刷新 active/revision。
- 切换调用 `POST /api/upper/branches/{branch_id}/switch`，请求体
  `{expected_revision, expected_tip}`，二者都来自最近一次 `GET /api/upper/graph`。
- 回退按钮文案为「回退到上一个完整上下文」，调用真实 `POST /api/upper/rollback`，
  请求体 `{branch_id, target_node_id, expected_revision}`；成功后使用响应中的
  `target_node_id` 与 `history_retained`，说明“整份上下文（记忆 / Skill / MCP / 预算）
  一起恢复，历史节点保留”。
- 409 / 422 失败会显示 HTTP 状态与 API 的 `reason`（如 `stale_revision`、
  `approval_required`、`graph_validation`），状态改为「未生效」，不会更新成成功态。
  GET graph/assets 失败只显示实际读取错误，不会伪称 409。
- 写操作（switch / rollback / replay）POST 成功但后续 `GET /api/upper/graph` 或
  `/api/upper/assets` 失败时，页面不会报“完整成功”：状态显示「…已提交 · 刷新失败」，
  正文标注「当前显示未刷新，状态待核对」，并显示「重试刷新」按钮；在刷新成功前，
  switch / rollback / replay 暂时禁用，避免基于旧 revision 重复写。只有 POST + 刷新都成功，
  才显示「已从 X 切换到 Y」/「已回退到上一个完整上下文」/「REPLAY 完成」。
  POST 本身失败时仍显示「…失败 · 未生效」。
- `active` 为 `null` 时显示「active = null」，并阻止发送分支快照；不绑定空值。
- graph/assets 只读；页面不提供图谱写入、审批或资产编辑。

## 重建候选图（静态预览）

「项目经验 / 分支回退」顶部有「当前执行图 / 重建候选图」视图切换，默认当前执行图；两套视图互不影响。

- 重建视图读取同目录静态文件 `scale-graph.json`，不调用 `/api/upper/**`，不读取也不修改
  `active` / `tip` / `revision`，**没有**采纳、切换、回退按钮。
- 数据面：`branch → 14 个来源批次节点 → 189 个候选叶子`；ops 104 / dev 85。
  41 条 allowlist 公开摘要中 39 条可读、2 条因个人线索不展示（仅计数与哈希）；
  另有 148 条 internal / restricted 以匿名叶子存在。5 条 `mcp_profile`
  全部不公开，页面不显示其标题、正文、`prefix` 或连接配置。
- 顶部固定统计为「14 个上下文批次 · 189 候选资产 · 39 条摘要公开（2 条 withheld）·
  非训练记录/未评测/未采纳」，并注明「14 个批次节点不是 189 个图节点；189 是候选叶子总数」。
- 批次节点可折叠：默认展开 ops / dev 各前 2 个批次，每批显示 `N candidates`，
  展开后每页最多 20 条叶子并可「显示更多」；「展开全部（含叶子）」可查看全部 189 条简单行。
- 叶子展示：39 条公开叶子显示 ≤60 字安全短摘要、`asset_<sha8>`、kind / branch / batch、
  未评测 / 未采纳；2 条 withheld 只显示「已屏蔽（个人线索）」与哈希；148 条匿名叶子只显示
  稳定匿名 ID、安全分类与分支/批次。所有叶子 `evaluated=false`、`adopted=false`。
- 筛选：分支、来源类别、公开状态（全部 / 公开摘要 / 已屏蔽 / 匿名）、是否评测；
  搜索覆盖公开摘要、匿名 ID 与分类/批次标签；缩放 60%–160%。
  「是否评测」实际读取 `evaluated`：选「已评测」→ 0 批次 / 0 叶子并显示明确空态；
  选「未评测」或「全部」→ 14 批次 / 189 叶子（选中后展开全部匹配批次）。计数行同时显示
  匹配叶子数与当前 DOM 渲染数（默认仅展开各分支前 2 批，故默认渲染数小于 189）。
- 点击叶子显示安全 ID、分支/批次、类别、公开状态、`evaluated=false`、`adopted=false`；
  技术 hash 收在折叠区。叶子与批次详情都没有采纳、切换或回退按钮。
- 可读性：候选叶标题/摘要 ≥14px，批次/分支标题 ≥15px，状态/筛选/顶部统计 ≥13px；
  技术哈希等 metadata 可用 12px。1920 保留两栏，1366 及以下单列，390 下叶子单列换行。
- 静态文件读取失败时，预览区显示「重建预览不可用」，当前执行图与聊天不受影响。
- 生成方式（仓外批次目录）：`python3 scripts/build_scale_graph.py`；
  校验漂移：`python3 scripts/build_scale_graph.py --check`。
  详细口径见 [`docs/upper-scale-preview.md`](../../docs/upper-scale-preview.md)。

## 资产目录

- 资产按 `kind` 分组：`policy_memory` → 记忆，`skill` → Skill，`mcp_profile` → MCP。
- 计数只来自 API 的 `counts` 与 `assets`，不按 UI 推测规模。
- 过滤：全部 / active / draft / rejected / 未评估；`未评估` 匹配 `eval_state =
  not_evaluated`，其余匹配 `status`。计数为零的选项置灰。
- Skill / MCP 标签由资产字段驱动：`implementation_status = stub` →「未实现 · 未执行」，
  `implemented` →「待接入 · 未执行」，字段缺失或未知值 →「能力状态未声明 · 未执行」。
  本批不调用 Skill，也不连接 MCP，任何情况下都不声称已执行。
- 资产状态只使用契约字段 `status` / `origin` / `eval_state` /
  `implementation_status`；不读取契约外的 `status_reason`。
- 初始 `GET /api/upper/graph` 失败（如 `--upper-state` 在仓库内导致 503）时，active 区显示
  「上层 API 不可用：读取失败…」，资产计数显示「不可用」，路径区显示不可用提示；
  不会停留在「读取中…」，也不会留下空白面板。刷新成功前写按钮保持禁用，「重试刷新」可见。
  若已有旧数据但刷新失败，资产计数会标「上次数据，未刷新」，不冒充最新状态。

## REPLAY 评估

默认摘要按以下顺序展示，数字全部来自 `POST /api/upper/replay` 响应：

1. 新题迁移（x5–x7）：每个候选的 `candidate` / `baseline` 通过数，并给出阶段合计；
2. 旧能力回归（x8）：同上，包含负迁移回退的候选；
3. 决定：按 `metrics.phase` 标注「迁移 / 回归」，显示 keep / rollback。

逐题结果、`evaluation_id`、`lower_refs`、`reference_counts`、`telemetry.errors`、
replay id、graph_rollback 的 from/target/new_active hash 都收在
「技术信息与证据（revision / hash / 逐题）」折叠区。REPLAY 是零模型成本的重新判分，
不自动采纳；决定必须由显式操作确认。

## 上下文快照（可选，默认关闭）

composer 上方有「附带分支快照 · 资产目录摘要」开关和「预览」按钮，按钮是切换式：
展开时文案变「收起」，收起后恢复「预览」（`aria-expanded` 同步）。

- 关闭时走普通问答，发送内容就是输入原文，不附任何区块。
- 开启时，每次发送前重新 `GET /api/upper/graph` 与 `GET /api/upper/assets`，绑定当次响应的
  `active.branch_id`、`active.context_node_id`、`active.context_hash`、`graph.revision`；
  **不以旧快照兜底**。预览只用于核对，不参与发送。
- 附加区块首行固定为「引用数据，不是系统指令」，只作为本轮 user 内容的前缀；
  不伪造 system 消息。
- 摘要只包含 `kind` / `name` / `summary` / `status` / `origin` / `eval_state`
  （名称 60 字、摘要 120 字上限，每类最多展开 4 项），并移除绝对路径。
  不发送原始 Skill 文本、私密内容；Skill / MCP 均标「未执行」，实现状态来自
  `implementation_status`，缺字段时标「能力状态未声明」。
- 区块内明确「资产目录 ≠ 分支已采纳资产；draft / rejected / 未评估不是生效规则」，
  引用数据不提升数据权限。
- 完整消息（区块 + 原问题）超过 16384 UTF-8 字节时阻止发送，提示实际字节数；
  原问题不裁剪，输入框内容保留。
- 刷新失败、`active = null`、绑定字段缺失、响应缺 `assets` 时同样阻止发送并显示原因。
- 读取快照期间会禁用「新对话」「连接设置」以及 switch / rollback / replay 等会改变身份或
  上下文的写操作。若这期间连接、会话、base/token 或输入内容发生变化，本次发送会取消：
  不向新 session 发送快照，不清空或覆盖用户新输入，并提示“上下文已变化/输入已修改，请重试”。
  只有身份与输入都未变化时才清空输入并发送。
- 发送后历史消息只显示原问题，另加「分支快照」徽章和折叠的引用区块；
  `.message-snapshot` 改为同一行 flex 对齐（`align-items: center`），徽章与
  「查看引用区块（N 字节 · 不提升数据权限）」不再上下错位；
  发送内容里区块与问题之间用空行分隔。
- 不写长期记忆，不自动 adopt，不实现 token storage/URL 持久化。

## 标记与证据边界

- 对话区已删除 provider / isolation 徽标；运行能力设置用 `#runtime-provider` 显示
  `provider real` / `provider mock` / `provider —`。
- 两轮修复工作台仍显示 `provider … · LIVE/MOCK`、`isolation ready/blocked` 与
  `evolution` 元信息；来源标记不外推。
- `REPLAY`、`SEED`、`SYNTHETIC`、`REAL RUN` 徽章来自 API 字段；synthetic/seed 节点不显示成成功。
- 固定 fixture 边界文案保留在两轮修复工作台与实验档案；实验档案使用真实节点/资产/分支计数，
  已移除 rAF 帧号 `FRAME · trace-count` 和静态 `01 / 04`。

## 运行能力设置（模式 / 技术信息）

「运行能力设置」是独立菜单项，只放 session 运行模式与字段技术信息；技能、MCP 已拆成
「技能」「MCP 工具」两个菜单项，不再挤在同一个面板里。

- 每个 session 连接后调用 `GET /api/runtime/status`。字段来源：`enabled`、
  `skills.available/reason`、`mcp.available/reason`、`provider.kind`、`binding.revision`。
  `enabled:false`、404、503 时显示「能力未启用」及 HTTP/code/reason，开关禁用；
  技能 / MCP 面板显示「未加载」，普通聊天不受影响。若新端点未部署且浏览器 CORS
  预检失败，只得到 `Failed to fetch`，页面同样显示「能力未启用」并提示“未取得 HTTP
  响应，可能是端点未部署或 CORS 预检失败”，不伪称 404/503，也不显示空表、假数据或
  把未部署能力标成已启用。
- **自动工具模式默认开启**：能力可用（`enabled:true`、provider 已声明、技能可用；
  real 下 MCP 也可用）时，状态读取完成后自动进入 `active`，开关下方文案标注「默认开启」。
  切换只调用 `POST /api/sessions/{session_id}/runtime`，请求体只有
  `{"mode":"off"|"active"}`；不把 mode 写入 `messages`。只有 200 且 `mode` 与请求一致、
  `runtime_enabled=true` 才更新 UI；400/404/409（如 `runtime_disabled`）/503 显示原因
  并回退到原状态。
- `provider.kind == "real"` 且 skills/mcp 可用时允许「自动工具模式」；
  `provider.kind == "mock"` 时开关文案为「仅加载技能上下文（mock 不调用工具）」，
  不声称自主工具调用。`skills.available=false` 或（real 下）
  `mcp.available=false` 时禁用开关并显示 reason。
- provider 与运行字段（enabled / skills / mcp / binding revision）收在「字段来源与技术信息」
  折叠区；不显示空表或假数据。

## 技能

「技能」菜单项展示 `GET /api/skills/catalog` 的公开字段：`skill_id` / `version` /
`available` / `bound` / `reason` / `family` / `title` / `description` / `permissions` /
`triggers`，以及 `asset_id` / `content_hash` / `source` 摘要；不展示正文、源文件 locator
或路径。坏包 `available:false` + `reason` 原样展示；目录为空显示「为空」，不伪造条目；
能力未启用时显示「未加载」与原因。技能目录不代表已执行或任务提升。

SSE `skill.loaded` 显示「已加载技能 skill_id@version」、content_hash 短码、reason、
binding_revision，并注明「仅表示技能块进入本轮上下文，不代表模型使用或任务提升」。

## MCP 工具

`GET /api/mcp/tools` 按 `server_id` 分组，展示 `available` / `reason` 与每个 tool 的
`name` / `namespaced_name` / `description` / `input_schema`。无工具或 sandbox 不可用
显示原因，不造数。`mcp.available=false` 时仍显示原因并保留工具选择尝试。

- Manual 调用只允许目录中的 allowlist 工具，请求体
  `{server_id, tool, arguments}`，`arguments` 必须是 JSON 对象。只有 HTTP 200 且
  `status:"ok"`、`result` 存在时才显示成功；400/413/503 展示 `details.reason`，
  200 但非 ok 或缺 result 不显示成功。manual 调用不启动模型（`calls_model=false`），
  不自动重试。
- SSE `tool.call` / `tool.result` 带 `source:"mcp"` 时显示 server / tool / namespaced_name /
  status / error，`status:"ok"` 必须有 result 才显示成功；旧实验工具事件没有 `source`
  时保持原有「读取实验」展示。
- 不写 token 到 localStorage/sessionStorage/cookie/URL。

字段契约见 skill-mcp 仓库的 `contracts/openapi-skill-mcp-v1.json`（本页面只读契约，不修改后端）。

## 服务器联调

当前 `molt serve` 默认只绑定 loopback，并严格校验 `Host` 与 `Origin`。远程浏览器不能直接把 `127.0.0.1` 当作服务器地址。推荐在 Web/API 同一台服务器上由反向代理把 `/api/` 转发给 loopback 上的 molt 服务，并：

1. 将浏览器页面的精确 origin 加到服务 `--allow-origin`，例如 `https://demo.example.org`。
2. 代理到 molt 时把上游 `Host` 设为 `127.0.0.1:<molt监听端口>`（或 `localhost:<端口>`），满足服务 Host 检查。
3. 对外提供 HTTPS；前端 API 地址使用浏览器可访问的同源根地址，避免混合内容和跨域配置。
4. token 通过受控方式交给演示者，勿放进页面源码、静态配置文件或 URL。
5. 确保代理支持长连接/关闭响应缓冲，否则 SSE 会延迟或断流；需转发 Authorization 请求头。

若网页和 API 部署在不同域，除了配置精确 Origin，还要确认代理与浏览器的 CORS 预检、`Authorization` 请求头和 SSE 响应均可通过。上线前用实际域名走一遍“连接 → 发问 → tool.call/tool.result → message.delta → done”的流程。

## 当前 API 能力边界

- 页面可创建和删除内存 session、发送用户消息并显示 SSE 状态、工具调用及回答；
  点「新对话」清空消息后回到欢迎块（含两个快捷问题），不再是空白聊天区。
- 对话工具仍只读 probe 实验投影；不会在聊天工具里暴露任意 shell 或服务器文件读取。
- 两轮修复工作台通过 `/api/lab/**` 在独立临时 workspace 里跑
  round1 → patch → diff → round2。隔离后端不可用时执行端点 fail closed（按钮 disabled，
  API 返回 503 `isolation_unavailable`）。
- 该工作台是**新的 coding fixture**，与 demo/probe 历史数据分离；它不是引擎的
  genome evolution，也不是“自进化”结论。mock patch 会明确标注 `MOCK 演示`。
- 任务集当前 12 条：2 条演示 fixture（`calc-v1` / `slow-v1`）+ 10 条难度梯度，
  每题单文件可修、round1 真实失败、`expected_fix` 后 round2 真实通过：
  - 中等 4：`interval-merge-v1`、`duration-parse-v1`、`csv-line-v1`、`retry-plan-v1`；
  - 难 4：`ttl-lru-cache-v1`、`topo-order-v1`、`expr-eval-v1`、`ledger-apply-v1`；
  - 极难 2：`glob-match-v1`、`mini-json-v1`。
- 测试命令带 `-B`（`python -B -m unittest -q`）：round1 的 `__pycache__` 在同秒、
  同尺寸 patch 后仍会被判为有效，导致 round2 读到旧字节码；`-B` 从源头避免该误判。
- 后端 session / lab run / upper replay 都保存在进程内或外部 state 目录；服务重启后
  session/lab 消失。页面刷新后不会恢复旧对话，REPLAY 卡片也不自动恢复。
- OpenAI-compatible provider 需要后端设置 `MOLT_CHAT_MODEL` 等环境变量；浏览器不接触模型 key。
  服务器当前没有 provider 配置，运行能力设置与两轮修复工作台据此显示 mock。
- 页面使用 `fetch` 读取 SSE，而不是 `EventSource`，因为浏览器 `EventSource` 不能设置 Bearer Authorization header。

## 文件

- `index.html`：页面结构、常驻功能菜单（6 项）、菜单模式与面板折叠开关、连接配置与快照开关。
- `chat.css`：对话界面、菜单/功能面板布局、面板折叠、消息 Markdown 样式、1920/1366/390 断点。
- `chat.js`：API、session、SSE、lab、upper graph/assets、REPLAY、runtime skill/MCP、上下文快照、重建候选图、菜单模式与面板折叠、输入区模式切换；服务内容先转义再按 Markdown 渲染。
- `chat-md.js`：自研 Markdown 渲染器（零依赖、先转义、链接白名单、流式容错）。
- `molt-brand.svg`：左上角 Molt 字标（源文件 `Molt.svg`，页面用 `filter: brightness(0)` 渲染为黑色）。
- `scale-graph.json`：重建候选图静态数据面（由 `scripts/build_scale_graph.py` 生成，不手改）。

## 上层上下文图谱 API

详见 [`docs/api/upper-api.md`](../../docs/api/upper-api.md) 与
`contracts/openapi-upper-v1.json`：

- `GET /api/upper/graph`：`revision`、`active`（可为 `null`）、`branches`、`nodes`
- `GET /api/upper/assets`：`counts`、`assets`（含 `status`、`origin`、`eval_state`、
  `implementation_status`）
- `POST /api/upper/replay`：固定 C1b 评估，零模型成本
- `GET /api/upper/evaluations/{id}`：逐题结果与 telemetry
- `POST /api/upper/branches/{id}/switch`：需 `expected_revision`、`expected_tip`
- `POST /api/upper/rollback`：需 `branch_id`、`target_node_id`、`expected_revision`

上层 state（`--upper-state`，缺省取 `--state`）必须在仓库外；仓库内部署时用
`--upper-state` 指到仓库外目录，否则 `/api/upper/**` 返回 503，页面显示未启用。
该区不读取 `web/data`，不修改 `web/page/**`。

## 验证

本批只做前端静态检查与浏览器验证，不启动 pytest：

```bash
node --check web/chat/chat.js
node --check web/chat/chat-md.js
python3 -m json.tool web/chat/scale-graph.json >/dev/null
```

浏览器检查点：1920×1080 / 1366×768 / 390×844 的六项菜单与面板切换、默认分支页、聊天可见性与无横向溢出；
菜单模式切换（图标 + 标题 / 仅图标，按钮始终居左且位置不变，刷新按
`localStorage[molt.chat.menu.mode]` 保留）；刷新后面板固定收起、菜单未选中；
点菜单项从左往右展开，面板内「收起」从右往左擦除并清除选中态，面板与对话框宽度同时过渡；
对话区无阶段条 / provider / isolation 徽标，模式切换在输入框底部且发送前缀随模式变化；
分支卡片 active 蓝边、selected 非 active 浅边，初次加载选中 active 分支；
快照预览按钮展开为「收起」、收起恢复「预览」，历史消息引用块同行对齐；
自动工具模式在能力可用时连接后自动进入 active，文案为「默认开启」；
真实 graph/assets 字段、资产过滤、REPLAY 顺序与折叠证据；switch/rollback 成功与
409/422 失败提示；快照开关关闭/开启、绑定字段、首行声明、历史徽章、失败阻断与 16384 字节预算；
重建候选图视图切换、14 批次节点 / 189 叶子（39 public / 2 withheld / 148 anonymous）、
189 / 104 / 85 计数、41 allowlist / 39 展示 / 2 不展示 / 150 仅计数、分支/类别/公开状态/评测筛选、
公开摘要与匿名 ID 搜索、缩放、批次折叠与“显示更多”、叶子详情、顶部统计与 14/14/13px 字号，
以及 1920/1366/390 无横向溢出、静态文件缺失时的「重建预览不可用」；
节点详情、横幅、无采纳/切换/回退按钮，以及静态文件缺失时的「重建预览不可用」；
localStorage 只出现菜单模式键（`molt.chat.menu.mode`），sessionStorage/cookie/URL 不出现 token。
面板折叠：1920/1366 下折叠后菜单保留、聊天居中放宽，点菜单项展开且刷新固定为收起；
功能面板标签字体为微软雅黑 Light，「未连接」字号 `.68rem`，顶栏 `RESEARCH CONSOLE ●` 居中；
「运行能力设置 / 技能 / MCP 工具」无顶部横线；「通用 Agent / 实验问答」悬停只显示蓝色内描边；
两轮修复工作台能力卡片的放大镜 SVG 在圆内居中（dx/dy = 0）；
消息 Markdown：标题/列表/表格/代码块/链接渲染、未闭合代码块流式容错、
`javascript:` 链接与原始 HTML 只作纯文本显示。


## 2026-11-14 完善

- 新增「历史会话」：只列出有消息的会话，按时间倒序，点击切换；标题取首条用户问题；非当前会话支持两次点击删除。
- 新增 session 刷新恢复：`sessionStorage` 只存 base + session id，token 仍只留在页面内存；刷新后重输 token 可恢复。
- 新增「停止接收」：先调用 `/api/sessions/{id}/cancel` 请求服务端协作取消；当前模型调用返回后跳过后续工具/进化，不写入本轮历史。请求失败时才回退为停止本地 SSE。
- 新增消息级「回到此处」：按快照 `message_count` 映射，两次点击确认；回滚使用当前 revision 做 CAS。
- 快照面板改为 git 风格分支图：主线 + `rN→rM` 回退分支，虚线表示回退边；点节点选择，回退生成新分支，可再选其他节点切回。
- 新增全屏「分支黑板」：与 chat 同色系的浅色演示板（indigo 主线、彩色分支车道、正交折线），支持拖动画布平移、滚轮/按钮缩放、点节点查看、选中后切换为新分支；右边栏显示 revision / evidence / messages / 来源分支。
- 新增「本轮执行轨迹」面板：快照、工具、进化、binding 事件按服务端顺序显示；每轮回答底部显示模型调用次数、工具次数、耗时和 provider 返回的 token 用量（无 usage 不伪造）；usage 写入消息与快照，历史重载和服务重启后仍保留 token 用量（耗时不重放）。
- 新增「进化目录」：读取 `/api/evolution/catalog`，展示 Skill / MCP / Memory 资产、来源 session、evidence、evaluation scope、复用会话数；Skill 提供全局 binding 回滚入口，Memory / MCP 提供「从复用池移除」与「恢复到复用池」（只改后续复用，不改已绑定会话）；目录可过滤「已移除」。
- 进化提交 evidence 持久化 `bottom_evidence`：probe / lab run 可从快照和目录追溯。
- 窄屏修复：状态文字不再逐字换行，窄屏隐藏「工具面板」入口（底部菜单承担）。
- 侧栏最小可读字号调整；历史会话对话框、目录统计和移动端布局已验证。

演示步骤见 [`DEMO.md`](DEMO.md)。
