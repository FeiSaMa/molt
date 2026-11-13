# ADR 0014：上层记忆 / Skill / MCP 图谱（平行结构）

- 状态：**待评审**
- 日期：2026-11-13
- 关联：`molt/upper/**`、`molt/agent/upper.py`、`contracts/openapi-upper-v1.json`、
  `docs/api/upper-api.md`、`web/chat/**`、`upper_scenarios/c1b-memory-v1/**`
- 上游：B1 图谱/评估实现；本 ADR 只记录 B2 的边界与互操作，不重写 core 协议。

## 背景

molt 现有证据链与门禁都围绕下层 agent/genome：`run → result → gate_decision → archive`，
比较域是 `scenario_version=demo-6`。C1b 实验表明“记录解法重新判分”可以验证记忆策略的
迁移收益与负迁移（x5–x7 transfer，x8 regression），但这类对象不是 genome，也不应被
塞进 core 的 `GateDecision`。

同时上层要管理至少三类资产：`policy_memory`、`skill`、`mcp_profile`。它们有独立版本、
来源、审批与权限语义；Skill/MCP 在本批仍为 stub，不能执行。若把上层结果写进下层
gate 记录，用户会把“底层候选 adopt”误读为“记忆/Skill/MCP 获批”。

## 决定

### 1. 上层是平行结构，不是 core 的扩展

- 上层状态存放在仓库外的 `<upper-state>/upper/`；记录类型、scenario、版本与哈希均为
  上层自有（`c1b-memory-v1` / `upper-c1b-1`）。`serve --upper-state` 未指定时沿用
  `--state`；若该目录落在仓库树内，`/api/upper` 直接 503（`upper_disabled`），
  不回退到仓库内写路径。目录由 `UpperService` 以 `0700` 创建。
- 不修改 `molt/core/**`、`schemas/**`、`bench/**`、`shells/**`、`probes/**`；不重写既有
  probe 证据。
- 上下层只通过引用字段互操作：`lower_scenario_id`、`lower_scenario_version`、
  `lower_genome_id`、`lower_gate_id`、`lower_run_ids`、`lower_solution_snapshots`。
  这些字段只用于溯源，不参与上层判定。
- 现有 `web/data`、demo-6 与 lab v1 语义不变。

### 2. 资产类型、版本与生命周期

- 资产类型固定为 `policy_memory | skill | mcp_profile`；每个资产记录 `version`、
  `source`、`family`、`created_by`、`evidence_refs`。
- 生命周期：`draft → evaluated → approved/rejected → active → rolled_back`。
- `mcp_profile` 必须带 `permission_scope` 与 `approval_level=strict`；权限范围变更走
  **独立更严审批**，未审批时只显示 `pending_permission`，不得激活。
- 本批不调用 MCP 工具、不执行 Skill；`implementation_status=stub`，`execute_stub`
  只报告“未执行”。

### 3. Graph：不可变节点 + 单一原子指针

- `Branch` 有稳定 `branch_id`、label、`permission_scope`、审批元数据，tip 指向保留的
  `ContextNode`。
- `ContextNode` 是不可变 canonical manifest，含 policy memory/Skill/MCP 引用与 hash、
  项目要求 ref/hash、budget、parent(s)、scenario/version/eval refs。
  `context_hash` 覆盖节点全部字段（不含自身），用 canonical JSON SHA-256 计算；
  不枚举字段组合。
- `upper/current.json` 是唯一权威指针（revision、active branch、context node/hash、
  branches）。切换使用 intent/commit：先写 intent 事件，再原子替换 current，最后写
  commit；启动时对未完成 intent 做恢复/abort。
- `switch` 一次更新 active branch、branch tip 与整份 context snapshot，禁止字段级混合；
  切换前后校验 context 与资产 hash。CAS 失败或审批失败时 pointer 保持旧值。
- 候选节点先在独立评估路径标记为 `candidate`，通过上层评估与审批后才可成为 tip；
  reject 保留节点与证据，不切 tip。
- `rollback` 只移动 branch tip 与 active pointer 到历史祖先节点；保留全部节点、事件与
  证据，可追加事件，不删除或改写。
