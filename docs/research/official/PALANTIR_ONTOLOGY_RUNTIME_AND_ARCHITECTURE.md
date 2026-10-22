# Palantir Ontology：运行时、界面与后端架构（官方资料摘录）

> 提取日期：2026-10-22。内容从已归档计划中提取，只保留 Palantir 官方资料的结论，去掉了旧系统的仓库事实和旧方案。
> 来源计划（`docs/archive/`）：
> - [A] `PALANTIR_ONTOLOGY_GAP_AUDIT_2026_09_23.md`（官方页面按 2026-09-23 版本阅读）
> - [B] `PLAN_PERSON_A_SEMANTIC_CORE_SECURITY_DATA_PLANE.md`
> - [D] `PLAN_SCENARIO_CONTEXT_PROVIDER.md`（2026-09-21）
>
> 证据等级标在每节。Palantir 私有后端实现不公开，以下内容都不代表其内部实现。

## 1. Actions（官方文档；A）

Action 是受控的决策写回边界，带参数、提交条件、权限、规则、副作用、日志和事务语义，不是 CRUD 接口的别名。

页面：[Overview](https://www.palantir.com/docs/foundry/action-types/overview/) · [Rules](https://www.palantir.com/docs/foundry/action-types/rules/) · [Submission criteria](https://www.palantir.com/docs/foundry/action-types/submission-criteria/) · [Permissions](https://www.palantir.com/docs/foundry/action-types/permissions/) · [Consistency guarantees](https://www.palantir.com/docs/foundry/action-types/consistency-guarantees/) · [Action log](https://www.palantir.com/docs/foundry/action-types/action-log/)。本地 PDF：`palantir-docs/palantir_action_types*.pdf`、`action_types_rules.pdf`、`action_types_test_run.pdf`、`Use_actions.pdf`。

## 2. Functions（官方文档；A）

Function 是有类型、有版本、可监控的实时逻辑。会编辑数据的函数只生成一批编辑（edit batch），真正写回必须由 function-backed Action 执行。

页面：[Overview](https://www.palantir.com/docs/foundry/functions/overview/) · [Types reference](https://www.palantir.com/docs/foundry/functions/types-reference/) · [Ontology edits](https://www.palantir.com/docs/foundry/functions/edits-overview/) · [Versioning](https://www.palantir.com/docs/foundry/functions/functions-versioning/)。本地 PDF：`palantir-docs/functions_*.pdf`。

## 3. Scenarios（官方文档；A, D）

- Scenario 是在当前 Ontology 上叠加 Action 编辑的隔离沙盒，**不是历史版本或历史快照工具**。
- 分临时场景和持久场景，分别满足不同的协作和治理需求；可以同时建多个场景做比较。
- Beta 版有 30 天 TTL（到期自动清理），大约每 10 分钟自动 rebase 到最新数据。

页面：[Overview](https://www.palantir.com/docs/foundry/ontology/overview-ontology-scenario/) · [Temporary](https://www.palantir.com/docs/foundry/ontology/temporary-scenario/) · [Persisted](https://www.palantir.com/docs/foundry/ontology/persisted-scenario/) · [Merge](https://www.palantir.com/docs/foundry/ontology/merge-scenario/)。本地 PDF：`palantir-docs/ontology_scenarios_overview.pdf`、`OSDK_Scenarios.pdf`、`palantir_whatif.pdf`。

## 4. Vertex 场景操作（官方文档 + 实际看过的官方截图；D）

| 页面 / 截图 | 可见事实 |
| --- | --- |
| [Scenarios Getting started](https://www.palantir.com/docs/foundry/vertex/scenarios-getting-started)；[Add Action 图](https://www.palantir.com/docs/resources/foundry/vertex/simulate-system-12.jpg) | 操作顺序：Add scenario → Add Action → 配参数 → Submit → Run。布局：左侧选中对象卡，中间系统图，右侧 Case Study / Actions / Run。图中旧的 Model 选择流程已被官方标为 sunset（停用）。 |
| [完成 Run 图](https://www.palantir.com/docs/resources/foundry/vertex/simulate-system-9.jpg)、[override 图](https://www.palantir.com/docs/resources/foundry/vertex/simulate-system-10.jpg) | Run 完成时显示绿色对勾和耗时；被覆盖的输入高亮；新输出与 baseline 并列比较；右侧有 Actions 和 Models 表格。 |
| [Scenario options](https://www.palantir.com/docs/foundry/vertex/scenarios-options)；[Scope/参数图](https://www.palantir.com/docs/resources/foundry/vertex/simulate-system-7.jpg) | 可设置图上对象的范围（Scope = Objects on Graph），自动生成 baseline，可添加输入/输出参数。 |

截图只能证明界面布局，不能说明背后服务如何存储或保证事务。更多 Vertex 截图见 `vertex-scenario-research-2026-10-12/REFERENCE_GALLERY.md`。

## 5. Object Explorer 与 Object Views（官方文档 + 官方配图；A）

- **Object Explorer**：动态 Exploration 保存的是查询定义，List 保存的是固定成员。筛选、沿关系跳转、聚合图表、比较、执行 Action、导出、分享属于同一个探索闭环。配图 `object-apps-oe.png`：查询条件用 chips 表示，关系跳转直接进入条件链；左边探索、右边结果；结果集可执行 Actions、Open in、Export、保存探索和列表。
  页面：[Overview](https://www.palantir.com/docs/foundry/object-explorer/overview/) · [Filter](https://www.palantir.com/docs/foundry/object-explorer/filter-results/) · [Pivot](https://www.palantir.com/docs/foundry/object-explorer/pivot-linked/) · [Compare](https://www.palantir.com/docs/foundry/object-explorer/compare-object-sets/) · [Save explorations](https://www.palantir.com/docs/foundry/object-explorer/save-explorations/) · [Save lists](https://www.palantir.com/docs/foundry/object-explorer/save-lists/)
- **Object Views**：每种类型自动有标准的 full view 和 panel view；可配置 view 是面向具体工作流的覆盖层。配图 `object-apps-object-view-hub.png`：单个对象页有对象导航、可配置 tabs、属性摘要、事件时间线、Links、评论、Action、编辑历史和嵌入应用。
  页面：[Overview](https://www.palantir.com/docs/foundry/object-views/overview/) · [Standard views](https://www.palantir.com/docs/foundry/object-views/standard-object-views/) · [Configured views](https://www.palantir.com/docs/foundry/object-views/config-overview/)
- **Vertex**：关系、事件、时间和模拟的工作台，不只是静态网络图。[Overview](https://www.palantir.com/docs/foundry/vertex/overview/)

## 6. 后端架构、权限、索引、编辑（官方文档；A, B）

| 主题 | 结论 | 页面 |
| --- | --- | --- |
| 服务分工 | 元数据服务（OMS）、对象数据库、Object Set 查询服务（OSS）、Actions、索引管道（Funnel）、Functions 各管一块。 | [Backend overview](https://www.palantir.com/docs/foundry/object-backend/overview/) |
| 权限两层 | "能看到类型定义"（资源权限）和"能看到对象实例/属性值"（数据权限）是两层；后者由 datasource 策略或对象/属性安全策略决定。分享一个资源不会扩大对其中底层数据的访问权限。 | [Object permissioning](https://www.palantir.com/docs/foundry/object-permissioning/overview/) · [Ontology permissions](https://www.palantir.com/docs/foundry/object-permissioning/ontology-permissions/) · [Managing object security](https://www.palantir.com/docs/foundry/object-permissioning/managing-object-security/) |
| 索引 | 定义从 datasource 到对象数据库的索引边界。 | [Indexing overview](https://www.palantir.com/docs/foundry/object-indexing/overview/) |
| 编辑与迁移 | 用户编辑是叠加在数据源上的一层；schema 迁移（删除/移动/改类型）要单独处理历史编辑。 | [Object edits](https://www.palantir.com/docs/foundry/object-edits/overview/) · [Schema migrations](https://www.palantir.com/docs/foundry/object-edits/schema-migrations/) |
| 物化 | 物化是最新对象状态的下游投影，不是主存储。 | [Materializations](https://www.palantir.com/docs/foundry/object-edits/materializations/) |

## 7. 公开 API 与官方开源 SDK（官方公开源码/API 文档；B, D）

- **Object Set API**（[Load](https://www.palantir.com/docs/foundry/api/ontologies-v2-resources/ontology-object-sets/load-object-set)、[Aggregate](https://www.palantir.com/docs/foundry/api/ontologies-v2-resources/ontology-object-sets/aggregate-object-set)）：`scenarioRid` 是独立的 query 参数，和 Object Set / 聚合定义的 body 分开；Load 的 `snapshot` 是另一种读取选项；分页以 `nextPageToken` 为准；Aggregate 有精度选项和精度响应信息。这只说明公开请求边界，不能反推私有存储设计。
- **OSDK（palantir/osdk-ts）类型定义**：[ObjectTypeDefinition](https://github.com/palantir/osdk-ts/blob/main/packages/api/src/ontology/ObjectTypeDefinition.ts)、[InterfaceDefinition](https://github.com/palantir/osdk-ts/blob/main/packages/api/src/ontology/InterfaceDefinition.ts)、[ObjectSet](https://github.com/palantir/osdk-ts/blob/main/packages/api/src/objectSet/ObjectSet.ts) 及其[类型测试](https://github.com/palantir/osdk-ts/blob/main/packages/api/src/objectSet/ObjectSet.test.ts)：对象、属性、关系、接口映射都是强类型；ObjectSet 的筛选、跳转、集合运算、派生属性、分页是强类型递归模型。这是公开客户端契约，不代表私有后端。
- **OSDK React**（[CONTRIBUTING](https://github.com/palantir/osdk-ts/blob/main/packages/react/CONTRIBUTING.md)、[Getting Started](https://github.com/palantir/osdk-ts/blob/main/docs/react/getting-started.md)、[Querying Data](https://github.com/palantir/osdk-ts/blob/main/docs/react/querying-data.md)）：React provider + 客户端 observable/cache；list/aggregate 按查询身份缓存，刷新期间允许旧数据继续显示。**本项目推断**（D）：可借鉴"缓存"和"多个视图显示同一份上下文"分开处理的思路；OSDK 依赖 Foundry 后端，不适合直接引入。
