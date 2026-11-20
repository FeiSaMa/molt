# molt 跨会话执行清单

日期：2026-11-19（UTC+8）

## 使用方式与范围

本文件是任务清单；`/home/feisama/.local/state/codex/molt-handoff.md` 是当前执行检查点；[启动 prompt](continue-prompt.md) 定义每次恢复和更新纪律。设计背景见 [推进建议](next-steps-2026-11-19.md)。

状态采用 `TODO / DOING / DONE / BLOCKED / DEFERRED`。只有证据已经写入 handoff，才能把任务标为 DONE；BLOCKED 必须写具体依赖和解除条件，不能用来表示“尚未开始”。每次最多一个 DOING。

本清单创建时未开始新实现。用户在新会话发送启动 prompt 后，才按该 prompt 授权推进 A、B、C 的非破坏性本地工作；D 为外部条件准备，E 暂缓。清单本身不批准删除、发布、M4/M5 或真实账号访问。

## A：比赛设计退出与通用底座验收

| ID | 初始状态 | 工作与验收 |
| --- | --- | --- |
| A01 | DONE | 核对当前 HEAD、既有修改、退出范围与依赖。记录保留/替代/退出的入口；识别 AGENTS 等旧约束与 ADR-0017 的冲突，不把旧比赛约束误当新方向。 |
| A02 | DONE | 为真实 UDS 集成准备有界合成对象，替代测试对比赛 demo 的依赖；保留 generation、幂等、冲突、断线、权限、空间不足及恢复的既有覆盖。 |
| A03 | DONE | 为 v1 只读审计和显式迁移准备专用兼容夹具，摆脱比赛目录依赖；校验源数据无写入、迁移重试和冲突语义。 |
| A04 | DONE | 改写 CLI 与加载入口，退出比赛专用命令和配置；通用查询、审计、checkpoint、迁移和恢复仍有实际调用路径及帮助说明。 |
| A05 | DONE | 改写构建、安装包验证与 CI：不携带或导入拟退出资产；从仓库外运行安装包级通用事务和恢复验证。 |
| A06 | DONE | 对齐 AGENTS、CONTRIBUTING、architecture、style、CODEOWNERS 和 PR 模板；只修订已确定退出范围，保留协议和安全约束。 |
| A07 | DONE | 复核剩余引用与链接，明确哪些历史协议代码因 v1 兼容保留；不能按文件名盲删 adapter/core。 |
| A08 | DONE | 执行批量退出：必须先有用户对完整 dry-run 清单的明确确认。确认后复核每条路径和摘要，以 `gio trash` 处理；变化项停止复核，新增路径重新预览确认。不递归删目录。 |
| A09 | DONE | 实现变更相关子集通过后，执行必要的集中 Python 回归、Rust fault injection、fmt/clippy、Ruff、schema/生成物一致性、依赖和协作检查。旧检查若引用被移除命令，先合理替代检查，禁止直接跳过。 |
| A10 | DONE | 以独立新路径构建候选包，记录源码状态、工具版本、精确包 SHA-256；隔离安装后验证真实 UDS、查询、checkpoint、备份恢复、审计及 v1 兼容；不覆盖旧 `1.1.0` 归档。 |
| A11 | DONE | 核对主运行/打包/CI 不再依赖退出资产，实际删除与最终清单一致；汇总源码和精确包验收证据、残余限制。A08 未完成则 A 阶段不能验收为 DONE。 |

A08 等确认时，可先推进不依赖实际删除的工作、B 的契约梳理与 C 的设计；不得绕过确认。也不得把本地禁用/排除资产说成“已删除”。

## B：最小外部集成契约

| ID | 初始状态 | 工作与验收 |
| --- | --- | --- |
| B01 | DONE | 盘点现有 kernel/Python 公共接口、错误码与信任边界，区分可直接复用、文档缺失和真正需要新增的能力。 |
| B02 | DONE | 编写集成契约：任务/request/外部业务 ID 关联、调用者身份来源、证据来源、unknown、查询重试、超时、恢复及人工接管；区分已有接口与提议，不编造可用 API。 |
| B03 | DONE | 定义最小记录及隐私规则：只保存必要摘要或受控引用，限制原始信息、日志、访问和留存；不把哈希当加密或匿名化。 |
| B04 | DONE | 在合适的独立示例位置编写无模型、无凭据的最小客户端；优先只组合现有 API，不新增通用 Agent 框架，不改 kernel 业务边界。 |
| B05 | DONE | 从仓库外使用已安装包和真实 socket，验证提交、查询、同 ID 重试、冲突、重启恢复与审计。保存运行命令、环境及结果。 |
| B06 | DONE | 汇总集成说明和已知限制；如确需改变冻结协议，提交本地 ADR 草案并停在兼容性决策边界，继续不依赖该变更的任务。 |

