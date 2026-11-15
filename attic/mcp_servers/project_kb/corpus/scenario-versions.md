doc_id: scenario-versions
title: scenario_version 与比较域

比较域由 `scenario_version` 定义。版本不同的两次执行不可比较，
既不能合并统计，也不能放进同一张对比表。

每次 run 与 gate_decision 都带 `shell_version` 与 `verifier_hash`；
任一项不同即判定 incomparable，拒绝比较而不是「尽力对齐」。

会改变比较域的改动包括 harness、verifier、task_provider、switches 的
schema，以及整个 bench 目录。改动这些文件必须升 `scenario_version`
并重跑基线。

不改变比较域的是新增候选：新的 genome 文件、新的 prompt 文件、新的
工具声明文件只产生新的 genome_id，不升 scenario_version。但原地修改
已被既有 genome 引用的文件属于破坏性变更，按冻结处理。

跨版本的历史数字可以留档，但必须标注版本，且不得与新版本数字混写。

source: molt AGENTS.md (c23d8d6)
