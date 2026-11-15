doc_id: zero-write
title: 零写入验收（zero-write）

验收命令跑完后，仓库工作树必须逐字节不变。判断依据是文件系统树快照，
不是 `git status`：被 gitignore 的产物（例如 `__pycache__`、`.pyc`）不会
出现在 `git status` 里，却已经污染了工作树。

测试进程自身也会写字节码。运行 pytest 或任何 python 入口前先设
`PYTHONDONTWRITEBYTECODE=1`，并用 `python3 -B` 关闭字节码缓存，否则
测试框架写出的缓存文件会让零写入断言假阴性或假阳性。

命令的输出目录、状态目录与证据目录一律指向仓库之外（例如 `/tmp`），
提交物里不出现 `state/`、`runs/`、`evidence/`。

校验方式：运行前后各取一次树快照并比较哈希；`git status --porcelain` 为空
只算辅助证据。允许的例外必须在文档里逐条列名（例如现场 demo 写 `/tmp`
下的探针数据），其余路径仍按零写入要求执行。

source: molt AGENTS.md (c23d8d6)