## C：合成业务闭环

| ID | 初始状态 | 工作与验收 |
| --- | --- | --- |
| C01 | DONE | 先写模拟预约设计：执行主体、确认对象、状态转移、业务查询、证据来源、失败语义、威胁模型；映射已有 API，不修改冻结协议。 |
| C02 | DONE | 实现独立的合成本地预约服务与受限业务 adapter，使用假账号/假数据，无模型费用、无真实校园凭据，不接入生产或正式用户服务。 |
| C03 | DONE | 实现并验证确认绑定：改日期、座位或动作后原确认无效；拒绝伪造主体和跨用户操作，授权检查独立于模型。 |
| C04 | DONE | 验证正常预约、无余位和身份过期/无权限；成功必须有模拟业务系统查询证据，不能只以本地提交成功作为依据。 |
| C05 | DONE | 故障注入：提交成功响应丢失、进程重启、重复请求；先查外部业务结果，明确区分 kernel 幂等与业务幂等。 |
| C06 | DONE | 验证不能查询且业务不支持去重时停止自动重试；结果保持 unknown，提供人工核对信息，不能虚报成功或失败。 |
| C07 | DONE | 验证取消与补偿：独立调用取消并查询结果，失败和未知明确保留；回退内部 generation 不伪装成业务撤销。 |
| C08 | DONE | 验证越权、确认重放、上下文隔离、恶意工具返回与敏感信息记录控制；未接模型时只能声称工具/策略层测试，不声称真实模型抗提示注入已验收。 |
| C09 | DONE | 从干净的独立环境跑完整模拟闭环及故障路径，保存来源标记为 synthetic 的证据，补充接入文档与限制；绝不标成校园真实 live 成功。 |

## D：真实校园接入的前置准备

| ID | 初始状态 | 工作与验收 |
| --- | --- | --- |
| D01 | DONE | 整理校方待确认项：模型位置/能力、数据分级与出站、飞书内容范围、SSO、正式接口、测试环境及访问授权。仅准备材料，不擅自联系第三方。 |
| D02 | DONE | 梳理成熟身份产品的接入需求：SSO、账号关联、MFA、找回、会话撤销；只形成方案，不自行重造认证系统或新增真实部署。 |
| D03 | DONE | 整理模型网关、凭据代理、授权、日志与运维责任矩阵，明确乙方和个人职责，安全预算、验收与停用条件。 |
| D04 | BLOCKED | 校方/乙方有权人员确认真实接入范围及凭据使用授权后，另行形成试点任务；缺少确认不影响 A/B/C 已授权工作。 |

## E：明确暂缓

| ID | 状态 | 边界 |
| --- | --- | --- |
| E01 | DEFERRED | M4 独立 judge：只有设计基线，无实现授权；模拟业务验证不借道实现 M4。 |
| E02 | DEFERRED | M5、自进化、多 Agent 平台、通用插件生态：不纳入当前任务。 |
| E03 | DEFERRED | 正式校园账号、真实模型 API、真实飞书应用写入和真实预约：另行明确授权及数据边界。 |
| E04 | DEFERRED | Git commit/push、PR、合并、版本发布、用户 state 迁移和正式服务变更：本启动流程不授权。 |

## V：Agent 配置与工具的版本管理与审计

设计输入见 [ADR-0018 草案](decisions/0018-agent-snapshots-and-audit-digests.md)。V01 完成设计核对，V02 和 V03 验收隔离 state 的版本闭环。V04–V07 已完成隔离本地验收，运行入口见 [合成配置与工具版本闭环](operations/synthetic-versions.md)。D04 独立等待专项授权，E 暂缓。

