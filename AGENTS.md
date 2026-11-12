# AGENTS.md — molt 协作与契约约定

> 这是 Agent 与人类协作者的统一入口。流程细节见 [CONTRIBUTING.md](CONTRIBUTING.md)，
> 写作与格式见 [docs/style.md](docs/style.md)，架构见 [docs/architecture.md](docs/architecture.md)。
> 字段级事实源是 `schemas/v1/*.schema.json` 与 `molt/core/types.py`，
> 生成物是 [docs/protocol.md](docs/protocol.md)（禁止手改）。三者冲突时以 schema 为准。

## 一、定位与 claim

molt 是自进化 agent 引擎的**外层编排器**：把「搜索候选 → 执行题集 → 判分 → 门禁 → 采纳 / 回退」
做成一条可复算（可重跑、可审计）的闭环。仓库交付编排、协议与证据，不交付模型能力。

**claim（只允许这样说）**

> 模型能力已经碾压这 10 道题；molt 证明的是：在成功率不降的约束下，进化可以发生在成本与墙钟上——high/full → low/full，cost −72%、wall −61%，gate adopt，1 live + 1 replay，证据可复算。

- **题目饱和**：真实 Pi 基线 **10/10**，成功率维度没有 headroom。fixture `3/10`
  只是 `molt calibrate` 的校准口径，**不代表真实难度**，不得写成真实基线。
- 在固定的 10 题搜索集上，**未观察到通过题数下降**（n=10，描述性结论，seed 不可控），
  且**单题执行成本不升**；搜索开销单列报告，不与执行成本互相抵消。
  原文见 [ADR-0003 §claim 措辞](docs/decisions/0003-scoring.md)。
- 真实 Pi demo（demo-6，2026-11-11）：起点 `high/full`（v0-pi）**10/10、$0.035560、
  106.9s**；proposal 派生候选 `low/full`（v0）**10/10、$0.009978、41.7s**。口径分开：
  **扫描级** cost **−72%**、wall **−61%**（10 题扫描 vs 10 题扫描）；
  **gate 级** cps **−70.0%**（`$0.003556 → $0.001068`，9 条 replay + 1 条 live）。
  holdout `5/5 → 5/5`，gate `adopt / lower_cost_per_solved`。
  这是**选定的高→低起点**，不是搜索空间最优性声称。
- 负结果（同批 6 组）：**没有候选比 low/full 更便宜**（最接近的 `off/minimal` 仍 +19.2%）；
  不得把 demo 表述成「baseline v0 被改进」或「搜索找到了更优配置」，也不得声称成功率提升。
- fixture 基线是 `3/10`，只用于 `molt calibrate` 校准，不得当作真实能力或对外 claim。
- 任何结论必须带逐题配对表；聚合数字不得单独出现。
- 每次执行落成可复算的证据链（§三），`molt audit` 能从 run + ledger 重算指标。

**not claimed**

- 不声称「通用自提升」「已实现自进化」「统计显著优于基线」——样本量与 seed 都不支持。
- 不声称 bash 之外的工具能力：搜索空间是 6 组（`thinking` × `context`），`tools` 锁定单值 `bash`。
- 不跨 `scenario_version` 比较（当前 `demo-6`）。跨版本数字不可比，也不得放进同一张表。
- 不声称成功率提升或搜索空间最优：10 题已饱和（真实基线 10/10），demo 只是选定的
  high→low 高成本起点；也没有候选比 low/full 更便宜。
- 不声称仓库包含 HTML 页面：本仓库只交付 `web/data` 数据面，页面由队友 C 消费，
  必须按 `demo_group` + `provenance` 展示 1 live + 1 replay + 10 replay。
- 不把 `demo` 的证据表述成「两次 live 运行」：现场闭环只有 1 条 live 候选 + 1 条 replay 基线，
  10 题扫描是回放（`demo_group=scan10`），页面必须按 `runs[].provenance` 与
  `runs[].demo_group` 分开标注。
- 不把 fixture 的 `3/10` 写成真实基线或真实难度。

**已落地（2026-11-11）**

- `demo-6`：10 搜索 + 5 holdout，`bench_hash` / genome / task 全量对齐。
- 第二 shell `svc-config` + deterministic runtime（零成本、manifest 驱动装载）。
- `molt demo`：1 live + 1 replay 闭环、10 题扫描回放、180s 硬上限（ADR-0008）；
  选定 `high/full → low/full` 的真实高→低起点，gate `adopt`（10/10；
  扫描级 cost −72% / wall −61%，gate 级 cps −70.0%，holdout 5/5 → 5/5）。
  仓库只交付 `web/data`，不含 HTML 页面。
