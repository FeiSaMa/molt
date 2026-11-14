# 本地 agent API（Batch A+B 合并 MVP）

> 契约级事实源：`contracts/openapi-v1.json` 与 `contracts/schemas/*.schema.json`。
> 本文只解释语义与运行方式；字段冲突时以 schema 为准。

服务只监听 loopback（`127.0.0.1` / `localhost`），默认端口 `0`（由内核分配），
只依赖标准库与仓库内已冻结的 probe 投影。进程内 session：**重启即丢**，不做持久化。

## 启动

```bash
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt serve \
  --state /tmp/molt-agent \
  --allow-origin http://127.0.0.1:5173
```

stdout **恰好一行** JSON（供脚本读取）：

```json
{"listen": "http://127.0.0.1:45123", "token_file": "/tmp/molt-agent/token"}
```

stderr 打印人读的 `listening on ...` 与 `token file: ...`。任何日志、响应、错误体都不含 token 值。

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--host` | `127.0.0.1` | 只允许 `127.0.0.1` / `localhost`；`0.0.0.0` 或非 loopback 直接退出 |
| `--port` | `0` | 临时端口；实际端口见 stdout `listen` |
| `--state` | `state` | token 默认写入 `<state>/token` |
| `--token-file` | `<state>/token` | 覆盖 token 路径；权限强制 `0600` |
| `--allow-origin` | 无 | 精确追加允许的 Origin，可重复；`*` 直接拒绝 |
| `--provider` | `mock` | `mock` / `openai-compatible` |
| `--probe-index` | 无 | 只读加载已导出索引；缺省从 manifest 现场构建 |
| `--data-dir` | `<repo>/web/data` | 只读任务结果数据目录（`scenario.json` + `runs.json`），服务不写入 |

退出码：`probe_index_invalid` → 2；`token_file_error` / `invalid_host` / `invalid_origin` /
`listen_failed` → 2；`provider_not_configured`（缺 `MOLT_CHAT_MODEL`）→ 1。失败一律**不监听**。

构建器文档另含 `excluded`（本批未投影的候选与理由），HTTP 端点不返回它。
`--probe-index` 加载时会重算 `revision`（去掉 revision 后规范化 JSON 的 sha256），
与文件内容不符即 `probe_index_invalid`，不会启动半残服务。

## 端点

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/health` | 免 | `provider/model/real_model/adapter_ready/provider_error`，无 base URL / key |
| GET | `/api/openapi.json` | 免 | 本契约 |
| GET | `/api/experiments` | Bearer | `{schema_version, generated_at, revision, experiments}` |
| GET | `/api/experiments/{id}` | Bearer | 单条实验投影；未知 id → 404 |
| GET | `/api/tasks` | Bearer | 搜索集逐题 `{id, cost_first, passed_first, cost_second, passed_second}`，顺序同 scenario |
| POST | `/api/sessions` | Bearer | 只接受 `{}`；201 返回 session |
| GET | `/api/sessions` | Bearer | `{"sessions":[SessionSummary]}`；摘要 6 键，不含 messages |
| GET | `/api/sessions/{id}` | Bearer | session 详情（含 messages / last_event_id） |
| DELETE | `/api/sessions/{id}` | Bearer | 空闲 → 204；turn 中 → 409 `details.reason="turn_in_progress"` |
| POST | `/api/sessions/{id}/messages` | Bearer | 202 `{turn_id, event_cursor}`；首轮 `event_cursor=null` |
| GET | `/api/sessions/{id}/events?since=` | Bearer | SSE；replay + live tail |

没有 PATCH / PUT；`HEAD` 在已知路径上返回 405；带尾斜杠的路径返回 404。
路由优先级：未知 path 404（含未知 path 的 OPTIONS）→ 方法不匹配 405 + `Allow` →
`Host` 非法 400 → `Origin` 不允许 403 → 缺/错 Bearer 401 → 合法 token 后未知 id 404。

## 实验投影形状

单条 experiment 的字段是冻结契约（`contracts/schemas/experiment.schema.json`）：

- **版本扁平**：`scenario_version` / `shell_version` / `verifier_hash` 直接挂在 experiment 上，
  不再有 `versions` 包装。
- **指标嵌套**：`metrics: {recorded_runs, passed, total, pass_rate, score_scope, cost, wall_ms}`；
  `score_scope ∈ {run, task, cell}`。
- **任务范围**：`task_scope: {count, reps, cells}`。
- **证据**：`evidence: {files, rows_count, verdict_runs_count, completeness}`；
  `completeness` 固定四键 `rows / verdict / reflection / traces`，值 ∈
  `present / absent / partial / not_applicable`。
- `provenance`、`comparison`、`comparability_key`（本批全 `null`）、`caveats`（16 项闭集）不变。

端点不返回 `excluded`；`excluded` 只出现在构建器文档与
`contracts/examples/experiment-index.json`。

## 任务结果形状

