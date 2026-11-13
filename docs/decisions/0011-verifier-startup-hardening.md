# ADR 0011：Verifier 启动加固与三比较域版本迁移

- 状态：**已决定（2026-11-13，用户授权执行 K4 批次；未记录独立评审/审批）**
- 日期：2026-11-13
- 关联：`shells/code-repair/verifier.py`、`shells/probe/verifier.py`、
  `shells/probe-c2/verifier.py`、`tests/test_verifier_startup_hardening.py`、
  `scenarios/{demo,probe,probe-c2}/scenario.json`、`shells/*/manifest.json`、
  `shells/*/genomes/*.json`、`probes/accept.py`
- 上游批次：K4-A（三个 verifier + 加固测试）；本 ADR 为 K4-B（迁移与验收）

## 背景

K4-A 之前的三个 verifier 都以候选工作区为子进程 `cwd`，并把该目录放进
`PYTHONPATH`。候选解自身是待执行的代码，因此候选旁置文件可以进入解释器或
pytest 的启动 / 发现路径：

- `sitecustomize.py` 在解释器启动时执行；code-repair 可提前 `os._exit(0)`，
  probe 可伪造全通过 JSON；
- 候选目录中的 `pytest.py` 可遮蔽 `python -m pytest`；
- 候选目录中的 `json.py` / `decimal.py` 可遮蔽 probe checker 导入的 stdlib 模块；
- old pytest 以 `cwd=候选目录` 运行，理论上给 `pytest.ini` / `conftest.py`
  留了配置发现面（本次真实复现为不可达，见下表）。

这些入口的共同后果是：**错误解可以获得 `passed=True`，且 bench 指纹保持不变**。
K4-A 只修启动 / 发现面，不把 verifier 变成完整 sandbox。

## 决定

1. **候选解只经 staging 进入判分进程**。每次 verify：
   - 在仓库外创建 `0700` 私有 staging（系统临时目录下的 `molt-verify-*`）；
   - 只把 `<workdir>/solution.py` 复制为 `<staging>/solution.py`；使用
     `O_NOFOLLOW | O_NONBLOCK` + `fstat` 拒绝 symlink / FIFO / 目录 / 其他非普通文件；
   - 子进程 `cwd=<staging>`，tests / checker 使用可信仓库的绝对路径；
   - 候选旁置普通文件全部忽略（不复制、不进入 argv、env 或启动搜索路径）。
2. **env 从零构造**，只传七个键：
   `PATH=/usr/bin:/bin`、`HOME=<staging>/home`、`PYTHONDONTWRITEBYTECODE=1`、
   `PYTHONNOUSERSITE=1`、`PYTHONHASHSEED=0`、`PYTHONPATH=<staging>`、`PWD=<staging>`。
   不继承 `OLDPWD`、`PYTHONHOME`、`PYTHONSTARTUP`、`PYTHONWARNINGS` 或任何
   `PYTEST_*`。
3. **启动参数收紧**：
   - probe / probe-c2：`<python> -P <abs check_cases.py>`；
   - code-repair：`<python> -P -m pytest <abs tests> -q -p no:cacheprovider -p molt_verify_env
     -c <staging>/pytest.ini --rootdir <abs tests> --confcutdir <abs tests>
     --disable-plugin-autoload`。
4. **可信辅助文件只由 verifier 生成**：`<staging>/pytest.ini`、
   `<staging>/sitecustomize.py`（移除解释器合成的 `LC_CTYPE`）、
   `<staging>/molt_verify_env.py`（pytest 插件，移除 pytest 自写的
   `PYTEST_VERSION`），以及 `<staging>/home`。staging 权限 `0700`，候选无法在运行前写入。
5. **保留既有语义**：guard / fingerprint / `bench_tree_hash` / 失败 / 超时 /
   完整性不匹配 / probe 脱敏 / `VerifyOutcome` 字段均不改变；非法 solution 返回既有失败结构，
   不伪装超时或 bench 篡改。成功、失败、超时、启动异常后 `finally` 清理 staging。
6. **两个 probe verifier 保持字节相同**（probe 与 probe-c2 同一份实现）。

## 七项攻击的真实结果

原版取本批开始时的 verifier 字节（sha256 `783e74…` / `b194ce…` / `b194ce…`），
用原启动方式在临时目录真实运行；加固版用同一攻击文件、同一题目重跑。
证据：`tests/test_verifier_startup_hardening.py`（加固版 7 项拒绝测试）、
K4-A 会话保留的 `pre-fix-results.json` / `post-fix-results.json`（外部临时目录，不随仓库提交）。

