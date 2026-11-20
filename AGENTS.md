# AGENTS.md — molt 协作与契约约定

本文件是当前协作入口。流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，格式见
[docs/style.md](docs/style.md)，架构见 [docs/architecture.md](docs/architecture.md)。
当前执行顺序与验收状态见 [执行清单](docs/execution-todo.md)，恢复纪律见
[启动 prompt](docs/continue-prompt.md)。旧文档若与当前源码或用户最新授权冲突，记录冲突并核验，不能借旧授权扩大范围。

## 一、当前定位与范围

molt 维护 agent 工作流的状态事务、证据审计、显式迁移与恢复。
当前源码已有 Rust kernel 与 Python UDS 客户端，默认 backend 为 `kernel`；
不再以“M0、禁止进入 Rust/M1”描述当前阶段。
比赛组件退出方向见 [ADR-0017](docs/decisions/0017-retire-competition-design.md)。
CLI、打包和 CI 已完成通用路径替代与本地验收，相关证据见执行清单。
用户确认的 v2 清单内比赛资产已退出当前工作树，退出后验收见执行清单。

本次授权范围是 A → B → C 的非破坏性本地实现、文档及必要测试；D 只准备材料。
M4/M5、自进化、多 Agent 平台、通用插件平台均不实施。
不访问真实账号、校园系统、付费模型 API，不联系第三方，不修改正式服务、用户 state 或外部归档。
不提交、推送、创建/合并 PR 或发布，不覆盖已验收的 `1.1.0` 包。
上述动作需要用户另行明确授权；历史“用户已批准”不是本次授权。

## 二、边界与不变量

- kernel 只管理不透明对象和事务，不运行 Agent、不理解校园业务、不持有校园凭据。
- 保留单写者、持久日志提交点、同 request ID 幂等及 unknown 恢复语义。
  结果未知先查询或恢复，禁止换新 ID 盲重试。
- kernel 幂等不等于外部业务恰好执行一次；本地状态回退不等于取消外部预约。
  本地与外部系统没有共同原子事务。
- 本地哈希链不证明执行真实性，不声称 tamper-proof；外部检查点与可信主体边界必须明确。
  模型输出、自报执行结果和工具返回均不能自动视为可信事实。
- 真实凭据与敏感原文不进入模型、仓库或不必要的证据；必要控制失效时拒绝执行，不能静默降级。
- 合成对象和模拟业务证据标为 `synthetic`。历史 `fixture` / `replay` / `live` 如实保留，
  不把比赛历史或模拟结果作为当前真实业务成功证据，不宣称统计显著或模型能力提升。

## 三、协议与兼容

v1 字段事实源是 `schemas/v1/*.schema.json` 与 `molt/core/types.py`；
`docs/protocol.md` 是生成物，禁止手改。冻结 schema 不改字段或语义。
1.1.x 的 v1 只读审计与显式迁移兼容不变；迁移源保持无写入。
审计、schema、policy、records 等代码若为兼容所需必须保留，不能按文件名判断退出范围。
现存历史比较仍遵守 `scenario_version` 隔离、逐题配对及原 provenance 语义，不能改写历史含义。

core 不 import 第三方库或 shell/adapter；保留兼容代码的单向依赖 `core ← shell/adapter`。
kernel 不接管 verifier 或业务判分。门禁阈值的历史 owner 仍是 `scenario.policy`。
历史冻结面 `shells/<id>/{harness.py,verifier.py,task_provider.py,switches.schema.json}` 与 `bench/**`
不原地改语义；退出资产走单独确认，不用改题集来凑新验收。

若集成必须突破冻结协议或 kernel 业务边界，先形成具体 ADR/决策请求，继续可独立任务，
不得自行实现。新源码、新包必须独立验收；旧精确发行包证据不能代替。

## 四、本地执行与证据

- Python 依赖只用 uv；所有 Python/pytest 命令带 `PYTHONDONTWRITEBYTECODE=1`，
  基线解释器为 3.12.13，不向系统 Python 装包。