- `molt doctor --repo-policy`：只读检查协作文件、冻结面与契约域。

**范围与冗余（ADR-0009）**

- not claimed 包含所有**未实现 / 按需**的冗余项：6 开关 / SoL-Pi、compaction 维度、
  本地 9B/双模型、LLM proposer 主路径、memory/RAG、skill evolving、multi-agent、
  模型维度、escalation/routing、git tag 回滚、HTML 页面。
- 实现原则：只在现有**端口 / adapter / shell** 边界内扩展；不改 core 协议。
  确需改 core 时先写 ADR，并按新 major 走（`schemas/vN/`）。
- 详见 [ADR-0009](docs/decisions/0009-scope-and-redundancy.md)。

## 二、三层与七端口

| 层 | 位置 | 职责 | 禁止 |
| --- | --- | --- | --- |
| core | `molt/core/` | 协议记录、门禁判定（`policy.py`）、存储事务（`store.py`）、哈希、审计、脱敏、提案核心 | import 第三方库；import shell / adapter |
| shell | `shells/<shell_id>/` | TaskProvider / Harness / Verifier；prompt 与工具声明；声明能力与上限 | 定义门禁阈值；写 `scenario.policy`；反向依赖 core 之外的层 |
| adapter | `molt/adapters/` | Runtime（`fixture/`、`pi/`）、LLM proposer transport | 判分、采纳、写证据链 |

七端口（`molt/core/ports.py`）：`TaskProvider`、`Harness`、`Verifier`、`Runtime`、
`Gate`、`Archive`、`Ledger`。前三个 owner 是 shell，`Runtime` 是 adapter，后三个有 core 默认实现。
门禁阈值的唯一 owner 是 `scenario.policy`。

依赖方向单向：`core ← shell`、`core ← adapter`。机械检查：

```bash
uv run python -m molt doctor --deps
```

## 三、证据链与比较域

一次执行产生的 canonical 证据（都在 `state/`，`state/` 不入库）：

```
run → result → gate_decision → archive → snapshot → event → redaction
```

- `run`：单次执行，含 `usage`（tokens / wall_ms / turns / attempts / cost_usd）、
  `outcome`、`ext.runtime`（含 `provenance.kind ∈ {fixture, live, replay}`）。
- `result`：题级聚合；`scope=report` 必须同时含搜索集与 holdout 双分数。
- `gate_decision`：必须带逐题配对表 `paired[]`，判定顺序见 `molt/core/policy.py::decide`。
- `archive` / `snapshot`：代际归档与树快照；采纳是单写者单事务（journal → generation →
  snapshot → archive → pointer）。
- `event`：按 `scope` 分流（`events/search/`、`events/report/`），单写者物化。
- `redaction`：脱敏报告；提交证据前必须过一遍。

审计只做只读重算：从 run/ledger 重算 result，从配对表与 genome 上限重算 gate，
并断言 search 流零 holdout id、report 流至少一条 `holdout.accessed`。

**比较域是 `scenario_version`**（当前 `demo-6`）。每次 run 与 gate decision 带
`shell_version` 与 `verifier_hash`；版本不同即 `incomparable`，禁止比较。
`live` 与 `replay` 必须在 `run.ext` 中如实标注：`live` 需 `model` 或 `pi*` runtime，
`replay` 需 `ext.replay_of_run_id`，否则导出 fail closed。**不得把 fixture 结果标成 live。**

## 四、常用命令

```bash
uv sync --extra dev                                  # 装 dev 依赖（pytest / ruff / jsonschema）
uv run ruff check .                                  # lint（line-length 100）
uv run pytest -q                                     # 311 passed / 2 skipped；pi 标记默认 skip
uv run python -m molt schema validate --all          # 36 条记录通过
uv run python -m molt scenario validate --scenario scenarios/demo/scenario.json  # validate 的 --scenario 是路径
# 注意：scenario validate 的 --scenario 是 scenario.json 路径；
# search/propose/audit/export/demo 的 --scenario 是 id（或 shell_id 别名）。
uv run python -m molt calibrate --scenario demo --min-pass 3   # fixture 基线 solved=3
uv run python -m molt search --runtime fixture       # 搜索集（fixture 确定性、零成本）
uv run python -m molt search --runtime pi            # 真实 Pi：需本地 pi + bwrap，受预算约束
uv run python -m molt propose --scenario demo --state /tmp/molt-state --offline
uv run python -m molt gate --fixture tests/fixtures/gate/   # G1–G8 夹具
uv run python -m molt adopt --state /tmp/molt-state --genome shells/code-repair/genomes/v0.json
uv run python -m molt revert --state /tmp/molt-state --to <archive_id>
uv run python -m molt audit --scenario demo          # 只读重算
uv run python -m molt export --scenario demo --out /tmp/molt-ci-web
uv run python -m molt demo --mode replay --state /tmp/molt-demo --out /tmp/molt-demo-web
uv run python -m molt demo --max-wall 180            # 现场：1 live + 1 replay + 10 题回放
uv run python -m molt doctor --deps                  # core 依赖边界
uv run python -m molt doctor --repo-policy           # 协作文件 / 冻结面 / 契约域自检
uv run python -m molt doctor --recover --state state # 事务兜底恢复（幂等）
```