| # | 入口 | 原版 | 加固版 | 结论 |
|---|---|---|---|---|
| 1 | code-repair `sitecustomize` 提前退出 | passed=True / rc=0 | passed=False / rc=1 | **已复现且阻断** |
| 2 | probe `sitecustomize` 伪造 JSON | passed=True；伪造 cases=1 | passed=False；真实 cases=4 全失败 | **已复现且阻断** |
| 3 | probe `json.py` 遮蔽 | passed=True；伪造 cases=1 | passed=False；真实 cases=4 全失败 | **已复现且阻断** |
| 4 | code-repair `pytest.py` 遮蔽 | passed=True / rc=0 | passed=False / rc=1 | **已复现且阻断** |
| 5 | code-repair `pytest.ini --collect-only` | passed=False / rc=1 | passed=False / rc=1 | **不可达**：原版以绝对 tests 路径运行，rootdir=repo，workdir 的 ini 不参与发现 |
| 6 | code-repair `conftest.py` 强制 pass | passed=False / rc=1 | passed=False / rc=1 | **不可达**：workdir 不在 rootdir / args 的 conftest 链上 |
| 7 | probe-c2 `decimal.py` 遮蔽 | passed=True；伪造 cases=1 | passed=False；真实 cases=4 全失败 | **已复现且阻断** |

**计数口径：5/7 已复现且阻断，2/7 未复现/不可达；不声称 7/7。** 不可达项不改变发现规则
去制造可达性；它们不进入修复数量。

## 覆盖与不覆盖

**覆盖**

- 候选旁置 `sitecustomize` / `pytest.py` / `json.py` / `decimal.py` / `pytest.ini` /
  `conftest.py` 对启动或发现的影响；
- 候选旁置 benign helper、启动 hook、模块遮蔽文件、pytest 配置时，正确解仍通过；
- 非法 solution 类型（缺失、目录、symlink、FIFO）不执行候选、不阻塞；
- 成功 / 失败 / 超时 / 启动异常后的 staging 清理；
- 真实 argv / env / cwd / solution 导入位置不含原 workdir；
- 27 题正确解通过、确定性错误解失败，正常运行时指纹匹配。

**不覆盖 / 残余（不声称修复）**

- **不是完整 sandbox**：staging 只加固启动与发现路径，不隔离文件系统、网络或同 UID 能力。
- **in-process 能力**：solution 与 pytest / checker 同进程；solution 可 monkeypatch
  `json`、`pytest`、`builtins`、`sys.modules`、`os.environ`，或主动读写宿主文件。
- **主动路径发现**：env / argv 不含 workdir，不代表候选不能通过文件系统扫描主动找到仓库、
  测试或原工作区；本 ADR 只保证 verifier 不把它放进启动搜索路径。
- **运行期写仓库**：guard 只能在运行前后比对哈希，无法阻止候选瞬时改写/还原 repo 文件。
- `PYTEST_CURRENT_TEST` 在 pytest 测试调用期间由 pytest 写入；solution 若在 test body
  内执行可观察到（不属于继承 env，导入时不可见）。
- 后续方向（独立批次，不在本 ADR 实施）：独立 judge 进程/服务、容器或更强隔离、
  对 solution 的文件访问与进程能力做白名单。

## 版本迁移

本批把三个活动配置迁入三个新比较域。`scenario_id`、`shell_id`、题面、workspace、
tests、题集成员和 policy 不变；27 个活动 `task.json` 只改 `scenario_version`，
`shell_version` 均保持不写（None）。

| 对象 | 旧 | 新 |
|---|---|---|
| `shells/code-repair/manifest.json` | `1.0.0` | `1.0.1` |
| `shells/probe/manifest.json` | `0.3.0` | `0.3.1` |
| `shells/probe-c2/manifest.json` | `0.3.0` | `0.3.1` |
| `scenarios/demo/scenario.json` | `demo-6` / `1.0.0` | `demo-7` / `1.0.1` |
| `scenarios/probe/scenario.json` | `probe-6` / `0.3.0` | `probe-7` / `0.3.1` |
| `scenarios/probe-c2/scenario.json` | `c2-1` / `0.3.0` | `c2-2` / `0.3.1` |
| 15 demo task | `demo-6` | `demo-7` |
| 8 probe task | `probe-6` | `probe-7` |
| 4 c2 task | `c2-1` | `c2-2` |

`bench_hash` 按最终内容用 `molt.bench.compute_bench_hash` 重算：

