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
- `--skill-state` 必须在仓库外；它是 runtime 与演化状态的唯一根：
  binding、candidates、evaluations、`skill-registry`、`skill-admin` 索引全部
  落在它下面。`--state` 只用于旧 agent token / 其它状态；未显式给
  `--skill-state` 时才默认取 `--state`。上线示例：
  `--skill-state $MOLT_UPPER_STATE/runtime`，则 binding 在
  `$MOLT_UPPER_STATE/runtime/skills/current.json`。
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
| `--skill-state` | `<state>` | runtime/演化 state 根：binding、candidates、evaluations、skill-registry 全在其下；必须在仓库外；显式给出即启用 runtime |
| `--mcp-allowlist` | 关闭 | allowlist JSON；给出路径才启用 MCP |

### state 目录

```
<skill-state>/skills/current.json            # binding 快照（revision + skills/mcp）
<skill-state>/skills/history/                # 内容寻址历史（immutable）
<skill-state>/skills/.lock                   # 单写者锁
<skill-state>/skills/packages/<asset_id>/<skill_id>/  # 激活时发布的包
<skill-state>/skills/commits/<revision>.json          # 激活/回退/迁移 commit record
<skill-state>/skills/intents/<intent_id>.json         # CAS 前 prepared intent
<skill-state>/skills/evaluations/            # 两阶段评测 evidence（core runner 写）
<skill-state>/skill-registry/registry.json   # 技能资产 registry（与 MCP registry 分开）
<skill-state>/skills/sidecars/                # 资产 sidecar（authorship/review）
<skill-state>/skill-candidates/<uuid>/       # 候选包（propose 写，未批准）
<skill-state>/skill-admin/evaluations.json   # API 的 evidence→asset 索引
```

**runtime 包解析**：`load_skill` 先用
`<skill-state>/skills/packages/<binding.asset_id>/<skill_id>`，且该 asset 必须
在 `<skill-state>/skill-registry` 中同 asset_id/version/package_hash 且
`status ∈ {approved, active}`；否则 `skill_package_untrusted`。只有 packages
目录不存在时才回退 repo `skills_root/<skill_id>`，且 binding 的 asset_id 必须
是已登记 skill asset 或 `operator_baseline`，hash 不符即
`skill_integrity_mismatch`。不会从 `skill-candidates/` 或任意 UUID 目录加载。

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
| POST | `/api/skills/propose` | Bearer | provider 时是 | 生成候选包并登记 draft asset；manual_body 优先 |
| POST | `/api/skills/evaluate` | Bearer | provider 时是 | 两阶段评测；无 provider 时必须给 manual_outputs |
| POST | `/api/skills/approve` | Bearer | 否 | evaluated → approved（操作者显式审批） |
| POST | `/api/skills/activate` | Bearer | 否 | CAS 写 binding；`expected_revision` 必填 |
| POST | `/api/skills/rollback` | Bearer | 否 | 从 history 找上一版本并 CAS 回退 |
| GET | `/api/skills/evaluations` | Bearer | 否 | 评测索引安全投影 |
| GET | `/api/skills/evaluations/{id}` | Bearer | 否 | 单条评测（阶段 verdict + 聚合指标） |
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

### `POST /api/skills/propose`

请求：

```json
{"skill_id":"repo-evidence","base_version":"1.0.0","feedback":"补充零写入步骤",
 "manual_body":"# 修订版\n…","version":"1.0.1"}
```

`manual_body` 给出时 `origin="manual"`、`calls_model=false`；否则必须配置
`real_model=true` 的 provider，`origin="provider"`、`calls_model=true`。
两者都没有 → 409 `conflict` + `details.reason="provider_unavailable"`，不写候选。

201：

```json
{"asset_id":"skill-repo-evidence-1-0-1",
 "candidate_id":"candidate-repo-evidence-1.0.1-…",
 "package_hash":"sha256:…","origin":"manual","calls_model":false}
```

### `POST /api/skills/evaluate`

请求：

```json
{"asset_id":"skill-repo-evidence-1-0-1",
 "baseline_asset_id":"skill-repo-evidence-1-0-0",
 "manual_outputs":{
   "baseline:zero-write-acceptance":"未验证。",
   "candidate:zero-write-acceptance":"零写入；树快照；git status --porcelain",
   "baseline:evidence-recompute":"…","candidate:evidence-recompute":"…"
 }}
```