- 先 `git status --short` 和目标文件定向 diff，保护既有修改；压缩恢复例外：第一步只读 handoff 顶部，
  再 `rg -n` 定位相关符号及前后约 40 行，不重扫全仓库。
- 每个 exec_command 显式 `max_output_tokens: 3000` 或更少；单次 sed 范围合计不超过 120 行。
  搜索先用 rg；不整树输出。完整测试日志落盘，终端只取尾部，并保留原命令退出码。
- 相关子集先跑；失败先修再重试，不重复盲跑全量。不靠删除覆盖、放宽断言或跳过必要控制过关。
  替代比赛用例时记录覆盖映射，保留事务、幂等、冲突、断线、权限、空间不足与恢复验证。
- 状态、日志、候选包放仓库外独立测试目录，不操作正式用户服务；临时进程记录 PID/用途/清理方式。
- 跨会话检查点放 `~/.local/state/codex/molt-handoff.md` 顶部，保留历史；每个验收项先持久化证据、
  更新检查点，再将 TODO 标 DONE。测试启动不等于通过。
- 证据目录为 `~/.local/state/codex/molt-work/<本次唯一目录>/`，记录完整命令、cwd、环境、退出码、
  日志、源码摘要；工作树有修改时 HEAD 不是完整源码标识。
  包验收额外记录精确路径、SHA-256、隔离安装位置与源码绑定；从仓库外验安装包。
- 零写入用文件系统树快照验证，单看 git status 不够；缺失的旧 /tmp 日志标缺失，不假造通过。

## 五、退出与删除

比赛资产批量删除仍需用户看过完整 dry-run 清单后明确确认。
本次“继续”和“可以修改旧 AGENTS”都不是清单确认。
A08 的获批清单及逐项核验记录见执行清单。后续批量退出仍须在执行前逐项复核路径和摘要；
变化项重新核对，新增路径重新预览确认。删除用 `gio trash`，不递归删除目录，不触碰清单外文件。
批量重命名/移动同样先完整 dry-run 并等确认。
A08 未完成时不得将 A 阶段标为 DONE；A11 还须通过退出后的源码和新包验收。

## 六、所有权与协作

CODEOWNERS 归属：契约域 @FeiSaMa（core、Rust kernel、Python kernel 客户端/mirror/迁移、protocol、ADR）；
schema 共同 owner 为 @FeiSaMa、@By-Diamond、@dong856。历史场景域 @By-Diamond（shell、bench），
集成域 @dong856（历史 adapter、attic、CI、非契约文档）。
调整 owner 必须同步 `.github/CODEOWNERS`，不能只改本表。
不根据旧文档臆测当前远端保护规则；本次无需查询远端。
GitHub 操作优先会话内 MCP，能力不足才说明原因回退；不直推 main、不 force push、不 reset --hard。

## 七、验收入口

相关子集先运行；以下均在仓库根执行，日志和状态路径必须设在仓库外：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.12.13 ruff check .
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.12.13 pytest -q
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.12.13 python -m molt schema validate --all
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.12.13 python -m molt doctor --deps
cargo test -p molt-kernel --features fault-injection
cargo fmt --all -- --check
cargo clippy -p molt-kernel --all-targets --features fault-injection -- -D warnings
```

旧 `doctor --repo-policy`、CLI 帮助、CI 与文档检查若仍绑定比赛范围，需合理替代后验收，
不能把文档更新视为检查已通过。A09 集中回归与 A10 新包验收单列记录，
A11 核对实际退出和清单一致后才结束 A 阶段。
外部集成边界见 [最小集成契约](docs/operations/external-integration.md)，
模拟能力见 [合成预约](docs/operations/synthetic-booking.md)，
真实接入的待确认项见 [校园接入前置材料](docs/operations/campus-readiness.md)。
材料完成不代表真实接入获批，具体状态以执行清单和可复验证据为准。
