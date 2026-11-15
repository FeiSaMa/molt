# molt

`molt` 是一个用于实验 agent 配置改进的外层编排器。它负责把候选配置放进受控流程：选择候选、执行题集、验证结果、比较成本与成功情况，再由门禁决定是否采纳。每一步都会留下可审查、可重跑的记录。

它不负责训练模型，也不提供新的模型能力。模型或执行器负责生成和运行候选；molt 负责控制实验流程、检查结果，并保存判断依据。这样，团队可以回答三个实际问题：候选具体改了什么、它在同一题集上表现如何、系统为什么接受或拒绝它。

## 适合做什么

- 比较同一场景下的多个 agent 配置或候选方案。
- 在固定题集和验证规则下运行候选，记录每题结果、用量与成本。
- 用统一门禁判断候选是否满足采纳条件。
- 需要时将已采纳版本回退到先前归档。
- 从运行记录和账本重算指标，检查导出的展示数据是否有依据。

当前仓库包含代码修复场景、确定性配置修复场景、Pi runtime 集成、fixture 和离线演示流程，也包含供网页使用的数据接口与只读实验助手 API。各场景的能力与约束不同，比较结果时必须使用对应场景定义的比较范围。

## 一次实验如何运行

一轮实验大致经过以下步骤：

1. **定义场景**：场景文件指定题集、候选搜索范围、预算和门禁策略。
2. **生成候选**：使用网格搜索或 proposer 产生候选配置。
3. **执行题集**：对应 shell 将题目交给 runtime 执行，再由 verifier 检查产物。
4. **计算结果**：系统汇总逐题结果、用量、成本和诊断信息。
5. **运行门禁**：比较基线与候选，按场景策略给出采纳、拒绝或不可比较等结论。
6. **归档与审计**：采纳时保存快照和归档；审计时从运行记录及账本重新计算结果。

```text
场景 + 候选
      |
      v
  执行器 runtime ---> 题目验证 verifier
      |                      |
      +---- run / ledger ----+
                 |
                 v
         result ---> gate
                     | 采纳 / 拒绝
                     v
              archive / snapshot
```

门禁策略由场景拥有，执行器和 shell 不自行决定采纳标准。审计使用持久化的运行证据重算结果，避免只依赖页面或人工汇总。

## 仓库地图

| 路径 | 内容 |
| --- | --- |
| `molt/core/` | 协议记录、指标计算、门禁、存储事务、审计和脱敏等核心逻辑。核心层保持标准库依赖。 |
| `molt/adapters/` | Runtime 与模型服务适配，包括 fixture、deterministic、Pi 和 proposer transport。 |
| `shells/` | 场景执行接口的实现：题目提供、prompt/harness、验证器和候选配置。 |
| `scenarios/` | 场景配置，包括题集引用、预算、搜索范围和门禁策略。 |
| `bench/` | 基准题目、工作区、测试与 holdout 题。题面和验证规则属于实验定义的一部分。 |
| `schemas/v1/` | 记录格式的 JSON Schema，是协议字段的事实来源。 |
| `docs/protocol.md` | 从 schema 生成的协议文档，不要手工编辑。 |
| `state/` | 本地运行产生的状态、记录和事务文件，不应提交。命令行运行时建议将 state 指向仓库外。 |
| `web/data/` | 网页消费的导出数据，不是引擎状态存储。需要通过导出或 demo 命令生成。 |
| `web/page/` | 读取 `web/data/` 的静态展示页，可直接在浏览器打开。 |
| `molt/agent/` | 本地 agent HTTP API、session/SSE 对话流程、只读实验工具及数据投影。 |
| `probes/` | 独立的研究实验与数据整理工具，不属于主 demo 的证据或比较域。 |
| `docs/decisions/` | ADR，记录架构决策、边界和兼容性约束。 |

## 主要组件

### Core、shell 与 adapter

- **Core** 管记录格式、指标、门禁、快照与审计。它不启动模型，也不依赖具体题集。
- **Shell** 连接场景与执行器：提供题目、组织输入、运行 verifier，并声明可用能力。
- **Adapter** 对接实际运行环境，例如确定性 runtime、fixture 或 Pi。

依赖方向是 shell 和 adapter 依赖 core。Core 不反向导入它们。新增场景时，优先沿用 shell 接口和 adapter 边界；不要把场景专属逻辑塞进 core。

### 场景与题集

场景文件定义题目集合、候选维度、执行限制和判定策略。Search 用于候选筛选；holdout 用于独立检查，不能混入搜索过程。题目、验证器或候选空间发生影响可比性的改动时，应按仓库的冻结面流程更新场景版本并重跑基线。

### 证据与网页

一次执行会留下 run、结果、门禁决定以及相应账本和事件。采纳还会形成归档与快照。`molt audit` 用这些记录进行只读重算。

`web/data/` 是面向展示的导出接口，网页只消费导出文件，不直接读取引擎内部状态。重新生成数据应使用 CLI，不要手改导出 JSON。Probe 研究数据使用独立的数据面和比较域，不能混作主场景证据。

### 本地 Agent API

`molt serve` 启动本地 HTTP 服务，供网页或本地客户端读取 probe 实验投影，并创建内存 session 与流式对话。默认从 probe manifest 构建经白名单校验的实验索引；也可用 `--probe-index` 指定已导出的索引。对话工具目前只允许读取实验列表或单项实验；它不执行 shell、不更改实验、不写入核心状态。服务默认使用 mock provider，可显式配置 OpenAI-compatible provider。