- `manual_outputs` 的 key 可以是 `task_id`（baseline/candidate 同输出），或
  `baseline:<task_id>` / `candidate:<task_id>`（分别给出）。每个任务至少要有
  一种覆盖，缺失 → 409 `provider_unavailable`（`details.missing_task_ids`）。
- 真实 provider 时直接走 live；此时忽略 `manual_outputs`。
- 结果写入 `<skill-state>/skills/evaluations/<evidence_id>.json`，asset 从 draft →
  evaluated；响应只给 `evidence_id`，不给路径。

200：

```json
{"evidence_id":"eval-repo-evidence-two-phase-…",
 "transfer":{"verdict":"keep","reason":"candidate pass_rate > baseline",
             "metrics":{"phase":"transfer","task_count":3,
                        "candidate":{"attempts":3,"passed":3,"pass_rate":1.0},
                        "baseline":{"attempts":3,"passed":2,"pass_rate":0.6667},
                        "transfer_delta":0.3333,"tasks_improved":["…"],
                        "tasks_regressed":[],"regression_count":0}},
 "regression":{"verdict":"keep","reason":"no regression","metrics":{…}},
 "final":"keep","mode":"manual","calls_model":false}
```

`mode` 只有真实 provider **实际被调用** 时才是 `live`；Scripted/manual 永远是
`manual` 且 `calls_model=false`。

### `POST /api/skills/approve`

请求：

```json
{"asset_id":"skill-repo-evidence-1-0-1","approved_by":"ops-reviewer",
 "evidence_id":"eval-repo-evidence-two-phase-…","note":"manual keep"}
```

```json
{"asset_id":"skill-repo-evidence-1-0-1","status":"approved"}
```

只有 `evaluated` 可以审批；`approved_by`、`evidence_id` 必填。evidence 必须
`final=="keep"`、`asset_id` 与资产一致、candidate hash 与资产一致；reject/rollback
一律 409 `conflict` + `details.reason="skill_not_keep"`，hash/asset 不符 →
409 + `details.reason="skill_evidence_mismatch"`。**默认 fail-closed，不提供静默
override**；人工例外需后续显式 `override_reason` 设计，本切片未实现。

### `POST /api/skills/activate`

请求：

```json
{"asset_id":"skill-repo-evidence-1-0-1","expected_revision":2,
 "actor":"ops-reviewer","evidence_id":"eval-repo-evidence-two-phase-…",
 "reason":"manual keep"}
```

```json
{"asset_id":"skill-repo-evidence-1-0-1","skill_id":"repo-evidence",
 "version":"1.0.1","package_hash":"sha256:…","revision":3,
 "commit_record_id":"3"}
```

`actor`、`evidence_id` 必填；`reason` 可选。激活前检查 registry 中同
`skill_id@version` 是否存在不同 `package_hash` 的记录；存在 → 409
`skill_identity_conflict`，不写 binding。激活同时把包发布到
`<skill-state>/skills/packages/<asset_id>/<skill_id>/`，并写
`<skill-state>/skills/commits/<revision>.json`（含 actor/reason/evidence/from/to）。

### `POST /api/skills/rollback`

请求：

```json
{"reason":"regression","rolled_back_by":"ops-reviewer","expected_revision":3,
 "evidence_id":"eval-…"}
```

```json
{"target_asset_id":"skill-repo-evidence-1-0-0","skill_id":"repo-evidence",
 "version":"1.0.0","package_hash":"sha256:…","revision":4,
 "commit_record_id":"4"}
```

从 binding history 找当前 skill 的上一版本；当前 binding 必须恰好有一个
skill（多个 → 409 `skill_rollback_ambiguous`）。上一版本必须在技能 registry
中且仍 approved/active；没有上一版本 → 404 `rollback_target_missing`；目标
不可复用 → 409 `rollback_target_unavailable`。history 文件保留，回退写
`<skill-state>/skills/commits/<revision>.json`。

### `GET /api/skills/evaluations`

```json
{"items":[{"evidence_id":"eval-…","asset_id":"skill-repo-evidence-1-0-1",
 "skill_id":"repo-evidence","version":"1.0.1","package_hash":"sha256:…",
 "status":"active","verdict":"keep","mode":"manual","calls_model":false,
 "revision":3,"created_at":"…"}]}
```

### `GET /api/skills/evaluations/{id}`

返回同上单项，并附 `transfer` / `regression` / `final`。不含逐题 rows、源码、
输出正文或文件系统位置。

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

