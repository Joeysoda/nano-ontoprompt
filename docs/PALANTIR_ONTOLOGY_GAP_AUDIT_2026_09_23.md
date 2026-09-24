# Palantir Ontology 系列与当前项目差距审计

> 状态：第一轮核心模块审计完成
> 基准日期：2026-09-23
> 官方导航入口：https://www.palantir.com/docs/foundry/ontology/overview/
> 当前官方左侧导航快照：398 个页面链接
> 审计对象：`nano-ontoprompt-whatif` 当前工作区；根目录早期 demo 仅作为历史与适配层参考。

## 1. 审计边界

第一轮优先覆盖五组内容：

1. 本体模型与设计规范；
2. Actions 与 Functions；
3. Search、Object Sets 与 Scenarios；
4. Object Explorer、Object Views、Ontology Manager 与 Vertex 核心 UI；
5. Ontology permissions、indexing、edits/materializations 与 Object backend。

Map、Machinery、Vulcan、Dynamic Scheduling、Foundry Rules 和完整 Legacy/Sunset 操作教程暂缓；如果这些页面定义通用契约，仍会回读相关页面。

### 证据分类

- **官方契约**：Palantir 当前公开文档直接说明的行为、限制或语义。
- **公开实现证据**：公开 SDK、API schema 或测试中可观察的行为；后续阶段补充。
- **仓库事实**：当前源码、迁移、测试和实际 UI 所实现的行为。
- **项目推断**：基于前述证据对当前项目作出的判断，不冒充 Palantir 私有实现。

## 2. 阅读进度

### 已完整阅读的核心页面

