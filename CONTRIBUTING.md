# 贡献指南

用户安装和快速开始见 [README](README.md)。模块职责与事务边界见 [架构](docs/architecture.md)。

初次试用可先运行[双文件工作区示例](docs/operations/project-quickstart.md)。
报告普通缺陷时，请附上版本或 commit SHA、运行环境、最小复现命令、退出码和脱敏输出。
[问题反馈](https://github.com/FeiSaMa/molt/issues) · [安全报告](SECURITY.md)

## 开发环境

在 Linux 上准备 uv 和 Rust，然后在仓库根目录运行：

```bash
PYTHONDONTWRITEBYTECODE=1 uv sync --locked --python 3.12.13
cargo build --locked -p molt-kernel
```

开发时使用独立的临时状态目录和 Unix socket。通过 `MOLT_KERNEL_SOCKET` 指定测试实例，服务的启动、停止和恢复步骤见 [CLI 参考](docs/operations/kernel-cli.md)。

## 验证

先运行受影响的测试。涉及状态事务、客户端或协议时，再运行对应的全量检查：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --locked --extra dev ruff check .
PYTHONDONTWRITEBYTECODE=1 uv run --locked --extra dev pytest -q
PYTHONDONTWRITEBYTECODE=1 uv run --locked python -m molt.schema validate --all
cargo test --locked -p molt-kernel --features fault-injection
cargo fmt --all -- --check
cargo clippy --locked -p molt-kernel --all-targets --features fault-injection -- -D warnings
```

在临时状态上验证断线恢复时，记录原 request ID，分别检查提交前中断和提交后丢响应的结果。兼容修改须覆盖旧状态的只读审计与显式迁移。修改文档时按源码验证命令，修改安装包时从仓库外安装新包并执行对应操作。

## 协议与评审

冻结的 v1 schema 和 Python 类型定义用于解释历史记录。`docs/protocol.md` 由仓库内的 `python -m molt.schema docs` 生成。协议所有权见 [CODEOWNERS](.github/CODEOWNERS)，实现、权限和证据规则见 [AGENTS.md](AGENTS.md)。

提交信息使用中文 Conventional Commits。评审说明应写清用户可见行为、兼容影响、已执行的验证和未验证的场景。

## 构建安装包

Linux x86_64 候选包的构建、隔离安装验证和制品核对步骤见 [构建与验证](docs/operations/v2-release-checklist.md)。
