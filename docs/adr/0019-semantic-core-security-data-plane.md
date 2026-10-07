# ADR-0019：语义核心、权限投影与统一数据平面

状态：实施中（主要服务与合同已实现；真实 PostgreSQL/FalkorDB 验收未完成）
日期：2026-10-07
范围：`factorynet-temporal-workbench`

## 决策

工作台继续以 PostgreSQL 作为事件、事实、修订和权限策略的权威来源，以
FalkorDB 作为可重建的图投影。新增的规范语义资源使用稳定 `resource_id`
和本体内唯一 `api_name`；定义只在 `OntologyRevision` 中按版本保存。旧的
`Entity`、`Relation`、JSON 属性结构保留为兼容投影，新的编辑请求通过语义
变更服务转译，不再建立第二套双写逻辑。

Link Type 的端点、双侧名称、方向和基数作为规范字段保存；关系自身的业务
属性仍需经过结构化校验。迁移预检会创建独立的 `SchemaMigrationRun`，运行
状态与撤回接口按 run id 可恢复查询。

每个终端读取先解析一次 `AuthorizationContext → AuthorizationProjection`。
对象级 deny 优先；未授权对象从列表、关系和聚合中移除；未授权字段保留键
但值为 `null`；按 ID 读取隐藏对象统一返回 404。策略条件只能使用结构化
`equals/in/range` 等比较符，不能提交任意 SQL。流式适配器复用同一策略投影，
按行求值条件，不重新查询策略表；修订级策略只与全局策略一起应用于对应修订。

查询结果带 `ResultManifest`，其中包含元数据修订与摘要、来源版本、权限摘要、
适配器版本、视图/快照上下文和 `ready/degraded/stale/expired/unavailable`
状态。分页游标绑定同一上下文，权限或元数据变化时返回 `CONTEXT_MISMATCH`。

破坏性 Schema 变化必须先 dry-run，随后在影子修订中执行、校验并原子切换。
属性 cast 会转换 PostgreSQL 实例值并在影子修订中验证；drop 可显式将现存值
归档到实例记录，以便撤回；replace source 写入新的修订级来源映射；move edits
更新 What-If 假设与覆盖项引用；rebuild projection 从已发布快照对应的 PostgreSQL
事实账本重建 FalkorDB 图。缺少快照、值无法转换、事实账本不一致或图数据库不可用
时会阻断执行。迁移启动状态先持久化，重启后 reconcile 可重放同一影子命名空间；
撤回恢复实例值、What-If 引用、兼容投影与原修订来源映射，并写入审计记录。
旧修订、旧投影和事件不会被删除。

数据平面能力不再写死为 ready：PostgreSQL 使用实际连接探测；FalkorDB 使用实际
连接、图命名空间和节点/边数量与快照清单核对。当前只声明实现的 live、pinned
和 snapshot 能力，分页游标仍绑定元数据、权限、来源和视图上下文。

## 不在本 ADR 中

- 不引入 Graphiti Core、OPA、Neo4j 运行时或供应商专有后端。
- 不实现队友 Plan B 的 Object Explorer、Scenario 管理和 Action/Function UX。
- 不把权限判断复制到每个页面；页面只能消费统一 API 返回的投影和结果清单。

## 兼容与迁移

Alembic 从 `0018_temporal_stream_facts` 进入 `0019_semantic_core_security_migrations`。
启动时会为现有修订做幂等语义回填；旧 API 仍可读，并通过 `Deprecation: true`
提示迁移到 `/api/v2/ontologies/{id}/semantic-changes`。没有匹配授权的非所有者
默认拒绝，local single-user 的内置管理员仍按所有者/管理员规则完整可见。

## 验证证据与边界

- `backend/tests/v2/test_semantic_core_security_migration.py` 覆盖回填、条件授权与
  字段遮蔽、deny 优先、游标失效、属性转换/归档与撤回、来源映射、What-If 编辑
  迁移、基于 PostgreSQL 事实账本的图重建和 reconcile。
- `backend/scripts/export_plan_a_contract.py` 从 Pydantic 生成 TypeScript 契约；
  `--check` 检查生成文件是否过期。关键 API 响应模型已纳入 OpenAPI 校验。
- 以上 SQLite 测试不能代替 PostgreSQL 和 FalkorDB 实测。本机 Docker 当前不可用，
  FactoryNet 真实快照上的迁移、投影一致性、重启恢复及性能分位数仍未验收。
- Plan B Object Explorer、Scenario/Logic/Action/Export 等上层入口尚未完成统一
  权限接入；本 ADR 不将底座完成等同于跨所有入口验收完成。
