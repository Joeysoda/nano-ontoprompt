# Plan A / Plan B 集成合同

这份合同是两部分代码之间唯一的共享边界。Plan B 到达后直接调用这些接口，
不要在查询、场景或逻辑资产模块中复制权限、修订或数据平面规则。

## 稳定类型

| 类型 | 责任 | 关键身份 |
| --- | --- | --- |
| `OntologySemanticResource` | Object Type、Property、Interface、Link、Value/Struct 等稳定资源 | `resource_id` + `api_name` |
| `OntologySemanticResourceVersion` | 某个本体修订中的不可变定义 | `revision_id` + `resource_id` |
| `AuthorizationContext` | 当前主体、修订、请求字段和对象类型 | `ontology_id` + `principal_id` |
| `AuthorizationProjection` | 可见对象、允许字段、deny 结果和权限摘要 | `permission_digest` |
| `ExecutionContext` | live/pinned/snapshot、来源、视图、场景和快照 | `context_id` |
| `ResultManifest` | 结果的来源、适配器、修订、权限和一致性状态 | `context_id` + `metadata_digest` |
| `SchemaMigrationPlan` | 影响报告、影子修订、切换和撤回记录 | `plan_id` |
| `SchemaMigrationRun` | 可恢复的迁移执行状态 | `run_id` + `plan_id` |
| `DataModelSnapshot` | PostgreSQL 事实账本的已发布版本与图投影清单 | `snapshot_id` + `snapshot_hash` |
| `source_mappings` | 修订内的数据集/版本/表/字段到规范资源的映射及候选状态、证据 | `id` + `revision_id` + `resource_id` |

Link Type 的规范字段包括 `source_resource_id`、`target_resource_id`、
`source_name`、`target_name`、`direction` 和 `cardinality`；不要把这些字段
重新塞回任意 edge JSON。`SchemaMigrationRun` 的公共路径接受 run id，服务层
同时兼容内部 plan id。

Pydantic 合同位于 `backend/app/schemas/v2/semantic_core.py`，前端类型由
`backend/scripts/export_plan_a_contract.py` 生成到
`frontend/src/types/semanticCore.ts`。增加合同字段后重新运行生成脚本；CI 使用
`python backend/scripts/export_plan_a_contract.py --check` 检查文件是否同步。

## 调用顺序

1. 调用语义 Schema 读取，固定 `revision_id` 和 `metadata_digest`。
2. 创建 `AuthorizationContext`，调用一次 `resolve_projection`。
3. 将投影传入 SQL/FalkorDB/规则/What-If/导出适配器；不要逐对象重新查权限。
   流式读者必须用同一投影对每行做条件判断；禁止仅因“有一条 allow 策略”就
   放行不匹配条件的对象。策略中的稳定 `resource_id` 会在适配器边界映射到
   当前修订的 `api_name`；关系必须有明确的关系级 allow，端点可见或字段级 allow
   本身都不能授权关系存在。
4. 用 `ExecutionContext` 声明数据模式，适配器能力不足时返回明确的
   `degraded` 或 `unavailable`，不得伪装成空集合。
5. 返回数据时附上 `ResultManifest`；游标只能由同一上下文解码。
6. Schema 破坏性变更只能走 dry-run → 影子执行 → 校验 → 原子切换 → 可撤回流程。

## 迁移指令的执行语义

- `rename_api_name`：改规范 API 身份及兼容投影；属性稳定 ID 不变，旧修订定义保留。
- `rename_display_name`：只改展示名称，不改 API 身份。
- `cast_property`：dry-run 检查每个已有实例值；无法转换的值成为 blocker；执行时
  更新 PostgreSQL 实例值与规范类型，撤回从私有执行日志恢复原值。
- `drop_property`：默认对仍含属性值的实例阻断。调用者显式传入
  `payload.archive_values=true` 后，旧值才会从活动属性键移入
  `__retired_properties[resource_id]`；执行日志保留精确旧记录以支持撤回。
- `replace_source`：要求 `payload.source_field`，在目标修订替换来源映射并记录
  dataset/version/table/evidence；旧修订映射保持不变。
- Source Mapping 可先以 `mapping_status=candidate` 保存带证据的候选，确认后通过
  语义变更更新为 `confirmed`；拒绝的候选标为 `rejected`。一个规范资源可关联多
  个数据源映射，同一数据源也可分别贡献多个规范对象；新修订会继承映射而不改写
  历史修订。