`GET /api/tasks` 只读 `<data-dir>/scenario.json` 与 `<data-dir>/runs.json`（默认
`<repo>/web/data`，只读不写）。返回 `{"results":[TaskResult...]}`，顺序与
`scenario.json` 的 `task_sets.search` 完全一致：

- `first`：优先 `demo_group="scan10"`；缺失时用 `demo_group="closure"` 且
  `provenance.kind="replay"`。
- `second`：优先 `demo_group="candidate-scan"`；缺失时用 `demo_group="closure"` 且
  `provenance.live=true`。
- 同组多条按 `run_id` 字典序取第一条。
- `cost_*` = run 的 `cost_usd`，`passed_*` = run 的 `solved`；无 run 时 `cost=0.0`、
  `passed=false`。

响应只含 `id` 与四项指标，不含 prompt / stdout / 逐题明细。数据文件缺失或 JSON 非法 →
503 `data_unavailable`。字段级契约见 `contracts/schemas/task-results.schema.json`。

## 鉴权

- token 由服务启动时 `secrets.token_urlsafe(32)` 生成，写入 `--token-file`（默认 `<state>/token`），
  权限 `0600`；创建或 chmod 失败 → stderr + 非零退出（fail closed）。
- 请求头 `Authorization: Bearer <token>`；比较用 `hmac.compare_digest`。
- **没有**环境变量 token、**没有**跳过鉴权的开关、**没有** `?token=` 查询参数。
- `GET /api/health`、`GET /api/openapi.json` 与已知 path 的 `OPTIONS` 免鉴权。

## Host 与 CORS

- `Host` 只接受 `127.0.0.1:<实际端口>` 与 `localhost:<实际端口>`，其余 400。
- 无 `Origin` 的请求允许（curl / 本机脚本）。
- 有 `Origin` 时只允许：API 自身同源（`http://127.0.0.1:<port>` / `http://localhost:<port>`）
  与 `--allow-origin` 的**精确**追加项；`Origin: null` 默认 403，只有显式
  `--allow-origin null` 才放行；`*` 永不接受。
- 预检：已知 path 的 `OPTIONS` 返回 204，回显
  `Access-Control-Allow-Origin`（精确值）、
  `Access-Control-Allow-Methods: GET, POST, DELETE, OPTIONS`、
  `Access-Control-Allow-Headers: Authorization, Content-Type, Last-Event-ID`、`Vary: Origin`。
- 实际响应在 Origin 允许时同样回显 `Access-Control-Allow-Origin` + `Vary: Origin`。

## 错误码

错误体固定 `{"error":{"code","message","details"?}}`；`code` 的唯一源是
`molt/agent/errors.py::ErrorCode`。

| HTTP | code | 场景 |
| --- | --- | --- |
| 400 | `bad_request` | 请求体非法 / 未知字段 / content 长度不合法 / since 非法 / Host 非法 |
| 401 | `unauthorized` | 缺/错 Bearer |
| 403 | `forbidden_origin` | Origin 不允许 |
| 404 | `not_found` | 未知 path、未知 experiment id、未知 session id |
| 405 | `method_not_allowed` | 方法不匹配（带 `Allow`） |
| 409 | `conflict` | turn 进行中（`details.reason="turn_in_progress"`） |
| 410 | `stale_event_id` | `since` 早于最旧保留（`details.reset_required=true`） |
| 413 | `payload_too_large` | 请求体 >64 KiB 或历史 ≥200 条 |
| 415 | `unsupported_media_type` | 有请求体但 `Content-Type` 不是 `application/json` |
| 500 | `internal` | 未预期内部错误（不泄漏细节） |
| 503 | `adapter_unavailable` | provider 不可用（保留码；本 MVP 启动即校验） |
| 503 | `data_unavailable` | `/api/tasks` 的数据文件缺失或 JSON 非法 |

以下**只作 SSE `error` 事件的 code，永不作为 HTTP 响应**（HTTP 不会出现 502/504）：

`upstream_timeout`、`upstream_unavailable`、`upstream_auth_failed`、`upstream_rate_limited`、
`upstream_error`、`tool_denied`、`too_many_tool_calls`、`tool_budget_exceeded`、
`skill_integrity_mismatch`、`mcp_unavailable`。后三者仅在 runtime 模式下出现，详见
[`skill-mcp-runtime.md`](skill-mcp-runtime.md)。

## SSE 事件

`GET /api/sessions/{id}/events?since=`，每 GET 一条连接，replay 后进入 live tail。
事件 id 每 session 从 1 递增；`since` 缺省 / `0` / `null` 从最旧保留开始且不参与 410；
正数排他；负数/非整数 → 400；早于最旧保留 → 410。支持 `Last-Event-ID` 头（query `since` 优先）。
每 15s 无事件发送 `: ping`；`turn.completed` → `done` 后关闭；`error` 后关闭；
已结束 turn 的 replay 发完即关；多订阅者各自独立。

