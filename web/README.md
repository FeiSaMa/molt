# web/data —— 给页面用的只读数据面

本目录由 `molt export` 从 `state/` 生成，**只含数据，不含页面代码**。页面（HTML/CSS/JS）
由后续批次实现；`file://` 直开时读 `bundle.js` 里的 `window.MOLT_VIEW`，走 HTTP 时读同名 JSON。

## 文件与字段来源

| 文件 | 内容 | 来源 |
|---|---|---|
| `index.json` | `schema_version` / `scenario_id` / `revision` / `generated_at` / `files`（每文件 sha256+字节数）/ `counts` | 导出时计算；`revision` 是全部数据文件 sha256 的 `content_hash` |
| `scenario.json` | `claim` / `not_claimed` / `policy` / `budget` / `task_sets` / `task_counts` / shell 与 isolation 信息 | `scenario` 记录（`scenarios/<id>/scenario.json`），claim 文案是 `molt/export.py` 常量 |
| `variants.json` | `id` / `parent` / `switches` / `runtime` / `adopted` / `budget` | `state/genomes/*.json` + `state/archive/*.json` |
| `runs.json` | `run_id` / `genome_id` / `task_id` / `scope` / `attempt` / `solved` / `status` / `cost_usd` / `tokens{in,out}` / `wall_ms` / `verifier` / `provenance` | `state/runs/*.json`（逐 run 明细） |
| `search_results.json` | 仅 `scope=search` 的聚合分 `{solved,total,pass_at_1_first_attempt,cost_total_usd,cost_per_solved_usd,invalid_ratio}` | `state/results/*.json` |
| `report_results.json` | `scope=report`，`scores.search` + `scores.holdout` 两组**聚合分** | `state/results/*.json` |
| `gate.json` | `policy_sha256`、每条 decision 的 `verdict` / `reason` / `reasons` / `budget_check` / `scores` / `pairs[]`（逐题配对 + `passed_delta` / `cost_delta_usd`）/ `deltas` | `state/decisions/*.json` |
| `proposals.json` | proposal 顶层字段 + `ext`（`source` / `fallback` / `cost_source` / `usage` / `prompt_sha256` / `response_sha256`） | `state/proposals/*.json` |
| `archive.json` | `lineage[]`（genome 谱系 + 是否被采纳）与 `archives[]` | `state/archive/*.json` |
| `snapshots.json` | `snapshot_id` / `generation` / `roots` / `tree_hash` / `file_count`；无快照时为空数组 | `state/snapshots/*.json` |
| `audit.json` | `{ok, checked:{runs,results,decisions,events,report_events}, mismatches[]}` | `molt.core.audit.audit_state` 现场重算 |
| `redaction.json` | 脱敏 manifest（`redaction_version` / `rules` / `pre_metrics` / `post_metrics` / 哈希）；无 manifest 时该文件内容为 `null` | `state/redaction/<scenario_id>.json` |
| `events/search.jsonl` | search 流事件，逐行一个 `event` 记录，按 `(ts, event_id)` 排序 | `state/events/search/*.jsonl`；无 per-run 目录时回退读 `state/events/search.jsonl` |
| `events/report.jsonl` | report 流事件（含 `holdout.accessed` / `report.scored`） | 同上，`report` 流 |
| `bundle.js` | `window.MOLT_VIEW = {scenario,variants,runs,search_results,report_results,gate,proposals,archive,snapshots,audit,redaction,events}` | 上述 payload 的合并，供 `file://` 直开 |

## 聚合 vs 明细（aggregate vs detail；页面务必注意）

- **聚合**：`search_results.json`、`report_results.json` 的分数块；`gate.json` 的
  `scores` / `deltas`。`holdout` **只导出聚合分**，不导出逐题明细，也不导出 `run_refs`。
- **明细**：`runs.json`（逐 run）、`gate.json` 的 `pairs[]`（逐题配对，仅 search 题）、
  `events/*.jsonl`（逐事件）、`proposals.json` 的 `ext`。
- 不要用 `runs.json` 里的 report run 反推 holdout 逐题通过率并展示；页面口径以
  `report_results.json` 的聚合块为准。

## provenance：fixture / live / replay

`runs.json[].provenance` 区分三档，**fixture 不是 live**：

| kind | 含义 | 判定 |
|---|---|---|
| `fixture` | B1 确定性 fixture runtime，无 live 模型调用 | 默认；genome `runtime.runtime_id` 非 `pi*`/`live*` |
| `live` | 真实模型（Pi adapter）运行 | run `ext.provenance.kind="live"`，或 genome runtime id 以 `pi`/`live` 开头 |
| `replay` | 对某次 run 的回放 | run `ext.replay_of_run_id` 或 `ext.provenance.kind="replay"`，带 `replay_of_run_id` |

每个 provenance 都有布尔 `live`；`fixture` 额外带 `note` 说明「不是 live 模型调用」。
页面应在 UI 上显式标注，禁止把 fixture 展示成 live。

## 重新生成

```bash
# 无 state：临时跑一遍 demo 证据链，只写 --out，不污染仓库
molt export --scenario demo --out web/data

# 有现成 state：直接读
molt export --scenario demo --state state --out web/data
```

- 缺省 `--state` 时：若 `./state/runs` 存在则读它，否则用临时 demo state。
- 输出口径：`json.dumps(ensure_ascii=False, sort_keys=True, indent=2)`；JSONL 用 canonical 单行 JSON。
- 生成物里的本机绝对路径会被替换为 `<repo>` / `<state>` / `<home>`（复用现有脱敏思路）。
- `revision` 标识**本次输入内容**；fixture demo 的 `run_id` 含随机后缀、`started_at` 取真实时钟，
  所以重新跑 demo 会得到新的 revision —— 只保证指标一致，不承诺字节级复现（ADR-0003）。
- 本目录的样例来自 `molt export --scenario demo --out web/data`，可直接从 git 消费；
  重新生成后 `audit.json` / `index.json` / `revision` 会随输入变化，属正常现象。

## 约束

- 本目录只读；页面代码不要写进 `web/data/`。
- `web/**` 在 `molt.core.isolation.DEFAULT_DENY_PATHS` 中，agent 侧不读 `web/`；
  需要数据时走导出命令重新生成。
