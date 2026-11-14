# Skill / MCP runtime API（C 切片）

本地 agent API 的 Skill/MCP 面：只读的绑定与选择投影、`context_loaded` 捕获证据、
手工 MCP 工具调用，以及按 session 切换的 runtime 模式。契约源是
`contracts/openapi-skill-mcp-v1.json`；旧 agent / lab / upper 契约不受影响。

> 本文件只描述接口与证据口径。**不声称**技能提升任务成功率：`context-check`
> 只证明受审查的技能正文进入了交给 adapter 的 messages（`calls_model=false`），
> 不产生 `improved` / `gain` / score 字段。

## 1. 启动

```bash
# 0) 一次性绑定（写 /tmp，仓库树不变）——skills 与 mcp 两个 section 都必须绑定
rm -rf /tmp/molt-serve-state
PYTHONDONTWRITEBYTECODE=1 python3 -B - <<'PY'
from pathlib import Path
from molt.mcp.allowlist import McpAllowlist
from molt.mcp.profile import build_profile_asset
from molt.skills import BindingStore, load_package

root = Path.cwd()
state = Path("/tmp/molt-serve-state")
store = BindingStore(state, repo_root=root)
pkg = load_package(root / "skills", "repo-evidence")
revision = store.commit(
    "skills",
    pkg.skill_id,
    {
        "skill_id": pkg.skill_id,
        "version": pkg.version,
        "asset_id": f"skill-{pkg.skill_id}-{pkg.version}",
        "package_hash": pkg.content_hash,
    },
    expected_revision=0,
)["revision"]
allowlist = McpAllowlist.load(root / "molt/mcp/allowlist.default.json", repo_root=root)
for server_id in allowlist.server_ids():
    spec = allowlist.get(server_id)
    asset = build_profile_asset(spec, repo_root=root)
    revision = store.commit(
        "mcp",
        server_id,
        {
            "server_id": server_id,
            "version": asset.version,
            "asset_id": asset.asset_id,
            "package_hash": asset.content["artifact_hash"],
        },
        expected_revision=revision,
    )["revision"]
print(f"binding revision: {revision}")
PY

# 1) 启动（state 全部在仓库外；mock provider，零成本）
uv run python -B -m molt serve \
  --state /tmp/molt-serve-state \
  --upper-state /tmp/molt-serve-upper \
  --skills-root skills \
  --skill-state /tmp/molt-serve-state \
  --mcp-allowlist molt/mcp/allowlist.default.json
```

- 启动输出一行 JSON：`{"listen": "...", "token_file": "/tmp/molt-serve-state/token"}`。
  所有新路由（除 `GET /api/skill-mcp/openapi.json`）都需要
  `Authorization: Bearer <token file 内容>`。
- **默认关闭**：不传 `--skills-root` / `--skill-state` / `--mcp-allowlist` 时
  runtime `enabled=false`，旧 turn 路径、旧路由、旧事件顺序完全不变。
- 三个 flag 任意显式给出即启用 runtime（`enabled=true`）；MCP 仍需要
  `--mcp-allowlist` 才可用。
- `--skill-state` 必须在仓库外（`BindingStore` 拒绝仓库内路径）；建议与
  `--state` 同根（默认就是 `--state`）。binding 实际路径是
  `<skill-state>/skills/current.json`。
- MCP 注册表读自 `--upper-state`（`<upper-state>/registry.json`）。选择规则是
  **binding 的 `asset_id` + `version` 精确匹配**，不是「同 server 第一条」：
  - registry 没有该 server 的任何 `mcp_profile` 记录 → 允许
    `manual_operator_config` bootstrap（`approved=false`）；
  - 有该 server 的记录但没有匹配的 asset_id+version → `mcp_asset_not_found`，
    **不回退** operator 配置；
  - 精确匹配多于一条 → `mcp_registry_ambiguous`；
  - binding 缺 `asset_id`/`version`/`package_hash` → `mcp_binding_incomplete`。
  - 匹配到的记录仍需 active + strict + implemented + 无 pending，并重新校验
    `verify_artifact_hash`；draft 记录会按 `mcp_profile_invalid` 失败，不会误用。
- 没有对应记录时，C 侧把 profile 标为 **manual_operator_config**
  （`approved=false`、`approved_by="operator-config"`、
  `approval_note="manual operator configuration; not model-evaluated"`）；
  存在记录但未 active/strict/implemented 或 hash 漂移时一律失败，不回退。

### flags

