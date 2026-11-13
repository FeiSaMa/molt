# 聊天执行工作台 API（lab v1）

> 契约事实源：`contracts/openapi-lab-v1.json`。本文件解释语义；与契约冲突时以契约为准。
>
> 这是**独立于** `contracts/openapi-v1.json` 的扩展 API。现有 11 条 agent API 路由
> 语义不变；lab 路由只新增在 `/api/lab/**`。

## 定位

lab 是一个**两轮 coding fixture 工作台**，不是引擎的 genome evolution：

```
seed fixture → round1 tests → patch（provider 或 mock）→ real diff → round2 tests
```

- `provenance.kind=lab_fixture`，`historical=false`；与 `web/data`、demo-6、
  probe 历史数据完全分离。
- mock provider 的 patch 标记 `source=mock-deterministic` / `real_model=false`，
  页面必须显示“MOCK 演示”。
- 真实 provider 只在 `MOLT_CHAT_MODEL` 等配置存在时使用；provider 失败不降级 mock。

## 鉴权与路由

除 `GET /api/lab/openapi.json` 外全部要求 `Authorization: Bearer <token>`。
`Host` / `Origin` 规则与 agent API 相同（精确 Host、精确 Origin）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/lab/openapi.json` | 本契约（免鉴权） |
| GET | `/api/lab/capabilities` | provider / isolation / evolution / 限额 |
| GET | `/api/lab/tasks` | 可用 fixture |
| POST | `/api/lab/runs` | `{"task_id":"calc-v1"}`；202 |
| GET | `/api/lab/runs/{run_id}` | 状态、结果、错误 |
| GET | `/api/lab/runs/{run_id}/events?since=` | SSE；replay + live tail |
| DELETE | `/api/lab/runs/{run_id}` | 取消；204 |

错误体沿用 `{"error":{"code","message","details"?}}`。HTTP 只使用既有错误码：

- 401 `unauthorized`
- 404 `not_found`
- 409 `conflict`，`details.reason=lab_busy`
- 503 `adapter_unavailable`，`details.reason=isolation_unavailable` 或 provider 失败

## 隔离与预算（硬门槛）

- 执行只通过 `bwrap`；启动自检检查 workspace 可写、`/srv/molt` 不可读、无网络、
  `/usr` 不可写。
- 自检失败：`capabilities.execution.available=false`，`POST /api/lab/runs` 直接 503，
  不创建 workspace、不执行任何代码。
- 固定命令 `python3 -m unittest -q`；没有任意 shell 参数。
- 文件只允许 workspace 内相对路径；拒绝绝对路径、`..`、符号链接、NUL。
- 限额：并发 1、2 轮、60s 墙钟、单命令 15s、输出 16KB、单文件 64KB、
  总文件 256KB、最多 16 文件。
- 完成后删除 workspace；结果保留在内存。

## 事件

每个事件 `data` 含 `run_id`（`done` 除外）。`done` 后 SSE 关闭。

| event | 关键字段 |
|---|---|
| `run.started` | `task_id`, `provider`, `model`, `real_model`, `provenance`, `isolation` |
| `round1.completed` | `round`, `exit_code`, `passed`, `stdout`, `stderr`, `duration_ms` |
| `evolution.proposed` | `source` (`mock-deterministic`/`provider`), `real_model`, `file` |
| `evolution.diff` | `diff`（真实 unified diff）, `files` |
| `round2.completed` | 同 round1 |
| `run.completed` | `adopted`, `round1`, `round2`, `diff`, `provenance`, `provider`, `isolation`, `usage` |
| `run.failed` | `code`, `message`, `detail?` |
| `run.cancelled` | `code=cancelled`, `message` |
| `done` | `{}` |

## 取消

`DELETE` 设置 cancel event 并 kill 进程组；worker 发 `run.cancelled`，随后 `done`。
取消后 workspace 清理；已完成的 run 再取消返回 204，不改变结果。

## 前端口径

`web/chat/` 左侧执行卡必须显示：

- `provider`：MOCK 或 LIVE（`real_model`）；
- `isolation`：ready / blocked（含 reason）；
- `evolution`：mock / live；
- round1 / round2 的真实通过状态与 stdout 摘要；
- 真实 diff；
- provenance 行：MOCK 演示不得写成真实进化。

隔离不可用时按钮 disabled，并显示阻断原因；不允许用动画或假数据代替。

## 超时、取消与失败语义（2026-11-13 加固）

- 单条测试命令超过 `command_ms` → `run.failed`，`code=command_timeout`，不再继续 patch/round2；
- 整个 run 超过 `wall_ms` → `run.failed`，`code=budget_exceeded`；
- provider 输出超过 64 KiB → `run.failed`，`code=provider_error`，不回退 mock；
- patch 的 `old` 片段不存在或文件未知 → `run.failed`，`code=patch_rejected`；
- 隔离在执行中失效 → 工具/运行失败，不执行任何后续命令（fail closed）；
- `DELETE` 先设置 cancel，再 kill 进程组；worker 发 `run.cancelled` + `done`；
- 客户端 SSE 断流不会解锁执行按钮；前端会用 `GET /api/lab/runs/{id}` 核对，
  只有 `completed/failed/cancelled` 终止态才解锁；无法确认时保持 busy 并提示刷新。
