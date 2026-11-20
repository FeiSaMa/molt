# molt

molt 是 agent 工作流的外层编排器。它管理候选状态、执行证据、验证结果、门禁决定和状态提交，让一次变更可以审计、复算，并在失败时恢复到已知 generation。

当前维护重点是可信状态事务、证据审计、迁移与恢复。比赛设计与历史材料由 `bohack-molt` 归档，本仓库不再承担比赛归档职责，也不以 benchmark 成绩、成本比较或“自进化”作为产品目标。

比赛组件退出主仓库的工作尚未完成：CLI 已改为通用事务入口；测试、CI 和打包仍在解耦。移除范围与兼容边界见 [ADR-0017](docs/decisions/0017-retire-competition-design.md)。

## 当前状态

- `1.1.0` 在 Linux x86_64 上默认使用 Rust v2 kernel；需要已启动并配置的 systemd user service。
- v1 state 在整个 `1.1.x` 版本线只允许读取、审计和显式迁移；写入会在持久化前拒绝。
- Rust v2 kernel 与 Python CLI 的 primary backend、v1 state 显式迁移、Linux x86_64 候选安装包均已实现。
- 服务不可用时会报错，不会静默回写 v1 state。`MOLT_KERNEL_BACKEND=v1` 仅可显式用于兼容读取，不能修改 v1 state。
- `1.1.0` 是首个 kernel 默认正式版本 R；v1 只读兼容覆盖整个 `1.1.x`，`1.2.0` 移除兼容须另行决定。
- 获批清单所列比赛资产已退出当前工作树。core 中为 v1 只读审计所需的协议、评分与门禁逻辑保留。

发布门禁和版本边界见 [ADR 0015](docs/decisions/0015-v2-kernel.md)、[v2 release checklist](docs/operations/v2-release-checklist.md) 与 [候选验收记录](docs/research/v2-candidate-evidence.md)。

## 工作方式

调用者上传不透明对象，创建 manifest，再以稳定 request ID 提交 generation。
Rust kernel 管理持久化对象、manifest、generation 与提交日志，不运行 Agent 或决定业务判分。
`molt/core/` 保留 v1 协议与只读语义审计；kernel 幂等不代表外部业务恰好执行一次。

UDS 命令的 state 由 socket 实例决定；`--state` 仅在显式磁盘审计、checkpoint、恢复和 v1 兼容入口使用。
通用命令、unknown 处理和兼容用法见 [CLI 说明](docs/operations/kernel-cli.md)。

## 环境

Python 基线固定为 3.12.13；依赖由 `uv` 管理。Rust kernel 使用仓库 `rust-toolchain.toml` 指定的 stable toolchain。

```bash
PYTHONDONTWRITEBYTECODE=1 uv sync --python 3.12.13 --extra dev
```

## 使用 Python v1

显式设置 `MOLT_KERNEL_BACKEND=v1`，读取仓库外既有 state 并提供对应 scenario JSON：

```bash
MOLT_KERNEL_BACKEND=v1 PYTHONDONTWRITEBYTECODE=1 uv run python -m molt audit \
  --scenario /path/to/scenario.json --state /path/to/v1-state
```

该命令只重算已有证据，不生成 run、不调用模型、不修改源 state。

## 使用 Rust v2 kernel

先构建并启动一个独立 kernel。`init` 只用于新 state 目录：

```bash
cargo build -p molt-kernel
target/debug/molt-kernel init --state /tmp/molt-v2-kernel
target/debug/molt-kernel serve --state /tmp/molt-v2-kernel
```

在另一个终端连接该服务并查询、审计。Linux x86_64 上默认后端为 kernel：

```bash
export MOLT_KERNEL_BACKEND=kernel
export MOLT_KERNEL_SOCKET=/tmp/molt-v2-kernel/kernel.sock
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt inspect
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt current
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt audit
```

连接失败、协议错误或提交结果不明确时不会回退写入 v1。结果未知先查询原 request ID；
禁止换新 ID 盲重试。常驻使用见 [systemd user service 运维说明](docs/operations/molt-kernel-systemd.md)，
备份演练见 [备份、checkpoint、恢复和回退](docs/operations/v2-backup-restore.md)。

从 v1 state 迁移时，先运行只读预览：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt migrate --state /path/to/v1-state
```

预览确认后，需连接目标 kernel 并显式指定迁移 ID 执行 `--apply --migration-id <id>`。迁移预览、幂等性、源 state 不变及兼容边界详见 [v2 migration contract](docs/research/v2-migration.md)。

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 uv run ruff check .
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt doctor --deps
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt doctor --repo-policy
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt schema validate --all
cargo fmt --all --check
cargo test --workspace --features molt-kernel/fault-injection --quiet
cargo clippy --workspace --all-targets \
  --features molt-kernel/fault-injection -- -D warnings
```

Kernel UDS 集成测试需要先构建 Rust 可执行文件并设置 `MOLT_KERNEL_BIN`：

```bash
cargo build -p molt-kernel
MOLT_KERNEL_BIN=target/debug/molt-kernel \
  PYTHONDONTWRITEBYTECODE=1 uv run pytest -q tests/test_kernel_client.py tests/test_kernel_cli_contract.py \
  tests/test_kernel_transactions.py tests/test_v1_compatibility.py
```

CI 包含 Rust 故障注入测试、Python/kernel UDS 集成、release bundle 构建和打包 kernel 的恢复测试。全量 Python 测试是集中执行的基线检查；Pi live 测试默认跳过。

本地复现安装包验收时，先构建、校验并解压组合包，再运行：

```bash
bash packaging/release/verify-bundle.sh /tmp/extracted/molt-1.1.0-linux-x86_64 /tmp/molt-bundle-check
```

第二个参数必须是仓库外尚不存在的工作目录。脚本按 `uv.lock` 安装依赖，使用独立 wheel 环境、
包内 schema/协议兼容夹具与 kernel 执行通用事务、checkpoint、恢复和 v1 审计/迁移验证，不依赖源码导入。
正式版本与 v1 兼容窗口见 [1.1.0 发布记录](docs/operations/v2-release-1.1.0.md)。

## 不变量

- 每次 run、result、gate decision 和 ledger 必须能由 `molt audit` 复算；审计是只读的。
- `fixture`、`live`、`replay` 必须如实标注；holdout 与 search 隔离。
- 不跨 `scenario_version` 比较结果，也不把历史比赛数据说成当前 live 证据。
- v1 schema 与判分/gate 语义保持兼容；Rust kernel 只管理持久化和提交，不执行 agent 或业务判分。
- 回退到 v1 只恢复迁移时的 v1 快照，不包含之后产生的 v2 写入；v2 备份需独立保留并验证。

更完整的协作约定见 [AGENTS.md](AGENTS.md) 与 [CONTRIBUTING.md](CONTRIBUTING.md)，架构见 [docs/architecture.md](docs/architecture.md)。现有比赛组件的退出计划见 [ADR-0017](docs/decisions/0017-retire-competition-design.md)。