| scenario | 旧 bench_hash | 新 bench_hash |
|---|---|---|
| demo-7 | `9d73c02090cfdf21f13e64020bc46b388a6ae8d373b2eae1f656e7c63ba41d81` | `390c2d9dc7bc65709a9f6e51213c19c3f4ab6993aafbf8f133e814ce8de95845` |
| probe-7 | `9b8de5e6c731925ba7b1e8d6b18362dce889e188c4bd047c6af98c6f79e586a9` | `ca89095b34ec5f97d6da9295d0f81b261c13907acc9643ad96a571138e2df10a` |
| c2-2 | `dc9353489fb2a0cbd69b929edc270769d12c57078796983a674f04c905b4f52a` | `4f5b51ccbe194c2cdcbbfaa8fdbe249aa5cbc5e5d974614b63f8cb8b38e82371` |

工作区实际有 **15 个** genome 文件：14 个属于三个迁移域，`shells/svc-config/genomes/v0.json`
保持 `svc-1 / 1.0.0` 不变。任务书写“16 个”，与树内文件数不符；本 ADR 按实际 15 个记录，
未虚构第 16 个文件。迁移域的 artifact 哈希：

| shell | verifier sha256 | manifest sha256 |
|---|---|---|
| code-repair | `554be66fdd86428808001bf02466a8da5f11759f38e58324ce41e5f16afeadfa` | `8d8832068a9f6f9852d5fe250a460de9897d7df603b4a57e1639a5700527204a` |
| probe | `5de991500f4c208af6295ece4f6b4ecd5a550adae4db069a9a0977345b73bb39` | `6a6577eb6c3139ae42f66bba0ad01b9dc98c25c9bfb65951bd6876f65a04ba57` |
| probe-c2 | `5de991500f4c208af6295ece4f6b4ecd5a550adae4db069a9a0977345b73bb39` | `0180eeea6696cb2655be6e07f2279b9030e47952049ae9186c6e47b99a3445f0` |

### Genome 旧 → 新 ID

父链：code-repair `v0 → v0-pi`；probe `v0-pro-max → gen2-sig / gen3 / gen4 /
v0-pro-max-sig`；其余为根。`gen2` 的旧父 `sha256:397d3453bbfdca2092891e9da3406a544c5f7824ea116f4f389a1ce770369433`
不在当前 genome 树中，也不等于任何被迁移 genome 的旧 ID（旧 `v0-pro-max` 为
`sha256:a2ce93d848bac18ba43de58b4571ca38c67740f6e6e1e18101eb1d0d864e5fb7`）。
按版本迁移规则，**保留原 `parent_id` 字符串不变，不重指**到新 `v0-pro-max`。
`gen2` 无子节点，因此没有其他节点需要因 `gen2` 的 ID 变化而更新父引用。