| 模块 | 页面 | 主要证据 |
| --- | --- | --- |
| Ontology foundations | [Overview](https://www.palantir.com/docs/foundry/ontology/overview/) | Ontology 同时包含 semantic elements 与 kinetic elements；目标是 operational decision-making。 |
| Ontology foundations | [Why create an Ontology?](https://www.palantir.com/docs/foundry/ontology/why-ontology/) | 决策由 Data、Logic、Action、Security 组成；需要捕获 decision lineage、场景评估与安全写回。 |
| Ontology foundations | [Ontology-aware applications](https://www.palantir.com/docs/foundry/ontology/applications/) | 区分 discovery、analysis、dashboard、application，以及 exploratory、workflow-specific、walk-up usable、customizable。 |
| Type system | [Types reference](https://www.palantir.com/docs/foundry/object-link-types/type-reference/) | 明确类型定义、实例、Object Set、Value Type 等不同层次。 |
| Type system | [Object types](https://www.palantir.com/docs/foundry/object-link-types/object-types-overview/) | Object Type 是现实实体或事件的 schema；对象可由 datasource 或 Action 创建。 |
| Type system | [Properties](https://www.palantir.com/docs/foundry/object-link-types/properties-overview/) | 属性类型、主键/标题键适用性以及数组、Struct、媒体、时序等限制。 |
| Type system | [Base types](https://www.palantir.com/docs/foundry/object-link-types/base-types/) | 基础类型决定应用可用操作；包括 Vector、Geo、Attachment、Time Series、Media、Struct 等高级类型。 |
| Type system | [Property reducers](https://www.palantir.com/docs/foundry/object-link-types/property-reducers/) | reducer 是展示/接口适配语义，不可直接按 reduced value 查询；无逆变换的接口 Action 会失败。 |
| Type system | [Derived properties](https://www.palantir.com/docs/foundry/object-link-types/derived-properties/) | 运行时沿最多三跳关系计算；to-many 必须聚合；只读并继承参与对象的安全上下文。 |
| Type system | [Structs](https://www.palantir.com/docs/foundry/object-link-types/structs-overview/) | Struct 不可嵌套、字段不可为数组；struct-array 查询是字段独立匹配，不保证同一元素同时满足条件。 |
| Type system | [Shared properties](https://www.palantir.com/docs/foundry/object-link-types/shared-property-overview/) | 共享的是属性语义和元数据，不是底层对象数据。 |
| Type system | [Value types](https://www.palantir.com/docs/foundry/object-link-types/value-types-overview/) | Value Type 是带领域语义和约束的可复用 primitive wrapper。 |
| Type system | [Value type versions](https://www.palantir.com/docs/foundry/object-link-types/value-types-versions/) | 名称等元数据可变；base type 与当前版本约束不可变；约束变化产生新版本。 |
| Type system | [Value type constraints](https://www.palantir.com/docs/foundry/object-link-types/value-type-constraints/) | Enum、Range、Regex、RID、UUID、数组唯一性/元素约束、Struct 字段约束。 |
| Links | [Link types](https://www.palantir.com/docs/foundry/object-link-types/link-types-overview/) | 单个 Link Type 天生双向、两侧分别命名；多对多可由独立 datasource 支撑；跨 Ontology Link 不支持。 |
| Interfaces | [Overview](https://www.palantir.com/docs/foundry/interfaces/interface-overview/) | Interface 是不可实例化的抽象能力/形状；支持多实现、多接口和多重扩展。 |
| Interfaces | [Link constraints](https://www.palantir.com/docs/foundry/interfaces/interface-link-types-overview/) | Interface 可声明指向 object/interface 的 ONE/MANY 必需或可选关系能力。 |
| Interfaces | [Action constraints](https://www.palantir.com/docs/foundry/interfaces/interface-action-type-constraints/) | 只约束 capability 与参数形状，不统一执行逻辑、规则、权限或 side effects。 |
| Interfaces | [Extend an interface](https://www.palantir.com/docs/foundry/interfaces/extend-interface/) | 扩展会继承 properties、link constraints、action constraints；支持多重扩展。 |
| Design | [Best practices](https://www.palantir.com/docs/foundry/ontology/ontology-best-practices/) | 优先级依次为 domain-driven、DRY、open/closed、composition；先建模现实再映射数据。 |
| Design | [Structural guidance](https://www.palantir.com/docs/foundry/ontology/ontology-structural-guidance/) | 明确事实唯一存储、动态派生、Struct、Interface、object-backed link、命名和分层安全规则。 |
| Design | [Anti-patterns](https://www.palantir.com/docs/foundry/ontology/ontology-anti-patterns/) | System/Department Silos、Kitchen Sink、God Object、Golden Hammer、Action Sprawl、Time Machine、Misnomer。 |
| Design | [Validation](https://www.palantir.com/docs/foundry/ontology/ontology-design-validation/) | 必须用未预演的真实业务问题和通用分析工具分别测试人和 Agent；记录正确率、路径、耗时与求助。 |

第一批建模范围精确共有 58 页。此后又阅读了 Actions、Functions、Scenarios、Object Explorer、Object Views、Ontology Manager、Vertex、Object backend、permissioning、indexing、edits 与 materializations 中决定产品契约的核心页；纯点击教程、Legacy/Sunset 迁移和 Marketplace 发布页未逐页纳入结论。

### 第二批已读的契约页（合并记录）

| 模块 | 代表页面 | 形成的判断 |
| --- | --- | --- |
| Actions | [Overview](https://www.palantir.com/docs/foundry/action-types/overview/)、[Rules](https://www.palantir.com/docs/foundry/action-types/rules/)、[Submission criteria](https://www.palantir.com/docs/foundry/action-types/submission-criteria/)、[Permissions](https://www.palantir.com/docs/foundry/action-types/permissions/)、[Consistency guarantees](https://www.palantir.com/docs/foundry/action-types/consistency-guarantees/)、[Action log](https://www.palantir.com/docs/foundry/action-types/action-log/) | Action 是带参数、条件、权限、规则、side effects、日志和事务语义的受控决策写回边界，而不是 CRUD endpoint 的别名。 |
| Functions | [Overview](https://www.palantir.com/docs/foundry/functions/overview/)、[Types reference](https://www.palantir.com/docs/foundry/functions/types-reference/)、[Ontology edits](https://www.palantir.com/docs/foundry/functions/edits-overview/)、[Versioning](https://www.palantir.com/docs/foundry/functions/functions-versioning/) | Function 是类型化、版本化、可监控的实时逻辑；编辑函数只生成 edit batch，真正写回必须由 function-backed Action 执行。 |
| Scenarios | [Overview](https://www.palantir.com/docs/foundry/ontology/overview-ontology-scenario/)、[Temporary scenarios](https://www.palantir.com/docs/foundry/ontology/temporary-scenario/)、[Persisted scenarios](https://www.palantir.com/docs/foundry/ontology/persisted-scenario/)、[Merge](https://www.palantir.com/docs/foundry/ontology/merge-scenario/) | Scenario 是在当前 Ontology 上叠加 Action edits 的隔离 sandbox，不是历史版本；临时与对象化持久场景承担不同协作和治理需求。 |
| Search/Object Sets | [Object Explorer](https://www.palantir.com/docs/foundry/object-explorer/overview/)、[Filter](https://www.palantir.com/docs/foundry/object-explorer/filter-results/)、[Pivot](https://www.palantir.com/docs/foundry/object-explorer/pivot-linked/)、[Compare](https://www.palantir.com/docs/foundry/object-explorer/compare-object-sets/)、[Save explorations](https://www.palantir.com/docs/foundry/object-explorer/save-explorations/)、[Save lists](https://www.palantir.com/docs/foundry/object-explorer/save-lists/) | 动态 Exploration 保存查询定义；List 保存静态成员。筛选、关系跳转、聚合图表、比较、Action、导出、分享属于同一探索闭环。 |
| Object UI | [Object Views](https://www.palantir.com/docs/foundry/object-views/overview/)、[Standard views](https://www.palantir.com/docs/foundry/object-views/standard-object-views/)、[Configured views](https://www.palantir.com/docs/foundry/object-views/config-overview/)、[Vertex](https://www.palantir.com/docs/foundry/vertex/overview/) | 每种类型自动有标准 full/panel view；可配置 view 是工作流化覆盖层。Vertex 是关系、事件、时间和模拟工作台，不只是静态网络图。 |
| Architecture | [Backend overview](https://www.palantir.com/docs/foundry/object-backend/overview/)、[Permissioning](https://www.palantir.com/docs/foundry/object-permissioning/overview/)、[Indexing](https://www.palantir.com/docs/foundry/object-indexing/overview/)、[Edits](https://www.palantir.com/docs/foundry/object-edits/overview/)、[Materializations](https://www.palantir.com/docs/foundry/object-edits/materializations/) | 元数据、对象存储、查询服务、Actions、索引 Funnel、Functions 职责分离；资源权限与实例/属性安全是两层；物化是最新对象状态的下游投影，不是主存储。 |

## 3. 配图证据（已实际查看）

| 图片 | 可观察信息 | 对当前项目的意义 |
| --- | --- | --- |
| `why-ontology-overview.png` | Data 提供历史、关系、时间与运营上下文；Logic 叠加规则、预测、优化和场景；Action 叠加审批、提议、写回、通知与升级；Security 横切整个决策链。 | 当前产品不能只按“图谱 + 推理 + Action”分菜单；还需检查这四部分是否共享同一身份、权限、上下文和 lineage。 |
| `object-apps-oe.png` | 查询条件以 chips 表达，关系跳转直接进入条件链；Explore 与 Results 分栏；结果集同时支持 Actions、Open in、Export、保存探索和列表。 | 当前 Object Query UI 应审计是否仍是开发者表达式编辑器，而不是可供普通用户探索、比较和执行操作的 Object Set 工作台。 |
| `object-apps-object-view-hub.png` | 单对象页包含对象导航、可配置 tabs、属性摘要、事件时间线、Links、评论、Action、Edit history 和嵌入式管理应用。 | 当前 Entity detail 若只是属性 JSON/关系表，缺少的是对象级工作中枢，而不仅是样式差异。 |

## 4. 第一批已确认的仓库事实

- `README.md` 将 Auto Mapping 描述为“dataset → entity type、column → property、FK → link type”。
- 当前代码已有独立 Object Query core，包含表达式、验证、normalize、compiler、executor、cursor、data view、resource、logic binding 和 terminal capability 边界。
- 当前代码已有 Scenario 模型、迁移、router 与前端 `ScenarioContextProvider`，因此不能沿用旧报告中“完全没有场景模型”的结论。
- 当前代码已有 `ObjectQueryTab.tsx`，旧验收报告中“Object Query UI 不存在”只代表当时快照，需按当前源码和真实页面重新验收。
- 当前对象查询测试已经覆盖递归集合表达式、接口范围、派生选择与 to-many cardinality violation；说明查询核心比根目录早期 demo 成熟得多。
- 当前 README 仍宣称 Neo4j/SQLite fallback，而工作区另有 FalkorDB 路径和多套服务边界；后续需确认生产事实来源与 README 是否漂移。

### 4.1 当前能力分布

| 领域 | 当前实现 | 成熟度判断 |
| --- | --- | --- |
| 元数据建模 | `Entity`/`Relation` 承担类型元数据，属性大多落在 JSON；存在 `EntityInstance`、`OntologyRevision`。没有看到 ObjectType、LinkType、Interface、ValueType、SharedProperty、StructType 各自稳定的持久化模型。 | 有原型能力，元模型不完整。 |
| Object Query | 有递归表达式 AST、filter/traverse/reachable、union/intersect/subtract、interface scope、derived selection、aggregation、compare、cursor、immutable data view 和 capability policy。 | 后端强，前端暴露很弱。 |
| Object Set resource | 有静态成员、动态 definition、不可变版本、依赖 manifest、ACL、审计、租约、temporary/permanent 生命周期。 | 这部分接近生产化骨架。 |
| Scenarios | 有隔离 data view、不可变 revision、change set、run/stage/artifact/metric、grant/audit、preview/submit/merge/rollback 及通用工作台。 | 能力丰富，但与主 Action 语义分叉。 |
| Actions | `v2_ontology_action_types` 已保存 parameters、criteria、effects、side effects、permission rules、function binding、version/status；另有更强的 Scenario Action compiler。 | 两套运行时并存，治理语义不统一。 |
| Functions/Logic | `LogicAsset` 有 JSON Schema 输入输出、接口/版本、binding、结构化 trace、run history、derived facts 和顺序 plan；目前 executor 是源码内注册的本地确定性函数。 | 好的本地逻辑资产原型，不是完整函数平台。 |
| UI | 有 Ontology 详情页、Entity 详情、Object Query tab、Action detail、Logic Assets、Graph 与 Scenario workbench。 | 页面不少，但跨页工作流和对象中心体验不足。 |
| 安全 | Ontology 主要按 admin/creator；Object Set 与 Scenario 有 owner/grant；QueryPolicy 支持字段与对象白名单，但常用 router 构造时仅传 principal，意味着默认不施加实例/字段限制。 | 资源 ACL 部分具备，数据级安全尚未贯通。 |

## 5. 总结性差距（按优先级）

### P0：会影响语义正确性或安全边界

1. **缺少独立、版本化的 Ontology 元模型。** 当前 `Entity` 同时像“对象类型定义”和普通业务实体配置，属性放在 JSON 中；Interface、Value Type、Shared Property、Struct、link-side cardinality/name、object-backed relationship 都没有形成同级一等资源。这会让查询、Action、UI、迁移各自解释同一份 JSON。
2. **授权没有成为统一的查询/函数/Action上下文。** 现有 owner/grant 能保护资源，但不是 object/property/cell security；`QueryPolicy(principal=...)` 默认等价于无限制数据访问。Scenario、Object Set、Logic Asset、Action 各自授权，派生值与日志也没有统一的安全传播证明。
3. **两套 Action 运行时正在产生语义漂移。** 主 Action router 只实现少量 criteria/effect，未实际执行 `permission_rules`；未知 effect 会被记为 `skipped`。Scenario Action 则有独立 compiler、preview、revision、merge 与幂等摘要。相同“Action type”在不同页面可能不是同一契约。
4. **写入一致性仍低于产品宣称所需。** 单次 SQL 提交不等于完整 Action ACID：当前没有统一 edit batch、对象级冲突检测、snapshot isolation、write-skew说明、重试策略、外部 side-effect 幂等和跨存储提交协议。

### P1：核心产品闭环缺失

5. **查询引擎强，但 Object Explorer 产品层几乎没有。** `ObjectQueryTab` 目前主要是类型下拉、首屏表格和 JSON 结果；没有可视 filter chips、嵌套 AND/OR、linked-property filter、多跳 pivot、图表联动、static list/dynamic exploration 保存、compare、share、Action、export。多数后端能力因此不可发现。
6. **对象页还不是可复用的 Object View。** 当前 Entity detail 更像类型/实例管理页，缺 standard full/panel form factor、prominent-property renderer、linked-object preview、timeline/comments/edit history、条件 tabs、workflow-specific configured views，以及在 Graph/Search/Scenario 内复用同一 panel view 的机制。
7. **Functions 尚未与 Object/Action 类型系统闭环。** 当前 Logic Asset 值得保留，但缺少对象/Object Set/Interface/Struct 等强类型签名、semver 依赖解析、发布兼容检查、运行配额/监控/流式结果、用户错误契约、编辑 provenance，以及“编辑只能经 function-backed Action 应用”的硬边界。
8. **Scenario 与 live Ontology 的关系需要重定义。** 当前项目把 immutable base view/revision 做得比 Palantir 公开 beta 更强，这是优势；但应明确它是产品扩展，而不是把 Scenario 当历史版本。还缺基于 execution context 的 criteria、持续 rebase/冲突策略、对象化 metadata/审批治理，以及所有 widget/query/function 的统一 scenario context。

### P2：治理、演进与体验缺口

9. **Ontology Manager 缺资源级 working state、change review 与 schema migration。** 当前有 revision snapshot，但尚未看到对属性删除/改名、类型 cast、datasource replacement、已有 edits 迁移的显式指令与恢复流程。
10. **索引与物化边界不清。** FalkorDB live graph、SQLite/Postgres 元数据、EntityInstance 与 data view 都可能成为“事实来源”；缺 source manifest → index version → query result → materialization 的单一契约、增量索引状态、失败恢复和最新对象状态下游投影。
11. **Vertex 式图工作台仍不完整。** 当前图能力需要补 selection panel、按关系 Search Around、多步参数化 traversal、histogram filters、events/time scrub、样式/layers、save/share/version，以及与 Object View/Action/Scenario 互通。
12. **验证缺真实用户与 Agent 的盲测。** 测试覆盖工程行为，但尚不足以证明新用户能发现 canonical type、选择正确关系、理解权限和在通用工具中回答未预演业务问题。

## 6. 设计纠正记录

| 之前或表面假设 | 证据 | 当前判断 | 分类 |
| --- | --- | --- | --- |
| 每个数据集自然对应一个 Object Type | Palantir Best practices 明确要求先识别现实实体；单行可能包含多个实体；System Silos 与 Kitchen Sink 均是反模式。当前 README 将 dataset → entity type 作为自动映射主路径。 | 自动映射可以生成候选，但不能把数据集边界当成权威类型边界；确认步骤必须允许拆分、合并、复用 canonical type，并保留映射 provenance。 | 可能需要 refactor；待查当前 materializer 是否已经允许这一点。 |
| “Derived property”就是任意计算结果 | 官方定义聚焦于运行时沿 link chain 派生，to-many 必须聚合，最多三跳，只读且继承参与对象权限。 | 当前项目的 derived facts、逻辑资产输出、图推理关系和 Object Query derived selection 需要分名分层，不能都叫 derived property。 | 需要语义拆分；具体影响待代码审计。 |
| Interface 只是共享属性集合 | 官方 Interface 还包含 link constraints、action capability constraints、多重扩展，以及在 Object Set/SDK 中作为抽象 type scope。 | 当前查询层已经出现 `interface_base` 与 `interface_traverse`，但 schema/Manager/UI 是否存在完整 Interface 定义、实现映射和 capability 验证仍未知。 | 明显部分具备。 |
| 图边直接表达所有关系 | 官方建议关系带日期、角色、状态、分配等自身元数据时使用 object-backed link type。 | 项目需要检查是否把有生命周期和业务身份的关系长期塞在 edge properties 中；双时态边不自动等于 object-backed relationship。 | 待查。 |
| 验证主要是 schema 和测试通过 | 官方要求用真实、未预演业务问题测试新用户和 Agent，并记录答案、路径、耗时、困惑和人工帮助。 | 当前大量 E2E/脚本很有价值，但若问题由实现者预先编码，不能单独证明 Ontology 可发现性和可用性。 | 验收体系缺口。 |
| 安全可以主要由应用或 Action 层检查 | 官方结构指导要求 row/column/cell 语义安全，且派生、工具调用、日志与 decision lineage 继承同一安全上下文。 | 当前 JWT/角色、轻量权限、query capability 与场景所有权需要进一步检查是否形成统一授权投影。 | 高风险待查。 |

## 7. 建议的后续产物（不是本轮实施计划）

1. 先画一张当前运行时事实图：metadata、live objects、graph index、data view、scenario revision、edits、derived facts 和 materialization 分别由谁拥有。
2. 为“统一 Ontology 元模型”“统一 Action/edit runtime”“Object Explorer UI”各做一份独立的 evidence-first 实施计划；三者不要混成一次重写。
3. 在计划之前用项目规定的 `grill-with-docs` 与产品负责人确认兼容目标：是忠实复刻 Palantir 契约，还是保留当前更强的 immutable Scenario/decision-lineage 特性并明确差异。
4. 建立 10–15 个未预演业务问题作为回归基准，同时覆盖普通用户、领域专家和 Agent。

## 8. 限制

- 本文是第一轮核心模块差距审计，不是实施计划，也不授权代码改造。
- Palantir 页面可能持续更新；结论按 2026-09-23 公开内容记录。
- 对 Palantir 私有后端实现不作推断。
- 未逐页阅读 398 个链接；未覆盖模块在第 1 节明确列出。官方配图只用于可观察 UI/信息架构，不据此推断内部实现。