| flag | 默认 | 说明 |
| --- | --- | --- |
| `--skills-root` | `<repo>/skills` | 只读技能包根目录；显式给出即启用 runtime |
| `--skill-state` | `<state>` | BindingStore state；binding 目录是 `<state>/skills`；必须在仓库外；显式给出即启用 runtime |
| `--mcp-allowlist` | 关闭 | allowlist JSON；给出路径才启用 MCP |

### state 目录

```
<state>/skills/current.json            # binding 快照（revision + skills/mcp）
<state>/skills/history/                # 内容寻址历史（immutable）
<state>/skills/.lock                   # 单写者锁
```

C 切片只读 binding；写入仍由 `molt.skills.binding.BindingStore.commit` 的
CAS 单写者路径负责。后续审批/评测切片可以在同一 store 上扩展，不需要改本 API
的读投影。

## 2. 路由

| 方法 | 路径 | 鉴权 | calls_model | 说明 |
| --- | --- | --- | --- | --- |
| GET | `/api/runtime/status` | Bearer | 否 | runtime 开关、skills/mcp 可用性、provider kind、binding revision |
| GET | `/api/skills/catalog` | Bearer | 否 | 已绑定技能的安全元数据投影（无正文/路径） |
| GET | `/api/skills/effective` | Bearer | 否 | 当前 binding（revision + skills + mcp） |
| POST | `/api/skills/select` | Bearer | 否 | 确定性选择预览，不加载正文到模型 |
| POST | `/api/skills/context-check` | Bearer | **否** | `context_loaded` 捕获证据 |
| GET | `/api/mcp/tools` | Bearer | 否 | allowlist 批准的工具名 + inputSchema |
| POST | `/api/mcp/call` | Bearer | **否** | 手工真实 MCP 调用（manual） |
| POST | `/api/sessions/{id}/runtime` | Bearer | 否 | 设置该 session 的 `off` / `active` |
| GET | `/api/skill-mcp/openapi.json` | **公开** | 否 | 本契约 |

旧 `/api/sessions/**`、`/api/lab/**`、`/api/upper/**` 行为不变；
`POST /api/sessions/{id}/messages` 的请求体仍只接受 `content`。

## 3. 请求 / 响应

### `GET /api/runtime/status`

```json
{
  "enabled": true,
  "skills": {"available": true, "reason": "ok"},
  "mcp": {"available": true, "reason": "ok", "approval_source": "manual_operator_config", "approved": false},
  "provider": {"kind": "mock"},
  "binding": {
    "revision": 2,
    "skills": {
      "repo-evidence": {
        "skill_id": "repo-evidence",
        "version": "1.0.0",
        "asset_id": "skill-repo-evidence-1.0.0",
        "package_hash": "sha256:…"
      }
    },
    "mcp": {
      "project_kb": {
        "server_id": "project_kb",
        "version": "1.0.0",
        "asset_id": "mcp-profile-project_kb",
        "package_hash": "sha256:…"
      }
    }
  }
}
```

- `provider.kind ∈ {mock, real}`；不含 model 名、endpoint 或 key。
- `skills.available`：binding 中至少一个技能包可加载且 hash 与 binding 一致。
  无绑定时 `false` + `reason="no_binding"`；hash 漂移时 `false` +
  `reason="skill_integrity_mismatch"`。
- `mcp.available`：allowlist 已加载、每个 server 都有 `mcp` binding、profile
  通过 `verify_artifact_hash` + `assert_profile_executable`，且子进程能完成
  initialize/tools-list。失败时 `false` + 稳定 `reason`：
  `not_configured`、`mcp_binding_missing`、`mcp_binding_incomplete`、
  `mcp_binding_mismatch`、`mcp_asset_not_found`、`mcp_registry_ambiguous`、
  `mcp_integrity_mismatch`、`mcp_profile_invalid`、`mcp_registry_invalid`、
  `mcp_unavailable`、`mcp_timeout`。
- **没有注册表记录**不会让 `mcp.available` 变成 true 的“已批准”：profile 走
  manual_operator_config，`/api/mcp/tools` 与 `/api/runtime/status` 都明确标
  `approval_source="manual_operator_config"`、`approved=false`。运行时
  `mcp.available` 仍可为 true（operator 显式配置可执行），但不得对外称“已批准”。
- `binding.error` 只在 binding 文件损坏/仓库内路径时出现，值固定
  `"binding_invalid"`；此时 `revision=0`、两个 section 为空。
- 响应里不得出现文件系统位置、环境值、server 命令或模型 endpoint。

### `GET /api/skills/catalog`