- `move_edits`：必须给出 `from_resource_id` 与 `to_resource_id`，将 What-If
  `assumptions_json` 和 `rule_overrides_json` 中精确匹配的资源引用迁移。没有匹配
  的编辑引用时 dry-run 返回 blocker/error，不能把空操作记为成功。
- `rebuild_projection`：必须有本体当前发布的数据快照，且 PostgreSQL 事实数与快照
  清单一致；从该快照的 `TemporalFact` 账本重建影子 FalkorDB 图。重建不可用时
  阻断切换，不会伪报 `applied`。
- 迁移启动 checkpoint 在执行外部存储/图写入前持久化。崩溃后查询原 `run.id` 并
  调用 reconcile 会重放相同影子命名空间；图写入使用稳定身份保持幂等。reconcile
  还会核对当前修订指针、元数据 digest 和重建图是否存在。

## 错误码

| 错误码 | 含义 | 客户端动作 |
| --- | --- | --- |
| `NOT_FOUND` | 本体或无权对象不可见 | 当作不存在处理 |
| `CONTEXT_MISMATCH` | 游标与元数据、权限或视图不一致 | 丢弃游标，重新分页 |
| `CAPABILITY_UNSUPPORTED` | 适配器不支持请求的一致性/操作 | 换适配器或显式允许降级 |
| `REVISION_CONFLICT` | 页面基于旧修订 | 重新读取当前修订 |
| `API_NAME_MIGRATION_REQUIRED` | API 身份不能普通编辑 | 生成迁移计划 |
| `DUPLICATE_API_NAME` | 本体内 API 名称冲突 | 修改名称或合并目标 |
| `CHANGE_BLOCKED` | 存在实例、关系、规则或证据依赖 | 处理依赖后重试 |
| `MIGRATION_STATE` | 迁移状态不允许当前操作 | 查询迁移状态 |
| `UNSUPPORTED_CAST` | 类型转换不受支持 | 改用支持的目标类型 |
| `PROJECTION_SOURCE_UNAVAILABLE` | 没有已发布 PostgreSQL 快照可重建图 | 先发布权威数据快照 |
| `PROJECTION_SOURCE_INCONSISTENT` | 事实账本与快照清单数量不一致 | 修复/重新发布权威快照 |
| `ADAPTER_UNAVAILABLE` | 图重建需要的 FalkorDB 不可用 | 恢复 adapter 后使用同一 run 重试 |
| `RECONCILE_DIGEST_MISMATCH` | 迁移目标元数据与修订摘要不一致 | 阻止确认并调查迁移状态 |

错误信封使用 `code/message/next_action/context_id`，不能写入被遮蔽字段原值。

## 禁止复制的规则

- 不在 Plan B 内重新实现对象/字段权限、deny 优先、字段 `null` 或 404 隐藏。
- 不直接写 `Entity`、`Relation` 的新业务逻辑；旧接口只能通过兼容适配器。
- 不直接切换 `OntologyProject.current_revision_id` 或数据快照指针。
- 不把未确认的模型候选直接发布为规范资源。
- 不把 scenario 分享或结果清单当成底层数据授权。
- 不在日志、推理轨迹或错误中打印被遮蔽原值。

## Plan B 接入检查清单

- [ ] 查询入口接受 `ExecutionContext` 并返回 `ResultManifest`。
- [ ] 查询前调用统一 `resolve_projection`，过滤对象、关系、聚合和日志。
- [ ] Scenario 固定 `revision_id`、数据版本和权限摘要，不静默升级。
- [ ] Logic Asset 只引用 `resource_id`，不用显示名称作为身份。
- [ ] 所有写入调用语义变更或 Schema Migration 服务。
- [ ] 迁移执行使用返回的 `run.id` 轮询 `/api/v2/schema-migrations/{run_id}`，
      不把 plan id 当作运行状态的唯一身份。
- [ ] 添加 Plan B 契约测试，证明上下文失配和权限变化不会复用旧结果。

底座本地合同测试使用 SQLite；真实 PostgreSQL/FalkorDB、FactoryNet 校验和快照、
各迁移阶段的进程重启和性能分位数仍须在 Docker 环境恢复后验收。
