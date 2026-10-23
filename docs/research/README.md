# 外部资料索引与处理流程

出设计方案前先查本页，找出相关笔记并在方案里注明出处。

## 收到外部资料时怎么处理

1. **分级**：Palantir 官方出品放进 `official/`；竞品、第三方分析、开源项目放进 `unofficial/`。
2. **写笔记**：只记对本项目有用的部分，格式见下方模板。每条结论标明证据等级：官方文档 / 官方视频 / 第三方分析 / 本项目推断。从截图推测的内容一律标“本项目推断”。
3. **存档**：
   - 笔记命名为 `<来源>_<主题>_<YYYY_MM_DD>.md`，并在 `.gitignore` 白名单里放行，方便组员共享。
   - **原件全部收进来**（图片、PDF、视频，包括聊天里直接贴的截图）：放在 `<official|unofficial>/evidence/<主题-日期>/`，旁边写一份 `source.json`，记录标题、链接、获取日期、提供人、证据等级、引用它的笔记。原件只留在本地，不进 git。
   - 运行 `python -I scripts/research_originals.py`，重新生成 [`ORIGINALS.md`](ORIGINALS.md)（原件清单）和 `originals.sha256`（校验和）。这两个文件进 git，组员不需要拿到原件，也能知道有哪些原件、从哪来。
4. **登记**：在下方索引表里加一行。同一主题已有笔记时，更新原笔记，不新建。

**拿到别人的原件后核对完整性**：`cd docs/research && sha256sum -c originals.sha256`。

### 笔记模板

```markdown
# <来源>：<主题>

> 日期：YYYY-MM-DD · 来源：<链接/文件名/谁提供> · 证据等级：<…>
> 原件：evidence/<主题-日期>/（不进 git，见 ORIGINALS.md）

## 一句话结论
## 对方怎么做的（看到了什么）
## 和我们现状的差距
## 可以借鉴（对应模块 + 优先级）
## 不该照搬 / 存疑
```

## 索引

| 笔记 | 等级 | 一句话要点 | 影响模块 |
|---|---|---|---|
| [OntoFlow 孪生应用搭建器截图](unofficial/ONTOFLOW_TWIN_APP_BUILDER_2026_10_22.md) | 第三方产品截图 + 本项目推断 | 分区模板 + 组件库 + 一键绑定数据；地图、环形图、排行条、告警列表；按任务划分的一级导航 | Workshop、全局导航 |
| [可复用查询（Functions）与 Ontology MCP](official/PALANTIR_FUNCTIONS_REUSE_AND_ONTOLOGY_MCP_2026_10_23.md) | 官方文档 | 函数语义化版本、按版本范围引用；组件按结果形状绑定、输入可接页面变量；MCP 按“应用范围 ∩ 用户权限”开放 | 本体查询、Workshop 绑定、MCP |
| [Workshop 时间筛选与汇总指标建模](official/PALANTIR_WORKSHOP_TIME_FILTERING_2026_10_23.md) | 官方文档 | 时间筛选作用在事件粒度的对象集上，维度指标查询时聚合（Search Around / 派生属性），不预存“维度×日期”汇总对象 | Workshop 数据源、时间范围、本体建模 |
| [Palantir NHS 供应链视频](official/PALANTIR_NHS_SUPPLY_CHAIN_VIDEO_SCENARIO_REVIEW_2026_10_18.md) | 官方视频 | 本体支撑的情景推理界面和用例，以及和本项目的差距 | 情景研究、决策 |
| [Palantir 官方工业案例视频](official/PALANTIR_OFFICIAL_INDUSTRIAL_USE_CASE_VIDEO_REVIEW_2026_10_18.md) | 官方视频 | 供应链、生产、工厂场景下的情景决策能力 | 情景研究、决策 |
| [Ontology 建模与设计原则](official/PALANTIR_ONTOLOGY_MODELING_AND_DESIGN.md) | 官方文档摘录 | 对象类型、属性、链接等建模原则 | 本体建模 |
| [Ontology 运行时与架构](official/PALANTIR_ONTOLOGY_RUNTIME_AND_ARCHITECTURE.md) | 官方文档摘录 | 运行时、界面与后端架构 | 对象查询、Action、情景 |
| [Vertex 情景研究资料](official/VERTEX_SCENARIO_REFERENCE_GALLERY_2026_10_12.md) | 官方资料 | Vertex 情景推演的参考图集 | 对象浏览器、情景 |
| [开源项目与公开数据集](unofficial/OPEN_SOURCE_AND_BENCHMARKS_FROM_ARCHIVED_PLANS.md) | 第三方 | 历史计划里评估过的开源项目和数据集（frePPLe、LUBM 等） | 测试数据、基准 |
| [Palantir 平台深度解析：数据 OS 到 AI-OS](unofficial/Palantir平台深度解析-数据OS到AI-OS.md) | 第三方分析 | Foundry 到 AIP 的能力演进 | 总体定位 |
| [Palantir 本体论建模平台深度解析](unofficial/Palantir本体论建模平台深度解析.md) | 第三方分析 | 五层架构、供应链数据流、指标告警驱动决策 | 本体建模、告警 |
| [本体工程参考（一）：架构总览与智能体](unofficial/Palantir本体工程参考(一)-架构总览与智能体.md) | 第三方分析 | 本体工程与智能体架构 | 智能体 |
| [本体工程参考（二）：可编程数字孪生](unofficial/Palantir本体工程参考(二)-可编程数字孪生.md) | 第三方分析 | 语义层、动力层、动态层；Action 让 AI 建议变成受治理的变更 | Action、决策 |
| [本体第一人称视角与 Ontology Action](unofficial/本体第一人称视角与Ontology_Action辨析.md) | 第三方分析（OntoFlow 作者） | 建模视角：第一人称本体与第三人称本体的区别 | 本体建模 |
| [动态本体 + 本体 MCP：ADIZ 空情研判](unofficial/动态本体+本体MCP-OpenClaw接入东海ADIZ空情研判MVP实战.md) | 第三方分析 | 智能体通过 MCP 接入本体、做态势研判的 MVP | 智能体、MCP |
| [Semantica README](unofficial/semantica_README.md) | 开源项目 | 图原生的上下文与可解释推理（项目已集成） | 推理工作台 |

**原件**：全部原件及来源见 [`ORIGINALS.md`](ORIGINALS.md)。其中标着“未被任何笔记引用”的，是已经收进来但还没分析过的原件，等需要时再整理成笔记。OntoFlow 白皮书只检索过一次，没有找到应用搭建器的内容。