```json
{
  "revision": 2,
  "skills": [
    {
      "skill_id": "repo-evidence",
      "version": "1.0.0",
      "asset_id": "skill-repo-evidence-1.0.0",
      "content_hash": "sha256:…",
      "bound": true,
      "available": true,
      "family": "molt-repository",
      "title": "仓库证据与零写入验收",
      "description": "…",
      "triggers": {"keywords": ["零写入"], "task_families": ["molt-repository"]},
      "permissions": {
        "tools": ["experiments_list", "mcp:project_kb:search_docs"],
        "network": false,
        "filesystem": "none"
      },
      "source": {"count": 3, "available_content": ["repository-policy"], "revisions": ["c23d8d6"]}
    }
  ]
}
```

坏包不隐藏：条目保留 `available=false` + `reason ∈ {skill_integrity_mismatch,
skill_unavailable}`，不返回正文或源文件 locator。

### `GET /api/skills/effective`

```json
{
  "revision": 2,
  "skills": {
    "repo-evidence": {
      "skill_id": "repo-evidence",
      "version": "1.0.0",
      "asset_id": "skill-repo-evidence-1.0.0",
      "package_hash": "sha256:…"
    }
  },
  "mcp": {
    "project_kb": {
      "server_id": "project_kb",
      "version": "1.0.0",
      "asset_id": "mcp-profile-project_kb",
      "package_hash": "sha256:…"
    }
  }
}
```

`revision` 是未来写操作的 CAS 基值；C 切片不提供写路由。

### `POST /api/skills/select`

请求：`{"task_text": "如何验证这次改动零写入？"}`

```json
{
  "binding_revision": 2,
  "selected": true,
  "selection": {
    "skill_id": "repo-evidence",
    "version": "1.0.0",
    "content_hash": "sha256:…",
    "reason": "matched keywords: 零写入",
    "matched_terms": ["零写入"],
    "mode": "deterministic-exact-prefix"
  },
  "mode": "deterministic-exact-prefix"
}
```

无命中：`selected=false`、`selection=null`（HTTP 200）。未知字段 → 400
`bad_request` + `details.unknown_fields`；缺 `task_text` → 400 +
`details.missing_fields`。

### `POST /api/skills/context-check`

请求同上。命中时：

```json
{
  "selected": true,
  "calls_model": false,
  "mode": "manual",
  "task_text": "如何验证这次改动零写入？",
  "reason": "matched keywords: 零写入",
  "evidence_kind": "context_loaded",
  "skill_id": "repo-evidence",
  "version": "1.0.0",
  "content_hash": "sha256:…",
  "system_block_sha256": "sha256:…",
  "captured_message_count": 3,
  "contains_body": true,
  "matched_terms": ["零写入"],
  "binding_revision": 2
}
```

- 使用 `CaptureAdapter` 记录 `complete()` 的 `messages`/`tools`/`tool_choice`；
  正文与 content hash 必须同时出现在捕获的 messages 中，否则 500。
- `calls_model` 恒为 `false`，`mode` 恒为 `manual`；没有 `improved`。
- 无绑定时 200 + `selected=false` + `reason="no_binding"`。

### `GET /api/mcp/tools`

```json
{
  "servers": [
    {
      "server_id": "project_kb",
      "available": true,
      "reason": null,
      "binding_revision": 2,
      "approval_source": "manual_operator_config",
      "approved": false,
      "approved_by": "operator-config",
      "approval_note": "manual operator configuration; not model-evaluated",
      "tools": [
        {
          "name": "search_docs",
          "namespaced_name": "mcp__project_kb__search_docs",
          "description": "在 project_kb 语料中做大小写不敏感的关键词/子串检索",
          "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}
        }
      ]
    }
  ],
  "calls_model": false
}
```

sandbox 无法启动、profile 校验失败或缺 binding 时 `available=false`、
`reason` 为稳定码、`input_schema=null`；不伪造 schema。注册表记录存在时
`approval_source="registry"`、`approved=true`、`approved_by` 取注册表值；
没有记录时固定 `approval_source="manual_operator_config"`、`approved=false`。

### `POST /api/mcp/call`

请求：`{"server_id": "project_kb", "tool": "get_doc", "arguments": {"doc_id": "zero-write"}}`

```json
{
  "server_id": "project_kb",
  "tool": "get_doc",
  "status": "ok",
  "result": {"content": [{"type": "text", "text": "…"}]},
  "calls_model": false
}
```

- manual 路径只受 allowlist 约束，不需要 skill 绑定、不启动模型。
- **仍必须通过 MCP profile 闸门**：`binding.mcp.<server_id>` 必须存在且与
  注册表记录（若有）的 `asset_id`/`version`/`artifact_hash` 一致；profile 需
  active + strict + implemented + 无 pending permission，并重新校验代码/语料 hash。
