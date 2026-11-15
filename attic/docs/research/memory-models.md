# 记忆库选型（embedding / rerank）

调研日期 2026-11。结论按"本机可离线跑、中英混合、日志里有大量标识符"这三个约束给出。

## 1. 候选表

| 模型 | 参数 | 维度 | 上下文 | 检索形态 | 许可 | 本机体积 | 适合 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **BGE-M3** | 567M | 1024 | 8K | **dense + sparse + ColBERT，一次推理全出** | MIT | ~1.2GB | 默认首选：中英混合、要 hybrid |
| Qwen3-Embedding-8B | 8B | 4096（MRL 可截） | 40K | dense | Apache-2.0 | 4.7GB (Q4) | 只追 MTEB 分数、有 GPU 时 |
| Qwen3-Embedding-4B | 4B | 2560（MRL） | 40K | dense | Apache-2.0 | 2.5GB | 质量/成本折中 |
| Qwen3-Embedding-0.6B | 0.6B | 1024（MRL） | 32K | dense | Apache-2.0 | 639MB | 笔记本快速档 |
| nomic-embed-text v1.5 | 137M | 768（MRL） | 8K | dense | Apache-2.0 | 274MB | 纯英文、CPU 兜底 |
| embeddinggemma | 300M | 768（MRL） | 2K | dense | Gemma 条款 | 622MB | 不推荐（上下文太短） |
| mxbai-embed-large | 335M | 1024 | **512** | dense | Apache-2.0 | 670MB | 不推荐（512 装不下日志） |
| nomic-embed-code / jina-v2-code | 137M / 161M | 768 | 8K | dense | Apache-2.0 | 小 | 只有库里真有代码片段时才用 |

重排：

| 模型 | 参数 | 许可 | 说明 |
| --- | --- | --- | --- |
| **Qwen3-Reranker-0.6B** | 0.6B | Apache-2.0 | 默认；llama.cpp 有 GGUF，`--reranking --pooling rank` |
| Qwen3-Reranker-8B | 8B | Apache-2.0 | iGPU 上不划算 |
| bge-reranker-v2-m3 | 568M | MIT | 与 BGE-M3 同族，配它最省心 |

MTEB multilingual 参考分（越高越好，但别当唯一依据）：Qwen3-8B 70.58 / Qwen3-4B 69.45 / Qwen3-0.6B 64.33 / nomic-v1.5 62.28（英文榜）。BGE-M3 没有单一分数，因为它混了三种检索模式。

## 2. 推荐

**主选：BGE-M3 + bge-reranker-v2-m3。**

理由不是分数，而是它一次推理同时给出 dense、sparse 和 multi-vector。失败日志里最关键的检索信号往往是精确标识符——错误码、文件路径、函数名、开关名——这些正是 sparse 的强项，纯 dense 经常输给 BM25。用 BGE-M3 就不必再单独维护一套 BM25。

有一个针对"开发者记忆系统"的评测（BGE-M3 vs Qwen3-Embedding-0.6B vs multilingual-e5-large）报告 BGE-M3 在两层语料上全面领先，R@5 = 0.925。该文档我这边抓取失败（404），只能作为旁证。

**想要更高 dense 质量：Qwen3-Embedding-4B。** 2.5GB、2560 维、40K 上下文，iGPU 上批量建索引可以接受。代价是没有原生 sparse，得自己配 FTS5/BM25。

**0.6B 是"先跑起来"的档位**，639MB，比 BGE-M3 小一半，适合笔记本常驻。

## 3. 本机怎么跑

已装 `llama-server`（`/usr/bin/llama-server`），没有 ollama、没有 postgres/pgvector。两个进程分开起：

```bash
# embedding
llama-server -m bge-m3-Q8_0.gguf --embeddings --pooling mean --port 8081
# rerank
llama-server -m Qwen3-Reranker-0.6B-Q8_0.gguf --reranking --pooling rank --embedding --port 8082
```

