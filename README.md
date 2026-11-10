# molt

自进化 agent 引擎的**外层编排器**：搜索受控开关、跑题集、按唯一口径打分与门禁、原子采纳 / 回退，
并把每次执行变成可复算的证据。

B1 已完成 core v1 协议、门禁引擎、快照事务、事件 / 账本分片、隔离抽象与 fixture runtime。
B2a 增加 LLM proposer；B2b 增加 Pi 1.5.0 adapter、bwrap bash sandbox、trust preflight、
session 账本与预算执行；B2c 增加第二个 shell `svc-config` 与确定性 runtime
（零 LLM / 零网络 / 零成本），并把 demo 题集扩到 **10 search + 5 holdout**（`demo-6`）；
B2d 增加 `molt demo`（1 live 候选 + 1 replay 基线 + 10 题扫描回放）与协作六件套。
HTML 报告页面仍属于后续批次。

## 分层

| 层 | 位置 | 职责 |
| --- | --- | --- |
| core | `molt/core/` | 协议类型、门禁、存储事务、哈希、审计；**运行时零第三方依赖** |
| shell | `shells/code-repair/`、`shells/svc-config/` | Task / Harness / Verifier；code-repair 是 6 组 bash-only 搜索空间，svc-config 是 4 组确定性配置修复 |
| adapter | `molt/adapters/` | `fixture/`、`deterministic/` Runtime（均不联网）；`llm_proposer.py` DeepSeek transport；`pi/` 为真实 runtime |
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
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m molt search --scenario svc-config --runtime deterministic
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m molt audit --scenario svc-config
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m molt demo --mode replay --state /tmp/molt-demo --out /tmp/molt-demo-web
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q
```

现场演示（真实 Pi，180s 硬上限）：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m molt demo --max-wall 180
```

`demo` 产出 1 条 live 候选 + 1 条 replay 基线（同一题，closure 组）与 10 条扫描回放
（`scan10` 组），并直接导出 `web/data`；证据与分组见 ADR-0008。

## 契约

协作规范见 [AGENTS.md](AGENTS.md)（契约 / 冻结面 / 禁止清单）与 [CONTRIBUTING.md](CONTRIBUTING.md)（分支 / 评审 / schema 流程）。

- 协议字段表：[`docs/protocol.md`](docs/protocol.md)（由 `molt schema docs` 从 schema 生成，禁止手改）。
- 决策：[ADR-0002 协议](docs/decisions/0002-protocol-v1.md)、
  [ADR-0003 评分](docs/decisions/0003-scoring.md)、
  [ADR-0004 shell 与搜索空间](docs/decisions/0004-shell-and-dims.md)、
  [ADR-0005 proposer](docs/decisions/0005-proposer.md)、
  [ADR-0006 isolation](docs/decisions/0006-isolation.md)、
  [ADR-0007 svc-config](docs/decisions/0007-svc-config.md)、
  [ADR-0008 demo](docs/decisions/0008-demo.md)。
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
- shell 由 `shells/<shell_id>/manifest.json` 的 `entrypoints` 装载；`svc-config` 用
  deterministic runtime，run 的 `usage` 全 0、`cost_source="none"`，不得与其它 shell 的分数比较。
- `molt demo` 的现场闭环只有 **1 条 live 候选 + 1 条 replay 基线**（同一题）；10 题扫描是
  **回放**（`demo_group=scan10`），不是 demo 的第二次运行。页面必须按
  `runs[].demo_group` 与 `runs[].provenance` 分开标注，不得暗示两条都 live。

### 真实 Pi 基线（demo-6，2026-11-11）

10 道 search 的实测（并发 1，费用为 run 记录实际值；详见 [ADR-0008](docs/decisions/0008-demo.md)）：

| config | solved | cost | cost_per_solved |
|---|---|---|---|
| **high/full（v0-pi，demo 起点）** | **10/10** | **$0.035560** | **$0.003556** |
| **low/full（v0，demo 候选）** | **10/10** | **$0.009978** | **$0.000998** |
| off/full | 10/10 | $0.012385 | $0.001239 |
| off/minimal | 10/10 | $0.011894 | $0.001189 |
| low/minimal | 10/10 | $0.021180 | $0.002118 |
| high/minimal | 10/10 | $0.021113 | $0.002111 |

- 现场 demo 的叙事是**选定的高成本起点 → 低成本候选**：`high/full`（v0-pi）
  → proposal 派生 `low/full`（v0）。成功率不变（10/10），执行成本 **−72%**
  （`$0.035560 → $0.009978`，按 gate 的 9 题 replay + 1 条 live 实测为 **−70.0%**），
  gate `adopt`。
- **同时保留负结果**：6 组候选中**没有一组比 low/full 更便宜**。demo 不是搜索空间最优性
  的声称，也不是「baseline v0 被改进」的声称——它只展示门禁能接受一个真实的
  「成功率不变、成本更低」的候选。
- holdout 实测：起点 high/full **5/5**，候选 low/full **5/5**（只导出聚合）。
- fixture 基线是 `3/10`（`molt calibrate` 的校准口径）；真实 Pi 是 `10/10`。
  **fixture 只用于校准，不是能力声明。**

## 状态

- 已实现：schema / 类型双校验、genome 哈希冻结、G1–G8 门禁、快照与 kill -9 恢复、
  并发 adoption 单胜、per-run 事件与账本、holdout 类型隔离、审计重算、零写入测试、
  LLM/grid proposer、Pi 1.5.0 runtime（bash-only sandbox / trust / 账本 / 预算）、
  第二 shell `svc-config` + deterministic runtime（零成本、manifest 驱动装载）、
  `demo-6` 的 10 search + 5 holdout 题集、`calibrate` 基线校准、
  `export` 的 `web/data` 数据导出（无页面代码）、
  `demo` 现场闭环（1 live + 1 replay + 10 题扫描回放，180s 硬上限；选定高→低起点，
  gate `adopt`，真实数字与负结果见 ADR-0008，证据在 `web/data`）。
- 未实现：HTML 报告页面。
