# LLM 质量与准确性评测报告

## 质量分层

数据层检查完整性、唯一性、类型和时间；映射层检查字段覆盖、主键命中和来源分类；本体层检查 Schema、孤立实体、断引用和时间约束；实例层使用 gold 计算 Precision/Recall/F1、幻觉率、重复率和来源可追溯率。

## 对照组

1. 规则模式；2. 本地 Ollama `qwen3.5:0.8b`；3. LLM + 确定性校验 + bridge fallback。模型失败时保留规则结果并标记 `llm_used=false`。

## 评测 API

`POST /api/v2/benchmarks/runs` 接收预测实体/三元组、gold 和可选 Schema，创建 `quality_benchmark` ConstructionRun；`GET /api/v2/benchmarks/runs/{id}` 读取指标。页面入口为“评测实验”。置信度只是生成信号，不是校准准确率。

OSKGC 作为 Schema 约束 KG 基准：[官方仓库](https://github.com/HeraclesWang/OSKGC)；CQ4OE 作为能力问题到术语/本体基准：[使用说明](https://github.com/oeg-upm/cq4oe-benchmark/blob/main/usage_instructions.md)。OAEI 只预留接口，本阶段不做完整对齐实验。
