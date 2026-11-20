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

对象、事务、原 request ID 恢复和 v1 兼容命令见 [CLI 参考](docs/operations/kernel-cli.md)。配置和工具的版本示例需要源码仓库。
