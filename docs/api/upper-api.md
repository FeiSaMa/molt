# 上层上下文图谱 API（upper v1）

> 契约事实源：`contracts/openapi-upper-v1.json`。本文件解释语义；与契约冲突时以契约为准。
>
> 这是**独立于** `contracts/openapi-v1.json`（agent v1）与 `contracts/openapi-lab-v1.json`
> （lab v1）的扩展 API。既有路由语义不变；上层路由只新增在 `/api/upper/**`。

## 定位

upper v1 暴露 B1 上层状态图的**只读投影**与三个固定操作：

```text
GET  graph / assets / evaluations/{id}    只读（首次 GET 会 bootstrap 写 state）
POST replay                              固定 scenario=c1b-memory-v1
POST branches/{id}/switch                原子切换 active branch（CAS）
POST rollback                            move-head-only 回退（CAS）
```

上层有自己的 scenario/version（`c1b-memory-v1` / `upper-c1b-1`）、资产版本与证据记录。
它不修改、替代或合并 core 的 gate / bench / probe 证据。**不存在 merge、delete 或任意
scenario / 命令 / 路径参数**。

## 鉴权与路由

除 `OPTIONS` 外全部要求 `Authorization: Bearer <token>`。`Host` / `Origin` 规则与既有
agent API 相同（精确 loopback Host、精确 Origin）。未知路径 404，方法不匹配 405。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/upper/graph` | 当前图谱投影 |
| GET | `/api/upper/assets` | 脱敏资产卡片 |
| POST | `/api/upper/replay` | 运行固定 C1b REPLAY |
| GET | `/api/upper/evaluations/{evaluation_id}` | 单个评估 + companion decision |
| POST | `/api/upper/branches/{branch_id}/switch` | 原子切换分支 |
| POST | `/api/upper/rollback` | 回退分支 head |

`GET` 除首次 bootstrap 外不写 state：第一次 GET 若 `<upper-state>/upper/` 下还没有
bootstrap 标记，会写入 synthetic 图谱、scenario 资产与 marker（init-on-first-use），
之后只读；`POST replay` 只接受空 JSON 对象（或空 body），其余字段 400。

## 部署与 state

- 上层 state 必须在仓库外。默认取 `serve --state`；若该目录在仓库树内，`/api/upper` 整体
  503（stderr 打印 `upper_disabled`），**不会**回退到仓库内写路径。
- 仓库内部署（例如 `--state /srv/molt/state/agent`，仓库为 `/srv/molt`）必须显式指定
  仓库外的上层目录：

  ```bash
  molt serve --state /srv/molt/state/agent --upper-state /srv/molt/state/upper
  ```

- `UpperService` 创建 `<upper-state>` 与 `<upper-state>/upper/` 时强制 `0700`；记录文件
  仍按 `0600` 原子写入。

## 投影字段

### Graph

```json
{
  "revision": 8,
  "active": {
    "branch_id": "br-…",
    "context_node_id": "ec17…",
    "context_hash": "…64 hex…"
  },
  "branches": [
    {
      "branch_id": "br-…",
      "label": "ops",
      "permission_scope": "read:probes/report/2c",
      "tip_node_id": "ec17…",
      "tip_hash": "ec17…"
    }
  ],
  "nodes": [
    {
      "node_id": "…64 hex…",
      "parents": ["…"],
      "kind": "context",
      "origin": "real_run",
      "eval_state": "replayed",
      "context_hash": "…64 hex…",
      "hash_short": "ec17318e7505",
      "created_at": "2026-11-13T15:18:44Z"
    }
  ]
}
```

- `origin ∈ {seed_import, synthetic, real_run}`；`eval_state ∈ {not_evaluated, replayed, evaluated}`。
- synthetic/seed 节点只表示**重构上下文**，不产生 upper decision，也不得展示为成功。
- 不返回 manifest 原文、资产原文、绝对路径或密钥。

### Assets

```json
{
  "scenario": {
    "upper_scenario_id": "c1b-memory-v1",
    "upper_scenario_version": "upper-c1b-1",
    "family": "c1b-memory"
  },
  "counts": { "policy_memory": 6, "skill": 2, "mcp_profile": 1 },
  "assets": [
    {
      "asset_id": "mem-c1b-gen2",
      "kind": "policy_memory",
      "name": "C1b memory gen2",
      "version": "gen2",
      "source_category": "probes/report/2c",
      "classification": "policy_memory",
      "summary": "…脱敏摘要…",
      "seed_identity_hash": null,
      "record_hash": null,
      "hash_short": "5d0e…",
      "status": "rolled_back",
      "origin": "real_run",
      "eval_state": "replayed",
      "permission_scope": null,
      "approval_level": "standard",
      "pending_permission": null,
      "implementation_status": "stub",
      "verified_lineage": false
    }
  ]
}
```

- `summary` 来自 `extracted_summary` 或资产名，不含原文。
- `seed_identity_hash` 只用于 seed 导入去重；`record_hash` 是资产记录指纹，二者不可混用。
- **不返回 `source_path`**；扫描到绝对路径或密钥形状的字符串会替换为 `<path>` / `<redacted>`。
- MCP profile 带 `permission_scope` 与 `approval_level=strict`；权限变更以
  `pending_permission` 展示，审批前不得激活。

### Evaluation

```json
{
  "evaluation_id": "eval-c1b-memory-v1-regression-mem-c1b-gen2",
  "upper_scenario_id": "c1b-memory-v1",
  "upper_scenario_version": "upper-c1b-1",
  "asset_id": "mem-c1b-gen2",
  "asset_version": "gen2",
  "family": "c1b-memory",
  "task_set": "regression",
  "mode": "replay",
  "mode_label": "REPLAY",
  "status": "failed",
  "cost_usd": 0.0,
  "cost_source": "none",
  "tasks": [
    {
      "task_id": "probe-order-x8",
      "rep": 1,
      "source_run_id": "…run id…",
      "recorded": {
        "solved": true,
        "outcome": "passed",
        "group": "memory",
        "memory": "mem-c1b-gen2",
        "genome": "gen2"
      },
      "replayed": {
        "passed": false,
        "outcome": "failed",
        "matches": false,
        "timed_out": false,
        "error": null,
        "cases_passed": 3,
        "cases_total": 4
      },
      "passed": false,
      "latency_ms": 81.3,
      "cost_usd": 0.0,
      "cost_source": "none",
      "retrieval": { "hits": [], "empty": true }
    }
  ],
  "metrics": { "candidate": { "passed": 0, "attempts": 4 }, "baseline": { "passed": 2, "attempts": 2 } },
  "reference_counts": { "historical": {}, "recomputed": {}, "match": true },
  "lower_refs": {
    "lower_scenario_id": "probe",
    "lower_scenario_version": "probe-7",
    "lower_run_ids": ["…"],
    "lower_solution_snapshots": ["probes/…"]
  },
  "decision_id": "dec-c1b-memory-v1-regression-mem-c1b-gen2",
  "decision": {
    "upper_decision": "rollback",
    "reason": "negative_transfer:0.000<1.000;regressed=['probe-order-x8']"
  }
}
```

`tasks[]` 是逐 rep 的真实 REPLAY 结果：`source_run_id` 指向被回放的记录 run，
`recorded` 是记录侧结果（`group` 即变体标签），`replayed` 是本次重新判分的结果。
逐题语义只以这三个字段为准；实现附带的其他子对象（如内部 `provenance`）不承诺稳定，
页面不应依赖。`metrics.per_task`、`reference_counts`、`retrieval`、`telemetry.errors`、
`cost_usd=0`、`latency_ms` 均可复算。

## REPLAY 语义

- `POST /api/upper/replay` 固定 `scenario_ref=c1b-memory-v1`，不接受 scenario/path/命令。
- 服务端调用 B1 `run_demo`（`mode=replay`、`calls_model=false`），把记录解法重新判分；
  **不调用模型，不执行 Skill/MCP**。每次判分以子进程运行 `molt.upper.replay_worker`
  （`ProbeVerifier`）重新判分仓内记录解法，因此是“重新判分”而不是“重新生成”：
  不产生新的模型调用、不执行 Skill 或 MCP 工具，也没有 token 成本。
- 并发 1：已有 REPLAY 在跑 → `409 {"code":"upper_busy","details":{"reason":"upper_busy"}}`。
- 响应顶层有 `mode="replay"`、`mode_label="REPLAY"`、`calls_model=false`；页面必须显示
  **REPLAY** 徽章，不能写成 LIVE / 真实模型运行。
- 响应同时保留 `phases.transfer`（x5–x7）与 `phases.regression`（x8），避免负迁移被
  聚合指标掩盖；`graph_rollback` 说明 gen2 的 x8 负迁移如何触发 rollback。

## CAS、审批与回退

### switch

请求：

```json
{ "expected_revision": 8, "expected_tip": "ec17…" }
```

- `expected_revision` 与当前 pointer 不一致 → `409 stale_revision`。
- `expected_tip` 与 branch tip 不一致 → `409 stale_tip`。
- 目标上下文引用的资产未 approved/active、缺 `approved_by`/`approved_at`、hash/version
  不匹配、MCP profile 非 strict → `422 approval_required` / `422 graph_validation`。
- 任何失败都**不写 pointer**；成功时 active 与整份 `context_snapshot` 一起切换，禁止字段混合。
- 返回 `{changed, revision, active, branch?}`；分支已是 active 且 tip 未变时
  `changed=false`，不递增 revision。

### rollback

```json
{ "branch_id": "br-…", "target_node_id": "3926…", "expected_revision": 8 }
```

- `target_node_id` 必须是当前 branch tip 的祖先；否则 `422 target_not_ancestor`。
- 只移动 branch tip 与 active pointer；**不删除、不改写历史节点或证据**。
- 成功后 `history_retained=true`；失败（409/422）pointer 不变。

## 错误码

错误体固定 `{"error":{"code","message","details"?}}`。本 API 只使用：

| HTTP | code | 典型 reason |
|---|---|---|
| 400 | `bad_request` | 未知字段、缺字段、类型错误 |
| 401 | `unauthorized` | 缺少/错误 Bearer |
| 403 | `forbidden_origin` | Origin 不允许 |
| 404 | `not_found` | 未知 branch/node/evaluation |
| 409 | `stale_revision` | `details.current_revision` |
| 409 | `stale_tip` | `details.current_tip` |
| 409 | `upper_busy` | REPLAY 并发 |
| 409 | `conflict` | 图谱 CAS 冲突 |
| 413 | `payload_too_large` | body > 64 KiB |
| 415 | `unsupported_media_type` | 非空 body 的 `Content-Type` 不是 `application/json`（共享前置校验，三个 POST 都有） |
| 422 | `approval_required` | 资产未审批 / MCP 非 strict |
| 422 | `graph_validation` | 资产 hash、上下文、祖先关系不合法 |
| 503 | `upper_unavailable` | state 不可用、无 active pointer |
| 503 | `upper_eval_unavailable` | REPLAY evaluator 不可用/失败 |

错误体不含绝对路径、token、key 或内部堆栈。

## 上下层互操作

- 上层评估记录只允许在 `lower_refs` 中引用 `lower_scenario_id`、
  `lower_scenario_version`、`lower_genome_id`、`lower_gate_id`、`lower_run_ids` 等
  `lower_*` 字段。
- 底层 `adopt` **不代表**记忆 / Skill / MCP 通过；上层只输出自己的
  `upper_decision ∈ {keep, reject, rollback}`。
- 没有把 lower verdict、分数或决策写进 upper decision 的代码路径；`molt.upper.types`
  会拒绝非 `lower_*` 引用键与 verdict 形状的 metrics 键。

## 不做的事

- 不提供 merge / CRDT / 语义合并 / 节点删除。
- 不接受任意 scenario、命令、文件路径、URL 或 shell。
- 不执行 Skill、MCP 或 seed 原文；B1/B2 均为 stub。
- 不引入 RAG / embedding；检索是确定性 exact/prefix，未命中显式返回空结果。
