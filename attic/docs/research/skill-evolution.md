# Skill evolving：前沿做法

调研日期 2026-11。两篇 2026 年的综述是主要骨架：

- **Dynamic Agent Skills: A Lifecycle Survey and Taxonomy of Evolving Skill Libraries**（arXiv 2607.10113，CMU，124 篇 2023–2026 论文）
- **Agent Skill Evaluation and Evolution: Frameworks and Benchmarks**（arXiv 2606.11435，Rutgers）

## 1. 先把"skill"这个词拆开

综述一给了六种形态，不同论文说的根本不是一回事：

| 形态 | 代表 | 可执行 | 可编辑 | 可校验 |
| --- | --- | --- | --- | --- |
| 代码函数 | Voyager、LATM | 是 | 是 | 单元测试 |
| 自然语言教训 | Reflexion 式 | 否 | 是 | 人工/LLM 判 |
| SKILL.md 包 | Anthropic Agent Skills | 部分（附带脚本） | 是 | 触发测试 |
| 工作流图 | 各类 workflow memory | 是 | 是 | 端到端 |
| 参数化适配器 | LoRA / adapter | 是 | 否 | 训练指标 |
| 记忆轨迹 / 能力标签 | 边界情形 | 否 | 是 | 弱 |

选哪种形态直接决定后面能不能验证。**能跑测试的形态（代码、SKILL.md + 脚本）优先**，因为准入环节才有硬信号。

## 2. 八阶段生命周期

综述一的核心贡献。前沿的差异几乎都落在某个阶段上：

| 阶段 | 前沿做法 | 关键结论 |
| --- | --- | --- |
| 证据获取 | 从失败轨迹里挖，不从零生成 | 证据质量 > 生成能力 |
| 提案 | 诊断与重写**分成两步**、两个角色 | SkillForge、CoEvoSkills 都这么做；分开的比不分的稳定 |
| 验证/准入 | 外部 verifier 卡门，不用 LLM 自评 | 综述明确：verifier 质量决定技能库质量 |
| 存储 | 目录 + 元数据（条件、接口、版本） | SKILL.md 已是事实标准 |
| 检索/组合 | 层级 + 路由，不是平铺向量检索 | **flat retrieval 随库增长会退化** |
| 维护 | 修复 + 剪枝 | 技能会腐烂，任务/工具/模型一漂移就失效 |
| 蒸馏/可移植 | 压缩、合并、去重 | 库越大重叠越严重，冗余会拖垮检索 |
| 治理 | 来源追踪 + 回滚 | 没有版本就无法归因 |

## 3. 四种演化信号

综述二把演化策略分成四类：

1. **执行反馈**：运行时报错、输出错误、未满足规格。最实用，信号最硬。
2. **轨迹蒸馏**：把成功轨迹固化成可复用的过程。Trace（arXiv 2609.22120）用"可执行 walkthrough"，报告强于 memory、retrieval、reflection、APO 这几类基线。
3. **压缩/增广**：处理重叠与冲突，生成互补技能。
4. **强化学习**：SkillOS（NeurIPS 2026）不学技能本身，而是学"怎么策展技能库"——把 curation 当策略来训。

## 4. 工程事实标准：SKILL.md

Anthropic 2025-10 的 Agent Skills 已经把格式定下来了：

```text
skill-name/
├── SKILL.md        # YAML frontmatter: name + description；正文是操作步骤
├── scripts/        # 可执行脚本
└── references/     # 按需加载的参考文件
```

三层渐进披露（progressive disclosure）：

- L1：`name` + `description` 常驻系统提示，只占几十 token；
- L2：命中后加载 SKILL.md 正文；
- L3：需要时再读 `references/` 里的文件。

这个格式的好处是**技能的元数据可以全量灌进上下文，正文按需加载**，库再大也不会撑爆前缀。Pi 原生支持这个格式（本机 `~/.pi/agent/skills/` 就是）。

## 5. 三个容易忽略的坑

1. **用法–效用差距**：技能被检索到 ≠ 被调用 ≠ 有用。SkillApt（arXiv 2609.26863）专门解决"何时激活"，用反事实证据训练激活判断。
2. **技能墓地**：2026-09 有篇论文标题就叫 *The Skill Graveyard*，研究 agent 生成技能在生产库里的存活率与使用经济学。结论方向很明确——大部分生成出来的技能会死掉，剪枝不是可选项。
3. **自评不可信**：和评估器论文同一个结论。技能准入必须过外部测试，LLM 自评会让库慢慢腐化。

## 6. 对 molt 的落点

molt 的回退事务目前是 config + tools + prompts 三样，正好缺 skill 这一样：

- `shells/` 对应工具目录；
- `prompts/` 对应提示；
- 应新增 `skills/`，格式直接沿用 SKILL.md，Pi 原生加载。

molt 在这条线上的差异化不是"让 agent 写技能"（2026 年已经很多），而是：**把技能纳入同一套版本化与回退事务，准入必须过外部测试**。综述一里 governance 和 admission 都被点名是薄弱环节。

但排序上这是第三档：没有稳定的参数基线和记忆库，技能演化的收益会被噪声吃掉。而且它需要 `family` 题集真的同族，否则跨任务复用根本发生不了。
