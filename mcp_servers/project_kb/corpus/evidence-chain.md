doc_id: evidence-chain
title: 证据链（run → result → gate → archive）

一次执行产生的规范证据按固定顺序落盘：
`run → result → gate_decision → archive → snapshot → event → redaction`。

`run` 记录单次执行的事实：用量（tokens、墙钟、轮次、成本）、结果
（outcome），以及运行来源 `provenance.kind`，取值只允许 fixture、live、
replay 三者之一。live 指真实模型调用；replay 必须带 `replay_of_run_id`；
fixture 不得被标成 live。

`result` 是题级聚合。`scope=report` 时必须同时给出搜索集与 holdout 的双分数。
`gate_decision` 必须带逐题配对表（paired），判定只依赖配对结果与 genome 上限，
不允许只看聚合数字。

`archive` 与 `snapshot` 记录代际归档与树快照；采纳是单写者单事务：
journal → generation → snapshot → archive → pointer，任一步失败都不留下半成品。
审计只做只读重算：从 run 与 ledger 重算 result，从配对表重算 gate。

source: molt AGENTS.md (c23d8d6)
