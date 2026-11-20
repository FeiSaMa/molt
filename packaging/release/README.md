# molt Linux 安装包

此安装包用于 Linux x86_64 单用户环境，需要 uv 和 systemd 用户管理器。

首次安装时，从解包后的目录运行：

```bash
bash ./install-user.sh
"$HOME/.local/bin/molt-kernel" init --state "$HOME/.local/share/molt/kernel"
systemctl --user enable --now molt-kernel.service
export MOLT_KERNEL_SOCKET="$HOME/.local/share/molt/kernel/kernel.sock"
"$HOME/.local/bin/molt" inspect
"$HOME/.local/bin/molt" audit
```

已有实例升级时，先按[备份与恢复](docs/operations/v2-backup-restore.md)保留状态和检查点，按[服务手册](docs/operations/molt-kernel-systemd.md)停止旧服务、更新后审计并重启。初始化命令仅用于新状态目录。

对象、事务、原 request ID 恢复与旧状态拒绝行为见 [CLI 参考](docs/operations/kernel-cli.md)。

只想在独立目录试用配置与工具版本时，从解包目录按[个人工作区手册](docs/operations/version-cli.md)安装 wheel、启动随包 kernel。版本事务与内置文本工具使用随包运行时。模型提案和摘要另外需要 Bubblewrap、llama-completion 及已安装的兼容 GGUF。
