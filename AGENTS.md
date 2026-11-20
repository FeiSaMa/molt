# AGENTS.md — molt 协作与契约约定

开发流程见 [贡献指南](CONTRIBUTING.md)，模块边界见 [架构](docs/architecture.md)。

## 一、当前定位与范围

molt 提供 Agent 工作流的状态事务、版本管理、证据审计及显式迁移。Rust kernel 是默认事务后端，Python 客户端通过 Unix socket 访问。

任务授权以当次用户指令为准。未经明确授权，不访问真实账号、正式外部系统或付费模型 API，不联系第三方，不修改正式服务、用户 state 或外部归档，不提交、推送、操作 PR 或发布，不覆盖已验收安装包。

## 二、边界与不变量

- kernel 只管理不透明对象和事务，不运行 Agent、不解释业务状态、不持有业务凭据。
- 保留单写者、持久日志提交点、同 request ID 幂等及 unknown 恢复语义。
  结果未知先查询或恢复，禁止换新 ID 盲重试。
- kernel 幂等不等于外部业务恰好执行一次；本地状态回退不等于取消外部预约。
  本地与外部系统没有共同原子事务。
- 本地哈希链不证明执行真实性，不声称 tamper-proof；外部检查点与可信主体边界必须明确。
  模型输出、自报执行结果和工具返回均不能自动视为可信事实。
- 真实凭据与敏感原文不进入模型、仓库或不必要的证据；必要控制失效时拒绝执行，不能静默降级。
- 合成对象和模拟业务证据标为 `synthetic`。历史 `fixture` / `replay` / `live` 如实保留，
  模拟结果只作为对应模拟环境的证据。

## 三、协议与兼容

v1 字段事实源是 `schemas/v1/*.schema.json` 与 `molt/core/types.py`；
`docs/protocol.md` 是生成物，禁止手改。冻结 schema 不改字段或语义。
1.1.x 的 v1 只读审计与显式迁移兼容不变；迁移源保持无写入。
审计、schema、policy、records 等代码若为兼容所需必须保留，不能按文件名判断退出范围。
现存历史比较仍遵守 `scenario_version` 隔离、逐题配对及原 provenance 语义，不能改写历史含义。

core 不 import 第三方库或 shell/adapter；保留兼容代码的单向依赖 `core ← shell/adapter`。
kernel 不接管 verifier 或业务判分。保留历史状态中 `scenario.policy` 门禁阈值的归属。

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
  替代测试时记录覆盖映射，保留事务、幂等、冲突、断线、权限、空间不足与恢复验证。
- 状态、日志、候选包放仓库外独立测试目录，不操作正式用户服务；临时进程记录 PID/用途/清理方式。
- 跨会话检查点放 `~/.local/state/codex/molt-handoff.md` 顶部，保留历史；每个验收项先持久化证据、
  更新检查点，再将 TODO 标 DONE。测试启动不等于通过。
- 证据目录为 `~/.local/state/codex/molt-work/<本次唯一目录>/`，记录完整命令、cwd、环境、退出码、
  日志、源码摘要；工作树有修改时 HEAD 不是完整源码标识。
  包验收额外记录精确路径、SHA-256、隔离安装位置与源码绑定；从仓库外验安装包。
- 零写入用文件系统树快照验证，单看 git status 不够；缺失的旧 /tmp 日志标缺失，不假造通过。

## 五、退出与删除

批量重命名、移动、删除或从 Git 取消跟踪前，先给出完整 dry-run 清单并等待明确确认。
执行前逐项复核路径和摘要。变化项重新核对，新增路径重新预览确认。
删除用户文件用 `gio trash`，按清单逐件处理。

## 六、所有权与协作

CODEOWNERS 归属：契约域 @FeiSaMa（core、Rust kernel、Python kernel 客户端/mirror/迁移、protocol、ADR）；
schema 共同 owner 为 @FeiSaMa、@By-Diamond、@dong856。历史场景域 @By-Diamond（shell、bench），
集成域 @dong856（历史 adapter、attic、CI、非契约文档）。
调整 owner 必须同步 `.github/CODEOWNERS`，不能只改本表。
远端保护规则以当前配置为准。
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

外部集成边界见 [外部集成契约](docs/operations/external-integration.md)。
