# Palantir Ontology Platform 能力与当前系统差距审计

> 审计日期：2026-10-18  
> 对比基线：当前工作区（`2266dbb` 及未提交开发内容）  
> 目标：优先建设 Ontology Platform 底层；AIP 仅作为调用方观察，不纳入本轮追平范围。

## 1. 审计口径

本次已清点 Palantir “Ontology building” 当前侧边栏的 **417 个文档入口**。清点不等于假装逐字精读所有页面：本次逐页复核了会定义底层契约的 Ontologies、Object and link types、Actions、Functions、Interfaces、Search/Object Sets、Scenarios、Ontology Manager 与 Ontology architecture；Object Explorer、Object Views 用于核对平台能力如何呈现；Map、Machinery、Vulcan、Dynamic Scheduling、Foundry Rules 等独立上层应用只登记依赖，不作为当前复刻目标；Sunset、Legacy 页面只用于理解兼容边界。

仓库已有的 `PALANTIR_ONTOLOGY_GAP_AUDIT_2026_09_23.md` 覆盖了当时侧边栏 398 个入口中的核心建模、Actions、Functions、Scenarios、Object Explorer、Object Views、Ontology Manager、Vertex、对象后端、权限、索引、编辑与物化。本报告在此基础上重新对照当前文档和当前代码。旧报告中“规范语义资源、对象/属性权限、Schema Migration 完全缺失”的结论已经过时：三者现在都有可运行骨架，但尚未达到 Palantir 的平台深度。

证据分为四级：

- **D**：Palantir 官方文档明确说明的公开产品契约。
- **S**：Palantir 官方公开 SDK/源码暴露的开发者契约。
- **R**：当前仓库代码、迁移或测试直接证明。
- **I**：根据 UI 和公开行为作出的实现建议；不是对 Palantir 私有架构的断言。

## 2. Palantir Ontology Platform 的核心边界

Palantir 把 Ontology 定义为组织的 operational layer，而不只是图数据库或 schema editor。底层可以分成六个互相约束的平面：

1. **语义资源平面**：Object type、Property、Link type、Interface、Shared property、Value type、Struct、状态、分组和稳定 API identity。
2. **对象数据平面**：多数据源映射、批量/流式索引、对象数据库、用户编辑叠加、物化和版本一致性。
3. **查询平面**：Object Sets、过滤、Search Around、聚合、派生属性、分页、规模边界和多级执行策略。
4. **动力学平面**：Action 的参数、规则、提交条件、事务编辑、Function-backed Action、side effects、回滚、日志和审计。
5. **治理平面**：schema resource 权限、对象/属性/链接数据权限、项目权限、分支、proposal、review、rebase、冲突和迁移。
6. **开发者平面**：版本化 Functions、类型注册、SDK/API 生成、函数接口、运行指标、trace、资源控制和发布兼容性。

