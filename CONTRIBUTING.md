# CONTRIBUTING

molt 的变更流程。契约与禁止清单见 [AGENTS.md](AGENTS.md)，格式约定见 [docs/style.md](docs/style.md)。

## 一、环境

```bash
PYTHONDONTWRITEBYTECODE=1 uv sync --python 3.12.13 --extra dev
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q
PYTHONDONTWRITEBYTECODE=1 uv run ruff check .
```

- 只用 `uv`。**禁止** `pip install` / `sudo pip` / `--break-system-packages`：
  本机是 Arch，系统 Python 由 pacman 管理，全局装包会污染 `/usr/lib/python3.11`。
- venv 在仓库内 `.venv/`（已 gitignore）；不要再建第二套解释器。
- 需要临时工具用 `uv run --with <pkg> ...`，不要写进 `pyproject.toml` 的运行时依赖。
- 依赖变更必须同时提交 `uv.lock`；CI 用 `PYTHONDONTWRITEBYTECODE=1 uv sync --python 3.12.13 --extra dev --locked`，lock 过期即失败。

## 二、分支

```bash
git switch -c feat/<area>/<slug> origin/main
```

- `<area>` ∈ `core` | `shell` | `bench` | `adapter` | `attic` | `docs` | `ci`。
- `<slug>` 小写连字符，一句话说清做什么：`feat/shell/svc-config`、`fix/bench/holdout-hash`。
- 通常从 `origin/main` 开；M0 特批 stacked 分支 `refactor/v2-scope-removal` 基于 `refactor/v2-kernel`；不允许直推 `main`，
  不允许对 `main` force push（分支保护与 CI 会拦）。

## 三、提交信息

Conventional Commits，标题英文、正文中文：

```
feat(shell): 新增 svc-config shell 的三端口实现

- harness 渲染 svc-config prompt；verifier 跑配置漂移检查
- switches.schema.json 仍为 6 组，tools 锁定 bash
- scenario_version 升到 svc-1
```

- 类型：`feat` / `fix` / `docs` / `test` / `refactor` / `chore` / `ci`。
- 一个提交只做一件事；格式化与逻辑改动分开提交，便于回滚。
- 涉及冻结面（§五）时，标题正文必须写明新 `scenario_version`。

## 四、评审矩阵（建议，非合并门禁）

| 变更面 | 建议评审 | 附加要求 |
| --- | --- | --- |
| 契约域：`schemas/**`、`molt/core/ports.py`、`docs/protocol.md`、`docs/decisions/**` | 1–2 人 | 必须有 ADR；建议至少 1 位 @FeiSaMa |
| 其余：shell / bench / adapter / attic / docs / CI | 1 人（建议） | — |

- 仓库不设 approve 数量门禁：两个 active ruleset（`main-protection` / `protect-main`）只有删除保护与
  禁 non-fast-forward，未登记 required approvals 或必需状态检查，approve 数量不构成合并条件。
- `.github/CODEOWNERS` 的作用是**自动请求 reviewer**，不是合并门禁；
  `molt doctor --repo-policy` 仍可做只读自检（协作文件、冻结面、契约域缺失即退出 1）。
- 评审意见未解决不合并；作者自己不能作为自己的 reviewer。
- 合并方式用 squash，保留一条可读的提交信息。

## 五、测试要求

- 行为变更必须带用例；只写实现不写用例的 PR 不合并。
- 全量：`PYTHONDONTWRITEBYTECODE=1 uv run pytest -q`，**手动 / 集中执行，不属于 CI**
  （CI 的 `kernel` 作业只运行 Rust、Python/kernel UDS、CLI 契约与安装包恢复子集）；PR 不要求贴全量结果。
- 真实 Pi 调用默认跳过（`pytest.mark.pi`），只有设置 `MOLT_RUN_PI_LIVE=1` 才跑，
  且受预算约束；CI 只在手动 `pi-smoke` 工作流里跑。
- 涉及仓库写入的测试，用文件系统树快照验收，**不要**用 `git status` 代替
  （pytest 自身会写 `__pycache__`）。
- 新增题集/题面：同步更新 `scenario.bench_hash` 与 genome 的 `scenario_version`，
  否则 `molt scenario validate --scenario scenarios/demo/scenario.json` 会失败。

## 六、Schema / 协议变更步骤

顺序固定，不可跳步：

1. **先改 schema**：`schemas/v1/<record>.schema.json`（顶层字段冻结，新增字段只能进 `ext`；
   major 变更才新开 `schemas/vN/`）。
