# molt

molt 是 agent 的外层编排器：propose → run → verify → score → gate → adopt / rollback。
它交付协议、门禁、事务和可复算证据，不训练模型或提供 agent 能力。

当前生产默认仍为 Python v1。Rust v2 kernel 与 M3 迁移路径已实现并可通过
`MOLT_KERNEL_BACKEND=kernel` 显式启用；默认切换仍受 [ADR 0015](docs/decisions/0015-v2-kernel.md)
的发布门禁约束。Linux systemd user service 示例见
[kernel 运维说明](docs/operations/molt-kernel-systemd.md)。
历史实验模块与展示资产已物理移入 [attic](attic/README.md)，不参与构建、lint 或主测试集。

## 使用

固定 Python 3.12.13，所有 state 均放仓库之外：

```bash
PYTHONDONTWRITEBYTECODE=1 uv sync --python 3.12.13 --extra dev
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q
PYTHONDONTWRITEBYTECODE=1 uv run ruff check .
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt doctor --deps
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt schema validate --all
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt demo --mode replay --state /tmp/molt-demo
PYTHONDONTWRITEBYTECODE=1 uv run python -m molt audit --scenario demo --state /tmp/molt-demo
```

`demo` 保存 canonical state 与 `demo-summary.json`，不再导出页面数据。
真实 Pi 调用需本机 Pi、bwrap 及预算；默认测试不发起 live 调用。

## 边界与证据

保留 `molt/core`、fixture/deterministic/pi adapters、code-repair 与 svc-config shells、
对应题集和场景。v1 schema、判分、gate 语义冻结。审计从 run/ledger 重算，
fixture/live/replay 必须如实标注，禁止跨 scenario_version 比较。

历史演示的三层数字与局限以 [AGENTS.md](AGENTS.md) 和
[ADR-0008](docs/decisions/0008-demo.md) 为 canonical 位置；不将历史 claim 当成新结果。
历史展示证据在 attic 保存，原始 v1 可从 `v1-final` 恢复。

协作见 [CONTRIBUTING.md](CONTRIBUTING.md)，架构见 [docs/architecture.md](docs/architecture.md)，
基线与失败归因见 [v2-baseline](docs/research/v2-baseline.md)。
