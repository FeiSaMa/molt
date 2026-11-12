# ADR 0010：probe bench 例外 —— `bench/probe/**` 只新增

- 状态：**已决定（2026-11-11，owner @FeiSaMa 批准）**
- 日期：2026-11-11
- 关联：`AGENTS.md` §六、`molt/cli.py` 的 `REPO_POLICY_FROZEN`、`molt/bench.py`、
  `scenarios/probe/scenario.json`、`probes/pilot-commands.json`

## 背景

`AGENTS.md` §六把 `bench/**` 列为冻结面：任一变更必须升 `scenario_version` 并重跑基线。
Batch 1 需要新增 `bench/probe/**`（probe 题集），但**不修改** demo 的
`bench/search/**`、`bench/holdout/**` 或 `scenarios/demo/scenario.json`。
本 ADR 申请一个窄例外：只新增 `bench/probe/**`，不改变既有题集的比较域。

## 决定

1. **只新增**：`bench/probe/**` 只允许新增文件；不得原地修改 `bench/search/**`、
   `bench/holdout/**`、`scenarios/demo/scenario.json` 或 demo 的 state。
2. **demo 引用题哈希不变**：`compute_bench_hash(demo)` 在 HEAD `d585d2f` 与当前工作区
   均为 `9d73c02090cfdf21f13e64020bc46b388a6ae8d373b2eae1f656e7c63ba41d81`。
   复算命令：

   ```bash
   uv run python - <<'PY'
   import json
   from pathlib import Path
   from molt.bench import compute_bench_hash
   root = Path(".")
   scenario = json.loads((root / "scenarios/demo/scenario.json").read_text())
   tasks = scenario["task_sets"]["search"] + scenario["task_sets"]["report"]
   print(compute_bench_hash(root, tasks))
   PY
   ```

   输出必须等于 `scenarios/demo/scenario.json:bench_hash`（`9d73c020…`）。
3. **`bench_tree_hash` 已变**：HEAD `d585d2f` 为
   `d0fc43b4db3b8f09f2bd5a370553e75dbdd3001cc983ea546c198c0974363168`；加入
   `bench/probe/**` 后为
   `2b167c22b2267d7dedd482556d3179defb3ec9883a4bb2e1b1abd9839901d3fa`；
   `probe-5` 给 T2 增加判别用例并重算题集哈希后为
   `9f49cbc9203bee8b6e672b92029f5ce479d75c2e80c3ab506e6fb1e86daecc22`；
   `probe-6` 换成 baseline 必败的 `["aaa", "bb", "bb"]` 判别用例后为
   `533b76d76783f63f8bfa5cd351af4db35330e74a6dd88ee5082e2ebbd17e63f7`。
   它是 **verifier 的运行期 guard 指纹**：每次 run 的
   `verification.tests_hash_before/after` 会持久化为证据，但**不作为跨 run 比较键**，
   也不得用于跨 `scenario_version` 比较。它变化只表示 bench 树新增了未引用题或
   probe 题面变更，不代表 demo 题面或判分语义变化。
4. **`doctor --repo-policy` 不是门**：`molt/cli.py:996-1023` 的
   `REPO_POLICY_FROZEN` 只检查冻结面路径**存在性**（`root.glob(...)`），不查内容或哈希。
   真正的门是 `molt scenario validate`（`compute_bench_hash` 比对）与
   `molt genome verify`（artifact 哈希比对）；不得把 `doctor` 通过当成冻结面未被修改。
5. **例外批准**：本 ADR 已由仓库所有者（`@FeiSaMa`，契约域 owner）批准
   （2026-11-11），例外生效；仅覆盖 `bench/probe/**` 的只新增场景，不得进入
   demo 的比较域或对外 claim。
6. **隔离**：probe 命令必须显式 `--state /tmp/molt-probe/<batch>/<cell>`，不得写 demo state。导出层不承担隔离职责（`molt/export.py` 不过滤 probe 记录）；`tests/test_probe_export_isolation.py` 验证的是「probe state 与 demo state 不相交」这一不变量，不是导出过滤。

## 后果

- `scenarios/probe/scenario.json` 使用独立 `scenario_version=probe-2`（当前 `probe-6`）；demo 保持 `demo-6`。
- 新增 probe 题不得改变 demo 的 `bench_hash`；任何对 demo 题的修改仍需按 §六升版本并重跑基线。
- `bench_tree_hash` 的新值写入证据时，必须与 run 的时间点一起记录，不能拿旧 run 的哈希
  与新树哈希直接比较。
- 例外只覆盖 `bench/probe/**` 的**新增**；一旦需要修改 probe 内已发布的题面/测试，
  按 probe 自己的 `scenario_version` 升版本处理。

## 待办

- [x] 仓库所有者批准本 ADR（2026-11-11，@FeiSaMa）。
- [ ] 在 PR 描述中引用本 ADR（PR 未创建）。
