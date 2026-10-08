# 人员 A 计划：Ontology 语义核心、安全与数据平面

> 计划性质：责任边界与差距说明；不是工期承诺，也不授权实施。
> 负责人：人员 A
> 基准日期：2026-09-23
> 上游审计：`docs/PALANTIR_ONTOLOGY_GAP_AUDIT_2026_09_23.md`

## 1. 目标与不做事项

人员 A 对“所有上层能力共同依赖的语义和数据可信度”负责，目标是让 Object Query、Action、Function、Scenario、Object View 和 Agent 使用同一套类型身份、授权投影、版本与数据来源解释。

本责任包覆盖四组差距：

1. 缺少独立、版本化的 Ontology 元模型；
2. 授权没有形成统一的查询/函数/Action 上下文；
3. 索引、快照、Scenario view 与物化的数据平面边界不清；
4. Ontology schema 演进缺少显式迁移与恢复语义。

本计划不负责 Object Explorer、Object View、Vertex 的最终交互设计，不负责统一 Action/Function 执行器的内部实现，也不把系统改写成 Palantir 的私有架构。人员 B 是这些上层运行时和 UI 的主要负责人。

## 2. 当前仓库事实

### 2.1 元数据与实例混用了过于宽泛的模型

- `backend/app/models/entity.py` 中的 `Entity` 同时保存名称、类型、canonical id、领域编码、任意 `properties` JSON、置信度和版本。它在当前页面与查询 metadata adapter 中承担了接近 Object Type 的职责，但名称和字段仍允许它被理解为普通实体记录。
- `backend/app/models/entity_instance.py` 又保存真正的行级实例，实例数据进入 `row_data` JSON；因此“Entity 是类型还是实例”“property definition 与 property value 分别在哪里”只能靠调用方约定，而不是模型约束。
- `backend/app/models/relation.py` 用 `source_entity`、`target_entity`、`type` 和任意 JSON 表示关系。它没有把一个双向 Link Type 的两侧名称、方向、基数、必需性、目标类型和 backing mode 建成稳定契约。
- `backend/app/services/v2/object_query/metadata.py` 将上述通用表适配成查询引擎的 `TypeMetadata`/`LinkMetadata`。查询核心因此已经需要一个较严格的元模型，但数据库的权威表示仍较松。
- 当前没有发现与 Object Type 同级、可独立版本和引用的 Interface、Value Type、Shared Property、Struct Type 资源模型。查询 schema 虽支持 interface scope，却没有完整的 persisted interface catalog 作为来源。

这不是单纯的表结构审美问题。当前 JSON 可以表达很多形状，却无法保证不同入口对同一字段得出同样答案。例如：一个属性是否数组、是否只读、是否 prominent、是否实现某个 Interface property、是否受 Value Type 约束，可能分别由查询 adapter、前端和 Action 校验自行猜测。随着功能增加，这会转化为不可复现的授权和写入错误。

### 2.2 自动映射把数据源结构放在了领域身份之前

- `README.md` 把主要自动映射路径描述为 dataset → entity type、column → property、foreign key → link。
- 这条路径适合作为候选生成器，但数据集边界通常反映采集系统、部门或导出格式，不保证对应现实世界实体边界。
- 如果把它固化为权威模型，一张宽表容易成为 Kitchen Sink/God Object；同一现实对象也可能因为来自两个系统而形成重复类型。
- 现有 `canonical_id` 提供了合并线索，但未看到“候选类型 → canonical type”“源字段 → canonical property”的独立版本化 mapping contract。

Palantir 的建模建议要求先识别现实对象和事件，再映射数据；同一输入行可以贡献多个对象。这里的差距是建模顺序和身份权威性，而不是少一个导入按钮。

### 2.3 当前授权主要保护资源，不等于保护对象数据

