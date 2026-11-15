# v2 范围清单（提议，待审阅）

基于 main `9de9df6`；分类为目标，不代表已移动或删除。M0 不改变生产代码。

| 路径 | 分类 | 依赖与测试影响 |
| --- | --- | --- |
| `molt/core/` | keep，v1 协议 freeze | 七端口、语义审计与 policy 保留；store/snapshot/events/ledger 写路径迁移，事务、审计、零写入测试须重做。 |
| `molt/adapters/`、`molt/core/engine.py` 等编排模块 | keep | fixture、deterministic、Pi 与 shell 仍使用 domain 端口；盘点所有 state 写入口。 |
| `molt/cli.py`、`molt/demo.py` | keep，改写 | CLI 直接加载 export 与 agent server，demo 加载 export；移除入口并改为通用证据输出，迁移 demo/CLI 测试。 |
| `molt/export.py`、`web/` | delete/改写 | web-only 导出删除，必要的通用证据功能重写；export、页面及 demo 测试明确迁移或删除。历史由 git 恢复。 |
| `molt/agent/`、`molt/upper/`、`molt/skills/`、`molt/mcp/` | attic | 从 wheel 与主测试集移出，先断开 CLI server；相关 API、lab、upper、skill、MCP 测试随模块迁移。`agent/runtime.py` 直接依赖这四个模块，不是独立通用 runtime，随 agent 移入 attic。 |
| `bench/`、`shells/`、`scenarios/` | keep / freeze | 题集和 verifier 不改；冻结面修改须升 scenario_version 与重跑，probe 依赖单独审查。 |
| `schemas/v1/`、`docs/protocol.md` | freeze | 保留历史记录与生成契约；新 envelope 等另建 v2 schema。 |
| `tests/` | keep / 分类 | 承重测试保留；实验和 web 测试随功能迁移，不留 skip 掩盖删除；parity、崩溃和篡改矩阵新增。 |
| `docs/` | keep / 分类 | 保留 ADR 和历史事实；现行指南迁移，展示与实验文档标历史或随 attic 移动。 |
| `.github/`、`pyproject.toml`、`uv.lock`、`scripts/` | keep / 改写 | CI、wheel、脚本当前覆盖实验和 web；单独核查依赖并同步构建与测试。 |
| `contracts/`、`mcp_servers/`、`skills/`、`skills_eval/`、`upper_scenarios/`、`probes/` | attic | 与实验模块耦合；逐项检查运行及测试引用后整体迁移，不能只移动 Python 包。 |
| `README.md`、`AGENTS.md`、`CONTRIBUTING.md`、`.gitignore` | keep / 更新 | M3 更新边界、流程和恢复方法；同步 CODEOWNERS 与相关文档。 |
| `runs/`、`evidence/`、`state/`、本地缓存和虚拟环境 | 不入库 | 用户本地数据不得删除；迁移以仓库外独立路径操作。 |

恢复：删除/迁移单独提交与 PR，可通过 git revert 恢复；不得覆盖用户工作区。实验模块及耦合资产物理移入 attic 并从构建、ruff 与 pytest 排除；先单独提交断依赖，再单独提交移动。contracts 随 agent 迁移。attic/README.md 记录恢复方法。

## 基线与审阅口径

用户已确认物理迁移及保留范围门禁。v1 全量基线只记录历史；迁移后保留集要求零 unexplained red。失败逐项分类 env:socket、env:bwrap、obsolete:scope、real:v1、flaky；真实缺陷独立修复，环境测试在支持环境运行并注明条件。README 契约与 README 重写同步更新。
