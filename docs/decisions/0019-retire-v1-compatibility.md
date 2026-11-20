# ADR-0019 · 退出 v1 兼容

状态：已实施。产品入口和候选制品已切换，历史迁移源码归档于 `attic/v1/`。

## 交付边界

后续以 kernel 为唯一事务后端。新源码和新制品退出 v1 状态读取、语义重算和显式迁移。历史发行及验收仍绑定原归档和源码。需要读取或导出旧状态时，使用当时已验收的版本和独立的旧数据副本。

本决定涉及产品的兼容面。保留 kernel 的单写者、持久提交点、原 request ID 恢复、checkpoint 与审计，以及 `project` / `version` 的现有快照读取、分支、合并和回退。历史 v1 字段和证据继续按当时定义解释。

## 实施与验收

1. **退出公开入口。** 从 `molt/cli.py` 的 backend 分派、`audit --scenario/--shell`、`migrate` 和 `cmd_migrate` 入手。核查 `schema`、`doctor`、`_repo_root`、`write_policy` 和环境变量的调用关系。为显式 `MOLT_KERNEL_BACKEND=v1` 保留清晰的失败行为，禁止降级为 kernel 写入。用隔离 v1 夹具验证拒绝及源目录零写入。同步当前中英文 README、CLI 手册和贡献指南。
2. **退出仅供 v1 的制品与回归。** 核对 `molt/kernel_mirror.py`、`schemas/v1/`、`molt/schema.py`、`docs/protocol.md`、`tests/test_v1_compatibility.py`、`tests/fixtures/records/` 以及历史 core 模块的导入和打包引用。`molt.core.fsutil` 和 `molt.core.hash` 仍由项目版本流使用。逐项确认哪些文件仅供 v1，先生成完整路径与 SHA-256 的 dry-run，再依项目规则取得删除或取消跟踪的明确确认。同步 `pyproject.toml`、两个 GitHub workflow，以及构建和验包脚本。历史 ADR、发行记录继续如实描述原行为。
3. **验收新基线。** 原子事务、checkpoint、审计、固定读取、未保存编辑保护和原请求恢复继续通过真实 UDS 测试。检查新 wheel、sdist 和 Linux 包各自的文件清单，从仓库外验证安装后的通用作业流程与旧入口的明确拒绝。记录源码指纹、包标识及 SHA-256。新制品使用独立标识，历史归档继续保留。

R01 的项目 CLI 回归和 R03 的现有 kernel 工作区迁址演练可以独立推进。R02 的新包验收采用退出 v1 后的制品清单。

原 `molt/kernel_mirror.py` 已移动到 [attic/v1/kernel_mirror.py](../../attic/v1/kernel_mirror.py)。历史源码测试继续引用该归档验证保真和重试。冻结 schema、记录夹具与协议生成一致性仍在源码 CI 中校验。