- 多数 Ontology router 的基础授权是 `admin` 或 `OntologyProject.created_by`。
- Object Set 和 Scenario 已有 owner/grant，这是值得保留的资源级 ACL：它决定谁能读取或编辑一个保存的集合或场景资源。
- `backend/app/services/v2/object_query/core.py` 的 `QueryPolicy` 已支持 object id 与 field allowlist，并把权限摘要纳入结果 manifest。
- 但常用 router 以 `QueryPolicy(principal=user.id)` 构造策略；代码注释明确 `None` 表示 ontology-authorized principal 拥有不受限数据访问。也就是说现有字段/对象裁剪能力通常没有接入真实 policy resolver。
- Logic Asset 直接从图对象读取声明字段；Action、Scenario、Object Query 又各自做一次权限判断。没有证据证明派生事实、聚合、Action log、Scenario view 和导出使用同一个授权投影。

Palantir 将“查看类型定义”和“查看对象实例/属性值”分成两层，并允许数据源策略或对象/属性安全策略决定后者。当前项目的差距不是 RBAC 角色数量少，而是没有一个可被所有入口调用、可解释、可版本和可摘要的 data authorization decision。

### 2.4 数据平面已经很多，但权威关系没有冻结

当前至少存在以下状态载体：

- SQL 中的 Ontology 元数据、`EntityInstance`、Action runs、Logic facts、Object Set/Scenario resources；
- FalkorDB 中的 live graph 与各类 view graph；
- `OntologyRevision.snapshot_json`、`graph_namespace`、hash；
- `QueryDataView` 的 source/metadata/changeset/index digest；
- Scenario revision、change set 与 result artifacts；
- README 中仍描述的 Neo4j/SQLite fallback 路径。

`data_views.py` 已经做了一件重要而正确的事：复制图后重新读取源图，源发生变化就拒绝把混合切面宣称为 strict snapshot。这说明项目知道 revision label 与真实 snapshot 不等价。但系统层面仍缺少一张权威关系表回答：

- 哪个存储保存事实，哪个只是索引或投影；
- metadata revision 和 object-data revision 如何组合成 query identity；
- live graph、immutable data view、Scenario revision 与 materialization 的一致性等级分别是什么；
- fallback 后是否还能保持相同语义，不能保持时如何返回“能力不可用”而不是静默给出不同结果；
- Action 写 SQL、写图、写 view 或写 edit log 时，失败后谁负责 reconcile。

Palantir 公开架构把 metadata、object database、Object Set reads、Actions、indexing funnel、Functions 分成职责明确的服务。项目无需复制微服务数量，但必须复制这种所有权清晰度。

### 2.5 Revision snapshot 不能替代 schema migration

- `OntologyRevision` 能保存父 revision、snapshot、hash 和 current 标记，适合审计与 assisted repair。
- 但删除/改名属性、改变数据类型、替换 datasource 时，历史用户编辑、Scenario changeset、Object Set definition、Logic binding 和前端配置会引用旧 schema。
- 当前没有看到显式的 drop/move/cast edit migration、依赖影响分析、失败恢复、分批应用和恢复后重新发布流程。
- 简单回滚 JSON snapshot 可能恢复 metadata，却无法自动恢复已经迁移或丢弃的 edits/index/materialization 状态。

因此差距是“schema 变化如何安全穿过现存数据和依赖”，不是有没有 revision 表。

## 3. 参考语义与必须守住的不变量

