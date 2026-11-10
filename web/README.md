# web/data —— 给页面用的只读数据面

本目录由 `molt export` 从 `state/` 生成，**只含数据，不含页面代码**。页面（HTML/CSS/JS）
由后续批次实现；`file://` 直开时读 `bundle.js` 里的 `window.MOLT_VIEW`，走 HTTP 时读同名 JSON。

## 文件与字段来源

| 文件 | 内容 | 来源 |
|---|---|---|
| `index.json` | `schema_version` / `scenario_id` / `revision` / `generated_at` / `files`（每文件 sha256+字节数）/ `counts` | 导出时计算；`revision` 是全部数据文件 sha256 的 `content_hash` |
| `scenario.json` | `claim` / `not_claimed` / `policy` / `budget` / `task_counts` / `holdout{task_count,detail}` / `provenance_counts{fixture,live,replay}` / shell 与 isolation 信息；只列 search 题 id | `scenario` 记录 + 当前 search run；`claim.live_model_calls` 由证据中是否存在 `kind=live` 决定，不是常量 |
| `variants.json` | `id` / `parent` / `switches` / `runtime` / `adopted` / `budget` | `state/genomes/*.json` + `state/archive/*.json` |
| `runs.json` | **仅 `scope=search`** 的逐 run：`run_id` / `genome_id` / `task_id` / `scope` / `attempt` / `solved` / `status` / `cost_usd` / `tokens{in,out}` / `wall_ms` / `verifier` / `provenance` / `demo_group` | `state/runs/*.json`；report run 整行不导出（见 `report_runs`） |
| `search_results.json` | 仅 `scope=search` 的聚合分 `{solved,total,pass_at_1_first_attempt,cost_total_usd,cost_per_solved_usd,invalid_ratio}` | `state/results/*.json` |
| `report_results.json` | `scope=report`：`scores.search` + `scores.holdout` 两组**聚合分**；每个 result 带 `report_runs`，顶层带 `report_runs_total`（`count` / `solved` / `by_status` / `cost_usd` / `wall_ms` / `tokens`） | `state/results/*.json` + `state/runs/*.json`（report 段只做聚合） |
| `gate.json` | `policy_sha256`、每条 decision 的 `verdict` / `reason` / `reasons` / `budget_check` / `scores` / `pairs[]`（逐题配对 + `passed_delta` / `cost_delta_usd`）/ `deltas` | `state/decisions/*.json` |
| `proposals.json` | proposal 顶层字段 + `ext`（`source` / `fallback` / `cost_source` / `usage` / `prompt_sha256` / `response_sha256`） | `state/proposals/*.json` |
| `archive.json` | `lineage[]`（genome 谱系 + 是否被采纳）与 `archives[]` | `state/archive/*.json` |
| `snapshots.json` | `snapshot_id` / `generation` / `roots` / `tree_hash` / `file_count`；无快照时为空数组 | `state/snapshots/*.json` |
| `audit.json` | `{ok, checked:{runs,results,decisions,events,report_events}, mismatches[]}` | `molt.core.audit.audit_state` 现场重算 |
| `redaction.json` | 脱敏 manifest（`redaction_version` / `rules` / `pre_metrics` / `post_metrics` / 哈希）；无 manifest 时该文件内容为 `null` | `state/redaction/<scenario_id>.json` |
| `events/search.jsonl` | search 流事件，逐行一个 `event` 记录，按 `(ts, event_id)` 排序 | `state/events/search/*.jsonl`；无 per-run 目录时回退读 `state/events/search.jsonl` |
| `events/report.jsonl` | report 流**聚合事件**（`holdout.accessed` / `report.scored`）；`holdout.accessed` 只保留 `count`，不含 `task_ids`；per-run 事件不导出 | 同上，`report` 流；导出层剥离 run 明细与 task id |
| `bundle.js` | `window.MOLT_VIEW = {scenario,variants,runs,search_results,report_results,gate,proposals,archive,snapshots,audit,redaction,events}` | 上述 payload 的合并，供 `file://` 直开 |

## 聚合 vs 明细（aggregate vs detail；页面务必注意）

- **聚合**：`search_results.json`、`report_results.json` 的分数块与 `report_runs`；
  `gate.json` 的 `scores` / `deltas`。`holdout` **只导出聚合分**，不导出逐题明细，
  不导出 `run_refs`、`task_id`、`run_id`。
- **明细**：`runs.json`（仅 search 逐 run）、`gate.json` 的 `pairs[]`（逐题配对，仅 search 题）、
  `events/search.jsonl`（逐事件）、`proposals.json` 的 `ext`。
