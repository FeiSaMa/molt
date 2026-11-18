# molt

molt 是 agent 工作流的外层编排器。它管理候选状态、执行证据、验证结果、门禁决定和状态提交，让一次变更可以审计、复算，并在失败时恢复到已知 generation。

项目最初服务于自进化 agent 比赛。比赛阶段的 benchmark 与成本比较仍作为历史验证材料保留；当前维护重点转向可信状态事务、证据审计、迁移与恢复。molt 不训练模型，也不把“自进化”作为已实现能力来宣称。

## 当前状态

- Python v1 仍是生产默认后端。
- Rust v2 kernel 与 Python CLI 的 primary backend、v1 state 显式迁移、Linux x86_64 候选安装包均已实现。
- v2 可通过 `MOLT_KERNEL_BACKEND=kernel` 显式启用；服务不可用时会报错，不会静默回写 v1 state。
- `1.1.0rc1` 是候选版本，不是默认后端切换版本。正式发布版本 R、v1 只读兼容窗口及单独维护者批准尚未确定。
- 主项目保留 `code-repair`、`svc-config`、对应 benchmark、core 和 fixture/deterministic/Pi adapters。历史 probe、agent、skills、MCP、web/export 等实验面在 `attic/`，不进入主 wheel、lint 和测试集。

发布门禁和版本边界见 [ADR 0015](docs/decisions/0015-v2-kernel.md)、[v2 release checklist](docs/operations/v2-release-checklist.md) 与 [候选验收记录](docs/research/v2-candidate-evidence.md)。

## 工作方式

典型闭环是：

```text
propose → run → verify → score → gate → adopt / revert → audit
```

`molt/core/` 保存协议、评分与门禁、账本、事件、快照、审计和事务；`shells/` 提供题目、harness 与 verifier；`molt/adapters/` 提供 fixture、deterministic 和 Pi runtime。Rust kernel 管理持久化对象、manifest、generation 与提交日志，不运行 agent，也不决定业务判分。

Python v1 记录与 Rust v2 state 是不同后端。v2 的 `--state` 是逻辑参数，实际 state 由 `MOLT_KERNEL_SOCKET` 指向的 kernel 实例管理；同一 socket 下的命令共享同一个 state。切换或恢复时必须明确核对 generation、state ID 和备份，不能把 v1 旧快照误认为包含后续 v2 写入。

## 环境

Python 基线固定为 3.12.13；依赖由 `uv` 管理。Rust kernel 使用仓库 `rust-toolchain.toml` 指定的 stable toolchain。

```bash
PYTHONDONTWRITEBYTECODE=1 uv sync --python 3.12.13 --extra dev
```

## 使用 Python v1

默认不设置 `MOLT_KERNEL_BACKEND` 时，CLI 使用 Python v1。state 放在仓库之外：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt demo \
  --mode replay --state /tmp/molt-demo
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt audit \
  --scenario demo --state /tmp/molt-demo
```

Replay 使用 fixture/replay 数据，不发起真实模型调用。真实 Pi 路径需要配置本机 Pi、sandbox 与预算；默认测试不会启用 live 调用。

## 使用 Rust v2 kernel

先构建并启动一个独立 kernel。`init` 只用于新 state 目录：

```bash
cargo build -p molt-kernel
target/debug/molt-kernel init --state /tmp/molt-v2-kernel
target/debug/molt-kernel serve --state /tmp/molt-v2-kernel
```

在另一个终端连接该服务并运行 replay 与审计：

```bash
export MOLT_KERNEL_BACKEND=kernel
export MOLT_KERNEL_SOCKET=/tmp/molt-v2-kernel/kernel.sock
target/debug/molt-kernel ping --socket "$MOLT_KERNEL_SOCKET"
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt demo \
  --mode replay --state /tmp/molt-v2-logical-demo
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt audit \
  --scenario demo --state /tmp/molt-v2-logical-demo
```

kernel 模式下传给 CLI 的 `--state` 路径必须尚不存在；它不是 kernel state 的磁盘路径。连接失败、协议错误或提交结果不明确时不会回退写入 v1。常驻使用应按 [systemd user service 运维说明](docs/operations/molt-kernel-systemd.md) 配置服务，并先演练[备份、checkpoint、恢复和回退](docs/operations/v2-backup-restore.md)。

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
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt scenario validate \
  --scenario scenarios/demo/scenario.json
cargo fmt --all --check
cargo test --workspace --features molt-kernel/fault-injection --quiet
cargo clippy --workspace --all-targets \
  --features molt-kernel/fault-injection -- -D warnings
```

Kernel UDS 集成测试需要先构建 Rust 可执行文件并设置 `MOLT_KERNEL_BIN`：

```bash
cargo build -p molt-kernel
MOLT_KERNEL_BIN=target/debug/molt-kernel \
  PYTHONDONTWRITEBYTECODE=1 uv run pytest -q tests/test_kernel_client.py
```

CI 包含 Rust 故障注入测试、Python/kernel UDS 集成、release bundle 构建和打包 kernel 的恢复测试。全量 Python 测试是集中执行的基线检查；Pi live 测试默认跳过。

## 不变量

- 每次 run、result、gate decision 和 ledger 必须能由 `molt audit` 复算；审计是只读的。
- `fixture`、`live`、`replay` 必须如实标注；holdout 与 search 隔离。
- 不跨 `scenario_version` 比较结果，也不把历史比赛数据说成当前 live 证据。
- v1 schema 与判分/gate 语义保持兼容；Rust kernel 只管理持久化和提交，不执行 agent 或业务判分。
- 回退到 v1 只恢复迁移时的 v1 快照，不包含之后产生的 v2 写入；v2 备份需独立保留并验证。

更完整的协作约定见 [AGENTS.md](AGENTS.md) 与 [CONTRIBUTING.md](CONTRIBUTING.md)，架构见 [docs/architecture.md](docs/architecture.md)，历史比赛 claim 及其限制以 [ADR-0008](docs/decisions/0008-demo.md) 和仓库协作约定为准。