**D4 门控**：只有本 turn 的 `TurnSnapshot.allowed_tools` 含 `mcp__*` 工具时
才进入上面的多轮循环；没有 MCP 工具时，即使 runtime 为 `active`，仍走旧
「单 tool + 第二次调用 `tool_choice="none"`」路径（技能上下文照常加载）。

## 5. 错误码

HTTP 错误形状沿用 `{error:{code,message,details?}}`。

| HTTP | code | 场景 |
| --- | --- | --- |
| 400 | `bad_request` | 未知字段、缺字段、非法 mode/参数 |
| 401 | `unauthorized` | 缺/错 Bearer |
| 403 | `forbidden_origin` | Origin 不允许 |
| 404 | `not_found` | 未知 session / asset / 评测；rollback `details.reason="rollback_target_missing"` |
| 409 | `conflict` | `skill_integrity_mismatch`、`runtime_disabled`、`provider_unavailable`、`skill_state_conflict`、`skill_identity_conflict`、`skill_rollback_ambiguous`、`skill_registry_conflict`、`skill_eval_invalid`、`skill_not_keep`、`skill_evidence_mismatch`、`skill_package_untrusted`、`rollback_target_unavailable`、MCP 绑定/profile 系列（见上） |
| 413 | `payload_too_large` | body >64 KiB、工具输出 >64 KiB |
| 415 | `unsupported_media_type` | 非 JSON body |
| 503 | `adapter_unavailable` | runtime/allowlist/sandbox 不可用、MCP 超时、`skill_admin_unavailable`、`skill_binding_invalid` |
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
| `skill_eval_manual` | `POST /api/skills/evaluate` + `manual_outputs` | 两阶段 evidence、`mode=manual`、`calls_model=false` | 不能说 live、不能说模型提升 |
| `skill_eval_live` | `POST /api/skills/evaluate` + real provider | 真实 provider 且实际调用时才 `mode=live` | provider 缺失时不得伪造；mock/scripted 不得标 live |
| `skill_lifecycle` | approve / activate / rollback | binding revision 与 pointer 变化 | 不是模型任务证据，不能用于技能效果 claim |
| `provider_task` | `--provider openai-compatible` 的 chat turn | 只有真实 provider 才能标 `live` | provider 环境缺失/为空时一律 **未验证**；mock 不得标 live |

- `context_loaded` 是 **manual/capture** 证据，不是 live run。
- `mcp_rpc` 是真实子进程证据，但不是模型任务证据。
- `skill_eval_manual` 与 `skill_eval_live` 分开写；同一份 evidence 不能混标。
- 当前 C 切片的 `provider.env` absent/empty：`provider_task` 与
  `skill_eval_live` 一律 **未验证**，不得写成 live。
- 服务端不主动写 MCP transcript（避免仓库/state 泄漏）；需要 transcript 时用
  `scripts/demo_mcp.py`，它只写 `/tmp/molt-mcp-transcript.jsonl`。

## 7. 旧 binding 升级迁移

线上旧 binding 可能仍是：

```json
{"skills":{"repo-evidence":{"skill_id":"repo-evidence","version":"1.0.0",
 "asset_id":"skill-repo-evidence-1.0.0","package_hash":"sha256:…"}}}
```

没有 `skill-registry` / `skills/packages` / `skills/commits`。新 runtime 会因
`skill_package_untrusted` / `skill_unavailable` 拒绝加载，需要一次性迁移。
**服务启动不会自动执行迁移。**

```bash
# 1) dry-run（默认，不写任何状态）
python3 -B scripts/migrate_skill_binding.py \
  --skill-state $MOLT_UPPER_STATE/runtime \
  --skills-root skills --repo-root . \
  --skill-id repo-evidence \
  --target-asset-id skill-repo-evidence-1-0-0 \
  --actor operator \
  --reason "migrate existing operator binding" \
  --json

# 2) 确认 planned 后 apply
python3 -B scripts/migrate_skill_binding.py ... --apply --json
```

- `--skill-state` 必填且在仓库外；`--skills-root` 默认 `<repo>/skills`。
- dry-run 输出 `status=planned/noop`，不改 binding、不创建 registry/packages/
  commits。
- `--apply` 先把 `<skill-state>/skills/current.json` 复制为同目录
  `current.json.bak-<UTC>`（0600），再调用
  `lifecycle.migrate_existing_binding`；发布包到
  `<skill-state>/skills/packages/<target_asset_id>/<skill_id>/`，写 commit
  record，单 CAS 更新 binding，保留 `mcp` 段。
