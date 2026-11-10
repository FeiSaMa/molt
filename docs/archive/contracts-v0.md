# 数据契约

两个落盘文件：`run.json`（单题一次运行）与 `result.json`（一个 config 版本跑完整题集的汇总）。字段名固定，新增字段必须标为可选并同步本文档。

## run.json

路径：`runs/<config_version>/<run_id>/run.json`；同名 `log.txt` 存子进程完整输出。

| 字段 | 类型 | 必需 | 含义 |
| --- | --- | --- | --- |
| `run_id` | string | 是 | 单次运行唯一 id，建议 `<config_version>-<task_id>-<n>` |
| `config_version` | string | 是 | 当次使用的开关版本，与 `Switches.config_version` 一致 |
| `task_id` | string | 是 | 题目 id，来自 `bench/<suite>/` 的文件名 |
| `suite` | string | 是 | 题集：`fixed` / `family` / `holdout` |
| `status` | string | 是 | `ok` / `fail` / `timeout` / `error` |
| `score` | number | 是 | 单题得分，0.0 或 1.0（多断言时取通过比例） |
| `tokens` | integer | 是 | 本次运行模型 token 总量（输入+输出） |
| `wall_ms` | integer | 是 | 墙钟耗时，毫秒 |
| `log_path` | string | 是 | 子进程日志路径，相对仓库根 |
| `started_at` | string | 否 | ISO 8601 起始时间 |
| `switches` | object | 否 | 当次实际生效的开关快照，便于复现 |
| `attempts` | integer | 否 | 含升级重试在内的执行次数 |
| `eval_guard` | object | 是 | 评测器完整性校验，见下三行 |
| `eval_guard.tests_hash_before` | string | 是 | 运行前测试目录的内容哈希 |
| `eval_guard.tests_hash_after` | string | 是 | 运行后测试目录的内容哈希 |
| `eval_guard.matches` | bool | 是 | 前后哈希是否一致；`false` 表示该次运行无效 |
| `error` | string | 否 | `status` 非 `ok` 时的简短原因 |

## result.json

路径：`runs/<config_version>/result.json`，由同一版本全部 run 汇总。

| 字段 | 类型 | 必需 | 含义 |
| --- | --- | --- | --- |
| `config_version` | string | 是 | 汇总对应的开关版本 |
| `suite` | string | 是 | 汇总所用题集 |
| `success_rate` | number | 是 | 通过题数 / 总题数，范围 0.0–1.0 |
| `tokens_total` | integer | 是 | 全部 run 的 `tokens` 之和 |
| `wall_ms_total` | integer | 是 | 全部 run 的 `wall_ms` 之和（并发下为实际墙钟） |
| `regressions` | array[string] | 是 | 相对上一版回归的 task_id 列表；空数组表示无回归 |
| `scores` | object | 是 | 双分数：搜索集与留出集各一组，见下六行 |
| `scores.search_set.success` | number | 是 | 固定集成功率，参与搜索与回归门禁 |
| `scores.search_set.tokens` | integer | 是 | 固定集 token 总量 |
| `scores.search_set.wall_ms` | integer | 是 | 固定集墙钟耗时 |
| `scores.holdout.success` | number | 是 | 留出集成功率，只作汇报，不参与搜索 |
| `scores.holdout.tokens` | integer | 是 | 留出集 token 总量 |
| `scores.holdout.wall_ms` | integer | 是 | 留出集墙钟耗时 |
| `guard_violations` | integer | 是 | 因哈希不一致被判废的 run 次数 |
| `task_count` | integer | 否 | 参与汇总的题数 |
| `passed_count` | integer | 否 | 通过题数 |
| `prev_config_version` | string | 否 | 对比基线版本，无则省略 |
| `gate_passed` | boolean | 否 | 回归门禁结论 |
| `finished_at` | string | 否 | ISO 8601 结束时间 |

## 约束

- `regressions` 非空 ⇒ `gate_passed` 必为 `false` ⇒ 该版本不得成为新基线。
- `eval_guard.matches = false` ⇒ 该次 run 无效，不计入任何分数，只累加 `guard_violations`。
- 缺失必需字段视为脏数据，`score` 直接报错，不做默认值填充。
- 字段改名视为破坏性变更，需同时改 `docs/`、`molt/store.py` 与已有 runs/ 的迁移脚本。

## archive.json

路径：`runs/archive.json`，一行一条候选，记录被搜索过的一切（含未采纳的）。

| 字段 | 类型 | 必需 | 含义 |
| --- | --- | --- | --- |
| `variant` | string | 是 | 候选标识，如 `v7` |
| `parent` | string | 是 | 父代 `variant`；基线的父代为 `v0` 自身或空串 |
| `scores` | object | 是 | 与 `result.json` 的 `scores` 同构（`search_set` / `holdout` 各含 `success` / `tokens` / `wall_ms`） |
| `adopted` | bool | 是 | 是否被采纳为新基线 |
| `run_dir` | string | 是 | 该候选的 runs 目录，相对仓库根 |
| `note` | string | 是 | 一句话说明这条候选改了什么 |

## snapshot.json

路径：`runs/snapshots/<snapshot_id>/snapshot.json`，一次回退事务的清单。

| 字段 | 类型 | 必需 | 含义 |
| --- | --- | --- | --- |
| `snapshot_id` | string | 是 | 快照唯一 id |
| `created_at` | string | 是 | ISO 8601 创建时间 |
| `config_version` | string | 是 | 快照对应的开关版本 |
| `paths` | object | 是 | 三处路径：`configs/`、`tools/`、`prompts/` |
| `hashes` | object | 是 | 与 `paths` 一一对应的内容哈希 |
| `reverted_at` | string \| null | 是 | 回退时间；未回退为 `null` |

`paths` 与 `hashes` 必须同键，缺任一键即视为快照损坏，`revert` 拒绝执行——三样必须一起回滚。
