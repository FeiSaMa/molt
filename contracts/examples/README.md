# contracts/examples

本地 agent API 的可执行样例。字段级事实源是 `contracts/schemas/*.schema.json` 与
`contracts/openapi-v1.json`；本目录只提供形状正确的样例，数字与
`contracts/examples/experiment-index.json` 保持一致。

| 文件 | 内容 | 对应契约 |
| --- | --- | --- |
| `experiment-index.json` | 构建器输出的完整索引（8 条 + `excluded` + revision）；HTTP 端点省略 `excluded` | `experiment-index.schema.json` |
| `experiment-detail.json` | `2c-main` 单条投影 | `experiment.schema.json` |
| `session.json` | 一个已完成 1 轮对话的空闲 session | `session.schema.json` |
| `events.jsonl` | 工具 turn 的完整 SSE 序列 + 一条 `upstream_timeout` error 样例（每行一个 `{id,event,data}` 信封） | `event.schema.json` / `tool-call.schema.json` / `tool-result.schema.json` |
| `events-simple.jsonl` | 不触发工具的 turn（无 tool.* 事件） | `event.schema.json` |
| `message-accepted.json` | `POST /messages` 的 202 响应；首轮 `event_cursor=null` | OpenAPI `createMessage` |
| `error.json` | 普通 404 错误体 | `error.schema.json` |
| `error-stale.json` | 410 `stale_event_id`，带 `details.reset_required=true` | `error.schema.json` |
| `startup.txt` | `molt serve` 的 stdout（一行 JSON）与 stderr（人读两行） | — |

## 读取 SSE（fetch + ReadableStream）

`EventSource` **不能用**：它无法附带 `Authorization` 头，会逼你把 token 放进 query，
而服务端只接受 Bearer 头，`?token=` 一律无效。也不要把 token 写进 URL、日志或 localStorage
之外的地方；token 只从 `<state>/token`（默认 `state/token`）读取。

```js
const base = "http://127.0.0.1:45123";
const token = await readTokenFile("<state>/token"); // 本地进程读文件，不要经 HTTP 取 token

// 1) 开一条事件流；since=0 表示从最旧保留开始
const response = await fetch(`${base}/api/sessions/${sessionId}/events?since=0`, {
  headers: {
    Authorization: `Bearer ${token}`,
    Accept: "text/event-stream",
  },
});
if (!response.ok) throw new Error(`events ${response.status}`);

// 2) 逐帧解析（每个 SSE 帧以空行结束）
const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = "";
for (;;) {
  const { value, done } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  let boundary = buffer.indexOf("\n\n");
  while (boundary >= 0) {
    const frame = buffer.slice(0, boundary);
    buffer = buffer.slice(boundary + 2);
    const lines = frame.split("\n");
    const id = Number(lines.find((line) => line.startsWith("id: "))?.slice(4));
    const event = lines.find((line) => line.startsWith("event: "))?.slice(7);
    const data = JSON.parse(lines.find((line) => line.startsWith("data: "))?.slice(6) ?? "{}");
    if (event === "done" || event === "error") return; // 服务端随后关闭连接
    handle({ id, event, data });
    boundary = buffer.indexOf("\n\n");
  }
}
```

## 提交一条消息

```js
const accepted = await fetch(`${base}/api/sessions/${sessionId}/messages`, {
  method: "POST",
  headers: {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({ content: "看一下 memory v3 的迁移结果" }),
});
const { turn_id, event_cursor } = await accepted.json(); // 202；首轮 event_cursor=null
```

拿到 `event_cursor` 后再开 SSE（`?since=${event_cursor ?? 0}`），第一个事件必定是该 turn 的
`turn.started`。`since` 早于服务端最旧保留事件时返回 410，客户端必须重新从 0 同步。

## 说明

- 索引里的 `revision` 是「去掉 revision 后的规范化 JSON」的 sha256；`molt serve --probe-index`
  加载时会重算，不一致即 `probe_index_invalid`。
- 所有示例数字来自 `probes/probe_experiments_manifest.json` 的冻结投影；不要手改示例来“修”数字。
- `events.jsonl` 的 `done` 帧 `data` 必须是 `{}`；其余帧的 `data` 必须含 `turn_id`。