官方公开说明可见于 [Ontology overview](https://www.palantir.com/docs/foundry/ontology/overview/)、[Ontologies overview](https://www.palantir.com/docs/foundry/ontologies/ontologies-overview)、[Indexing overview](https://www.palantir.com/docs/foundry/object-indexing/overview/index.html) 和 [Object edits](https://www.palantir.com/docs/foundry/object-edits/how-edits-applied)。

## 3. 当前系统已经具备且应保留的能力

| 能力 | 当前证据 | 判断 |
|---|---|---|
| 稳定语义资源与不可变 revision definition | `v2_semantic_resources`、`v2_semantic_resource_versions`；资源 stable id 与 revision 分离 | 已有正确骨架，继续扩展，不能退回 `Entity.properties` 作为真相源 |
| Interface、Shared Property、Value Type、Struct 资源种类 | `SEMANTIC_KINDS` 已包含这些类型 | 数据模型已占位，语义校验、运行时多态和 UI 尚不完整 |
| 显式数据源映射和 provenance | `OntologySourceMapping` | 已有基础映射，缺索引生命周期和完整多源规则 |
| 对象/属性/链接权限投影 | `OntologySecurityPolicy`、`authorization_service.py` | 已能进行 allow/deny、条件和字段裁剪；离企业级 policy inheritance、markings、组织/项目治理仍远 |
| Schema Migration | plan/run/instruction/dependency、dry-run、shadow revision、apply/revert | 已从“缺失”升级为“早期可运行”；需要覆盖更多 breaking change 和真实数据迁移 |
| Object Query / Object Sets | 编译、校验、执行、cursor、data view、边界测试 | 当前较强模块，可作为统一读取层继续建设 |
| Action 基础模型 | 参数、criteria、effects、side effects、function binding、run snapshot、revert | 已具产品雏形，事务与副作用语义仍不足 |
| Function 契约和版本选择 | semver、published release resolution、schema validation、edit provenance | 已有 contract 层；缺真正隔离、可观测、可伸缩的 function runtime |
| Scenario 数据分支 | pinned/tracking、10 分钟 rebase、compare/changes/merge、冲突处理 | 与 Palantir Scenario 的概念方向接近；它不是 schema Global Branching |
| Object Explorer / Object Views | 查询、图、分析、操作、视图配置及 E2E | 是不错的消费层验证工具，应反向驱动底层契约 |

## 4. 最大差距，按底层优先级排序

### P0-1：资源模型“有表”但还不是统一的平台真相源

Palantir 中 Object type、Link type、Action type、Interface、Shared property 等是可独立授权、分支、审查、迁移和追踪依赖的资源。当前系统的新 canonical tables 与旧 `Entity`、`Relation`、`Action`、`LogicRule` 并存，服务仍承担大量双写和兼容投影。前端编辑器也主要围绕“实体/关系/规则表单”，未统一呈现所有资源。

最大风险不是字段少，而是同一概念可能从不同接口得到不同定义。下一阶段应完成：所有读取走 revision-pinned canonical schema；旧表只作为明确标识的 projection；每个资源具有 dependency、status、usage、owner/project、security、change history 和 migration contract。

### P0-2：缺完整的 Object Storage / Indexing 服务语义

Palantir OSv2 公开契约包含 Funnel batch/streaming pipelines、primary-key 和数据限制校验、多数据源合并、用户编辑队列与 offset、读后写保证、持久编辑存储、direct datasource 和物化。当前系统有 PostgreSQL、FalkorDB、对象 materializer 和 result manifest，但更像若干适配器与重建流程，没有等价的持续索引控制平面。

当前主要缺口：

- 每个 object type 的 indexing state、lag、last successful transaction、错误和重试；
- batch/stream transaction 的确定性合并与乱序策略；
- datasource state、user edits、live index、materialization 四者的明确所有权；
- object version/offset 驱动的 read-after-write 和 stale-write 检查；
- 按 object type 配置的限制校验、dead-letter 与运维界面；
- 多数据源 column-wise merge 和冲突诊断。

这部分是当前与原版最大的工程差距。图可视化再复杂也不能替代数据平面保证。参考 [Indexing overview](https://www.palantir.com/docs/foundry/object-indexing/overview/index.html)、[Data restrictions](https://www.palantir.com/docs/foundry/object-indexing/data-restrictions)、[How user edits are applied](https://www.palantir.com/docs/foundry/object-edits/how-edits-applied) 和 [Materializations](https://www.palantir.com/docs/foundry/object-edits/materializations)。

### P0-3：Action 还未成为唯一、严格的业务写入协议

Palantir Action 是单个事务：参数和对象引用经过类型校验与 submission criteria，Ontology edits 原子提交；外部 side effects 有明确的非原子边界；Action Log、edit history、metrics、monitoring 和 revert 各有不同职责。当前系统保存 before/after snapshot 并支持 revert，也能绑定 Function，但部分业务路径仍可绕过统一 Action runtime，且缺少完整 isolation/write mode、对象版本冲突、读写授权上界和 side-effect delivery 语义。

应把所有对象/链接修改收口到一个 edit batch 协议，定义 prepare → authorize → validate → commit ontology edits → dispatch side effects → audit 的状态机，并明确 idempotency、重试、outbox、补偿和失败可见性。Action 的 API limit 也应成为可测试 contract，而不是散落常量。

### P0-4：Function 目前是本地逻辑资产执行器，不是平台运行时

Palantir Functions 是版本化、类型化、隔离的 server-side runtime，可读 Object Sets，可通过 edit batch 交给 Action 写入，并提供 release、usage、logs、traces、metrics、P95、资源配置和权限。当前系统已具 semver、发布状态、输入输出 schema 和绑定固定版本，但多数实现来自进程内 `EXECUTORS`。

仍缺：隔离 worker/container、CPU/内存/超时/并发限额、依赖打包、网络出口策略、immutable artifact、release compatibility 检查、OpenTelemetry、调用图、成本和使用关系，以及 staged writes 的跨函数一致性。公开 SDK 也显示 Palantir 把 Object/Interface/Action 当作强类型开发契约，而非自由 JSON；参见 [Palantir OSDK Maker](https://github.com/palantir/osdk-ts/blob/main/packages/maker/README.md)。

### P0-5：Ontology Global Branching 与 proposal 治理缺失

当前 revision 和 scenario 支持保存版本、情景 rebase/merge，但 Palantir Global Branching 针对的是 **schema/resource development**：受保护资源只能在 branch 修改，ontology proposal 汇总资源级 change，支持 reviewer、policy approval、comments、rebase、conflict resolution 和 merge。

当前没有等价的 ontology branch、resource task、proposal review 和保护策略。Scenario branch 不能替代它：Scenario 隔离对象数据编辑；Global Branching 隔离 builder 对 schema 与平台资源的修改。官方也明确区分两者，参见 [Branching the ontology](https://www.palantir.com/docs/foundry/ontologies/branching-ontology) 和 [Ontology scenarios](https://www.palantir.com/docs/foundry/ontology/overview-ontology-scenario)。

### P0-6：权限已有查询裁剪，但治理模型还不完整

当前 `OntologySecurityPolicy` 支持 user/role、allow/deny、ontology/object/property/link scope、条件与字段 allowlist，且 Object Query 会使用 permission digest。这是实质能力，不应低估。

与 Palantir 的主要差距是：schema resource 权限与 object instance 权限尚未形成两套清晰、可解释的体系；缺 organization/space/project 资源归属和迁移；缺 markings、classification、mandatory control property；Action 的 read authorization 和 write authorization 未形成独立 contract；安全策略在所有读写入口的覆盖证明不足。需要用“入口矩阵”验证 Graph、Object Query、Explorer、Function、Action、materialization 和 export 都走同一授权投影。

### P1-1：丰富类型只覆盖了一部分

当前 canonical base types 主要是 string/numeric/boolean/date/time/json/geopoint/media。Palantir 还公开支持 arrays、GeoShape、Attachment、Media reference、Time Series、Geotemporal、Cipher、Struct 等，并对 primary key、nested array、Struct nesting、搜索和聚合能力有明确限制。当前 `metadata_json/constraints_json` 可以存信息，但“能存”不代表查询、索引、Action 参数、SDK 和 UI 都理解该类型。

应建立统一 type registry 和 capability matrix，使每种类型明确支持：source mapping、primary key、filter、sort、aggregate、full-text、Action input、serialization、SDK type 和 migration。

### P1-2：Interface / Shared Property / Value Type 只有资源形状，缺运行时语义

Palantir Interface 提供多态 Object Set、property mapping、link constraint 和 interface action；Shared Property 复用语义定义；Value Type 是可版本化、带约束的基础类型封装。当前已能保存相关 kind 和引用，但尚未证明：

- interface implementation 的完整性和兼容性校验；
- interface Object Set 返回不同 concrete object type；
- interface link constraints 与 interface Actions；
- Shared Property 的继承/override 规则；
- Value Type constraint 的 immutable version 和全链路执行。

### P1-3：查询能力强于多数模块，但缺 Palantir 的规模执行层

当前 Object Query 的 AST、编译、分页、Data View 和测试基础较好。Palantir 的公开系统进一步定义 pushdown → in-memory → Spark 的分级执行、Search Around/derived property 的阈值、query compute 成本和可预期限制。当前系统更依赖单机 PostgreSQL/FalkorDB/DuckDB，缺 query planner 的 cost/scale contract、异步大查询、spill、取消、资源配额和统一 explain。

### P1-4：Ontology Manager UI 仍是开发工作台，不是资源管理产品

Palantir UI 的核心不是“更像某张截图”，而是信息架构稳定：左侧按资源浏览/搜索，中间是当前资源，顶部显示 status/branch/保护状态，主体按 Overview、Properties、Datasources、Security、Capabilities、Usage/Dependencies 等任务组织，修改进入 working changes，保存时统一 review diff。

当前详情页把“本体、数据模型、实体、对象查询、逻辑规则、质量审查、推理、决策、Agent、业务数据、逻辑绑定”等十多个不同抽象层放在同一 tab bar；编辑时又同时展示大型 `OntologyEditorPanel` 和 `SemanticControlPanel`。这会让用户无法判断自己正在修改 schema、数据、规则、运行实例还是消费应用。

底层建设期建议把主 UI 重构为资源管理器：

- 左栏：Object types、Link types、Action types、Interfaces、Shared properties、Value types、Functions、Groups；
- 中栏：选中资源的 Overview / Schema / Datasources / Security / Capabilities / Usage；
- 右栏或底栏：working changes、validation、dependency impact、save/review；
- Object Explorer、Scenario、Reasoning、Agent 移到“使用/应用”入口，不与 schema editor 并列。

### P1-5：平台可观测性和运营控制不足

目前 result manifest、run status 和部分 metrics 已存在，但没有统一资源 usage graph、index health、Action success/failure/latency、Function P95/trace、policy decision audit、migration progress、materialization freshness 和告警。Palantir 的 UI 截图反复呈现这些运营状态，说明它们属于平台契约，不只是装饰。

## 5. 概念上最容易混淆的区别

| 当前容易混在一起的概念 | Palantir 中的边界 |
|---|---|
| Entity schema 与 object instance JSON | Object type 是版本化资源；object 是索引后的实例 |
| revision 与 branch | revision 是不可变状态；branch 是可持续修改、rebase、review、merge 的开发线 |
| Scenario 与 Global Branching | Scenario 隔离对象数据 edits；Global Branching 隔离平台资源/schema 变更 |
| Function 与 Action | Function 计算并可产生 edit batch；Action 负责授权、验证和提交业务修改 |
| user edits 与 source data | 两者独立持久，live object state 是受规则约束的合并结果 |
| graph database 与 Ontology | 图存储只是一个执行后端；Ontology 还包含类型、动作、权限、版本、索引和治理 |
| UI tab 与 platform capability | 一个 tab 不证明底层具有一致性、安全、规模或生命周期保证 |

## 6. 文档覆盖台账

### 6.1 本轮逐页复核的底层组

- Foundations：overview、why create、core concepts、models in Ontology。
- Ontologies：overview、create/manage、usage、permissions、project-permission migration、resource migration、branching。
- Object and link types：create/edit/metadata/properties/required/edit-only/mandatory controls/derived properties/structs/shared properties/value types/render hints/statuses/groups、link definition/cardinality/multi-source/editing。
- Action types：overview、rules、parameters、submission criteria、Function-backed、batch execution、side effects、permissions、read/write authorization、consistency/isolation、revert、Action Log、metrics、monitoring、branching、limits。
- Functions：overview、types、Object Sets、edit functions、function interfaces、releases、permissions、resource configuration、telemetry、performance、staged writes、limits。
- Interfaces：definition、implementation、properties、links、Object Sets、Actions 和限制。
- Search/derived：semantic search、Object Sets、Search Around、aggregation、derived property。
- Scenarios：temporary、persisted、merge、options、execution context 与限制。
- Ontology architecture：permissions、security policies、多数据源、indexing batch/streaming/direct datasource、edits、migration、materialization、OSv1/OSv2 边界。

### 6.2 作为底层验收界面复核

- Object Explorer：search、filter、pivot、compare、explore graph、actions、dynamic/static object sets。
- Object Views：standard/configured views、widgets、versioning、branching。
- Ontology Manager：资源创建、metadata、datasources、security、capabilities、working changes、usage/dependencies 和 branching UI。

### 6.3 已清点但不作为当前复刻目标

- Vertex：消费 Object Sets、Functions、Actions 和 Scenarios；保留为情景分析验收方。
- Map、Machinery、Vulcan、Dynamic Scheduling、Foundry Rules：独立分析/运营应用；仅记录其对底层类型、查询、Action 和权限的需求。
- AIP/Agent：当前明确排除；只保证未来能通过受控 Function/Action/Object Query 接入。
- Sunset monitors、Legacy OSv1：只做迁移与兼容参考。

## 7. 当前最合理的建设顺序

这不是完整实施 plan，而是由差距得出的依赖顺序：

1. 收口 canonical resource truth，并完成全资源 dependency/status/usage/security。
2. 建立统一 object data plane：source transaction、edit log、live projection、version/offset、index lifecycle。
3. 把所有写入收口到 transactional Action runtime。
4. 建隔离且可观测的 Function runtime，并支持 typed Object Sets 与 edit batches。
5. 实现 schema Global Branching/proposal/review，与 Scenario data branch 明确分离。
6. 完整类型 registry、Interface 多态、Shared Property 和 Value Type 运行时。
7. 重构 Ontology Manager 信息架构，再用 Object Explorer/Scenario 做端到端验收。
8. 最后扩展分布式查询、物化、streaming 和高规模压力测试。

## 8. 审计结论

当前系统已经不再是单纯的“实体关系图 demo”：canonical semantic resources、revision、Object Query、权限投影、Schema Migration、Action run、Function contract 和 Scenario 都有真实代码与测试基础。它与 Palantir 原版最大的差距也因此不在页面颜色或节点数量，而在 **六个平面尚未由统一、可治理、可观测、可扩展的运行契约连接起来**。

如果以 Ontology Platform 底层为主线，最应避免的是继续在同一详情页增加独立 tab 或业务 demo。每增加一个上层功能，都应先证明它只通过统一的 schema、query、authorization、Action 和 Function contract 工作。这样后续 Vertex、AIP 或任何自定义应用才是在复用平台，而不是各自复制一套逻辑。
