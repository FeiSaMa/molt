# molt Linux 安装包

此安装包用于 Linux x86_64 单用户环境，需要 uv 和 systemd 用户管理器。

解包后从该目录执行安装器。安装器先在发布目录的同一文件系统准备独立版本，再核验 CLI 与 kernel 可执行文件，最后切换 `~/.local/bin` 入口和用户 unit 并运行 `daemon-reload`。同版本精确重跑不重建虚拟环境；冲突的用户文件会被拒绝。升级会保留已安装版本和工作区状态，不会自动切换运行中的服务。

项目的 MIT 许可证见 `LICENSE`，Rust 二进制所含第三方 crate 的许可文本见 `THIRD-PARTY-LICENSES.txt`。用户级安装会将两份文件复制到安装前缀。

首次安装时，从解包后的目录运行：

```bash
bash ./install-user.sh
"$HOME/.local/bin/molt-kernel" init --state "$HOME/.local/share/molt/kernel"
systemctl --user enable --now molt-kernel.service
export MOLT_KERNEL_SOCKET="$HOME/.local/share/molt/kernel/kernel.sock"
"$HOME/.local/bin/molt" inspect
"$HOME/.local/bin/molt" audit
```

已有实例升级时，先按[备份与恢复](docs/operations/v2-backup-restore.md)保留状态和检查点，再运行新版本安装器。确认安装完成后，按[服务手册](docs/operations/molt-kernel-systemd.md)重启服务并审计。切换或 reload 失败时安装器恢复原入口和 unit，并保留可核对的旧入口副本；重新运行同一安装器可重试。补偿失败时依据终端报告的备份目录核对并恢复三个入口，再运行 `systemctl --user daemon-reload`。初始化命令仅用于新状态目录。

对象、事务、原 request ID 恢复与旧状态拒绝行为见 [CLI 参考](docs/operations/kernel-cli.md)。

只想在独立目录试用配置与工具版本时，从解包目录按[个人工作区手册](docs/operations/version-cli.md)安装 wheel、启动随包 kernel。版本事务与内置文本工具使用随包运行时。模型提案和摘要另外需要 Bubblewrap、llama-completion 及已安装的兼容 GGUF。
