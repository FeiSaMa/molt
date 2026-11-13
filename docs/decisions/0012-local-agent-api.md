# ADR 0012：本地 agent API（Batch A+B 合并 MVP）

- 状态：**已决定（2026-11-13，按 critic PASS 的冻结方案落盘）**
- 日期：2026-11-13
- 关联：`molt/agent/`、`molt/cli.py`（`serve`）、`probes/build_probe_experiments.py`、
  `probes/probe_experiments_manifest.json`、`contracts/**`、`docs/api/probe-agent-api.md`

> 编号说明：`0010` 已被 `0010-probe-bench-exception.md` 占用，`0011` 也已存在；
> 本 ADR 按契约 owner 的修正指令使用 `0012`，**未改动**既有 ADR。

## 背景

页面侧只有一个 `web/agent.sample.json` 示意样例：session、turn 事件与
experiments 投影都没有可调用的后端，也无法验证「模型只读实验投影」的路径。
Batch A（probe API + 生成器）与 Batch B（session/SSE chat）的目标是把这条链路
变成可运行、可测试、可审计的最小闭环，同时不碰 demo 的 Tier 1 数据面与 Pi 适配器。

约束来自仓库既有边界：`bench/**` / `shells/**` / `web/**` / `molt/export.py` 冻结或只读；
`molt/core/**` 不反向依赖 adapter/shell；本批不新增第三方依赖。

## 决定

### 1. 只读 probe 投影 + 可复算生成器

- `probes/probe_experiments_manifest.json` 保存 8 条实验的冻结值、16 项 caveat 闭集、
  coverage 谓词与逐源文件 `bytes/sha256`。
- `probes/build_probe_experiments.py` 先校验全部源文件哈希与 verdict 交叉引用，再在内存里
  投影；`generated_at` 取 manifest 固定值（不读时钟），`revision` = 去掉 revision 后规范化
  JSON 的 sha256，两次构建字节一致。
- `--out` 必须显式且在仓库外；失败不写部分文件。`molt serve --probe-index` 只读加载并重算
  revision，不符即 `probe_index_invalid`、exit 2、不监听。
- 投影只含白名单字段：prompt / response / thinking / memory / cases / stdout / stderr /
  solution_snapshot / ext / task_id / run_id / task_ids 一律不返回；verdict 也不进 API。

### 2. loopback-only HTTP 服务

- 只用 stdlib `ThreadingHTTPServer`；默认 `127.0.0.1`、`--port 0`；拒绝 `0.0.0.0` 与非 loopback。
- 路由固定 10 条（8 个 path）；无 PATCH/PUT；HEAD → 405；trailing slash → 404。
- 优先级：未知 path 404（含未知 path 的 OPTIONS）→ 405 + `Allow` → Host 400 → Origin 403 →
  Bearer 401 → 未知 id 404。
- 错误码与 HTTP 映射的唯一源是 `molt/agent/errors.py`；上游错误
  （`upstream_*`）与 `tool_denied` / `too_many_tool_calls` **只作 SSE error code**，
  HTTP 层永不返回 502/504。

### 3. 鉴权与 CORS

- `secrets.token_urlsafe(32)` 生成 token，写入 `--token-file`（默认 `<state>/token`），
  权限 0600；创建/权限失败 fail closed。无 env token、无跳过鉴权；比较用 `hmac.compare_digest`。
- `Host` 只允许 `127.0.0.1:<port>` / `localhost:<port>`；Origin 默认只允许自身同源，
  `--allow-origin` 精确追加，`Origin: null` 需显式放行，`*` 拒绝。
- 已知 path 的 OPTIONS 免 Bearer，回显精确 Origin 与固定的 Allow-Methods / Allow-Headers。

### 4. 内存 session 与 SSE

- session 存内存 dict（重启即丢）；每 session 事件缓冲 ≤4096，淘汰最旧；事件 id 从 1 递增。
- `POST /messages`：先捕获 `last_event_id` 作为 `event_cursor` → 202 → 后台线程发 `turn.started`；
  客户端用 `since=cursor` 能保证第一个事件属于该 turn。
- SSE：replay 后 live tail，15s ping；`done` / `error` 后关闭；`since` 早于最旧保留 → 410
  `details.reset_required=true`；多订阅者独立。
- 历史只保留成功 turn 的 user/assistant 对；失败 turn 不落库；`in_progress` 删除 → 409。

### 5. Provider 与单步工具

- 默认 `mock`（确定性、零网络、零成本）；`openai-compatible` 需 `MOLT_CHAT_MODEL`，
  缺则 exit 1 不监听。key 只读 `MOLT_CHAT_API_KEY`，不回退其它变量，不进响应/日志。
- 请求 `stream=false`，服务端切 40 字符 delta；工具名固定 `experiments_list` /
  `experiments_get`；首调 `tool_choice="auto"`，工具后二调 `tool_choice="none"`。
- 明确**单步**：最多一次工具调用 + 一次工具后回复；>1 tool_calls 一个都不执行，
  error `too_many_tool_calls`；工具错误仍进入二调。

## 不做（本批范围外）

- 不做 UI / 页面改造（`web/**` 只读，`web/agent.sample.json` 不动）。
- 不接真实 Pi、不调用模型、不启动 llama-gpu、不装依赖（`dependencies = []`）。
- 不做持久化：session、消息、事件全在内存；重启即丢。
- 不做通用 agent loop（多步 ReAct / 多工具编排 / 并发工具）。
- 不做多用户 / 多租户、token 轮换、审计日志落盘。
- 不改 `bench/**`、`shells/**`、demo Tier 1 五文件、`molt/export.py`、`probes/report/probe_state_schema.json`。

## 后果

- 正面：probe 数字可复算（manifest 哈希 + revision 自校验）；页面可用 fetch + Bearer +
  ReadableStream 联调；mock 让 CI 零网络；错误码单源，HTTP 与 SSE 通道不混。
- 代价：单进程、单机、内存态；事件缓冲上限意味着长时间不消费的客户端会收到 410，
  必须从 0 重新同步；`--allow-origin` 是精确匹配，新增前端端口要显式配置。
- 兼容性：`contracts/openapi-v1.json` 的 paths 与服务路由集合相等；新增路由或错误码需要
  同时更新 schema、OpenAPI、`docs/api/probe-agent-api.md` 与测试。

## 备选（未采用）

- 引入 FastAPI/uvicorn：需要第三方依赖，违反本批 `dependencies = []`。
- 用 `EventSource` 消费 SSE：无法附带 `Authorization`，会诱导 query token；文档明确禁用。
- 持久化 session 到 `state/`：会破坏零写入验收，且与「重启即丢」的 MVP 语义冲突。