- 已迁移再执行返回 `status=noop`，revision 不变。
- 退出码：0 成功/noop，2 校验失败（含 `migration_package_mismatch`），3 内部错误。
- 失败时 binding 不被修改；需要回退时用同目录 `current.json.bak-<UTC>` 覆盖
  `current.json`。脚本不会自动回滚。

### 崩溃恢复

激活/回退/迁移在 CAS 前写
`<skill-state>/skills/intents/<intent_id>.json`（`status="prepared"`），CAS
后写 `<skill-state>/skills/commits/<revision>.json`。若进程在两者之间退出：

```bash
# 只对账，不迁移：补写缺失 commit record，输出 adopted/stale
python3 -B scripts/migrate_skill_binding.py \
  --skill-state $MOLT_UPPER_STATE/runtime \
  --repo-root . --reconcile-only --json

# 只把不可采用的 prepared intent 标 stale；不删文件、不改 current、不写链
python3 -B scripts/migrate_skill_binding.py \
  --skill-state $MOLT_UPPER_STATE/runtime \
  --repo-root . --cleanup-stale-intents --json
```

- `reconcile_commits` 幂等：current 已匹配 intent.to 时补 record 并标
  `committed`；CAS 未发生或 current 已走远时标 `stale`，不进入采用链。
- `--apply` 前后各对账一次；迁移抛错或返回 `commit_record_pending` 时输出
  `recovery.command`（`--reconcile-only`）与 `recovery.backup`，退出非 0，不自动
  删除 backup/intent。
- API 的 evaluate/activate/rollback（及 `/api/skills` 状态入口）会先做一次
  低成本 reconcile；发生 adopted 时响应带 `reconciled:[{intent_id,revision}]`。
- rollback 只认领 commit chain；prepared intent 的 `from` 只有在对账 adopted 后
  才会成为合法回退目标；stale intent 永不进入采用链。

## 8. 后续切片接口预留

- `/api/skills/effective` 返回的 `revision` 是 CAS 基值；approve/evaluate/activate/rollback
  全部复用 `BindingStore.commit(section, key, entry, expected_revision=...)`，
  不新增第二套 binding 存储。A 的 mcp entry 只允许
  `server_id/version/asset_id/package_hash` 四个字段（`_validate_entry` 拒绝未知
  字段），per-entry revision 不存在；**revision 是 binding 快照级值**，与
  entry 在同一快照原子提交。
- 技能 registry 固定在 `<skill-state>/skill-registry`（`UpperRegistry`），与
  `--upper-state` 的 MCP registry 分开；approve/activate/rollback 只通过
  `molt/skills/lifecycle.py` 操作它。
- MCP 审批记录走 `--upper-state` 的 `registry.json`：C 只读并核对
  `asset_id/version/artifact_hash` + active/strict/implemented/无 pending；
  没有记录时明确标 `manual_operator_config`，不冒充 evaluated/approved。
- `/api/runtime/status` 的 `enabled` 与 session `mode` 分离，便于后续按
  route 或按实验切换，而不改变旧 message 契约。
- 本切片不实现通用 adopt/evolve 平台；回退目标由 lifecycle 的 commit chain
  派生，C 侧用 binding history 做上一版本断言。

## 从复用池移除 Memory / MCP 资产

`POST /api/evolution/assets/{asset_id}/rollback`

- 需要 Bearer token；body：`{"reason": "...", "rolled_back_by": "..."}`。
- 只允许 `policy_memory` / `mcp_profile`；Skill 仍走 `POST /api/skills/rollback` 的 binding 回滚。
- 语义：把 registry 资产标记为 `rolled_back`，后续自动复用不再选中它；**不修改已绑定会话的快照**，已绑定的 MCP profile / memory 仍按原快照运行。
- 200：`asset_id / kind / title / version / status / status_reason`；409：`asset_not_found` / `asset_not_active` / `asset_kind_not_retirable`。
- `GET /api/evolution/catalog` 的每个资产新增 `reuse_count`：当前仍引用该资产的会话快照数；只统计数量，不暴露其他 session id。

### 恢复到复用池

`POST /api/evolution/assets/{asset_id}/restore`

- 需要 Bearer token；body：`{"reason": "...", "restored_by": "..."}`。
- 只允许 `policy_memory` / `mcp_profile`，且资产必须是 `rolled_back`。
- 恢复为 `active`，后续自动复用重新可见；**不修改已绑定会话的快照**。
- `GET /api/evolution/catalog?include_rolled_back=1` 可同时返回 active 与 rolled_back 资产，并带 `status` 字段。