两个坑：

1. **pooling 必须按模型卡来**。BGE-M3 用 mean；Qwen3-Embedding 是 last-token，用默认 mean 会悄悄降质。
2. **Qwen3 / nomic 需要 query 指令前缀**。Qwen3 用 instruct 模板，nomic 用 `search_query:` / `search_document:`。前缀写错通常掉几个点，而且没人会报错。

## 4. 比模型更重要的三件事

1. **切片方式**。一条记忆 = 一个可独立成立的单元：`{task, switch, 症状, 动作, 结果}`。别按 512 token 机械切，日志切成碎片后谁都检不出来。
2. **先过滤再检索**。按 `config_version`、开关名、失败类型做结构化过滤，再在子集里做向量检索。这比换更大的 embedding 模型收益高。
3. **写入策略**。只记已验证的结论（过了门禁的配置改动），提案期的不入库；同一条教训去重后递增 `repro_count`。否则记忆库会自己污染自己——这也是 `landscape.md` 里记的"错误经验被永久化"。

## 5. 规模判断

- **< 200 条**：SQLite + FTS5 就够，别上向量。BM25 对标识符的召回比 dense 好。
- **200–2000 条**：FTS5 + BGE-M3 dense，混合打分，可选重排。
- **> 2000 条**：再考虑独立的向量库（Qdrant / sqlite-vec）。pgvector 需要 Postgres，本机没有。

## 6. 评估

别信 MTEB 排名直接选型。从自己的 lessons 里抽 30 条当查询，人工标注相关项，量 Recall@5 和 MRR。半天工作量，比看榜单准。两三个候选（BGE-M3 / Qwen3-4B / FTS5 基线）跑一遍就够。

## 7. 参数量越大越好吗

不是，是明显的对数曲线。Qwen3 家族实测：

| 模型 | 参数 | MTEB multilingual | 边际收益 |
| --- | --- | --- | --- |
| 0.6B | 0.6B | 64.33 | — |
| 4B | 4B | 69.45 | +5.1（参数 ×6.7） |
| 8B | 8B | 70.58 | +1.1（参数 ×2） |

参数 ×13 换 +6.2 分，最后一档翻倍只买到 1.1。而 1–2 分在自建评测集上通常测不出来。

要分开看的三个量：

- **参数量** → 语义理解，边际递减。
- **维度** → 索引体积与内存；MRL 可截断，小语料截到 1024 维基本无损。
- **上下文长度** → 能否整段吞下一条日志。与参数量无关：mxbai-embed-large 335M 参数但只有 512 上下文，装不下 failure log，直接排除。

比参数量更决定效果的：

1. **检索形态**：BGE-M3（567M，带 sparse）在标识符精确匹配上常赢纯 dense 的 8B。
2. **重排**：embedding 只需把正确项捞进 top-50，排序交给 cross-encoder，比升级 embedding 划算。
3. **阈值/弃权**：错记忆比没记忆更伤，带阈值的 0.6B 比裸奔的 8B 安全。

结论：先 FTS5 基线 → 加 BGE-M3 混合 → Recall@5 不达标再上 4B → 8B 留到有 GPU。参数量是最后调的旋钮。

## 参考

- Ollama embedding 对照表（2026-06）：`morphllm.com/ollama-embedding-models`
- BGE-M3 multi-vector GGUF：`huggingface.co/Uncino/bge-m3-multivector-gguf`
- Qwen3-Reranker GGUF + llama.cpp 用法：`huggingface.co/Voodisss/Qwen3-Reranker-0.6B-GGUF-llama_cpp`
- llama.cpp embedding/rerank 参数：`--embeddings`、`--reranking`、`--pooling`
- 开发者记忆系统选型研究：`Aik358/dsh-auto-memory` → `docs/M7-RESEARCH-PAPER.md`（抓取 404，仅参考摘要）