1. **类型定义与实例数据分离。** Object Type/Interface/Link Type/Value Type/Struct 等是可引用的 metadata resources；object/link instances 是受其约束的数据。
2. **现实身份优先。** datasource mapping 是有 provenance 的适配，不是 canonical identity 本身。
3. **Link 是单一双向定义。** 两侧分别有名称、目标、基数和可见语义；带业务属性/生命周期的关系应建模为对象支持的关系，而不是随意扩充 edge JSON。
4. **Interface 不可实例化。** 它声明 properties、links、actions 的能力约束；实现映射必须可验证且可被查询类型系统使用。
5. **Value Type 的基础类型和版本约束不可被就地改变。** 破坏性约束变化必须形成新版本并进行兼容检查。
6. **授权投影先于读取和计算。** 查询、聚合、派生、函数输入、Action criteria、导出和日志都必须从同一 principal/context 得到允许的对象和字段。
7. **资源分享不扩大数据权限。** 获得 Object Set/Scenario 资源权限，不自动获得其中底层对象的访问权限。
8. **数据平面身份可重建。** 每个结果能追溯 metadata revision、source manifest、index/view version、scenario/edit context、permission digest 和执行器版本。
9. **不可用不是空结果。** 缺索引、权限不足、snapshot 过期、adapter 不支持和合法空集合必须有不同稳定错误/状态。
10. **Schema 迁移显式。** 任何破坏引用或历史 edits 的变更必须有 impact report、migration instruction、dry run、结果审计与恢复边界。

## 4. Design corrections

| 之前的假设 | 证据 | 修正后的设计 | 对现有代码影响 | 动作 |
| --- | --- | --- | --- | --- |
| `Entity.properties` 足以长期承载类型定义 | 查询 core、Action 和 UI 已经需要不同的强约束；官方和 OSDK 将 object/interface/property/link 分成明确类型 | 建立 canonical metadata contract；JSON 只保留可扩展 metadata，不承担全部不变量 | metadata adapter、导入、Action validator、前端类型读取需迁移 | refactor + migrate |
| dataset 与 Object Type 一一对应 | 官方反模式和 domain-first 指导；当前 README 明示自动一一映射 | 自动映射只生成候选和 mapping provenance，允许拆分、合并和复用 canonical type | construction draft/materializer 需区分 candidate 与 published type | extend |
| 有 ontology 访问权就可读取全部对象字段 | `QueryPolicy` 的 unrestricted 默认与官方两层权限模型冲突 | 引入统一 authorization projection，并要求所有 terminal read/write 显式携带 | routers、query、logic、scenario、export 需接入 | refactor |
| revision id 就代表一致快照 | `data_views.py` 已主动检测复制期间源变化，证明两者不同 | 分离 metadata revision、source revision、projection/view identity 和 consistency level | cursor/cache/manifest/scenario context 需携带复合身份 | preserve + extend |
| FalkorDB/SQLite/Neo4j fallback 可透明互换 | 各 adapter 的查询、snapshot 与事务能力不等价 | 能力协商必须显式；语义不等价时返回 unavailable/degraded | README、capability API、error codes 和 tests 需调整 | deprecate silent fallback |
| 恢复旧 snapshot 等于完成 schema rollback | 官方 schema migration 单独处理历史 edits；当前依赖资源也可能引用旧 schema | metadata restore 与 data/edit migration 分开建模 | revision service、dependency graph、migration records 需扩展 | extend |

## 5. 目标边界（简要行动建议）

人员 A 建议按以下顺序推进，具体表结构与接口在实施前形成短 ADR：

1. 定义 canonical Ontology metadata schema 和稳定 API identity；为旧 `Entity`/`Relation` 提供只读兼容 adapter。
2. 定义 `AuthorizationContext → AuthorizationProjection` 合同，并先接入 Object Query terminal paths。
3. 产出数据平面 ownership/consistency 表与统一 result manifest；停止语义不同的静默 fallback。
4. 建立 schema dependency graph、migration instruction 和 dry-run/report 机制。
5. 再迁移 Action、Logic、Scenario 与 UI 到新合同；迁移期保持旧 API 有明确弃用期。

不建议现在引入新的分布式 metadata 或 policy 服务。先把合同做成进程内核心服务并验证边界，只有规模或独立部署需求被测量后再拆分。

## 6. 与人员 B 的接口和文件所有权

### 人员 A 主要拥有

