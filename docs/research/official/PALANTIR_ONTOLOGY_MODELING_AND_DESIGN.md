# Palantir Ontology：建模与设计原则（官方资料摘录）

> 提取日期：2026-10-22。内容从已归档计划中提取，只保留 Palantir 官方资料的结论，去掉了旧系统的仓库事实和旧方案。
> 来源计划（`docs/archive/`）：
> - [A] `PALANTIR_ONTOLOGY_GAP_AUDIT_2026_09_23.md`（官方页面按 2026-09-23 版本阅读）
> - [B] `PLAN_PERSON_A_SEMANTIC_CORE_SECURITY_DATA_PLANE.md`
> - [C] `ontology-optimization-plan.md`
>
> 证据等级：除特别标注外，均为**官方文档**。"本项目推断"表示是当时项目的解读，不是 Palantir 原话。

## 1. Ontology 是什么

| 结论 | 官方页面 | 来源 |
| --- | --- | --- |
| Ontology 同时包含 semantic elements（对象、属性、关系）和 kinetic elements（Action、Function）；目标是支撑运营决策。 | [Overview](https://www.palantir.com/docs/foundry/ontology/overview/) | A, B |
| 一个决策由 Data、Logic、Action、Security 四部分组成；要记录 decision lineage（决策溯源）、支持场景评估和安全写回。配图：Data 提供历史/关系/时间上下文；Logic 叠加规则、预测、优化、场景；Action 叠加审批、提议、写回、通知、升级；Security 贯穿整条链。 | [Why create an Ontology?](https://www.palantir.com/docs/foundry/ontology/why-ontology/)（含配图 `why-ontology-overview.png`） | A |
| Ontology-aware 应用分 discovery / analysis / dashboard / application 几类；按使用方式分 exploratory、workflow-specific、walk-up usable、customizable。 | [Ontology-aware applications](https://www.palantir.com/docs/foundry/ontology/applications/) | A |

## 2. 类型系统

| 主题 | 结论 | 官方页面 | 来源 |
| --- | --- | --- | --- |
| 层次 | 类型定义、实例、Object Set、Value Type 是不同层次。类型定义是可引用的元数据资源，对象实例是受它约束的数据。 | [Types reference](https://www.palantir.com/docs/foundry/object-link-types/type-reference/) | A, B |
| Object Type | 现实实体或事件的 schema；对象可以由 datasource 生成，也可以由 Action 创建。 | [Object types](https://www.palantir.com/docs/foundry/object-link-types/object-types-overview/) | A |
| Properties | 规定了属性类型、哪些能当主键/标题键，以及数组、Struct、媒体、时序属性的限制。 | [Properties](https://www.palantir.com/docs/foundry/object-link-types/properties-overview/) | A |
| Base types | 基础类型决定应用里能对它做哪些操作；高级类型包括 Vector、Geo、Attachment、Time Series、Media、Struct。 | [Base types](https://www.palantir.com/docs/foundry/object-link-types/base-types/) | A |
| Property reducers | reducer 只用于展示/接口适配，不能按 reduce 后的值查询；没有逆变换的接口 Action 会失败。 | [Property reducers](https://www.palantir.com/docs/foundry/object-link-types/property-reducers/) | A |
| Derived properties | 运行时沿关系链计算，最多三跳；to-many 必须聚合；只读，继承参与对象的安全上下文。不是"任意计算结果"的统称。 | [Derived properties](https://www.palantir.com/docs/foundry/object-link-types/derived-properties/)；本地 PDF `palantir-docs/palantir_derived_properties.pdf` | A |
| Structs | 不可嵌套，字段不能是数组；struct 数组查询时各字段独立匹配，不保证同一个元素同时满足所有条件。 | [Structs](https://www.palantir.com/docs/foundry/object-link-types/structs-overview/) | A |
| Shared properties | 共享的是属性语义和元数据，不是底层数据。 | [Shared properties](https://www.palantir.com/docs/foundry/object-link-types/shared-property-overview/) | A |
| Value types | 带领域语义和约束、可复用的基础类型包装。 | [Value types](https://www.palantir.com/docs/foundry/object-link-types/value-types-overview/) | A |
| Value type 版本 | 名称等元数据可以改；base type 和当前版本的约束不可改，约束变化会生成新版本。 | [Value type versions](https://www.palantir.com/docs/foundry/object-link-types/value-types-versions/) | A, B |
| Value type 约束 | 支持 Enum、Range、Regex、RID、UUID、数组唯一性/元素约束、Struct 字段约束。 | [Value type constraints](https://www.palantir.com/docs/foundry/object-link-types/value-type-constraints/) | A |

## 3. Link 与 Interface

| 主题 | 结论 | 官方页面 | 来源 |
| --- | --- | --- | --- |
| Link type | 一个 Link Type 天生双向，两侧分别命名；多对多可以由独立 datasource 支撑；不支持跨 Ontology 的 Link。 | [Link types](https://www.palantir.com/docs/foundry/object-link-types/link-types-overview/) | A, B |
| Object-backed link | 关系本身带日期、角色、状态、分配等信息时，应建成由对象支撑的关系，不要塞进边属性里。 | [Structural guidance](https://www.palantir.com/docs/foundry/ontology/ontology-structural-guidance/) | A, B |
| Interface | 不可实例化的抽象能力/形状；一个对象可实现多个 Interface，Interface 可多重扩展；在 Object Set/SDK 里可作为抽象类型范围使用。不只是"共享属性集合"。 | [Interface overview](https://www.palantir.com/docs/foundry/interfaces/interface-overview/) | A, B |
| Link constraints | Interface 可以声明指向 object/interface 的 ONE/MANY、必需或可选的关系能力。 | [Link constraints](https://www.palantir.com/docs/foundry/interfaces/interface-link-types-overview/) | A |
| Action constraints | 只约束能力和参数形状，不统一执行逻辑、规则、权限和副作用。 | [Action constraints](https://www.palantir.com/docs/foundry/interfaces/interface-action-type-constraints/) | A |
| 扩展 | 扩展会继承 properties、link constraints、action constraints，支持多重扩展。 | [Extend an interface](https://www.palantir.com/docs/foundry/interfaces/extend-interface/) | A |

## 4. 设计原则与反模式

**原则优先级**（[Best practices](https://www.palantir.com/docs/foundry/ontology/ontology-best-practices/)，来源 A, B, C）：domain-driven > DRY > open/closed > composition（组合优先于继承）。核心是**先建模现实世界的对象和事件，再映射数据**；一行数据可能贡献多个对象。

**结构指导**（[Structural guidance](https://www.palantir.com/docs/foundry/ontology/ontology-structural-guidance/)，来源 A, B）：每个事实只存一处、能派生的就动态派生；合理使用 Struct、Interface、object-backed link；有命名规范；安全分层，要求行/列/单元格级安全，派生值、工具调用、日志和决策溯源都继承同一个安全上下文。

**反模式**（[Anti-patterns](https://www.palantir.com/docs/foundry/ontology/ontology-anti-patterns/)，来源 A, B, C）：

| 反模式 | 一句话含义（本项目整理） |
| --- | --- |
| System / Department Silos | 按来源系统或部门分别建类型，同一现实对象重复出现 |
| Kitchen Sink | 把源表所有列原样塞进一个类型 |
| God Object | 一个类型承担太多职责 |
| Golden Hammer | 不管合不合适都用同一种工具/建模手法 |
| Action Sprawl | Action 数量膨胀、相互重叠 |
| Time Machine | 把历史/时间维度错误地塞进对象结构 |
| Misnomer | 命名不反映真实语义 |

**本项目推断**（A, B）：自动映射"数据集 → 类型、列 → 属性、外键 → 关系"只能产出候选，不能当成权威类型边界；确认步骤要允许拆分、合并、复用已有类型，并记录映射来源。

## 5. 设计验证

[Validation](https://www.palantir.com/docs/foundry/ontology/ontology-design-validation/)（来源 A）：要用**没有预演过的真实业务问题**，在通用分析工具里分别测试人和 Agent；记录答案正确率、路径、耗时、困惑点和需要的人工帮助。schema 校验和工程测试通过不能证明 Ontology 好找、好用。