| ID | 状态 | 工作与验收 |
| --- | --- | --- |
| V01 | DONE | 本地源码核对与设计记录完成。现有 manifest 可原子发布应用层索引与直接对象；并发比较、原 ID 恢复、在途版本与暂存 GC 的边界、最小接口及验收见 [ADR-0018](decisions/0018-agent-snapshots-and-audit-digests.md#v01-源码核对)。完成范围为源码与文档，候选设计待授权后实现与运行时验收。 |
| V02 | DONE | 已授权的本地 synthetic 闭环。验证暂存校验、配置与工具原子激活、在途版本跨重启固定、应用回退、digest 证据与历史、原 ID 恢复及 GC 可达性。命令、源码与二进制摘要见证据索引。 |
| V03 | DONE | 在隔离 synthetic state 中复用单一 broker 和现有 UDS，完成命名分支创建、切换、核对旧 generation 和分支头的推进、候选保留及双父合并。专项覆盖冲突、独立组合校验失败、显式激活、原 ID 恢复、在途版本固定和审计追溯。设计与验收范围见 [ADR-0018 的 V03](decisions/0018-agent-snapshots-and-audit-digests.md#v03-命名分支与合并)。 |
| V04 | DONE | **受控写入与运行。** 从示例的 `advance`、`_publish`、`pin` 和 `run` 定位复用点，交付本地 Agent 可调用的提案入口。接受实际暂存的配置与配套工具，按一次逻辑变更校验并提交一个不可变版本，绑定运行所需的受管依赖。提案应包含简短判断、依据及未证实假设，主体与批准从可信入口取得。用独立 Agent 进程证明它能提案，但无法直接写活跃目录、kernel socket 或证据库。控制不可用时拒绝写入。验证失败保留旧版本，成功切换只影响新任务，旧任务重启后仍能读取固定版本。 |
| V05 | DONE | **审计 digest。** 在应用层先持久化事件、版本、原始判断、批准来源及验证结果，再按固定事件和获准证据投影异步生成 digest。提供本地模型接入点与确定性测试替身，验证逐结论证据引用、原始判断与模型推断的区分、缺失或冲突标记、越权引用拒绝、模型失败后的原始证据可读、重生成历史和并发更新保留。记录生成器、模型与模板版本、输入证据集及生成时间。确定性替身结果标 synthetic。可与 V04 并行设计，集成验收依赖 V04 的实际事件。 |
| V06 | DONE | **隔离端到端。** 走 V04 的正常入口，用隔离 Agent 测试进程提交配置与工具，在两分支变更、合并、激活并回退。验证整套版本可见性、原 ID 恢复、旧任务固定、拒绝绕过及审计者从 V05 digest 追回原始记录。至少覆盖一次提交临界点的进程中断和重启，验证只见整套旧版或新版，并在独立 state 验证受管旧版本及审计证据在一次实际串行 GC 后仍可读。使用替身得到的结果按 synthetic 记证据。 |
| V07 | DONE | **真实本地 Agent 与模型验收。** 用可用且符合授权的本地 Agent 和离线模型走通 V06 同一入口，用合成输入验收实际改写与真实 digest。保存脱敏输出及逐条人工可复核结果，至少含支持充分、证据缺失或冲突、模型失败三类。摘要应指出实际改动、当时判断及证据，逐条可回链且无虚构依据。缺少可用 Agent、离线模型、身份隔离能力或使用权限时，写明具体依赖与解除条件，再标 BLOCKED；继续完成其他可独立工作。 |

V04–V06 是本地交付范围，V07 通过才算真实 Agent 与模型的需求已验收。使用单一可信 broker 和串行维护即可满足本路线，独立写入口、并行 GC、正式部署和真实校园试点各自另行决定。接口和验收约定见 [ADR-0018 的 V04–V07](decisions/0018-agent-snapshots-and-audit-digests.md#v04v07-受控写入与真实审计验收)。

## 完成标准

各项 DONE 均需具备对应实现/文档、验证命令与结果、持久证据路径和限制说明。一次会话只完成部分任务时，准确更新检查点后结束；后续会话从首个可执行项继续，不将整个项目误标完成。

全部当前可执行项完成而 D/E 仍缺外部条件时，给出已完成范围与待决策事项，不无限扩展功能或反复跑同一套测试。

## 本次执行证据索引
V01 本地源码与文档验证：命令、退出码、源码 SHA-256 和定向文档差异见 `/home/feisama/.local/state/codex/molt-work/v01-20261120-02tBUJ/` 的 `findings.md`、`check-docs.log`、`source-binding.log` 和 `target-after.diff`。运行时验收待实现授权。
V02 合成闭环：4 项专项测试、10 项相关 Python 回归、26 项 Rust 故障注入配置测试和真实 UDS CLI 示例通过。完整命令、退出码、源码及二进制摘要见 `/home/feisama/.local/state/codex/molt-work/v02-20261120-9Rla4Y/` 的 `check-final.log`、`cli.log`、`source-sha256.txt`。示例验证 GC 计划、未执行 sweep。

V03 命名分支与合并：13 项专项、36 项相关 Python 回归、26 项 Rust 故障注入配置测试、真实 UDS CLI 示例、Ruff、11 条 schema、依赖和文档检查通过。完整命令、退出码及源码与本次二进制绑定见 `/home/feisama/.local/state/codex/molt-work/v03-20261120-i84L8S/` 的 `result.md`、各验收日志和 `source-sha256.txt`。验收层级为本地源码及示例二进制，GC 使用计划。

V04–V06：真实 UDS 专项 17 项通过，覆盖受控提案、固定版本和依赖、摘要并发、两处实际 kernel 进程中断与同 ID 恢复。串行 GC 在一次性 state 中只回收测试新建的游离对象，旧任务和证据仍可读。完整命令、退出码和源码／二进制摘要见 `/home/feisama/.local/state/codex/molt-work/v04-v07-20261120-N87U9c/` 中的 `v04-v06-result.md` 和 `source-runtime-sha256.txt`。

V07：用隔离的 `llama-completion` 和已安装的 Qwen3.5-9B-Q4_K_M 完成双分支提案与真实摘要，在实际 kernel 中断后按原 ID 恢复，再合并、激活、回退和串行 GC。证据含支持充分、证据缺失及无效 GGUF 导致的模型失败。脱敏输出及逐条人工复核见同一证据目录的 `v07-redacted-audit.json` 和 `v07-result.md`。

A08 实际退出：用户确认完整 v2 清单。587 项逐条核对路径、大小和 SHA-256 后由 `gio trash` 处理，源树前后快照变化与获批路径一致。证据 `/home/feisama/.local/state/codex/molt-work/a08-a11-20261120-TpDb5v/a08-result.json`。A11 已完成实际退出后的验收。

A11 实际退出后验收：源码 Python 275 项、Rust 故障注入 26 项通过，Ruff、fmt、Clippy、schema 11 条、协议生成一致性、依赖检查及 65 个现存相对文档链接均通过。独立构建的新候选包在仓库外安装后 19 项通过，并在私有 tmpfs 中复验真实 ENOSPC 后查询及同 ID 恢复。源码和包使用同一份源文件摘要。精确包 SHA-256、隔离安装位置、命令与结果见 `/home/feisama/.local/state/codex/molt-work/a08-a11-20261120-TpDb5v/a11-result.json` 和 `/home/feisama/.local/state/codex/molt-work/a08-a11-20261120-TpDb5v/package-result.json`。远端 CI 未执行，清单外 Git 忽略项保留，历史 A10 归档 SHA-256 复核一致。

A 阶段本地验收完成。B、C 已验收；D04 仍需有权人员确认并另行授权，E 暂缓。

### 历史验收记录（按当时状态）

下列 A01–C09 段落按执行先后保留；其中“尚未完成”只描述该次记录时点，
当前状态以任务表和后续对应验收记录为准，不从早期段落恢复过期下一步。

A01 静态核对见 `/home/feisama/.local/state/codex/molt-work/abc-20261119-restore-01/a01-dependencies.md`；当前 HEAD 不是完整源码标识。A08 仍未获删除确认。

A03 运行时证据：同目录 `a03-python-final.log`、`independent-tests-short-socket.log`（仓库外禁比赛导入，6 passed）；源码绑定见 `source-sha256.json`。A02 通用用例已通过，旧 CLI bridge 替换仍随 A04 进行，暂不标 DONE。AGENTS 依用户新授权先行修订，A06 尚未整体完成。

A02/A04 本轮证据：`/home/feisama/.local/state/codex/molt-work/abc-20261119-215541-cli` 下 `a04-verification.json`、`a04-subset-final.log`（19 passed）、`a04-docs.log`（8 passed）、`a04-source-sha256.json`、`a02-a04-coverage.md`。A05 接续实施，尚无新包验收；A08 仍未确认。

A05 本次验收：`/home/feisama/.local/state/codex/molt-work/abc-20261119-a05-resume/a05-result.json`，包含精确候选包 SHA-256、安装位置及命令元数据。仓库外安装包 19 passed；CI 相关本地子集 60 passed，协议生成物一致。远端 CI 未执行，A10 故障验收与 A08 删除仍待完成。

A06 证据见同一本次目录 `a06-result.md` 与 `a06-docs-final-verification.json`：7 passed、协作检查通过、本地文档链接存在；保留历史架构段并明确非当前入口。

A07 引用复核：本次目录 `a07-review.md`、`a07-reference-inventory.txt`、`a07-subset-verification.json`；17 passed、repo-policy/deps/Ruff 通过。旧比赛测试与退出 CLI 的冲突明确保留，尚未通过 A09 集中验收。

A09 集中验收：`/home/feisama/.local/state/codex/molt-work/abc-20261119-221527-a09/a09-result.md`，393 passed/1 skipped；测试 Ruff 通过，原 Rust/fmt/clippy/static 源码摘要未变，见 `prior-source-recheck.json`。覆盖替代映射已写入结果文件；不代表 A08/A11 删除验收完成。

A10 新包验收：`/home/feisama/.local/state/codex/molt-work/abc-20261119-221527-a09/a10-result.json`；仓库外安装 19 passed、真实私有 tmpfs ENOSPC 恢复/同 ID 幂等与审计通过。精确包 SHA 和源码绑定已记录，无发布，无修改旧发行包。

B01–B06：`/home/feisama/.local/state/codex/molt-work/abc-20261119-221527-a09/b-result.json`；契约见 `docs/operations/external-integration.md`，独立示例与真实 UDS 源码/安装测试各 2 passed。安装包同 A10，文档与样例单独摘要绑定，不声称已进入 wheel。

C01 设计：`docs/operations/synthetic-booking.md`，本次 `c01-design-review.json` 绑定文档；仅设计核对，C02–C09 运行验收尚未完成。

C02–C08：本次 `c02-c08-result.json` / `c-source-process-final-verification.json`，26 passed，源码绑定已保存。涵盖真实业务子进程与 client 进程重启、查询恢复、停止重试、取消及畸形工具返回；仅 synthetic 工具/策略层。

C09：本次 `c-result.json`，全新隔离 venv 安装 A10 精确 wheel，仓库外 28 passed；最终 `final-regression-verification.json` 为 421 passed/1 skipped、Ruff/diff 通过。A08 旧清单复核 `a08-manifest-recheck.json`：582 未变/9 变化/0 缺失，不能直接执行；A11 仍缺实际删除和删除后的依赖验收。

D01–D03 前置材料验收：`/home/feisama/.local/state/codex/molt-work/abc-20261119-d-close-01/d-result.json`，文档/协作子集 7 passed，本地链接 14 个有效；补齐确认记录、身份沙箱验收和责任/预算字段。仅材料完成，真实决策全部待确认，D04 保持 BLOCKED。

A08 本轮前置核对：`/home/feisama/.local/state/codex/molt-work/abc-20261119-d-close-01/a08-static-preflight.json` 与 `a08-readiness.md`。旧清单 591 项/9 变化；清单外 conftest 和 proposal 测试仍直接依赖退出模块。须先完成剩余测试解耦与完整清单修订；本轮无删除，A08/A11 保持 BLOCKED。


A08 本次非破坏性前置完成（删除仍 BLOCKED）：共享审计 fixture 与 genome/snapshot/并发/per-run 测试已独立于比赛数据；新增 v1 proposal/ledger 兼容覆盖。当前相关子集 78 +42 passed，仓库外省略拟退出文件的副本最终 275 passed、schema 11 条与 deps 通过；文档/协作 7 passed、tests Ruff/diff/副本链接通过。证据 `/home/feisama/.local/state/codex/molt-work/abc-20261119-a08-fixtures-01/result.json`；覆盖映射及限制见同目录 `coverage-and-preview.md`。初次副本 30 项失败已修复，原日志保留。修订完整预览为 `delete-paths-v2.txt` / `delete-manifest-v2.json`（587 文件、11,120,571 字节），旧清单保留 5 个已替代文件，新增 test_proposal.py 待退出；未获清单确认、未删除。A08/A11 状态不变，副本演练不替代实际删除后的源码/新包验收。
