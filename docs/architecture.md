# 架构

molt 分三层。底座能力全部来自上游，molt 只做编排与评测，不改 Pi 源码、不写 Pi 扩展。

## 一、三层职责

| 层 | 组件 | 职责 | 不负责 |
| --- | --- | --- | --- |
| harness | Pi（earendil-works/pi） | 会话、工具调用、模型接入、SDK 与 RPC | 效率取舍、评测、回退 |
| 效率机制 | SoL-Pi（NVlabs/SoL-Pi，MIT） | 动作融合、观测句柄、证据保留式 reducer、在线上下文压缩；配置走 `sol-pi.json` | 全局调度、题集、打分 |
| 编排与评测 | molt（本仓） | 开关搜索、跑题集、字典序打分、回归门禁、回退、留证据 | 实现 agent 能力、改 Pi 行为 |

调用关系：`molt run` 为每道题准备隔离 workdir，把当前开关渲染成 `.pi/sol-pi.json`，再 spawn Pi 子进程执行；结果只从子进程产物与外部测试读取。

SoL-Pi 的四个机制彼此独立、默认全关。reducer 的 provider/model 可在 `sol-pi.json` 里单独指定，与主模型解耦——这正是把 reducer 开关放进搜索空间的理由。

## 二、双模型分工

| 角色 | 模型 | 输入 | 输出 | 硬约束 |
| --- | --- | --- | --- | --- |
| 提案 signal | 本地小模型 | 失败日志、上一版 config、指标 | 一个开关的改动建议 + 理由 | 不许自评，不许碰题集与判分 |
| 执行 action | 前沿模型（DeepSeek 等） | 提案 + 当前 config | 新 config、必要的补丁 | 只改开关与外壳，不改测试 |
| 裁判 verify | 固定题集 + 外部测试脚本 | 执行产出的 diff 与运行轨迹 | success / tokens / wall_ms | 判分脚本与题集对提案方只读 |

裁判与提案方隔离：本地小模型看不到 holdout 题，也读不到判分脚本的期望值；否则分数不可信。

## 三、6 个受控开关

只搜索下面 6 个，不允许提案方自由新增维度（新增维度须走 ADR）。

| 键 | 类型 | v0 基线 | 候选空间 | 作用 |
| --- | --- | --- | --- | --- |
| `reducer_enabled` | bool | `false` | false / true | 开启证据保留式 reducer，长诊断日志压成可校验收据 |
| `compaction_ratio` | float | `0.0` | 0.0 / 0.2 / 0.35 | 在线上下文压缩比例，0 表示不压缩 |
| `action_fusion` | bool | `false` | false / true | edit/write 同一次工具调用顺带跑验证命令 |
| `tool_granularity` | str | `"file"` | file / hunk / command | 工具粒度：整文件、按 hunk、直接给命令 |
| `escalation_threshold` | int | `2` | 1 / 2 / 3 / 4 | 连续失败几次后升级到前沿模型 |
| `max_concurrency` | int | `4` | 1 / 2 / 4 / 8 | 单题集内并发跑题数 |

默认值定义在 `molt/config.py` 的 `Switches`，基线落在 `configs/v0-baseline.json`，两边字段必须一一对应。

## 四、打分规则

字典序，先成功率，再 token，最后耗时：

```
score = (-success_rate, tokens_total, wall_ms_total)
```

比较时按元组升序，越靠左的项权重越高；成功率不同时，token 与耗时完全不影响名次。

**回归门禁**：固定集成功率不得低于上一版。上一版为 `v(n-1)` 时，只有 `success_rate(vn) >= success_rate(v(n-1))` 才允许把 `vn` 作为新基线；成功率持平时才用 token、耗时决定是否采纳。门禁不过 → 丢弃该候选，不出现在轮次报告里。

## 五、回退机制

两层保障：

1. **config 版本化**：每次采纳的 config 是 `configs/vN-*.json` 里的一个文件，`config_version` 字段与文件名一致。
2. **git tag**：采纳时给该提交打 `vN` tag，tag 指向 config 文件 + 当轮 result。

`molt revert --to vN` 把当前 `configs/current.json`（或等价指针）切回 vN 对应的文件，**必须用同一题集、同一模型版本复跑**，指标回落到 vN 的水平才算回退成功；只改文件不跑，等于没有证据。回退不删除旧 runs/，历史证据保留。

## 六、现状

本仓是骨架：`runner`、`score`、`store` 三个模块只有签名与契约，调用即 `NotImplementedError`；`cli` 只解析参数并打印"未实现"。填实现前先读 `docs/contracts.md` 确认落盘格式。

## 七、相关工作与边界

| 工作 | 优化什么 |
| --- | --- |
| SoL-Pi | Pi 内的效率机制：动作融合、观测句柄、证据保留式 reducer、在线上下文压缩；配置面是 `sol-pi.json` |
| Harness² | harness 结构本身；在开放世界任务上以改前/改后的同任务对比代替 ground-truth verifier |
| Agno | agent 平台层，Apache-2.0，自称 self-improving agent platform：SDK（Agno / Claude Agent / Codex）+ AgentOS（持久服务：REST API、MCP server、鉴权、人工审批、断点续跑、调度）+ Control Plane（托管 UI）。它改进的是提示与工具，不是 harness 配置，因此不作为本项目的对标基线 |
| DGM | agent 的自身实现代码；靠 benchmark 分数驱动变异与归档 |

我们的边界：**我们不发明 harness 优化算法**；我们证明这套东西能在"测试当裁判、改动可退回、端侧离线"的条件下成立。算法层面的新东西留给上游，本项目的产出是可复现的编排、契约与证据链。

## 八、裁判独立性的三条实现要求

1. **评测器外挂**：测试目录不放进候选工作区，只读挂载；每次运行前后计算目录哈希，不一致则该次运行判废。
2. **候选不得触碰评审器与测试用例**：对应 config 的 action 范围，越界即违规。
3. **报告双分数**：搜索用固定集的分数，与留出集的分数，两者都写进 `result.json`。

第 3 条的理由是防止"只在搜索集上变好"。留出集不进搜索回路，只作汇报，任何对留出集的过拟合都会立刻暴露。第 1、2 条的具体落盘见 `docs/contracts.md` 的 `eval_guard` 与 `guard_violations`。

## 九、回退是一个事务

一次快照同时包含三样：harness 配置、工具目录、提示文件。`revert` 三样原子回滚，不允许只回其中一样。

理由一句话：分开回滚会产生"配置是新的、工具是旧的"这种无法归因的中间态。
