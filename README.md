# molt

agent 每次失败后自己写工具、调开关，但改动必须过测试、能回退。molt 是外层编排器：不实现 agent 能力，只负责把候选改动跑起来、打分、留证据、回退。

四件套：

- **signal**：本地小模型读失败日志，提案改哪个开关，不许自评
- **action**：前沿模型按提案产出新 config
- **verify**：固定题集 + 外部测试当裁判，字典序打分（成功率 > token > 耗时）
- **rollback**：config 版本化 + git tag，分数不涨就回退

跑两轮：

```bash
molt run --config configs/v0-baseline.json
molt revert --to v0
```

背景与边界见 `docs/architecture.md`，数据结构见 `docs/contracts.md`。
