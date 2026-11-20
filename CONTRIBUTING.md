# CONTRIBUTING

当前路线与验收顺序见 [执行清单](docs/execution-todo.md)，协作边界见
[AGENTS.md](AGENTS.md)，格式见 [docs/style.md](docs/style.md)。

## 一、环境与本地工作

仓库根运行，基线 Python 3.12.13：

```bash
PYTHONDONTWRITEBYTECODE=1 uv sync --python 3.12.13 --extra dev --locked
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.12.13 ruff check .
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.12.13 pytest -q
```

依赖只走 uv，不向系统 Python 装包；临时工具用 `uv run --with <pkg>`。
依赖变更同步 `uv.lock`。构建验包使用独立 venv，不能从 checkout 冒充安装包导入。
修改前看 `git status --short` 和目标文件定向 diff，保护既有修改；压缩恢复按 handoff 纪律。
状态、日志、证据和候选包放仓库外；证据长期保存在 handoff 指定目录，不能仅留 `/tmp`。

## 二、分支与提交

本次执行不提交、不推送、不创建或合并 PR、不发布。后续获得明确授权后，
默认分支名使用 `codex/<slug>`；不直推 main、不 force push、不改写已推送历史。
提交信息用中文 Conventional Commit，例如 `docs(kernel): 对齐事务恢复说明`。
批量移动、重命名和删除先完整 dry-run 并等明确确认；删除用 `gio trash`。
比赛资产的实际退出及退出后的验收证据见执行清单 A08、A11。

## 三、评审与所有权

所有权见 `.github/CODEOWNERS` 与 AGENTS §六；CODEOWNERS 用于请求 reviewer。
契约域建议 1–2 人评审并附 ADR，其他变更建议 1 人评审。
旧文档记录的远端 ruleset 不视为当前事实；需要远端操作时再核验，不根据历史记录扩大权限。
本次不修改远端保护规则，不操作第三方系统。

## 四、测试与 CI

新增行为必须有必要用例。先跑相关子集，修复后再扩大；全量 Python pytest 是手动集中验收。
日志保存完整命令、cwd、环境、退出码和源码绑定，不能把 `tail` 的退出码当成测试结果。
零写入以文件系统树快照为证据，`git status` 仅辅助；测试使用 `PYTHONDONTWRITEBYTECODE=1`。

`.github/workflows/ci.yml` 有六个作业：`lint`、`schema`、`boundary`、`audit`、`secret-scan`、`kernel`。
`audit` 跑独立 v1 synthetic 兼容、通用事务恢复和零写入；`kernel` 跑 Rust 故障注入、
fmt/clippy、真实 UDS、CLI 契约和仓库外安装包验证。
历史文件名 `pi-smoke.yml` 目前仅提供手动 synthetic 冒烟，不读凭据、不调用模型 API。
本地执行等价子集不能声称远端 CI 已通过。

```bash
cargo build -p molt-kernel
PYTHONDONTWRITEBYTECODE=1 MOLT_KERNEL_BIN=target/debug/molt-kernel uv run --python 3.12.13 pytest -q \
  tests/test_kernel_client.py tests/test_kernel_transactions.py \
  tests/test_kernel_cli_contract.py tests/test_v1_compatibility.py
cargo test -p molt-kernel --features fault-injection
cargo fmt --all -- --check
cargo clippy -p molt-kernel --all-targets --features fault-injection -- -D warnings
```

历史比赛测试的退出需记录覆盖映射；不能丢失幂等、冲突、断线、单写者、权限、空间不足与恢复覆盖。
不通过改题、放宽断言或跳过控制凑通过。真实账号、校园系统及付费 API 不在当前验收范围。

## 五、冻结协议与兼容

先写 ADR 再考虑契约变更；本轮不修改冻结 v1 字段或语义。
字段事实源为 `schemas/v1/*.schema.json` 与 `molt/core/types.py`，`docs/protocol.md` 必须生成。
1.1.x 保留 v1 只读审计和显式迁移，源 state 不写入，不从 kernel 失败回落到 v1 写入。

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.12.13 python -m molt schema validate --all
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.12.13 python -m molt schema docs --output /tmp/molt-protocol-review.md
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.12.13 python -m molt doctor --deps
```

比较生成结果与 `docs/protocol.md`，不手改生成物；兼容用例见 `tests/test_schema_compat.py`、
`tests/test_v1_compatibility.py`。历史 shell/bench 冻结语义及 `scenario_version` 隔离继续有效，
不能原地改写历史证据。比赛 CLI 已退出注册，不以旧 `scenario/gate/demo` 命令作为当前验收入口。

## 六、证据、恢复与安全

kernel 只管理不透明对象与事务，不执行 Agent 或保存业务凭据。
保持单写者、持久日志提交点和同 request ID 幂等；unknown 先查询或恢复，禁止换 ID 盲重试。
kernel 幂等不等于业务恰好执行一次；generation 回退不取消外部操作。
audit 与本地哈希链不证明执行真实性，不声称 tamper-proof；外部 checkpoint 的可信保管边界需单列。
必要生产控制失效即拒绝执行，不静默降级。

不入库 `runs/` 原始日志、`state/`、`evidence/`、凭据、敏感原文。
合成用例标 `synthetic`，历史 `fixture/live/replay` 标签保持原义；不能把合成结果说成真实业务成功。
新源码与新包分别验收，记录 dirty diff/内容摘要、精确包 SHA-256 和独立安装路径；
旧 1.1.0 证据不能替代本次验证，旧归档不得覆盖。
命令与恢复步骤见 [通用 CLI](docs/operations/kernel-cli.md) 和
[备份恢复](docs/operations/v2-backup-restore.md)。每个验收项先落证据、再更新 handoff、最后更新 TODO。