- 进仓库的命令一律带 `PYTHONDONTWRITEBYTECODE=1`（`uv run` 已隔离 `.venv`，但脚本入口仍需显式设置）。
- `--state` / `--out` 指到仓库之外（`/tmp/...`），不要提交 `runs/`、`state/`、`evidence/`。
- `molt demo` 已实现：`--mode live` 跑 1 条真实 Pi 候选，`--mode replay` 全 fixture/replay
  （CI 用）；证据里恰好 1 条 live（live 模式）或 0 条（replay 模式），`scan10` 是回放组。
- `molt doctor --repo-policy` 已实现（B2c-2）：检查 `AGENTS.md` / `CONTRIBUTING.md` /
  `.github/**` / 冻结面 / 契约域，缺失退出 1。

## 五、所有权（CODEOWNERS 的语义来源）

| 域 | owner | 范围 |
| --- | --- | --- |
| 契约域 | @FeiSaMa | `molt/core/**`、`schemas/**`、`docs/protocol.md`、`docs/decisions/**`（ADR） |
| 场景域 | @By-Diamond | `shells/**`、`bench/**`、verifier 语义、题集与 holdout |
| 集成域 | @dong856 | `molt/adapters/**`、`web/**`、`.github/**`、CI、非契约文档 |

可按领域调整，但调整必须同时改 `.github/CODEOWNERS` 与本表，并走一次 PR。

评审矩阵：

| 变更面 | 最少评审 |
| --- | --- |
| 契约域（`schemas/**`、`molt/core/ports.py`、`docs/protocol.md`、ADR） | **2 人** + ADR |
| 其余（shell、bench、adapter、docs、CI） | 1 人 |

## 六、冻结面与版本触发

**冻结面**（任一变更必须升 `scenario_version` 并重跑基线）：

```
shells/<id>/harness.py
shells/<id>/verifier.py
shells/<id>/task_provider.py
shells/<id>/switches.schema.json
bench/**
```

理由见 [ADR-0004](docs/decisions/0004-shell-and-dims.md)：这些文件直接决定题面、判分与
搜索空间的形状，改动即破坏可比性。跨版本比较一律禁止。

**不在冻结面内**（只产生新 `genome_id`，不升 `scenario_version`）：

```
shells/<id>/genomes/*.json     # 新增候选 genome
shells/<id>/prompts/*          # 新增 prompt 候选
shells/<id>/tools/*.json       # 新增工具声明候选
```

注意边界：**新增文件**是候选；**原地修改已被既有 genome 引用的文件**是破坏性变更，
按冻结面处理。CODEOWNERS 因此只锁 `/shells/` 与 `/bench/` 的整体 owner，
**不为 `genomes/`、`prompts/`、`tools/` 单独加锁**，避免候选迭代被拖死。

### `web/data` 契约冻结（前端）

`web/data/` 是引擎与页面的唯一接口；页面由前端 owner 维护，破坏性变更会让页面静默出错。

**冻结三层**：

- **Tier 1（冻结）**：`index.json`、`scenario.json`、`runs.json`、`gate.json`、`variants.json` 的字段名、类型、可空性与取值域。只允许新增字段；改名、删字段、改语义一律禁止。
- **Tier 2（内部，页面不得依赖）**：`redaction.json`、`events/*.jsonl`、`bundle.js`、各文件的 `ext` 子对象。随时可变。
- **Tier 3（聚合）**：`search_results.json`、`report_results.json` 只保证 `solved` / `total` / `cost_total_usd` / `cost_per_solved_usd` / `invalid_ratio`。

**语义不变量**：

- `runs[].provenance.kind ∈ {fixture, live, replay}`；`live` 仅指真实模型调用；`replay` 必须带 `replay_of_run_id`；`fixture` 不得标成 `live`。
- `runs[].demo_group ∈ {closure, scan10, candidate-scan}`；`null` 表示未分组。
- `gate.decisions[].verdict ∈ {adopt, reject, incomparable, unstable}`。
- `holdout.detail` 固定 `"not-exported"`；页面不得展示 holdout 逐题。
- `index.revision` 是刷新信号，不是兼容性信号；不得用它做缓存键。