- **holdout 明细不导出**：`runs.json` 不含 `scope=report` 的 run，`events/report.jsonl`
  不含 per-run 事件与 holdout task id，`scenario.json` 只给题数。页面展示 holdout
  成本/通过情况时用 `report_results.json` 的 `report_runs` 聚合块。

## provenance：fixture / live / replay

`runs.json[].provenance` 区分三档，**fixture 不是 live**：

| kind | 含义 | 判定 |
|---|---|---|
| `fixture` | B1 确定性 fixture runtime，无 live 模型调用 | 默认；genome `runtime.runtime_id` 非 `pi*`/`live*` |
| `live` | 真实模型（Pi adapter）运行 | run `ext.provenance.kind="live"` 且带 `model` 或 `pi*` runtime |
| `replay` | 对某次 run 的回放 | run `ext.provenance.kind="replay"` 或 ext 带 `replay_of_run_id`，必须有 `replay_of_run_id` |

fail closed：`kind="live"` 同时带 `replay_of_run_id`、`kind="live"` 无 `model`/`pi*` 证据、
`kind="replay"` 无来源，`molt export` 都直接报错（非零退出），不会静默标 live。

每个 provenance 都有布尔 `live`；`fixture` 额外带 `note` 说明「不是 live 模型调用」。
页面应在 UI 上显式标注，禁止把 fixture 展示成 live。

## demo 证据分组（`runs[].demo_group`）

`molt demo` 的现场闭环与 10 题扫描**必须分开标注**（ADR-0008）：

| `demo_group` | 内容 | provenance |
|---|---|---|
| `closure` | 1 条 replay 基线 + 1 条同题候选 | baseline=`replay`（带 `replay_of_run_id`）；live 模式候选=`live` |
| `scan10` | 10 题扫描的回放（每题的基线 run 各复制一次） | `replay`，每条带 `replay_of_run_id` |
| `candidate-scan` | 候选在其余 9 题上的 fixture 执行（只为 10 题分母） | `fixture`，不是 live |
| `null` | 普通 `search`/`export` 证据，无 demo 分组 | 按 genome runtime 推断 |

- 「1 live + 1 replay」只指 `closure`；`scan10` 是**扫描回放**，不是 demo 的第二次运行。
- live 模式下全文件恰好 1 条 `kind=live`；`--mode replay` 下恰 0 条，closure 候选标 `fixture`。
- 页面讲法：closure 讲「基线回放 → 提案 → 真实候选 → 门禁」，scan10 讲「此前 10 题扫描的回放」。
- 当前提交的样例来自真实 Pi 扫描：baseline 10/10、候选 10/10 但单位成本更高，
  `gate.json` 结论为 `reject / cost_not_lower`，`archive.json` 记录回滚；这是负结果，
  页面不得改写成「已采纳」。

## 重新生成

```bash
# 现场闭环（live：1 条真实 Pi 候选；同时导出 web/data）
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m molt demo --max-wall 180

# CI / 无 Pi：全 fixture/replay，证据里 0 live
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m molt demo --mode replay --state /tmp/molt-demo --out web/data

# 无 state：临时跑一遍 demo 证据链，只写 --out，不污染仓库
molt export --scenario demo --out web/data

# 有现成 state：直接读
molt export --scenario demo --state state/demo --out web/data
```

- 缺省 `--state` 时：若 `./state/runs` 存在则读它，否则用临时 demo state。
- 显式 `--state` 且 0 run：退出码 2 并提示；确需空数据面时加 `--allow-empty`。
- 输出口径：`json.dumps(ensure_ascii=False, sort_keys=True, indent=2)`；JSONL 用 canonical 单行 JSON。
- 生成物里的本机绝对路径会被替换为 `<repo>` / `<state>` / `<home>`（复用现有脱敏思路）。
- `revision` 标识**本次输入内容**；fixture demo 的 `run_id` 含随机后缀、`started_at` 取真实时钟，
  所以重新跑 demo 会得到新的 revision —— 只保证指标一致，不承诺字节级复现（ADR-0003）。
- 本目录的样例来自 `molt demo`（`demo-6`：10 search + 5 holdout，closure + scan10 分组），
  可直接从 git 消费；重新生成后 `audit.json` / `index.json` / `revision` 会随输入变化，属正常现象。

## 约束

- 本目录只读；页面代码不要写进 `web/data/`。
- `web/**` 在 `molt.core.isolation.DEFAULT_DENY_PATHS` 中，agent 侧不读 `web/`；
  需要数据时走导出命令重新生成。
- B2b 的 agent 只启用 sandboxed bash（read/write/edit 不启用）；bash 无网络且看不到
  `bench/`、`web/`、`evidence/`。搜索空间为 6 组（thinking × context，tools 锁定 bash）。
