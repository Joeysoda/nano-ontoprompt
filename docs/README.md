# docs 目录说明

| 目录 | 放什么 | 何时过期 |
|---|---|---|
| `research/official/` | Palantir 官方出品的资料：官方文档、官方视频/案例（含 Vertex）及其整理 | 不按时间淘汰，只增不删 |
| `research/unofficial/` | 第三方对 Palantir 架构的分析，以及类似产品、开源项目的资料 | 不按时间淘汰，只增不删 |
| `plans/` | 当前正在执行的最新版计划和差距审计 | 被新版取代即移入 `archive/` |
| `adr/` | 架构决策记录：已拍板的决定及理由，只追加不修改 | 不过期；被推翻时新写一份 ADR 说明取代关系 |
| `guides/` | 现有功能的使用手册、演示操作稿 | 功能改动时同步更新；功能删除时移入 `archive/` |
| `reports/` | 当前仍有效的验收记录、实验报告、指标 | 对应功能大改后移入 `archive/` |
| `archive/` | 过期的计划、手册、报告，只作历史备查 | — |

## Project 4 整体方案参考

- [组员原稿：主路线图与 PRD](plans/PROJECT4_PLAN_A_2_0_BASELINE_MASTER_ROADMAP_AND_PRD.md)：原样保留，属于待评审提案，基于较旧的 Plan A 2.0 进度，不取代当前执行计划。
- [架构速读与当前基线差异](plans/PROJECT4_PLAN_READING_GUIDE_2026_10_24.md)：整体架构、阶段目标和与当前合并代码的静态对照；不包含 OntoAct。

## 使用规则

- **设计时官方资料优先**：`research/official/` 的结论权重高于 `research/unofficial/`，两者冲突时以官方为准，并注明冲突。
- 引用调研结论时标明证据等级：官方文档 / 官方视频 / 第三方分析 / 本项目推断。
- **`archive/` 默认不读**：里面的内容描述的是旧系统或旧方案，会误导对当前系统的判断。只有在追查历史决策时才查阅。
- 计划过期移入 `archive/` 前，先把其中关于 Palantir 官方设计、第三方产品的调研内容按主题提取到 `research/`，并注明"提取自哪份计划"。
- 大文件（PDF、视频、截图）只放本地，不进 git；`.gitignore` 对 `docs/` 采用白名单，新增要共享的文档需在 `.gitignore` 中显式放行。
- 外部资料（竞品截图、文章、视频等）一律按 [`research/README.md`](research/README.md) 的流程分析成笔记并登记索引；出设计方案前先查这个索引。
