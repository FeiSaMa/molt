## 变更摘要

<!-- 具体问题、最终行为和范围；创建 PR 必须已有用户授权。 -->

## 对应任务与契约影响

- 任务 ID：
- 契约影响：无 / 有（附 ADR 与兼容性说明）
- 冻结 v1 字段、历史 scenario_version 与 provenance 语义是否保持：
- 比赛资产退出是否依赖完整 dry-run 清单确认：

## 验证证据

<!-- 记录完整命令、cwd、必要环境、退出码、日志与源码绑定；全量 Python 为手动集中执行。
     包变更另附精确路径、SHA-256、仓库外安装位置；本地检查不能冒充远端 CI。 -->

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.12.13 ruff check .
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.12.13 python -m molt schema validate --all
PYTHONDONTWRITEBYTECODE=1 uv run --python 3.12.13 python -m molt doctor --deps
```

- 相关测试及结果：
- 未验证项与限制：
- 回退 / 恢复步骤：

## Checklist

- [ ] 新增行为有用例，必要控制及替代覆盖保留
- [ ] 协议生成物一致，v1 只读审计与显式迁移兼容保持
- [ ] 零写入用文件系统树快照验证；git status 仅辅助
- [ ] 同 request ID 幂等、unknown 查询/恢复和单写者边界保持
- [ ] 合成证据标 synthetic；历史 fixture/live/replay 如实保留
- [ ] 不把 kernel 提交当成业务成功，不把 generation 回退当成外部撤销
- [ ] 脱敏通过；无凭据或敏感原文；必要控制失效时拒绝执行
- [ ] 哈希链 / audit 未被宣称为执行真实性证明或 tamper-proof
- [ ] handoff、TODO、实现与证据一致；未确认删除不标整体退出 DONE