- `backend/app/models/entity.py`、`relation.py`、`entity_instance.py`、`ontology_revision.py`；
- 新 canonical metadata、security policy、migration 与 data-plane manifest 模块；
- `backend/app/services/v2/object_query/metadata.py`、`core.py`、`data_views.py` 中的共享契约；
- 与 metadata/security/migration 直接相关的 Alembic 迁移和 contract tests。

### 共享合同，先冻结后由双方消费

- `TypeRef`、`PropertyRef`、`ObjectRef`、`LinkRef` 的稳定身份；
- metadata revision/digest 和 compatibility rules；
- `AuthorizationContext`、field/object projection 与 permission digest；
- data/view/scenario execution context 和 consistency enum；
- edit batch 允许引用的 canonical property/link identifiers；
- stable error envelope 与 capability/degradation codes。

人员 B 不应在 UI 或 Action compiler 中复制这些规则；人员 A 也不应直接重做 B 所拥有的页面和运行时。共享 schema 修改采用小型 ADR 和生成类型，避免双方同时手改 Python/TypeScript 两份定义。

## 7. 验证与验收证据

| 层级 | 最小验收证据 |
| --- | --- |
| Unit/contract | 非法基数、Interface 映射、Value Type 版本变化、Link side、Struct 限制被稳定拒绝；identity/hash/immutability 可重复。 |
| Authorization component | 同一请求从 Query、aggregate、Logic binding、Scenario context 进入时得到相同对象/字段投影；分享资源不扩大底层访问。 |
| Migration integration | 在真实 Postgres/FalkorDB 组合上完成 rename/drop/cast/datasource replacement 的 dry run、apply、restart、reconcile 和 rollback 边界验证。 |
| Snapshot/data view | 区分 live、pinned、expired、stale、unavailable；cursor 与 permission/metadata/view digest 不匹配时拒绝重放。 |
| Security | 未授权字段不出现在结果、聚合、派生值、trace、audit 和错误文本；日志不泄漏值。 |
| Performance | 记录 metadata resolve 与 authorization projection 的 p50/p95/p99；避免每对象一次 policy 查询；设定查询 fan-out 与缓存失效指标。 |
| Static gates | Backend changed scope 执行 `pytest`、迁移检查和现有静态检查；涉及生成前端类型时执行 `npm run lint:src` 与 `npm run build`，changed files 零 error/零 warning。 |

真实数据验证建议使用公开且有稳定版本的 NYC TLC Trip Record Data 或同类关系丰富数据集：保留原始下载 URL、retrieval date、checksum、真实记录数与许可证说明；用真实数据验证 canonical identity、跨表 link、行/字段安全和增量索引。另建明确标记为 synthetic 的规模夹具进行百万级对象压力测试，不能把复制数据宣称为真实数据集。

若在相同环境和夹具下连续出现“修复一个入口、另一个入口权限/版本语义再次失效”的重复模式，暂停依赖该边界的补丁，启动一次有界 architecture review；检查规则是否仍被多个 router/adapter 复制，而不是直接扩大成全仓重写。

## 8. 风险与未决项

- 旧 `Entity` 是否被外部 API 当作业务实例使用，需要在迁移前做调用方清单；这是兼容风险，不应凭名称推断。
- 生产事实存储究竟是 Postgres+FalkorDB 还是仍支持 Neo4j/SQLite，需要由部署证据确认；README 不能作为唯一事实。
- Object/property policy 的产品表达方式尚未决定；本计划只冻结执行合同，不预设必须采用 OPA 或特定规则语言。
- Palantir 的具体内部微服务和存储实现不公开，本项目只采用公开语义，不声称复制其后端。

## 9. 差距参考文档

### Palantir 官方文档

