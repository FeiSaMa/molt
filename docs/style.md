# 风格约定

适用于 `molt/**`、`shells/**`、`bench/**`、`tests/**`、`docs/**`、`.github/**`。
与工具冲突时以工具配置（`pyproject.toml`）为准，本文件解释「为什么」。

## 一、Python

- **Python 3.11+**（`requires-python = ">=3.11"`，ruff `target-version = "py311"`）。
  可以用 `X | None`、`match`、`tomllib`；不要用 3.12+ 语法。
  全量 Python pytest 不在 CI 内（手动 / 集中执行）；kernel 子集在 CI 运行。历史比赛夹具曾有 3.11 浮点差异；
  当前验收基线固定 3.12.13，不由旧结果推断当前通过状态。
- **行长 100**（`[tool.ruff] line-length = 100`，但 `E501` 被 ignore：
  长 URL / 长字符串可以超，代码行不要随意超）。
- 规则集 `E,F,I,UP,B`。提交前 `uv run ruff check .` 必须干净；不要用 `# noqa` 掩盖逻辑问题。
- **类型标注**：公开函数、dataclass 字段、Protocol 方法都要标；内部一次性变量可省。
  返回 `None` 写 `-> None`。`from __future__ import annotations` 不是必需，保持与所在文件一致。
- 不可变优先：记录用 `@dataclass(frozen=True)`（参考 `molt/core/types.py`）；
  默认参数不要用可变对象。
- 异常：core 抛 `molt.core.errors` 里的类型，CLI 层转成 `error: ...` + 退出码；
  不要 `except Exception: pass`。
- 路径：代码里用 `pathlib.Path`，不要字符串拼接；比较路径用 POSIX 形式（`as_posix()`）。

## 二、JSON

- **canonical**：键排序、UTF-8、无多余空白（`json.dumps(obj, sort_keys=True, ensure_ascii=False,
  separators=(",", ":"))`）；写盘结尾保留一个换行。
- 哈希用 sha256；树哈希按相对路径 POSIX 排序，逐行 `path\0content_hash\n`，不含 mtime，
  拒绝 symlink（实现见 `molt/core/hash.py`、`molt/bench.py`）。
- 新增字段只能进 `ext`；顶层字段增删改名 = major 变更 + ADR。
- 记录文件不写注释、不写 NaN/Infinity。

## 三、Markdown

- 标题层级从 `#` 开始，单文件只有一个 `#`；不跳级（`##` 后不直接 `####`）。
- 中文正文用全角标点；中英文之间留一个空格（`ruff` 的 `line-length` 不检查 Markdown）。
- 术语固定，不造同义词：

  | 用 | 不用 |
  | --- | --- |
  | genome | 基因组、候选体 |
  | shell | 插件、外壳 |
  | adapter | 适配器（正文可写「adapter 层」） |
  | scenario_version | 场景版本号 |
  | holdout | 留出集（首次出现可括注） |
  | 门禁 | gate（正文用中文，命令/字段用 `gate`） |
  | 采纳 / 回退 | adopt / revert（命令名保留英文） |

- **禁止手写字段表**。11 类记录的字段表由 `molt schema docs` 生成到
  [docs/protocol.md](protocol.md)；文档里引用它，不要复制粘贴（会漂移）。
- 表格前后各空一行；代码块必须带语言标签（`bash`、`python`、`json` 等）。

## 四、时间与路径

- 时间一律 **UTC ISO 8601**、带 `Z`：`2026-11-10T16:20:00Z`。
  需要本地时间时在正文注明时区（`2026-11-12 18:00 UTC+8`），但存储值仍是 UTC。
- 文档里的路径**相对仓库根**，用 `/`：`shells/code-repair/verifier.py`。
  普通源码链接不写绝对路径；handoff 与验收证据应记录可定位的绝对路径。
- 命令示例必须**可直接执行**：给出工作目录前提（默认仓库根）、必要环境变量、
  以及会写文件的参数（`--state` / `--out` 指向 `/tmp/...`）。

## 五、示例与证据

- 示例命令要能跑通，且**默认零写入**：`--state`、`--out` 指到仓库外。
- 当前示例使用通用 CLI 或无模型 synthetic 客户端；不再提供比赛 `search/demo/gate` 命令入口。
  Python 示例显式设置 `PYTHONDONTWRITEBYTECODE=1`，状态与输出指向独立测试目录。
- 合成对象与模拟业务结果标 `synthetic`；不得声称真实校园或真实模型成功。
- 历史记录保留 `fixture` / `live` / `replay` 原义；fixture 不得标 live，replay 带 `replay_of_run_id`。
- 历史数字给出比较域（`scenario_version`）与样本量，附逐题配对表，禁止跨版本比较。
- 分开写文档检查、源码运行测试、精确包验收和真实业务结果；本地提交不证明外部业务成功。
- 本地哈希链不证明执行真实性，不声称 tamper-proof；unknown 保持未知，查询或恢复后才更新结论。

## 六、claim 语言

以下比赛措辞只适用于历史证据，不作为当前产品能力声明。

允许：

- 「在 10 题搜索集上未观察到通过题数下降（n=10，描述性）」
- 「单题执行成本不升」
- 「门禁判定为 adopt / reject，原因是 …」（附配对表）

不允许：

- 「自进化已提升」「效果显著提升」「证明模型能自我改进」
- 「成本降低 20%」这类没有配对表的孤立百分比
- 把 fixture 上的确定性收益说成真实模型的能力提升

一句话原则：**只写证据能支撑的最小断言**，并把「描述性」写在句子里。
