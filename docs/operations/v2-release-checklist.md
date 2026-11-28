# 构建和验证 Linux 安装包

安装包适用于 Linux x86_64，常驻运行需要 systemd 用户管理器。候选版以 kernel 为唯一事务后端。旧状态档案见[发行说明](v2-release-1.1.0.md)。

在源码仓库根目录运行：

```bash
work=$(mktemp -d /tmp/molt-package-XXXXXX)
archive=$(bash packaging/release/build-linux-x86_64.sh "$work/out")
(cd "$work/out" && sha256sum --check "$(basename "$archive").sha256")
mkdir "$work/unpacked"
tar -xf "$archive" -C "$work/unpacked"
sdist="${archive%-linux-x86_64.tar.gz}.tar.gz"
bash packaging/release/verify-bundle.sh "$work/unpacked"/molt-* "$work/verification" "$sdist"
```

构建脚本生成 wheel、sdist 和 Linux 候选归档，并拒绝覆盖已有同名文件。记录三个制品的 SHA-256、
源码清单和隔离安装路径。验包脚本检查 wheel、sdist 和 Linux 包边界与服务配置，然后从仓库外安装
wheel。真实 UDS 回归覆盖原 request ID 恢复、A/B 固定读取和回退、按 B 的事件 ID 查审计，
以及旧入口拒绝与旧状态目录树零写入。带断点的项目保存恢复使用启用 fault-injection 的调试 kernel。

版本说明：本节的 `1.2.0rc9` 与 rc8 内容是历史候选上下文，不代表当前发布入口；当前发布见 [1.2.0rc10 发行说明](../releases/1.2.0rc10.md)。本地候选 1.2.0rc9 把配套的 CLI、TUI 与默认功能 kernel 打入同一归档。构建按照 `Cargo.lock` 为两个 Rust 二进制核对目标依赖许可，按照 `packaging/release/build-requirements.txt` 的版本与哈希取得 wheel 构建后端。每次候选使用新输出目录，以源码清单、锁文件、二进制及归档的 SHA-256 标识。历史 1.1.0 和 rc8 各自的验收只对应其精确归档。

旧包升级验收需要另外核验旧归档的校验文件，再解包并作为第三个参数交给下列脚本。省略旧包时，验包流程使用当前候选验证两处安装前缀切换。

```bash
bash packaging/release/verify-user-install.sh "$work/unpacked"/molt-* "$work/upgrade" "$old_bundle"
```

CI 的包外入口冒烟运行已安装的 TUI 80×24 selftest，并验证同前缀选择、显式覆盖、失败保全及配套回执选项。真实交互另按包内 `share/molt/README.md` 启动 mock Chat、Versions 和固定 Tree，记录 PTY 转录、CLI 对照与退出清理；断点恢复另用同源码 fault-injection kernel 验证。

验收候选发布时，还要用同一包演练持久用户服务安装、异常退出重启、磁盘空间耗尽及权限错误。保留独立 checkpoint，演练备份、恢复和升级回退。记录源码、精确归档路径和 SHA-256、隔离安装位置、运行命令及退出码。I05 已在隔离用户管理器的 transient unit 中完成两种场景，持久 `enable --now`、登录自启、多用户权限隔离和正式环境责任确认仍是未验收门项。

包外验证同时运行 `packaging/release/verify-user-install.sh`，覆盖精确重跑、旧版本升级、预存用户文件、准备失败、入口切换失败、reload 失败及补偿失败。该脚本使用 `systemctl` 替身，只验证安装器状态变化，不作为真实用户服务启动证据。真实服务仍需在隔离的 systemd 用户管理器内完成首次启动、升级、恢复与重启。