- 未批准的 server/tool → 400 `bad_request` + `details.reason ∈
  {server_not_allowed, tool_not_allowed}`。
- 缺 binding / binding 不完整 / binding 不一致 / asset 不在注册表 / profile 未 active / hash 漂移 → 409 `conflict`，
  `details.reason ∈ {mcp_binding_missing, mcp_binding_incomplete,
  mcp_binding_mismatch, mcp_asset_not_found, mcp_registry_ambiguous,
  mcp_integrity_mismatch, mcp_profile_invalid, mcp_registry_invalid}`。
- bwrap 缺失 → 503 `adapter_unavailable` + `details.reason="mcp_unavailable"`；
  超时 → 503 + `details.reason="mcp_timeout"`；输出超限 → 413 +
  `details.reason="tool_budget_exceeded"`。
- 不会静默回退到非沙箱模式。超时/预算/协议错误后该 client 立即从缓存移除并
  close；下一次调用新建 client，不复用死进程。

### `POST /api/sessions/{id}/runtime`

请求：`{"mode": "active"}`（或 `"off"`）

```json
{"session_id": "sess-…", "mode": "active", "runtime_enabled": true}
```

- 默认 `off`：旧 turn 路径（单 tool + 二调 `tool_choice="none"`）。
- `active` 只在 runtime 启用时可设；否则 409 `conflict` +
  `details.reason="runtime_disabled"`。
- `begin_turn` 只读一次 binding，返回不可变 `TurnSnapshot`
  `{revision, skill, mcp_profiles, allowed_tools}`；本 turn 后续工具暴露与执行
  只用该快照。turn 进行中改 mode 或换 binding 都不会影响当前 turn，下一次
  turn 才生效。
- 未知字段、未知 mode → 400；未知 session → 404。

## 4. SSE 事件（runtime 模式）

顺序（命中技能且发生工具调用时）：

```
turn.started
status {state:"thinking"}
skill.loaded                       # 仅命中时；无命中不发
runtime.warning                    # 仅 allowlist 完全未配置时
status {state:"tool"}              # 仅本 turn 首次工具调用前
tool.call / tool.result            # 逐个；最多 3 轮、总计 5 次
status {state:"responding"}
message.delta × N                  # 40 字符窗口
message.completed
turn.completed
done
```

失败（预算/完整性/MCP 不可用）：

```
... error {code:"tool_budget_exceeded" | "skill_integrity_mismatch" | "too_many_tool_calls" | "mcp_unavailable", message, retryable:false}
```

MCP 已配置但 profile 校验/启动失败时，`mode:"active"` 的 turn **必须**以
`error code=mcp_unavailable` 结束，不允许当无事发生继续普通回答。只有
allowlist **完全未配置**时，`mode:"active"` 才发 `runtime.warning`
（`code="mcp_not_configured"`）并只加载技能上下文，不暴露 MCP 工具；
`mode:"off"` 不要求 MCP。

### `skill.loaded`

```json
{
  "turn_id": "turn-…",
  "skill_id": "repo-evidence",
  "version": "1.0.0",
  "content_hash": "sha256:…",
  "reason": "matched keywords: 零写入",
  "binding_revision": 2,
  "mode": "active"
}
```

### `tool.call` / `tool.result`

MCP 工具：

```json
{
  "turn_id": "turn-…",
  "call_id": "call-…",
  "source": "mcp",
  "server": "project_kb",
  "tool": "search_docs",
  "name": "mcp__project_kb__search_docs",
  "arguments": {"query": "零写入"},
  "binding_revision": 2
}
```

`tool.result` 额外有 `status ∈ {ok, error, denied}`；`ok` 带 `result`，
否则带 `reason`（如 `mcp_timeout`、`mcp_unavailable`、`tool_not_allowed`）。
两种事件都带本 turn 快照的 `binding_revision`。
旧实验工具（`experiments_list` / `experiments_get`）在 `mode=off` 下保持原字段
`{turn_id, call_id, tool, arguments}` / `{turn_id, call_id, tool, status, result|reason}`；
在 runtime 模式下额外带 `binding_revision`，但没有 `source` / `server` / `name`。

### 预算

| 维度 | 上限 | 超限行为 |
| --- | --- | --- |
| 轮数 | 3 | `error code=tool_budget_exceeded` |
| 工具调用总数 | 5 | 同上 |
| 单轮 tool_calls | 3 | `error code=too_many_tool_calls` |
| 墙钟 | 60s | `tool_budget_exceeded` |
| 累计工具输出 | 64 KiB | `tool_budget_exceeded` |