服务默认绑定 loopback，使用 Bearer token；session 不持久化，服务重启后会消失。若要从网页调用，需要按 API 文档配置允许的 Origin 和认证方式。部署到服务器前，应根据实际网络拓扑配置反向代理与访问控制，不要把本地开发配置直接当成公网部署配置。

## 环境准备

需要 Python 3.12、[`uv`](https://docs.astral.sh/uv/) 和 Git。Pi live runtime 另需本机安装 Pi CLI 与 Bubblewrap；普通单元测试、fixture 演示和静态网页检查不需要真实模型调用。

```bash
uv sync --extra dev
```

在仓库中运行 Python 命令时设置 `PYTHONDONTWRITEBYTECODE=1`，并将运行状态、导出目标放在仓库外，避免把生成文件混入代码变更。

## 快速验证

```bash
# 检查协议 schema
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt schema validate --all

# 检查 demo 场景配置
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt scenario validate --scenario scenarios/demo/scenario.json

# 检查 core 依赖方向与仓库协作约定
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt doctor --deps
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt doctor --repo-policy

# 运行测试与 lint
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q
PYTHONDONTWRITEBYTECODE=1 uv run ruff check .

# 验收静态展示页
node web/page/check.mjs
```

## 常用流程

### 离线运行

Fixture 和 replay 模式用于开发、测试及演示流程，不调用真实模型。完整离线演示写入 `/tmp`：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt demo --mode replay --state /tmp/molt-demo --out /tmp/molt-demo-web
```

若只需运行搜索，可用：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt search --scenario demo --runtime fixture --state /tmp/molt-search-state
```

具体命令参数以 `uv run python -m molt --help` 及子命令的 `--help` 为准。真实 Pi demo 会调用模型并产生实际费用，只有明确需要现场演示时再运行：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt demo --max-wall 180
```

### 导出网页数据

网页数据由 CLI 从实验状态生成。指定场景 ID、对应 state 和仓库外的输出目录：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt export --scenario demo --state /tmp/molt-demo/state --out /tmp/molt-web-data
```

静态页默认读取仓库内的 `web/data/`；本地检查可以使用临时导出目录，或将导出结果按团队流程放入展示数据目录。不要直接编辑导出文件。

### 启动本地 Agent API

缺省 provider 是确定性 mock，不产生模型请求。服务会在 state 目录创建访问 token，并将监听地址输出到标准输出：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt serve --state /tmp/molt-agent-state --port 0
```

`--port 0` 表示由操作系统分配可用端口。启动输出会给出 `listen` 地址与 `token_file` 位置；请求受保护的 API 时，从 token 文件读取 Bearer token。健康检查和 OpenAPI 文档不需要 token。

常用只读端点包括：

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/health` | 查看服务和 provider 状态。 |
| `GET /api/openapi.json` | 查看完整 API 契约。 |
| `GET /api/experiments` | 读取已校验的实验索引投影。 |
| `GET /api/experiments/{experiment_id}` | 读取单项实验投影。 |
| `GET /api/tasks` | 读取展示用的逐题结果。 |
| `/api/sessions` 与其子路径 | 创建、读取、删除内存会话并通过 SSE 接收对话事件。 |

接口字段、错误码和事件格式以 [`contracts/openapi-v1.json`](contracts/openapi-v1.json) 为准。更多限制与网页接入说明见 [`docs/api/probe-agent-api.md`](docs/api/probe-agent-api.md)。

## 重要边界

- **同一场景版本内比较**：不同 `scenario_version` 的分数不能直接比较。
- **holdout 保持隔离**：搜索过程不得读取或使用 holdout 题；报告阶段才可按场景流程访问。
- **Provenance 要如实标注**：fixture、live、replay 含义不同；回放不是一次新的真实模型运行。
- **候选执行不等于完整沙箱**：Pi 的 bash 工具会使用 Bubblewrap 隔离；verifier 启动面加固不代表恶意 Python 已被完整隔离。细节见 ADR 与安全研究文档。
- **网页是数据消费方**：`web/page/` 不产生实验数据，也不改变门禁结果。
- **Probe 不等于主 demo**：Probe 是独立研究用途，不能与 demo 的版本或题集混合比较。
- **不要把生成物当源文件**：`state/`、`runs/`、原始日志、密钥和临时证据不得提交；协议文档与 `web/data/` 也须按各自生成流程更新。

项目允许的公开结论、数据口径和已知限制由协作规范及对应 ADR 维护；README 不复制实验数字，避免文档中的数字与证据更新不同步。

## 协作入口

- [AGENTS.md](AGENTS.md)：项目定位、数据口径、依赖边界、冻结面和禁止事项。
- [CONTRIBUTING.md](CONTRIBUTING.md)：开发流程、分支、测试、schema 更新和提交要求。
- [docs/architecture.md](docs/architecture.md)：核心架构、端口、数据流、门禁与事务细节。
- [docs/protocol.md](docs/protocol.md)：由 schema 生成的记录字段说明。
- [docs/decisions/](docs/decisions/)：架构决策及兼容性说明；[ADR-0008](docs/decisions/0008-demo.md) 记录 demo 历史证据。
- [docs/api/](docs/api/)：本地 Agent API 与网页接入文档。
- [probes/README.md](probes/README.md)：独立 Probe 实验工具、流程及数据说明。
