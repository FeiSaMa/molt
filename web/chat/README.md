# molt Agent 对话页

这是独立于 `web/page/` 路演数据页的真实对话客户端，使用浏览器原生 HTML/CSS/JS，不需要构建工具或 npm 依赖。

## 本地打开

可以通过静态 HTTP 服务访问（推荐；也支持直接打开 `index.html`）：

```bash
python3 -m http.server 8000 --directory web
```

然后打开 `http://127.0.0.1:8000/chat/`，点击“连接设置”，填写 Agent API 根地址和服务端 token。
API 根地址是 origin，例如 `http://127.0.0.1:45123`，不要在末尾加 `/api`。

API 默认 mock provider，连通后可以立即进行本地对话；提到实验时 mock 会调用只读实验工具，适合检查完整交互路径。页面不把 token 写入 localStorage/cookie；连接配置只留在当前页面内存中。

## 服务器联调

当前 `molt serve` 默认只绑定 loopback，并严格校验 `Host` 与 `Origin`。远程浏览器不能直接把 `127.0.0.1` 当作服务器地址。推荐在 Web/API 同一台服务器上由反向代理把 `/api/` 转发给 loopback 上的 molt 服务，并：

1. 将浏览器页面的精确 origin 加到服务 `--allow-origin`，例如 `https://demo.example.org`。
2. 代理到 molt 时把上游 `Host` 设为 `127.0.0.1:<molt监听端口>`（或 `localhost:<端口>`），满足服务 Host 检查。
3. 对外提供 HTTPS；前端 API 地址使用浏览器可访问的同源根地址，避免混合内容和跨域配置。
4. token 通过受控方式交给演示者，勿放进页面源码、静态配置文件或 URL。
5. 确保代理支持长连接/关闭响应缓冲，否则 SSE 会延迟或断流；需转发 Authorization 请求头。

若网页和 API 部署在不同域，除了配置精确 Origin，还要确认代理与浏览器的 CORS 预检、`Authorization` 请求头和 SSE 响应均可通过。上线前用实际域名走一遍“连接 → 发问 → tool.call/tool.result → message.delta → done”的流程。

## 当前 API 能力边界

- 页面可创建和删除内存 session、发送用户消息并显示 SSE 状态、工具调用及回答。
- 对话工具仍只读 probe 实验投影；不会在聊天工具里暴露任意 shell 或服务器文件读取。
- 左侧新增“两轮修复工作台”：通过 `/api/lab/**` 在独立临时 workspace 里跑
  round1 → patch → diff → round2。隔离后端不可用时执行端点 fail closed（按钮 disabled，
  API 返回 503 `isolation_unavailable`）。
- 该工作台是**新的 coding fixture**，与 demo/probe 历史数据分离；它不是引擎的
  genome evolution，也不是“自进化”结论。mock patch 会明确标注 `MOCK 演示`。
- 后端 session / lab run 都保存在进程内，服务重启后消失；本页刷新后也不会恢复旧对话。
- OpenAI-compatible provider 需要后端设置 `MOLT_CHAT_MODEL` 等环境变量；浏览器不接触模型 key。
  服务器当前没有 provider 配置，因此显示 MOCK。
- 页面使用 `fetch` 读取 SSE，而不是 `EventSource`，因为浏览器 `EventSource` 不能设置 Bearer Authorization header。

## 文件

- `index.html`：页面结构与连接配置对话框。
- `chat.css`：对话界面与移动端布局。
- `chat.js`：API、session 和 SSE 客户端；使用 `textContent` 展示服务内容。

## 执行工作台 API

详见 [`docs/api/chat-lab-api.md`](../../docs/api/chat-lab-api.md) 与
`contracts/openapi-lab-v1.json`：

- `GET /api/lab/capabilities`：provider / isolation / evolution 状态
- `GET /api/lab/tasks`：可用 fixture
- `POST /api/lab/runs`：创建两轮 run
- `GET /api/lab/runs/{id}`：状态/结果
- `GET /api/lab/runs/{id}/events`：SSE
- `DELETE /api/lab/runs/{id}`：取消

除 `/api/lab/openapi.json` 外均要求 Bearer。服务器没有 `bwrap` 时执行端点不会上线。