system prompt 只由 `SYSTEM_PROMPT` + `render_system_block(pkg)` 组成；
MCP 结果只回填成 `role:"tool"` 消息，不拼进 system prompt。

## 5. 错误码

HTTP 错误形状沿用 `{error:{code,message,details?}}`。

| HTTP | code | 场景 |
| --- | --- | --- |
| 400 | `bad_request` | 未知字段、缺字段、非法 mode/参数 |
| 401 | `unauthorized` | 缺/错 Bearer |
| 403 | `forbidden_origin` | Origin 不允许 |
| 404 | `not_found` | 未知 session |
| 409 | `conflict` | `skill_integrity_mismatch`、`runtime_disabled`、`mcp_binding_missing`、`mcp_binding_incomplete`、`mcp_binding_mismatch`、`mcp_asset_not_found`、`mcp_registry_ambiguous`、`mcp_integrity_mismatch`、`mcp_profile_invalid`、`mcp_registry_invalid` |
| 413 | `payload_too_large` | body >64 KiB、工具输出 >64 KiB |
| 415 | `unsupported_media_type` | 非 JSON body |
| 503 | `adapter_unavailable` | runtime/allowlist/sandbox 不可用、MCP 超时 |
| SSE only | `tool_budget_exceeded` | runtime turn 预算耗尽 |
| SSE only | `skill_integrity_mismatch` | binding hash 与包不一致 |
| SSE only | `too_many_tool_calls` | 单轮 >3 个 tool_calls |
| SSE only | `mcp_unavailable` | active turn 但 MCP profile/启动不可用 |

SSE-only 码不会出现在 HTTP 响应里；`errors.py` 的 `SSE_ONLY` 与
`contracts/schemas/error.schema.json` 的 enum 必须保持一致。

## 6. 证据口径（三栏，不得混写）

| 栏位 | 来源 | 能说什么 | 不能说什么 |
| --- | --- | --- | --- |
| `context_loaded` | `POST /api/skills/context-check`（CaptureAdapter） | 技能正文与 hash 出现在捕获的 messages；`calls_model=false` | 不能说模型已使用技能、不能说任务提升 |
| `mcp_rpc` | `POST /api/mcp/call`、`GET /api/mcp/tools`；`scripts/demo_mcp.py` 的 `/tmp/molt-mcp-transcript.jsonl` | 真实子进程 initialize / tools/list / tools/call；bwrap 只读 workspace | 不能说模型调用了工具；`calls_model=false` |
| `provider_task` | `--provider openai-compatible` 的 chat turn | 只有真实 provider 才能标 `live` | provider 环境缺失/为空时一律 **未验证**；mock 不得标 live |

- `context_loaded` 是 **manual/capture** 证据，不是 live run。
- `mcp_rpc` 是真实子进程证据，但不是模型任务证据。
- 当前 C 切片的 `provider.env` absent/empty：`provider_task` 一律 **未验证**，
  不得写成 live；mock provider 的回复只能标 mock。
- 当前 C 切片不实现 replay/evaluate；任何「提升」结论都不在本切片范围内。
- 服务端不主动写 MCP transcript（避免仓库/state 泄漏）；需要 transcript 时用
  `scripts/demo_mcp.py`，它只写 `/tmp/molt-mcp-transcript.jsonl`。

## 7. 后续切片接口预留

- `/api/skills/effective` 返回的 `revision` 是 CAS 基值；未来审批/评测/回退
  写路由应复用 `BindingStore.commit(section, key, entry, expected_revision=...)`，
  不新增第二套存储。A 的 mcp entry 只允许
  `server_id/version/asset_id/package_hash` 四个字段（`_validate_entry` 拒绝未知
  字段），per-entry revision 不存在；**revision 是 binding 快照级值**，与
  entry 在同一快照原子提交。
- MCP 审批记录走 `--upper-state` 的 `registry.json`：C 只读并核对
  `asset_id/version/artifact_hash` + active/strict/implemented/无 pending；
  没有记录时明确标 `manual_operator_config`，不冒充 evaluated/approved。
- `/api/runtime/status` 的 `enabled` 与 session `mode` 分离，便于后续按
  route 或按实验切换，而不改变旧 message 契约。
- 本切片不提供 evaluate / adopt / rollback 路由；`molt/upper` 的
  `skill` / `mcp_profile` 资产生命周期保持不变。
