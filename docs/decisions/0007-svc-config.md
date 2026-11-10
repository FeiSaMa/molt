# ADR 0007：第二 shell `svc-config` 与确定性 Runtime

- 状态：已决定
- 日期：2026-11-10
- 关联：`shells/svc-config/`、`molt/adapters/deterministic/runtime.py`、`molt/shell_bundle.py`、
  `scenarios/svc-config/`、`tests/test_adapter_conformance.py`

## 背景

B2c-2 要证明 shell 端口可迁移：同一 core、同一 CLI、同一 evidence 格式，换一个任务域与
Runtime 后仍能跑通 search → gate → audit。改造前有两处硬编码：

1. `molt/demo.py` 的 `build_engine` 写死 `shells/code-repair/{task_provider,harness,verifier}.py`，
   `manifest.entrypoints` 只被 schema 校验、从未被消费；
2. `molt/cli.py` 的 `search` / `audit` 用 `args.scenario != "demo"` 直接拒绝其它 scenario。

## 决定

1. **第二个 shell 是 `svc-config`**：配置文件修复，2 道 search + 1 道 holdout，
   `scenario_version=svc-1`、`shell_version=1.0.0`。任务与场景独立于 demo，不修改
   `bench/search/smoke-*`、`bench/holdout/holdout-even` 与 `scenarios/demo`。
2. **entrypoints 被真正消费**：`build_engine` 读 `scenario.shell_id` → `shells/<id>/manifest.json`
   → `entrypoints.{harness,verifier,task_provider}` 按路径加载模块。实例化是固定 ABI：
   - 模块提供 `build_<role>(...)` 工厂时优先调用；
   - 否则在模块自身命名空间里按后缀找唯一实现类：`*TaskProvider` / `*Verifier` / `*Harness`
     （从 core 导入的同名 Protocol 因 `__module__` 不同被排除）；
   - provider / verifier 签名 `cls(repo_root)`；harness 签名
     `cls(runtime, verifier, repo_root, *, shell_id, shell_version, clock, domains, llm_proposer)`。
3. **deterministic Runtime**：`molt/adapters/deterministic/runtime.py` 只执行题面声明的
   `task.ext.deterministic.edits`（`json_set` / `text_replace`），支持 `when` 偏映射匹配
   genome switches；**零 LLM、零网络、零成本**：`usage` 全 0，`diagnostic.cost_source="none"`。
   路径逃逸、目标文件/文本缺失、非法指针都映射为 run 的 `error` 终态，不崩溃。
4. **switches 复用 v1 三字段，不升 core major**：`thinking ∈ {low, high}`、
   `context ∈ {full, minimal}`、`tools=["bash"]`，共 4 组，`locked: true`。语义限定：
   `thinking` 是修复步序上限，`context` 是允许读取的配置范围；它们只作为题面 `when`
   条件的匹配输入，不引入任何智能选择。`Genome.switches` 的 v1 协议不变。
5. **Verifier 是外部校验器 + 黄金 diff**：运行题目 `tests_ref` 下的 `validate*.py`
   （stdlib only，工作区之外），全 0 退出、未超时、且 `bench/**` 树哈希前后一致才算 passed。
   `guard` / `verify(before=...)` 与 code-repair 同签名，成本恒为 0。
6. **零成本是硬约束**：scenario `policy.budget` 两个 USD cap 都为 0；svc-config harness 拒绝
   注入 `llm_proposer`；acceptance 断言 `usage` 全 0 且 `ext.cost_source="none"`。
   本 shell 不声称任何智能增益，跨 shell 分数不可比较（不同 `scenario_version`）。
7. **CLI 泛化**：`search` / `propose` / `audit` / `export` 的 `--scenario` 接受 scenario id；
   `--shell` 是同一参数的别名，先按 `scenarios/<name>/scenario.json` 解析，再按
   `scenarios/*/scenario.json` 的 `shell_id` 反查。`--runtime` 增加 `deterministic`；
   `--genome` 缺省时取该 scenario 的 `v0.json`（版本/ shell 不匹配则拒绝）。
8. **schema 覆盖新 shell**：`RECORD_ARTIFACTS` 改为 glob `shells/*/manifest.json`，
   `schema validate --all` 同时校验两个 manifest；`scenario validate` 补 switches.schema
   完整性（维度集合、值域 ⊆ v1 且 ⊆ manifest 候选域、`combinations` 等于笛卡尔积、`locked`）
   与 genome switches 值域校验。

## 后果

- 新增 shell 不再需要改 core 或 CLI 硬编码：manifest + 三 entrypoints + genome + scenario 即可。
- `tests/test_adapter_conformance.py` 对「两 shell × 两 runtime」做协议与端到端检查；
  `molt search --shell svc-config --runtime deterministic` 秒级完成且成本 0；
  `molt audit --scenario svc-config` 与 `--scenario demo` 都能重算通过。
- 证据格式、七端口数量、`policy.decide` 顺序与 v1 `tools=bash` 单值协议均未改变。
- 不实现 `molt demo`、HTML 报告与正式 10+5 题集；那些仍属于后续批次。
