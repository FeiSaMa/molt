# CONTRIBUTING

molt 的变更流程。契约与禁止清单见 [AGENTS.md](AGENTS.md)，格式约定见 [docs/style.md](docs/style.md)。

## 一、环境

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check .
```

- 只用 `uv`。**禁止** `pip install` / `sudo pip` / `--break-system-packages`：
  本机是 Arch，系统 Python 由 pacman 管理，全局装包会污染 `/usr/lib/python3.11`。
- venv 在仓库内 `.venv/`（已 gitignore）；不要再建第二套解释器。
- 需要临时工具用 `uv run --with <pkg> ...`，不要写进 `pyproject.toml` 的运行时依赖。
- 依赖变更必须同时提交 `uv.lock`；CI 用 `uv sync --extra dev --locked`，lock 过期即失败。

## 二、分支

```bash
git switch -c feat/<area>/<slug> origin/main
```

- `<area>` ∈ `core` | `shell` | `bench` | `adapter` | `web` | `docs` | `ci`。
- `<slug>` 小写连字符，一句话说清做什么：`feat/shell/svc-config`、`fix/bench/holdout-hash`。
- 分支从 `origin/main` 开，不基于别人的未合并分支；不允许直推 `main`，
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

## 四、评审矩阵

| 变更面 | 最少 approve | 附加要求 |
| --- | --- | --- |
| 契约域：`schemas/**`、`molt/core/ports.py`、`docs/protocol.md`、`docs/decisions/**` | 2 | 必须有 ADR；至少 1 位 @FeiSaMa |
| 其余：shell / bench / adapter / web / docs / CI | 1 | — |

- 契约 PR 单人 approve 不得合并（这是 B2-D6 的硬要求；规则由分支保护 + CODEOWNERS 承载，
  `molt doctor --repo-policy` 尚未实现，实现前评审时人工核对）。
- 评审意见未解决不合并；作者自己不能作为自己的 reviewer。
- 合并方式用 squash，保留一条可读的提交信息。

## 五、测试要求

- 行为变更必须带用例；只写实现不写用例的 PR 不合并。
- 全量：`PYTHONDONTWRITEBYTECODE=1 uv run pytest -q`。
- 真实 Pi 调用默认跳过（`pytest.mark.pi`），只有设置 `MOLT_RUN_PI_LIVE=1` 才跑，
  且受预算约束；CI 只在手动 `pi-smoke` 工作流里跑。
- 涉及仓库写入的测试，用文件系统树快照验收，**不要**用 `git status` 代替
  （pytest 自身会写 `__pycache__`）。
- 新增题集/题面：同步更新 `scenario.bench_hash` 与 genome 的 `scenario_version`，
  否则 `molt scenario validate` 会失败。

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
   PYTHONDONTWRITEBYTECODE=1 uv run python -m molt scenario validate
   ```
5. **更新 golden**：`tests/fixtures/records/` 下对应记录样例；`tests/test_schema_compat.py`
   同时验证「删字段两侧都拒、`ext` 新增是 minor 兼容」。
6. **ADR**：`docs/decisions/000N-*.md` 写清动机、兼容性、回滚。

## 七、证据入库

- 只入库**可复算的聚合证据**：`web/data/**` 由 `molt export` 生成，`index.json` 的 sha256
  必须与文件一致（`tests/test_export_contract.py` 会校验）。
- **不入库**：`runs/` 原始日志、`state/`、`evidence/`、`.env`、`.pi/`。
- 提交前脱敏：`redaction` 报告覆盖的字段不得出现在 diff 里；任何 key/token 一律走 secrets。
- `live` 证据必须带成本与 `replay_of_run_id`（若为 replay），否则导出 fail closed。

## 八、PR checklist（模板里的同一份）

- [ ] 写清影响的 **D 编号**（如 B2-D6），以及本次改动对应的验收方式。
- [ ] 标注**契约影响**：有 / 无。有则附 ADR 链接与 2 位 reviewer。
- [ ] 标注是否触碰 **holdout**（`bench/holdout/**`、report 流、双分数）。
- [ ] 标注是否触碰 **verifier / scenario_version 构件**（冻结面，见 AGENTS §六）。
- [ ] **零写入**：`git status --porcelain` 为空；`tests/test_zero_write.py` 通过。
- [ ] **provenance**：`run.ext.provenance.kind` 与实际一致，fixture 未标 live。
- [ ] 粘贴验证命令与输出（`ruff`、`pytest`、`schema validate`、`scenario validate`）。
- [ ] 回滚方式：revert 提交 / `molt revert --to <archive_id>` / 重生成导出。

## 九、CI 期望

`.github/workflows/ci.yml` 六个作业必须全绿：`lint`、`schema`、`boundary`、`unit`、
`audit`、`secret-scan`。`pi-smoke` 是独立的手动工作流（`workflow_dispatch`，带费用上限），
不属于必需检查。CI 只用仓库 secrets，任何 key 都不得写进工作流文件。

已知兼容性缺口：CI 的 `unit` 目前只在 **3.12** 上跑（必需检查名为 `unit (3.12)`）。
3.11 暂不进 CI：CPython 3.12 起内置 `sum()` 改用 Neumaier 补偿求和，
`tests/fixtures/gate/` 里 `cost_per_solved_usd` 的期望值按 3.12 的浮点结果固化
（3.11 下 4 条断言差 1e-16）。修复方向是统一取整或重生成夹具，需单独开 PR
（会碰 `bench/**` 或 `tests/fixtures/`，按冻结面流程处理）；修好再把 3.11 加回矩阵。
在此之前，本地用 3.11 跑出的 red 不代表本次改动有问题。