| file | 旧 genome_id | 新 genome_id |
|---|---|---|
| `shells/code-repair/genomes/v0.json` | `sha256:6b492ef0e60c51888ebecce7e0b7d28f21dfe6d81074da543a51d86b9dc0a55c` | `sha256:1b383c808e9ce54fd181d1cfe6959b3e77546a754e3a266cb7055c7058fe7906` |
| `shells/code-repair/genomes/v0-pi.json` | `sha256:9753c7bb8d3fe33c9ff3ade1f48c67d7f683101ac9e6b3a4dd90105520498bc9` | `sha256:09f9420e4fdacd786d41388e1b0ad8beb2130c34e4ab138e8fa5fee31680a9b4` |
| `shells/probe/genomes/v0-pro-max.json` | `sha256:a2ce93d848bac18ba43de58b4571ca38c67740f6e6e1e18101eb1d0d864e5fb7` | `sha256:c5b407f6eb0f17ef1de46562c262c5b685c1e6bd6d8e0da6911f77b6a4c98594` |
| `shells/probe/genomes/gen2.json` | `sha256:44f6b2c1b1e003aa8a95d542a3eaa7f6d50779a0140a9170d6772ee898b7c838` | `sha256:01d98b7ec9edc571dfff8bef56a6a230270b44dcffeade840ed12acae4aa1332` |
| `shells/probe/genomes/gen2-sig.json` | `sha256:250f2aea241e50373c55d1f1397b1603e5a909e89f3e03ec453c5604125c3918` | `sha256:16d882c58d2fe288ddd3181167e9886697068577e8849f0e5823483bb844c2be` |
| `shells/probe/genomes/gen3.json` | `sha256:0e4c21818bf7d8c65af4b3b76abf522228fb2f7a0657756b8c3da26e818a75ad` | `sha256:cef0c65477d6da1ee57a95a1f9dab1f1a5453b848dc8b736bed54adab46534f5` |
| `shells/probe/genomes/gen4.json` | `sha256:749d1f6f6800cf4750cb14f8b3e3b998e57596514d303bf6998babf0b0698292` | `sha256:5f6cbea34bf04fbaa447fe635062aeac0aa56bf67f042022a0b64e1cc938f786` |
| `shells/probe/genomes/v0-pro-max-sig.json` | `sha256:3a16b9742ebcaf6e064a77a6dba8f621e8fa0b87e85e6e2b3f091aa718a3d69f` | `sha256:33a0e820036fb97d739811abb572d4fd1b5f327cb8e58dbb489107e36b497839` |
| `shells/probe/genomes/oracle.json` | `sha256:8e571f8c516ca817ce4db26dc0ed257a48693696e6f429f2e071d968d2f05902` | `sha256:2f12c282726383c5dc0e0bb886ca225a542c90abfa9c8bf3188153709166d35e` |
| `shells/probe/genomes/v0-flash-high.json` | `sha256:ac4213478a321455a6f87ba384b0d844ae0888154c32ed9d9e5ea77877c57504` | `sha256:45ceff2bad164f7de126061b2a449d439ec6c4d7871991d1d40dcbc91a01f4ca` |
| `shells/probe/genomes/v0-flash-max.json` | `sha256:329d423461527be14af83cb45e80aa9635bc520a393734e9e4c06b449a044df0` | `sha256:37030f04c87ab3b10f58525ee583c831e2c33ac3e30918281d16c1c8f6e9c141` |
| `shells/probe/genomes/v0-pro-high.json` | `sha256:822932962f5dc0adef83f3a9ffa4ed9d0906efddb6cdb6587c8700b797b11f23` | `sha256:abc8e5c0b78165260afb4e9ab4a1798dde75b2043b589f7b64a0f4e8c76059f1` |
| `shells/probe-c2/genomes/c2-oracle.json` | `sha256:a85523d90b2b1d8d7f2b6bb02125cd095cc509a9d2ab952786beb505ed73fc97` | `sha256:5c3aa118d0f076fce14c55cb979ebf4a232aaa24dd2c73bf32c293a7ade92ab5` |
| `shells/probe-c2/genomes/c2-pro-max.json` | `sha256:f9914edc62abbc627c37d51cea4570305924882b7f626cf3a10db71dc4e10718` | `sha256:88d652f496d877df984e224251f12e95117dd663b21418f4727179305d775df6` |

## 冻结例外

`bench/**` 属于冻结面。本 ADR 只记录一次窄例外：**27 个活动 `task.json` 的
`scenario_version` 元数据迁移**（demo 15、probe 8、c2 4）。题面、workspace、tests、
题集成员、policy 均未变；`bench_hash` 仅因版本字段变化而重算。该例外不构成对
`bench/**` 其他修改的授权。

K4 的 verifier 加固状态由本 ADR 描述；`docs/research/core-known-gaps.md` 的更新
属于另一批次，本批未改。

## 回滚

- 本批未产生 commit，未使用 `stash` / `clean` / `checkout`。
- 回滚按本 ADR 的映射反向执行：恢复三个 manifest 的旧 `shell_version`、27 个 task 的旧
  `scenario_version`、三个 scenario 的旧版本 / `bench_hash`、14 个 genome 的旧字段与旧
  `genome_id`（父引用按旧 ID 恢复），删除 ADR-0011 与 accept.py 的 c2 版本读取变更。
- 回滚后应重新跑 `molt scenario validate`、`molt genome verify` 与全量 pytest；不得只改
  genome 文件而保留旧 run 的 `verifier_hash` 作为可比证据。

## 验收口径与边界

- 本批 **$0 / 0 Pi**：所有验收为离线 pytest、ruff、schema / scenario / genome 校验、
  doctor、history、freeze 与静态页面检查；没有真实 Pi 调用，**离线验收不是新域的 Pi 基线**。
- `history` 在临时候选仓库运行，避免写 `probes/report/**`；freeze 在源树预期红
  （27 个 task 未提交），在复制完整工作树并 commit 的临时候选仓库运行应通过。
- `demo-6` / `probe-6` / `c2-1` 的旧 run、gate 与报告证据属于旧比较域，与
  `demo-7` / `probe-7` / `c2-2` 不可比较，不得并入同一张表；旧报告保持原样，
  不用新域结果重写。
- 本 ADR 不声称 verifier 是完整 sandbox，也不声称 in-process 攻击已修复；
  后续独立 judge / 容器方向需单独 ADR 与真实证据。
