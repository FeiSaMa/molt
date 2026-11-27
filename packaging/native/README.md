# Linux 原生包候选

当前维护者候选版本为 `1.2.0rc10`。Debian/RPM 包版本附加候选提交短 SHA，Arch 包固定源码到 `3e7d01bb1ea32f307fbb109553f7bf9b968861b6` 并附加 `+git3e7d01b`。当前只为 Linux x86_64 构建。Rust 二进制最低要求 glibc 2.43。

`build-deb-rpm.sh OUTPUT_DIR NFPM_BIN` 从当前源码构建 Debian 和 RPM 包。调用者须提供 nfpm 2.x、uv、Python 3.12.13 和 Rust 工具链。构建先检查 uv lock，再从锁定依赖构建 wheel、kernel 和 TUI，核对第三方 Rust 许可声明，最后生成包和 SHA-256 清单。脚本拒绝覆盖相同版本输出。

Arch Linux 使用仓库根目录的 `PKGBUILD`。它固定 GitHub 源码归档 URL 和 SHA-256，从源码构建本机二进制，不依赖预编译发布资产。常规构建命令是 `makepkg --syncdeps --cleanbuild`。

包安装到 `/usr/bin`、`/usr/lib/molt`、`/usr/lib/systemd/user` 和 `/usr/share`。安装后可由用户自行启用 `molt-kernel.service`。打包不自动启用或启动服务，也不修改用户状态。CLI 运行需要 Python 3.11 或更高版本，原始 wheel 保存在 `/usr/lib/molt/molt-python.whl` 并由启动器通过 zip import 使用。

`.github/workflows/native-packages.yml` 在 Debian sid、Fedora Rawhide 和 Arch Linux 容器中运行对应包管理器的安装、升级和卸载流程。它先安装 `1.2.0rc9` 基线包，再升级到当前提交构建的候选包，验证依赖解析、安装文件、CLI/TUI 入口、卸载清理，以及用户目录数据保留。基线包使用同一候选构建内容并只降低包版本，因此矩阵验证包管理器事务与文件生命周期，不代表旧发行包到新发行包的应用行为回归。

三种包都声明 glibc 2.43 下限，因此 CI 使用提供该 glibc 基线的 Debian sid 和 Fedora Rawhide，以及 Arch Linux rolling。验收通过覆盖这些 rolling 环境中的 x86_64 包管理器流程。生成的候选包尚未签名、上传或发布。