2. **同步数据类**：`molt/core/types.py` 的 dataclass 与字段校验。
3. **生成文档**：`uv run python -m molt schema docs`，让 `docs/protocol.md` 由 schema 生成；
   **禁止手改** `docs/protocol.md`。
4. **双校验**：
   ```bash
   PYTHONDONTWRITEBYTECODE=1 uv run python -m molt schema validate --all
   PYTHONDONTWRITEBYTECODE=1 uv run python -m molt scenario validate --scenario scenarios/demo/scenario.json
   ```
5. **更新 golden**：`tests/fixtures/records/` 下对应记录样例；`tests/test_schema_compat.py`
   同时验证「删字段两侧都拒、`ext` 新增是 minor 兼容」。
6. **ADR**：`docs/decisions/000N-*.md` 写清动机、兼容性、回滚。

## 七、证据入库

- 历史展示聚合证据保存在 attic，不再导出或更新；新运行的 canonical state 放仓库外。
- **不入库**：`runs/` 原始日志、`state/`、`evidence/`、`.env`、`.pi/`。
- 提交前脱敏：`redaction` 报告覆盖的字段不得出现在 diff 里；任何 key/token 一律走 secrets。
- `live` 证据必须带成本与 `replay_of_run_id`（若为 replay），否则证据无效。

## 八、PR checklist（模板里的同一份）

- [ ] 写清影响的 **D 编号**（如 B2-D6），以及本次改动对应的验收方式。
- [ ] 标注**契约影响**：有 / 无。有则附 ADR 链接；评审为建议，仓库不设 approve 数量门禁。
- [ ] 标注是否触碰 **holdout**（`bench/holdout/**`、report 流、双分数）。
- [ ] 标注是否触碰 **verifier / scenario_version 构件**（冻结面，见 AGENTS §六）。
- [ ] **零写入**：`git status --porcelain` 为空；`tests/test_zero_write.py` 通过。
- [ ] **provenance**：`run.ext.provenance.kind` 与实际一致，fixture 未标 live。
- [ ] 粘贴验证命令与输出（`ruff`、`schema validate`、
      `scenario validate --scenario scenarios/demo/scenario.json`）。
- [ ] 全量 `pytest` 手动 / 集中执行，不属于 CI，非 PR 必贴项。
- [ ] 回滚方式：revert 提交 / `molt revert --to <archive_id>`。

## 九、CI 期望

`.github/workflows/ci.yml` 六个作业：`lint`、`schema`、`boundary`、`audit`、`secret-scan`、`kernel`；
`kernel` 运行 Rust 测试、Python/kernel UDS 与 CLI 契约子集，并在仓库外安装 wheel、
使用包内数据和 release kernel 验证 replay 备份恢复；全量 Python pytest 仍为手动 / 集中执行（见 §五）。`audit` 作业同时跑
`molt demo --mode replay`（180s 上限、state 落 `/tmp`），随后 audit 重算；不导出 web 数据。`pi-smoke` 是独立的手动工作流（`workflow_dispatch`），不是 CI 作业：
只跑 1 条 live task，`max_cost_usd` 是**本次单 run** 的运行后验收上限（不是 scenario 总预算），
超过即 fail。CI 只用仓库 secrets，任何 key 都不得写进工作流文件。

已知兼容性缺口：3.11 下全量 `pytest -q` 有 4 例浮点差异——`test_gate_policy` 的 G1、G3，
`test_gate_cli` 与 `test_zero_write`（都经 `gate --fixture`）。原因是 CPython 3.12 起内置
`sum()` 改用 Neumaier 补偿求和，`tests/fixtures/gate/` 里 `cost_per_solved_usd` 的期望值
按 3.12 的浮点结果固化，3.11 下差 1e-16。因此**手动 / 集中执行全量 pytest 时用 3.12**；
修复需改 gate 夹具或引入容差，会碰 `bench/**` 或 `tests/fixtures/`，按冻结面流程另行开 PR。

分支保护现状：仓库有**两个 active ruleset**（`main-protection` 与旧的 `protect-main`）。
两者求并集，严格项为准：启用的规则是删除保护与禁 non-fast-forward，
**未登记必需状态检查**；CI 作业不构成合并阻塞项。
旧 ruleset 是否删除由用户决定，未在本批次处理。

## M0 评审交接

attic 已从 wheel、ruff 与 pytest 排除；历史模块不保证在新路径直接运行。
M0 变更可推送到基于 refactor/v2-kernel 的 draft PR，不合并 main；基线采用 Python 3.12.13，详情见 docs/research/v2-baseline.md。
恢复见 attic/README.md；不修改 v1 schema、判分、gate 或题集语义。
