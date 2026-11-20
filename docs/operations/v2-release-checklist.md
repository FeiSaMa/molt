# 构建和验证 Linux 安装包

安装包适用于 Linux x86_64，常驻运行需要 systemd 用户管理器。候选版以 kernel 为唯一事务后端。旧状态档案见[发行说明](v2-release-1.1.0.md)。

在源码仓库根目录运行：

```bash
work=$(mktemp -d /tmp/molt-package-XXXXXX)
archive=$(bash packaging/release/build-linux-x86_64.sh "$work/out")
sha256sum --check "$archive.sha256"
mkdir "$work/unpacked"
tar -xf "$archive" -C "$work/unpacked"
sdist="${archive%-linux-x86_64.tar.gz}.tar.gz"
bash packaging/release/verify-bundle.sh "$work/unpacked"/molt-* "$work/verification" "$sdist"
```

构建脚本生成 wheel、sdist 和 Linux 候选归档，并拒绝覆盖已有同名文件。记录三个制品的 SHA-256、
源码清单和隔离安装路径。验包脚本检查 wheel、sdist 和 Linux 包边界与服务配置，然后从仓库外安装
wheel。真实 UDS 回归覆盖原 request ID 恢复、A/B 固定读取和回退、按 B 的事件 ID 查审计，
以及旧入口拒绝与旧状态目录树零写入。带断点的项目保存恢复使用启用 fault-injection 的调试 kernel。

1.1.0 的验收只对应其发行记录中的精确归档。候选包使用独立标识。公开许可、授权范围和正式发行另行决定。

验收候选发布时，还要用同一包演练持久用户服务安装、异常退出重启、磁盘空间耗尽及权限错误。保留独立 checkpoint，演练备份、恢复和升级回退。记录源码、精确归档路径和 SHA-256、隔离安装位置、运行命令及退出码。
