# molt Linux 安装包

本候选包 1.2.0rc9 用于 Linux x86_64 单用户环境，内含同源的 CLI wheel、TUI 和 kernel。用户安装器需要 uv、Python 3.12.13 和可用的 systemd 用户管理器。独立工作区可直接使用包内文件和前台 kernel。

解包后从归档顶层运行安装器。安装器在同一文件系统准备独立版本，检查 CLI 回执选项、TUI 自检和 kernel 启动，再切换 `~/.local/bin` 入口和用户 unit 并运行 `daemon-reload`。重复运行同一精确包会复核已安装版本。遇到冲突的用户文件会退出并保留原文件。升级后由操作者决定何时重启运行中的服务。

项目的 MIT 许可证见 `LICENSE`，Rust 二进制所含第三方 crate 的许可文本见 `THIRD-PARTY-LICENSES.txt`。用户级安装会将两份文件复制到安装前缀。

首次安装时，从解包后的目录运行：

```bash
bash ./install-user.sh
"$HOME/.local/bin/molt" tui --mock --selftest --width 80 --height 24
"$HOME/.local/bin/molt-kernel" init --state "$HOME/.local/share/molt/kernel"
systemctl --user enable --now molt-kernel.service
export MOLT_KERNEL_SOCKET="$HOME/.local/share/molt/kernel/kernel.sock"
"$HOME/.local/bin/molt" inspect
"$HOME/.local/bin/molt" audit
```

已有实例升级时，先按[备份与恢复](docs/operations/v2-backup-restore.md)保留状态和检查点，再运行新版本安装器。确认安装完成后，按[服务手册](docs/operations/molt-kernel-systemd.md)重启服务并审计。切换或 reload 失败时安装器恢复原入口和 unit，并保留可核对的旧入口副本；重新运行同一安装器可重试。补偿失败时依据终端报告的备份目录核对并恢复三个入口，再运行 `systemctl --user daemon-reload`。初始化命令仅用于新状态目录。

对象、事务、原 request ID 恢复与旧状态拒绝行为见 [CLI 参考](docs/operations/kernel-cli.md)。

只想在独立目录试用配置与工具版本时，从解包目录按[个人工作区手册](docs/operations/version-cli.md)安装 wheel、启动随包 kernel。版本事务与内置文本工具使用随包运行时。模型提案和摘要另外需要 Bubblewrap、llama-completion 及已安装的兼容 GGUF。

项目级 CLI 可供脚本或自有 Agent 工作流读取固定配置。包内的 Bash 示例可从解包目录
演练保存、固定读取、回退和审计。Node.js 示例展示如何把快照 ID 和配置交给自己的作业。
[项目工作区快速开始](docs/operations/project-quickstart.md)

## 离线终端试用

先在归档所在目录运行 `sha256sum -c molt-1.2.0rc9-linux-x86_64.tar.gz.sha256`，再解包。在用户安装后，`"$HOME/.local/bin/molt" tui --mock` 可直接启动。裸 `molt` 在交互终端发现可用 TUI 时进入默认 live Chat；自动查找缺失、关闭 TUI 或非交互输入时显示 CLI 帮助。显式 `MOLT_TUI_BIN` 可覆盖同前缀的二进制。Ctrl+Q 退出。

要使用真实 Versions 与固定 Tree，按[工作区手册](docs/operations/version-cli.md#安装与首次运行)从归档顶层建立新的 synthetic 工作区，并在同一 Bash 中运行该手册的「终端界面」示例。保存需要本包的 `commit-files --include-request-id`。Chat 用 `--mock` 离线运行，Settings、Diff、Approval 显示模拟数据。

### 试用记录

| 项目 | 请填写实测值 |
| --- | --- |
| 制品与环境 | 归档 SHA-256、Linux/架构、Python、uv、终端尺寸、依赖来源 |
| 安装与启动 | 工作目录、安装方式及前缀、命令、退出码、首次卡点及维护者介入 |
| 保存与恢复 | generation、snapshot、event、原 request ID 查询结果；Esc 返回、冲突前后草稿与刷新重试 |
| 审计与读取 | 按 event ID 查询的理由、固定 Tree 的快照和文件字节或摘要 |
| 退出与反馈 | TUI/服务退出码、临时目录回收、脱敏转录和使用者原话 |
