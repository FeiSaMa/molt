# 构建和验证 Linux 安装包

安装包适用于 Linux x86_64，常驻运行需要 systemd 用户管理器。安装后使用 kernel，v1 状态保留只读审计和显式迁移。1.1.0 的兼容期见[发行说明](v2-release-1.1.0.md)。

在源码仓库根目录运行：

```bash
work=$(mktemp -d /tmp/molt-package-XXXXXX)
archive=$(bash packaging/release/build-linux-x86_64.sh "$work/out")
sha256sum --check "$archive.sha256"
mkdir "$work/unpacked"
tar -xf "$archive" -C "$work/unpacked"
bash packaging/release/verify-bundle.sh "$work/unpacked"/molt-* "$work/verification"
```

构建脚本拒绝覆盖已有归档。验证脚本在源码树外安装 wheel，检查打包内容、schema、服务配置以及真实 UDS 上的事务、断线恢复和 v1 兼容。

验收候选发布时，还要用同一包演练持久用户服务安装、异常退出重启、磁盘空间耗尽及权限错误。保留独立 checkpoint，演练备份、恢复和升级回退。记录源码、精确归档路径和 SHA-256、隔离安装位置、运行命令及退出码。