- [Ontology overview](https://www.palantir.com/docs/foundry/ontology/overview/)：semantic/kinetic elements 与 operational layer。
- [Ontology best practices](https://www.palantir.com/docs/foundry/ontology/ontology-best-practices/)：domain-first、DRY、open/closed、composition。
- [Ontology anti-patterns](https://www.palantir.com/docs/foundry/ontology/ontology-anti-patterns/)：System Silos、Kitchen Sink、God Object 等。
- [Structural guidance](https://www.palantir.com/docs/foundry/ontology/ontology-structural-guidance/)：事实唯一存储、Interface、Struct、object-backed link 与安全。
- [Object and link type reference](https://www.palantir.com/docs/foundry/object-link-types/type-reference/)：类型定义、实例、Object Set 与 Value Type 的层次。
- [Interface overview](https://www.palantir.com/docs/foundry/interfaces/interface-overview/)：不可实例化、多实现和扩展语义。
- [Value types and versions](https://www.palantir.com/docs/foundry/object-link-types/value-types-versions/)：基础类型与约束的版本不变量。
- [Ontology architecture](https://www.palantir.com/docs/foundry/object-backend/overview/)：OMS、object database、OSS、Actions、Funnel、Functions 职责。
- [Object permissioning](https://www.palantir.com/docs/foundry/object-permissioning/overview/) 与 [Ontology permissions](https://www.palantir.com/docs/foundry/object-permissioning/ontology-permissions/)：schema resource 与 object/link data 两层授权。
- [Managing object security](https://www.palantir.com/docs/foundry/object-permissioning/managing-object-security/)：datasource policy 与 object/property security。
- [Indexing overview](https://www.palantir.com/docs/foundry/object-indexing/overview/)：datasource 到 object database 的索引边界。
- [Object edits](https://www.palantir.com/docs/foundry/object-edits/overview/)、[schema migrations](https://www.palantir.com/docs/foundry/object-edits/schema-migrations/) 与 [materializations](https://www.palantir.com/docs/foundry/object-edits/materializations/)：edit overlay、迁移和最新状态投影。

### Palantir 官方公开源码

- [OSDK ObjectTypeDefinition](https://github.com/palantir/osdk-ts/blob/main/packages/api/src/ontology/ObjectTypeDefinition.ts)：object metadata、property、link、interface mappings 的公开类型形状。
- [OSDK InterfaceDefinition](https://github.com/palantir/osdk-ts/blob/main/packages/api/src/ontology/InterfaceDefinition.ts)：interface 与 object 的类型区分、implementedBy 与 link metadata。
- [OSDK ObjectSet](https://github.com/palantir/osdk-ts/blob/main/packages/api/src/objectSet/ObjectSet.ts) 与 [类型测试](https://github.com/palantir/osdk-ts/blob/main/packages/api/src/objectSet/ObjectSet.test.ts)：类型化 filter/pivot/set arithmetic/derived property/page 行为。它证明公开 client contract 是强类型递归模型，不代表 Palantir 私有后端实现。

### 当前仓库证据

- `backend/app/models/entity.py`
- `backend/app/models/relation.py`
- `backend/app/models/entity_instance.py`
- `backend/app/models/ontology_revision.py`
- `backend/app/services/v2/object_query/metadata.py`
- `backend/app/services/v2/object_query/core.py`
- `backend/app/services/v2/object_query/data_views.py`
- `backend/app/services/v2/object_query/resources.py`
- `backend/app/models/v2/object_set.py`
- `backend/app/models/v2/scenario.py`

## 10. 决策摘要

- **保留：** Object Query recursive core、Object Set version/resource 骨架、immutable data view 的一致性防护、OntologyRevision 审计价值。
- **实施前必须改变：** canonical metadata、统一授权投影、数据平面 ownership 与 schema migration 合同必须先冻结。
- **组件决定：** 继续把 SQLAlchemy/FalkorDB 当 adapter；不新增分布式 policy/metadata 服务；Palantir OSDK 只作公开类型契约参考。
- **第一阶段：** 输出 metadata/security/data-plane ADR、canonical schema 与兼容映射，不先迁移全部调用方。
- **进入后续阶段的证据：** contract tests 证明旧/新读取一致，授权从至少 Query/aggregate/Logic 三入口一致，且 migration dry run 能报告全部已知依赖。