**版本与流程**：

- `index.schema_version` 当前 `1.0.0`。Tier 1 的破坏性变更必须升 major、写 ADR、在 PR 描述里列出字段差异，并提前通知前端 owner。
- 新增字段按 minor 处理；页面必须忽略未知键。
- probe 数据永不进入 `web/data`；隔离靠 state 白名单（`--state /tmp/molt-probe/...`），导出层不承担过滤职责（`molt/export.py` 不过滤 probe 记录）。

## 七、禁止清单

- 直推 `main`；`git push --force`（含 `--force-with-lease`）到 `main`。
- `molt/core/**` 反向依赖 `molt/adapters/**` 或 `shells/**`（`doctor --deps` 会拦）。
- 绕过 holdout 隔离：把 holdout 题放进 search 流、在 search 事件里出现 holdout id。
- 提交 `runs/` 原始日志、`state/`、`evidence/`、`.env`、`.pi/`。
- 全局 `pip install`（Arch 系统 Python 会被污染）；一律 `uv`。
- Pi 调用加 `--no-session`、`--no-tools`（argv 契约见 ADR-0006，`assert_required_flags` 会拦）。
- 手改 `web/data/**`（导出产物，必须由 `molt export` 生成）。
- 把 `fixture` 结果标成 `live`，或把 `live` 结果写成 `fixture`。
- 跨 `scenario_version` 比较，或把小样本描述性结论写成统计显著。

## 八、契约变更流程

1. 先在 `docs/decisions/` 落 ADR（新增 `000N-*.md`，格式对齐现有 9 篇）。
2. 改 `schemas/v1/**`（major 变更才新开 `schemas/v2/`），再跑 `molt schema docs` 生成
   `docs/protocol.md`，然后 `molt schema validate --all` +
   `molt scenario validate --scenario scenarios/demo/scenario.json`。
   （`scenario validate` 的 `--scenario` 是路径；其它命令的 `--scenario` 是 id。）
3. PR 需要 **2 人 approve**，其中至少 1 人来自 @FeiSaMa；`main` 由分支保护挡住直推与 force push。
   当前仓库有**两个 active ruleset**（`main-protection` 与旧的 `protect-main`），求并集、严格项为准；
   旧 ruleset 是否删除由用户决定。
4. **冻结窗口：2026-11-12 18:00（UTC+8）之后**，契约只允许 `ext` 新增或新增记录类型；
   顶层字段改名 / 删除 / 语义变更一律推迟到窗口结束后的新批次。

## 九、DoD（完成定义）

一次变更合并前必须满足：

- [ ] 测试：`uv run pytest -q` 全绿（新增行为有对应用例）。
- [ ] Schema：`molt schema validate --all` 与
      `molt scenario validate --scenario scenarios/demo/scenario.json` 通过；
      `docs/protocol.md` 无手改漂移。
- [ ] 零写入：命令跑完仓库树不变（`tests/test_zero_write.py` + `git status --porcelain` 为空）。
- [ ] 证据可复算：涉及 run/result 的变更能在 `molt audit` 下重算出同样数字。
- [ ] replay / live 标注：`run.ext.provenance.kind` 与实际一致，`replay_of_run_id` 存在。
- [ ] claim 口径：措辞符合 §一（未观察到下降 + 单题成本不升，附配对表）。

## 十、历史教训（外部，摘要）

来自 feisama-base 的 lessons（本仓库不复制全文）：

1. **零写入验收**不能用 `git status` 或裸跑 `pytest`：pytest 自身会写 `__pycache__`，
   必须 `PYTHONDONTWRITEBYTECODE=1` + 文件系统树快照（见 `tests/test_zero_write.py`）。
2. **argparse** 拒绝以单连字符开头的位置参数；传 `-` 开头的 path 要么加 `--`，
   要么在 `parse_args` 前重排 argv。
3. **uv 大 wheel 下载失败**多半是代理链路问题，不是包本身；大包走国内镜像，
   多索引场景下 `--extra-index-url` 可能装到错误构建，优先用 lock 文件。

## 十一、相关文档

- [CONTRIBUTING.md](CONTRIBUTING.md)：分支、PR、评审、schema 变更步骤。
- [docs/style.md](docs/style.md)：代码 / JSON / Markdown / 时间 / 路径 / claim 语言。
- [docs/architecture.md](docs/architecture.md)：三层、七端口、数据流、事务。
- [docs/protocol.md](docs/protocol.md)：11 类记录字段表（生成物）。
- [docs/decisions/](docs/decisions/)：ADR 0001–0009。
