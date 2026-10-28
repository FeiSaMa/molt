# 调研综述：自进化 agent 的谱系与裁判问题

本文件只整理公开资料与既有结论，不含本项目实测数据——项目尚未跑过任何实验。

## 1. 五层谱系

按"改什么"分层，越往下改动面越大。

| 层 | 代表 | 机制一句话 | 是否训练 |
| --- | --- | --- | --- |
| L1 提示与上下文 | Reflexion（2023）、STOP（2023） | 语言化反思或改进器程序改写 prompt，按效用择优 | 不训练 |
| L2 记忆与经验 | ChronoMem（2026）、Brevet（2026，PyPI 0.4.0） | 失败教训进可检索记忆，并给记忆加版本与回滚 | 不训练 |
| L3 技能库 | Voyager（2023） | 成功轨迹固化成 skill，按检索调用 | 不训练 |
| L4 改自身代码 | Gödel Agent（2024）、SICA（2025）、DGM（2025）、HyperAgents（2026） | 运行时改自身实现，基准验证后留档 | 不训练 |
| L5 harness 配置 | AlphaEvolve（已 GA）、SoL-Pi（2026-09，NVlabs）、Harness²（2026-10，Google）、Agentic Harness Engineering（2026-04）、Turbo Harness（2026-09） | harness 本身成为被优化对象 | 不训练 |
| L0 权重训练 | — | 改模型参数（SFT / RL） | 训练；本项目不做 |

## 2. 各家的衡量方式

| 工作 | 证据 | 是否承认没有 ground truth |
| --- | --- | --- |
| Reflexion | HumanEval pass@1 91% | 否 |
| Voyager | 自建指标（3.3× 物品、15.3× 科技树） | 部分 |
| DGM | SWE-bench 20.0%→50.0%，Polyglot 14.2%→30.7% | 否 |
| SICA | SWE-bench Verified 子集 17%→53% | 否 |
| SoL-Pi | EdgeBench 51 任务，性能持平、token −44.7~49%、成本约 −1/3 | 否（以效率为裁判） |
| Harness² | 三个 benchmark 分别 +11.5、+4.5、+11.8 点 | **是**（明说开放世界任务没有 ground-truth verifier） |

DGM 与 SICA 把同一个 benchmark 同时当搜索信号和汇报口径，因此存在高估风险；另有 2026-09 的论文指出，复用自带 benchmark 会系统性高估。这两点决定了"分数能不能信"要单独论证，见下节。

## 3. 裁判可信的五个条件

引自 2026-10 的 "The Evaluator Is the Bottleneck"：

1. 保真度——评测器测的确实是任务目标。
2. 抗篡改完整性——候选无法改写评分路径。
3. 跨候选统计功效——样本量足以分辨候选之间的差异。
4. 接受后状态覆盖——验收发生在改动落地后的真实状态上，而非改动前的快照。
5. 评估器误差与提议者误差相互独立——两者不共享同一信息源。

论文记录的反例：去掉外部锚点后，系统进化出"永远通过"的评分器，而下游任务分数看不出退化。

结论：沙箱只防破坏，不判好坏；归档只让回滚可能，不替你决定回滚。

## 4. 仍然空白的四件事

1. **确定性测试当唯一裁判**：现有工作要么用 benchmark 当信号，要么走无 verifier 的行为对比。
2. **回退作为一等公民**：把工具代码 + harness 配置 + prompt 放进同一个回退事务，未找到实现。
3. **端侧离线双模型**：本地提议 + 本地评审，无网络。
4. **分钟级、可现场两轮演示**。

## 5. 可借机制

| 机制 | 来源 | 工时 | 风险 |
| --- | --- | --- | --- |
| 归档 + 父代采样（求多样不求最优） | DGM | 4h | 归档膨胀与重复候选 |
| 测试只读挂载 + 前后哈希校验 | 评估器论文 | 2h | 测试不完备导致假接受 |
| 单文件评审器、候选禁触 | STOP | 3h | 奖励作弊 |
| 失败→一条反思→下次注入 | Reflexion | 2h | 错误经验被永久化 |
| 变更即 release：manifest + 哈希 + `incapabilities.lock`（把"已知做不到"版本化） | Brevet | 5h | 过度工程 |
| 无 verifier 时用改前/改后同任务对比探针 | Harness² | 6h | 方差大 |

## 6. 许可证

| 仓库 | 许可 | 能否用于比赛与公开仓库 |
| --- | --- | --- |
| jennyzzt/dgm | Apache-2.0 | 可 |
| NVlabs/SoL-Pi | NVIDIA 版权 + MIT 式条款 | 可 |
| agentic-harness-engineering | MIT | 可 |
| facebookresearch/HyperAgents | **CC BY-NC-SA 4.0，禁止商用** | 演示可、衍生须同协议 |
| lobehub/awesome-rsi | CC0 | 可 |
| Brevet、Reflexion、Voyager、Gödel Agent | 许可证未核实 | `[unverified]` |

## 7. 失败模式

- 同一 benchmark 既做搜索信号又做汇报 → 高估
- 奖励作弊：评分口径可被候选影响
- 外锚被移除 → 永远通过的评分器
- 沙箱幻觉：只防破坏，不判好坏
- 越改越差 / 灾难性遗忘：单一持久文件持续追加，旧能力退化
- 无版本 → 无法归因