| event | data |
| --- | --- |
| `turn.started` | `turn_id`, `session_id` |
| `status` | `turn_id`, `state ∈ {thinking, tool, responding}` |
| `tool.call` | `turn_id`, `call_id`, `tool`, `arguments` |
| `tool.result` | `turn_id`, `call_id`, `tool`, `status ∈ {ok, error, denied}`, `result?` / `reason?` |
| `message.delta` | `turn_id`, `message_id`, `text`（40 字符窗口） |
| `message.completed` | `turn_id`, `message_id`, `role`, `content` |
| `turn.completed` | `turn_id`, `session_id`, `turn_count`（成功 turn 数，含本轮） |
| `error` | `turn_id`, `code`, `message`, `retryable` |
| `done` | `{}` |

除 `done` 外，`data` 必含 `turn_id`。

## session 语义

- `POST /api/sessions` 只接受 `{}`；未知字段（含 `provider`）→ 400。session 的 provider
  由服务启动参数决定。
- `GET /api/sessions` 只返回 `SessionSummary`（`session_id` / `status` / `provider` /
  `created_at` / `last_event_id` / `message_count`），**不含 messages**；完整 session（含
  messages）只在 `GET /api/sessions/{id}` 返回。
- `POST /messages` 只接受 `{"content": string}`：1–16384 UTF-8 字节；未知字段（含 `role` /
  `provider`）→ 400；请求体 >64 KiB → 413；历史 ≥200 条 → 413；turn 中 → 409。
- 受理顺序固定：先捕获 `last_event_id`（作为 `event_cursor`）→ 返回 202 → 后台线程发
  `turn.started`。因此 `?since=<event_cursor>` 的第一个事件必定属于该 turn。
- 历史只保留**成功** turn 的 user/assistant 对；失败 turn（error 事件）不落库。
- `DELETE`：`in_progress` → 409；`idle` → 204 并关闭该 session 的 SSE；删除后写事件会先检查
  session 是否存在并静默停止。

## Provider

默认 `--provider mock`：确定性、零网络、零成本，便于 CI 与联调；「实验 / experiment / probe」
关键词会触发一次 `experiments_get`，覆盖工具事件路径。mock **完全忽略** `MOLT_CHAT_*`。

启用真实后端：

```bash
export MOLT_CHAT_MODEL=<model-id>          # openai-compatible 必填，缺 → provider_not_configured，exit 1
export MOLT_CHAT_BASE_URL=http://127.0.0.1:8080/v1   # 可选，默认就是这个
export MOLT_CHAT_API_KEY=<key>             # 可选；缺省就不发 Authorization
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt serve --provider openai-compatible
```

- key 只读 `MOLT_CHAT_API_KEY`，**不回退** `DEEPSEEK_API_KEY` / `MOLT_PROPOSER_API_KEY`；
  key 不进响应、日志、错误体、health。
- 请求体：`messages = system + 成功历史对 + 本轮 user`、`tools` 为两个工具声明、
  `tool_choice="auto"`、`stream=false`；不传 `session_id`。服务端把最终文本切成 delta 事件。
- 上游错误在 202 之后只走 SSE `error`：超时 → `upstream_timeout`；连接失败 →
  `upstream_unavailable`；401/403 → `upstream_auth_failed`；429 → `upstream_rate_limited`；
  5xx / 坏 JSON → `upstream_error`；`retryable` 表示是否值得重试。

## 单步工具 turn 边界

本 MVP **不做通用 agent loop**。每个 turn 最多一次工具调用 + 一次工具后回复：

1. 首调必须 `tool_choice="auto"`；`content + tool_calls` 同时出现时工具优先，丢弃 content。
2. 首调返回 >1 个 tool_calls → 一个都不执行，SSE `error code=too_many_tool_calls`。
3. 工具只读 probe index/detail；`experiments_get` 未知 id → `tool.result{status:"error",
   reason:"not_found"}`，**仍然**进入第二次上游调用；内部异常 → `reason:"internal"`，同样二调。
4. 二调固定 `tool_choice="none"`，消息为 2n 条成功历史 + assistant tool_calls + tool result
   （相对首调多 2 条）；二调仍返回 tool_calls → 同上 `too_many_tool_calls`。

## fixture → canonical 映射

`web/agent.sample.json` 只是示意样例。逐字段对照（含 ID 改号、`passed_runs:20` 不沿用、
2c main coverage 16/16 → 20/20、verdict 不进 API 等）见
`contracts/examples/agent-sample-mapping.md`。索引的构建与哈希校验：

```bash
PYTHONDONTWRITEBYTECODE=1 python probes/build_probe_experiments.py --out /tmp/probe/index.json
```

`--out` 必须显式且位于仓库外；源文件 bytes/sha256 不符、JSON 非法或 verdict 交叉核对失败
都会非零退出且不写文件。`generated_at` 来自 manifest（不读时钟），两次构建字节一致。

## 相关：聊天执行工作台

`/api/lab/**` 是独立的执行工作台契约，见
[`docs/api/chat-lab-api.md`](chat-lab-api.md) 与 `contracts/openapi-lab-v1.json`。
它不改变本文件描述的 11 条 agent API 路由；执行端点要求隔离后端可用，否则 fail closed。