- 不做语义 merge / CRDT；后续需求另立 ADR。

### 4. REPLAY vs synthetic

- `origin ∈ {seed_import, synthetic, real_run}`；`eval_state ∈ {not_evaluated, replayed, evaluated}`。
- seed/synthetic 节点表示重构上下文，即使被展示也不产生 core verdict 或真实 upper
  `keep/reject`；节点默认 `not_evaluated`，页面标“未评估”。
- C1b REPLAY 属于 `real_run` + `replayed`：重新判分记录解法，不调用模型，成本 0；
  评估记录必须明确 `mode=replay` 与 `calls_model=false`，页面显示 REPLAY 徽章。
- 评估以追加 eval 记录/ref 表达，节点本体不可变；D3 要求能从 x5–x7 transfer 追到
  x8 regression 及 gen2 `rollback` / gen4 `keep`，独立评估记录可复算。

### 5. 检索可追溯

- `retrieve(family, query|task_id)` 返回资产版本、命中来源与 score；当前只支持确定性
  exact/prefix 匹配，不声称语义检索。无命中时显式返回空结果。
- 每个评估记录保存实际命中（`retrieval`）与 `lower_refs`，可复现任务使用了什么记忆。

### 6. Seed 导入与真实性

- 只从用户授权的文件级 allowlist 读取 lessons/AGENTS/agents/skills；deny 规则优先。
- 禁止读取 `.zshenv`、auth/token/credential；拒绝项只记录类别，不记录绝对路径。
- 原文只作数据，不执行 Skill/MCP/shell，不编辑热配置。
- seed 资产记录 `source_path_hash`、`content_hash`、`extracted_summary`、`classification`、
  `importer_version`、`provenance=seed_import/reconstructed`、`verified_lineage=false`。
- `seed_identity_hash` 只覆盖 `source_path_hash/content_hash/summary/classification/importer_version`，
  用于去重与同输入同 ID；`record_hash` 是资产记录指纹；`context_hash` 仅用于 ContextNode。

### 7. B2 API 边界

- 新契约 `contracts/openapi-upper-v1.json`，与 agent v1 / lab v1 分离。
- 只读：`GET graph`、`GET assets`、`GET evaluations/{id}`。GET 首次会 bootstrap 写 state
  （init-on-first-use：写 synthetic 图谱、scenario 资产与 bootstrap marker），此后只读。
- 固定操作：`POST replay`（固定 scenario，无路径/命令参数，并发 1）、
  `POST branches/{id}/switch`、`POST rollback`；全部 CAS + 审批校验。
- 不提供 merge / delete / 任意 scenario / shell / MCP 调用。
- 响应脱敏：不含原文、绝对路径、token/key；资产不返回 `source_path`。

### 8. 暂不引入

- 不引入 RAG / embedding / 向量库；检索保持 deterministic exact/prefix。
- 不做 semantic merge / CRDT；不引入新存储依赖（继续 stdlib + JSON/JSONL）。
- 不把 Skill/MCP 变为可执行能力；权限与审批仍 fail closed。

## 后果

- 正面：上下层比较域隔离，审计可区分“底层候选进化”与“上层记忆/Skill/MCP 获批”；
  REPLAY 证据可复算；切换/回退原子且历史保留；MCP 权限绕过被 422 + pointer 不变挡住。
- 代价：上层状态是第二套存储与第二套审批语义；需要额外测试覆盖 CAS/审批/脱敏；
  UI 要区分 synthetic/real_run 与 REPLAY 徽章。
- 风险：如果未来把上层结果同步进 core，必须先写新 ADR 并定义显式映射，不能复用
  `GateDecision` 字段。

## 验证

- `tests/test_upper_api.py`：401/403/400/404/409/415/422；GET 除首次 bootstrap 外不重复写；
  replay 固定参数与并发 1；
  switch CAS 失败 pointer 不变；rollback 历史保留；响应无绝对路径/token；契约 path 集合
  与实现一致。
- B1 既有测试继续覆盖 manifest hash、candidate 不自动成为 tip、审批/回滚与 seed 哈希。
- `molt/core/**`、`bench/**`、旧 probe 证据无变更；`git status` 只应出现新增文件。
