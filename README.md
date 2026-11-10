# molt

自进化 agent 引擎的**外层编排器**：搜索受控开关、跑题集、按唯一口径打分与门禁、原子采纳 / 回退，
并把每次执行变成可复算的证据。

B1 已完成 core v1 协议、门禁引擎、快照事务、事件 / 账本分片、隔离抽象与 fixture runtime。
B2a 增加 LLM proposer；B2b 增加 Pi 1.5.0 adapter、bwrap bash sandbox、trust preflight、
session 账本与预算执行。svc-config、HTML 报告与正式题集仍属于后续批次。

## 分层

| 层 | 位置 | 职责 |
| --- | --- | --- |
| core | `molt/core/` | 协议类型、门禁、存储事务、哈希、审计；**运行时零第三方依赖** |
| shell | `shells/code-repair/` | Task / Harness / Verifier；6 组搜索空间与 bash-only prompt |
| adapter | `molt/adapters/` | `fixture/` Runtime（不联网）；`llm_proposer.py` DeepSeek transport |
| schema | `schemas/v1/` | 11 类记录的 JSON Schema，协议唯一事实源 |

依赖单向：`core` 只 import stdlib 与 `molt.core.*`；shell 依赖 core；adapter 依赖 core。
`python -m molt doctor --deps` 会做机械检查。

## 快速开始

```bash
uv venv .venv
uv pip install pytest ruff jsonschema      # 或 uv sync --extra dev

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m molt schema validate --all
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m molt genome verify shells/code-repair/genomes/v0.json
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m molt propose --scenario demo --state /tmp/molt-state --offline
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m molt gate --fixture tests/fixtures/gate/
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m molt audit --scenario demo
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q
```

## 契约

- 协议字段表：[`docs/protocol.md`](docs/protocol.md)（由 `molt schema docs` 从 schema 生成，禁止手改）。
- 决策：[ADR-0002 协议](docs/decisions/0002-protocol-v1.md)、
  [ADR-0003 评分](docs/decisions/0003-scoring.md)、
  [ADR-0004 shell 与搜索空间](docs/decisions/0004-shell-and-dims.md)、
  [ADR-0005 proposer](docs/decisions/0005-proposer.md)、
  [ADR-0006 isolation](docs/decisions/0006-isolation.md)。
- 旧契约已归档：[`docs/archive/contracts-v0.md`](docs/archive/contracts-v0.md)。

## 关键口径

- `cost_per_solved` 只含执行成本：该 genome 全部执行 run（含 invalid / 超时 / 超预算 / 判废）
  的消耗除以有效通过题数；提案成本单列 `search_overhead_budget_usd`，不得互相抵消。
- 门禁统计量是首轮 pass@1；重复 run 只进 `diagnostic.*`。
- 采纳要求：题数不劣（`k`）、单题执行成本不升、四项执行上限完全相同、两侧 invalid 比例不超阈。
- 结论必须带逐题配对表；`scope=report` 的 result 必须同时含搜索集与 holdout 双分数。
- 采纳 / 回退是单写者单事务；读者校验指针 + generation + tree hash。
- 搜索空间是 6 组：thinking × context；tools 锁定 bash，agent 只能用 sandboxed bash 读写，
  read/write/edit 不启用。Pi 主进程没有进程级 egress allowlist，只有 bash 被 bwrap 隔离。

## 状态

- 已实现：schema / 类型双校验、genome 哈希冻结、G1–G8 门禁、快照与 kill -9 恢复、
  并发 adoption 单胜、per-run 事件与账本、holdout 类型隔离、审计重算、零写入测试、
  LLM/grid proposer、Pi 1.5.0 runtime（bash-only sandbox / trust / 账本 / 预算）、
  `export` 的 `web/data` 数据导出（无页面代码）。
- 未实现：`svc-config` shell、HTML 报告、正式 10+5 题集、`demo` 命令。
