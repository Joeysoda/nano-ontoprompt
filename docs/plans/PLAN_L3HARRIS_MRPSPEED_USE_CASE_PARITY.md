# L3Harris MRPSpeed 同等复杂度用例复现与通用情景决策工作区计划

> 日期：2026-10-18  
> 状态：实施中（进度见第 12 节）  
> 主验收案例：[L3Harris MRPSpeed 官方 Demo](https://www.youtube.com/watch?v=EX1T2CEJzL4)  
> 需求证据：[Palantir 官方工业案例视频审计](../research/official/PALANTIR_OFFICIAL_INDUSTRIAL_USE_CASE_VIDEO_REVIEW_2026_10_18.md)  
> 适用分支：当前整合分支 `codex/merge-impact-chains`

## 1. 目标与非目标

### 1.1 目标

在不重写现有 Scenario Study、Object Query、Action、Rule 和求解器基础的前提下，完整复现一个与 L3Harris Demo 同等复杂度的制造业缺件决策闭环：

1. 从收入风险、客户承诺和计划需求发现生产异常。
2. 从聚合风险逐层钻取到订单、产品、多级 BOM 和根因物料。
3. 正确计算现有库存、预留、在途、供应承诺和时间窗口下的净可用量。
4. 区分“许多部件短缺”和“一个关键件阻塞整机”。
5. 生成库存重分配、供应商催料、替代来源、工单加急、改排程和暂停非关键采购等候选动作。
6. 对候选动作计算收入、准时交付、采购成本、滞留库存、产能和受损订单的变化。
7. 在 Scenario 中预览全部对象与关系修改，经过人工审核后作为单一治理动作合并。
8. 保存运行版本、影响路径、证据、人工覆盖理由、审批、执行回执和回滚记录。
9. 让 Wendy's 的多级库存补货和 Heineken 的订单/运输重分配能复用同一事件、影响、候选、审批和执行框架。

### 1.2 完整复现的业务问题

> 本季度多个高价值产品存在交付风险。到底是哪些订单受影响、哪个 BOM 组件是真正瓶颈、现有或在途库存能否重新分配、应该催料还是替代采购、采取动作后能保护多少收入，又会牺牲哪些订单？

验收 Demo 必须包含至少 3 个成品、2 层以上 BOM、20 个以上组件、共享关键件、多个客户订单、库存预留、在途供应、两个供应商、两条生产资源和有冲突的交期。不能用“一个订单 + 一个物料 + 一个布尔可行性”冒充完整复现。

### 1.3 非目标

- 不复刻 AIP 对话或自治 Agent；解释和候选动作必须由确定性、可审计的 Ontology/Function/Action 契约产生。
- 不实现地理空间地图；用对象关系、分组表和时间视图表达网络。
- 不为 L3Harris、Wendy's 或 Heineken 建专用 API/表；行业语义通过 Ontology 类型和 Study 模板配置。
- 不宣称复现 Palantir 私有算法、私有数据或内部架构。
- 不在本计划内删除所有旧 supplier/dynamic what-if API；只建立迁移、观测和退役门槛。

## 2. 当前仓库现实与差距

### 2.1 已完成，必须保留

| 能力 | 当前实现 | 决策 |
|---|---|---|
| Study 定义与不可变版本 | `StudyDefinition`、`StudyVersion` | preserve |
| Case 与隔离 Scenario | `CaseDefinition.scenario_id`、Scenario revisions | preserve |
| 固定执行上下文 | `RunPlan` 固定 metadata、permission、scenario revision、data view 和 hash | preserve |
| 有界 DAG 运行 | `scenario_studies.py`、异步 Run/Stage、取消与重试 | preserve |
| 多级 BOM/供应/排程 | `supply_chain_reasoning_v1`、`capacity_replan_v1` | extend |
| 模型设计图 | `StudyGraphCanvas` + XYFlow | preserve，移到高级模型配置语境 |
| 基线/候选对比 | `StudySimulationTable` | extend |
| Action 预览、提交与回滚 | `OntologyActionType/Run`、preview、revert | preserve |
| Scenario 合并 | merge-preview、merge | preserve |
| 规则审核 | proposal review、自审禁止 | reuse for governance primitives |
| UI 基础组件 | TanStack Query/Table、ECharts、Cytoscape、XYFlow | adopt existing |

### 2.2 真实缺口

1. `supply_chain_reasoning_v1` 返回 shortage、alternative、schedule 和订单影响，但没有标准的因果/关系证据路径。
2. 库存主要是 item + quantity，缺少 location、lot、available_at、reserved quantity、reservation owner、supply status 和时间有效性。
3. 供应逻辑没有把 on-hand、reserved、in-transit、committed、expired 和 qualified alternative 明确分层，容易重复计算可用量。
4. 当前结果页按节点输出展示，无法从 revenue at risk 钻取到订单、产品、BOM 和单个短缺件。
5. 没有通用的业务事件资源；用户必须先理解 Study/Case，而不是从 Shortage/Delay/DemandChange 进入。
6. `DecisionRecord.payload` 是松散 JSON，缺少 schema version、状态机、revision、digest、提交者/审核者约束和 Action 回执关联。
7. Rule 审批、Scenario merge、Action revert 分散，尚未形成方案级 submit/review/apply/revert 流程。
8. 当前约束结果只给 satisfied/observed，缺少余量、责任对象、失败路径、受影响订单和可修复动作。
9. 旧 supplier/dynamic API facade、双读观测和退役门槛仍未完成。
10. 1m 对象完整导入/快照/运行、24 小时 soak、代表性资源指标和完整 outage 矩阵仍未完成。

## 3. 证据台账

| 来源 | 公开证据 | 对本计划的约束 |
|---|---|---|
| [L3Harris MRPSpeed](https://www.youtube.com/watch?v=EX1T2CEJzL4) | 收入风险、可生产量、BOM 缺件、库存解锁、建议动作 | 主用例和 UI 交互证据；不推断私有算法 |
| [Wendy's QSCC](https://www.youtube.com/watch?v=8wX9YjrR_1k&t=297s) | 门店级例外、预测库存、原料—生产—配送链、批量执行 | 验证通用框架能覆盖库存网络和逐项执行 |
| [Heineken](https://www.youtube.com/watch?v=uo6y_0DG29I) | 未来订单、海运 ETA、容器优先级、目的地交换 | 验证时间有效性、分配唯一性和不可逆状态 |
| [Ontology scenarios overview](https://www.palantir.com/docs/foundry/ontology/overview-ontology-scenario) | Scenario 是隔离编辑沙箱、自动 rebase、Functions 读取情景状态 | 不把 Scenario 当历史快照；运行必须固定自己的 data view/revision |
| [Temporary scenarios](https://www.palantir.com/docs/foundry/ontology/temporary-scenario) | 临时 what-if 不持久化元数据 | 临时探索和可审核持久方案分开 |
| [Persisted scenarios](https://www.palantir.com/docs/foundry/ontology/persisted-scenario) | Scenario 作为对象，支持共享、权限、状态与审核 | Decision Package/Scenario metadata 应可治理 |
| [Merge scenarios](https://www.palantir.com/docs/foundry/ontology/merge-scenario) | 所有 staged edits 通过 Action 作为单一事务应用 | 方案批准后走既有 merge action，不逐项直接写 live |
| [Action submission criteria](https://www.palantir.com/docs/foundry/action-types/submission-criteria) | 提交条件独立于编辑权限，可引用用户、参数和执行上下文 | hard constraint、角色、stale 状态都在服务端重验 |
| [Action test run](https://www.palantir.com/docs/foundry/action-types/test-run) | 预览 proposed changes 和执行细节，不产生真实编辑 | 候选动作预览与提交必须共享校验路径 |
| [Action log](https://www.palantir.com/docs/foundry/action-types/action-log) | 决策及其上下文作为可查询记录 | Decision Package 关联 Action runs、对象和世界状态 |
| [Dynamic scheduling core concepts](https://www.palantir.com/docs/foundry/dynamic-scheduling/scheduling-concepts) | schedule/resource 对象、save/search/suggestion/validation functions 和 active scenario | 排程建议必须读取 Scenario 当前状态，修改通过 Action staged |
| 当前仓库代码与测试 | Study、RunPlan、Scenario、Action、Rule、BOM、solver 和 100k FalkorDB 证据 | 在现有 canonical runtime 上扩展，不建平行执行栈 |

证据分类：官方文档和视频属于公开合同/可见行为；目标领域模型、API 和 UI 是本项目推断；Palantir 的求解器、数据存储和私有后端实现仍未知。

## 4. 参考语义与不变量

### 4.1 权威边界

- **Ontology objects/links**：订单、产品、物料、BOM、库存、预留、供应承诺、PO、工单、资源和业务事件的当前业务状态。
- **StudyVersion**：计算步骤、指标、约束和预算的不可变定义。
- **Scenario**：候选 Action edits 的隔离沙箱；不是历史快照。
- **RunPlan/Run**：一次可复现计算的固定输入与执行记录。
- **Run artifacts**：不可变的影响路径、短缺明细、候选方案和时间序列输出。
- **Decision Package**：引用某个完成 Run 和 Scenario revision 的治理资源；不复制运行结果作为第二权威源。
- **Action/merge run**：唯一的 live 写入和回滚边界。
- **FalkorDB**：对象关系和影响查询投影；PostgreSQL 保存资源、版本、运行和治理权威记录。

### 4.2 制造领域对象

| 对象类型 | 核心属性 | 关键关系 |
|---|---|---|
| CustomerOrder | quantity、due_at、priority、unit_revenue、status | demands Product；belongs Customer |
| Product/Item | item code、kind、revision、status | has BOMLine；uses component |
| BOMLine | quantity_per、effective_from/to、revision | parent Product；component Item |
| InventoryBalance/Lot | on_hand、reserved、available_at、expiry、status | for Item；at Facility |
| Reservation | quantity、status、priority | reserves Inventory；for Order/WorkOrder |
| SupplyCommitment | quantity、promised_at、confidence、status | from Supplier；for Item/PO |
| PurchaseOrder | quantity、due_at、status、expedite state | supplier、item、facility |
| WorkOrder | quantity、start/end、status、fixed | produces Product；requires Operation |
| Resource | capacity calendar、qualification、status | assigned WorkOrder/Operation |
| SupplyIssue | type、severity、detected_at、status、data health | affects objects；rooted at Item/Commitment |

### 4.3 核心不变量

1. 同一库存或供应承诺的数量不能同时分配给两个订单。
2. `available = on_hand - active reservations`；在途量只有 `promised_at <= need_at` 且状态可用时才计入。
3. 多级 BOM 按有效 revision 和时间展开；环、非正数量、过深和过大扇出必须稳定失败。
4. 收入风险只来自仍未满足且会错过承诺日期的订单；不能把订单总额全部当风险。
5. 分配重排必须同时列出获益订单和受损订单，净影响可复算。
6. 未认证供应商、过期材料、停用工厂和不具资格资源是 hard constraints。
7. 候选动作不能越过不可逆状态，例如已消耗库存、已完成工序或已发运 PO。
8. 影响路径每一步包含 source object、relation/action、target object、quantity/time delta 和证据引用。
9. Preview 与 Apply 使用同一个编译与校验函数；Apply 前重新验证权限、revision 和 hard constraints。
10. Decision Package 一旦提交审核，引用的 run digest 和 scenario revision 不可变；输入变化生成新 revision/package。
11. 审核者不能审批自己提交的 package。
12. 合并作为单一事务成功或失败；外部 side effects 使用幂等 outbox/回执，不能伪装成数据库事务。
13. stale、partial、unavailable、invalid 和 empty 是不同状态，UI/API 不得合并表达。

## 5. Design corrections

| Previous assumption | Evidence | Corrected design | Impact on existing code | Action |
|---|---|---|---|---|
| 再做一个 MRPSpeed 专用页面和 worker 最快 | 仓库已有 canonical Study/Case/RunPlan/Run | 新 UI 只编排现有 runtime，领域差异由模板和对象类型表达 | 扩展 ScenarioStudies 和 artifacts，不建新执行栈 | extend |
| Scenario 可以作为运行时历史快照 | 官方说明 Scenario 会 auto-rebase，且不是数据版本工具 | RunPlan 继续固定 data view、scenario revision 和 digest | 保留当前 pinned-on-run 设计 | preserve |
| shortage 列表足以解释影响 | 四个视频都从聚合风险钻取到对象和动作 | 定义类型化 `ImpactPathArtifact/v1` | executor、artifact serializer、结果 UI | extend |
| `DecisionRecord.payload` 足以承担审批 | 当前只有宽松 JSON，无状态/etag/digest 约束 | 新增类型化 `DecisionPackage` 治理资源；旧 DecisionRecord 可做关联记录/投影 | migration、API、权限和状态机 | extend/migrate |
| Action preview 和 Scenario merge 可由 UI 自行串联 | 官方以 submission criteria、scenario merge action 治理 | 服务端提供 package submit/review/apply 编排，并复用原 Action/merge | 新 orchestration service，不绕过现有服务 | extend |
| 库存是 item + quantity | L3/Wendy/Heineken 都依赖预留、时间、地点、在途和不可逆状态 | 引入 InventoryBalance/Lot、Reservation、SupplyCommitment | 新模板、查询 projection、模型输入 adapter | extend |
| 单个 feasible 布尔即可 | 官方 UI 展示根因、余量、受影响对象与动作 | ConstraintResult 增加 margin、subjects、path refs、remediations | spec/result 向后兼容 v1→v2 | migrate |
| 计算 DAG 是业务用户主视图 | 视频主视图围绕异常、对象、方案和动作 | DAG 保留在模型配置；新增 Decision Workspace | 前端路由与组件重排 | preserve/extend |
| 推荐动作可以直接落地 | 官方场景区分预览、审核、发布/Apply | 所有 live 动作先形成 package 并重验 | Action/Scenario UI | extend |
| 现有 generic Decision 与 scenario decision 是一个概念 | generic Decision 描述已发生/记录的决定；package 是待审核执行提案 | 两者分开但通过 `decision_record_id` 关联，批准执行后才写最终 DecisionRecord | 避免双重权威 | refactor |

源代码反向检查还发现：当前已有两套 legacy scenario study 模型与新版 versioned study 模型，计划不能再增加第三套；迁移和调用量观测必须进入阶段门禁。

## 6. 目标架构

### 6.1 新增契约

#### `ImpactPathArtifact/v1`

```json
{
  "schema_version": "impact-path/v1",
  "roots": [{"object_type": "SupplyIssue", "object_id": "SI-42"}],
  "nodes": [{"ref": {"object_type": "Item", "object_id": "M-42"}, "snapshot_digest": "..."}],
  "edges": [{
    "source": "Item:M-42",
    "kind": "required_by_bom",
    "target": "Product:P-7",
    "quantity_delta": 24,
    "time_delta_seconds": 172800,
    "evidence_refs": ["run-input:bom:193"]
  }],
  "terminal_impacts": [{"object_ref": "CustomerOrder:O-9", "metric": "revenue_at_risk", "value": 250000, "unit": "USD"}],
  "completeness": "complete"
}
```

必须保持路径方向和多路径；UI 可以折叠，但 API 不得只返回“第一个父对象”。

#### `DecisionPackage`

字段：`id`、`ontology_id`、`study_version_id`、`case_id`、`run_id`、`scenario_id`、`scenario_revision`、`run_digest`、`title`、`summary`、`rationale`、`status=draft|in_review|approved|rejected|applying|applied|apply_failed|reverted|superseded`、`submitted_by/at`、`reviewed_by/at`、`etag`、`decision_record_id`、`created_at/updated_at`。

附属不可变表/资源：

- `DecisionPackageAction`: 有序 Action invocation、preview digest、expected edits、external side-effect class。
- `DecisionPackageEvidence`: artifact/path/metric/constraint/object references，不复制大 artifact。
- `DecisionPackageEvent`: append-only 状态转换、actor、reason、request id、before/after digest。

### 6.2 执行链

1. `SupplyIssue` 或手工问题选择产生工作区上下文。
2. Object Query 在用户权限投影下读取订单、产品、BOM、库存、预留、供应和生产资源。
3. RunPlan 固定输入并执行 `material_shortage_decision_v1` Study。
4. executor 输出 metrics、constraint results、impact path、time-phased inventory、candidate actions 和 affected orders。
5. 用户比较候选，在 Scenario 中 preview Action edits。
6. 创建 Decision Package；服务端校验 run 完成、digest、权限、scenario revision 和 artifacts 完整性。
7. submit → reviewer approve/reject；禁止 self-approval。
8. approve 不自动绕过校验；apply 时重新运行 merge-preview/submission criteria。
9. Scenario edits 原子合并；外部采购/通知进入幂等 outbox，逐项记录回执。
10. 成功后创建/关联最终 DecisionRecord；失败保留可重试或补偿状态。

### 6.3 API

- `GET /ontologies/{oid}/supply-issues`：过滤状态、严重度、订单、产品、物料。
- `GET /ontologies/{oid}/supply-issues/{id}/workspace`：只返回对象引用、KPI 和最新可用 Run，不复制数据。
- 继续使用现有 Study compile/run/compare API。
- `GET /scenario-study-runs/{run_id}/artifacts/{type}`：分页读取 impact paths、candidate actions、time series。
- `POST /scenario-study-runs/{run_id}/decision-packages`：从固定 Run 创建草稿。
- `PATCH /decision-packages/{id}`：仅 draft 可改 rationale/title，ETag 必填。
- `POST /decision-packages/{id}:submit`
- `POST /decision-packages/{id}:review`：approve/reject + reason。
- `POST /decision-packages/{id}:apply`：幂等 request id；执行 merge + outbox。
- `POST /decision-packages/{id}:revert`：只对可逆 action，返回不可逆 side effects 清单。

稳定错误码至少包括：`run_not_complete`、`artifact_incomplete`、`stale_scenario_revision`、`preview_digest_mismatch`、`hard_constraint_failed`、`self_review_forbidden`、`invalid_transition`、`permission_changed`、`merge_conflict`、`external_effect_partial`、`not_revertible`。

### 6.4 UI：Decision Workspace

路由：`/ontologies/{oid}/scenario-decisions?issue={issueId}&case={caseId}&run={runId}`。

1. **顶部**：事件、负责人、状态、数据新鲜度、Study/版本、运行 digest。
2. **KPI**：收入风险、受影响订单、可生产数量、根因短缺件、滞留库存价值。
3. **风险趋势**：ECharts 显示计划需求、可交付量和 time-phased inventory。
4. **钻取表**：产品 → 订单 → BOM → 短缺物料；TanStack Table 支持排序、过滤、展开、虚拟化/分页。
5. **影响图**：Cytoscape 显示所选根因到订单的证据路径；只加载局部子图。
6. **候选方案**：基线、催料、库存重分配、替代采购、改排程；显示获益、受损、成本、交期和 constraint margins。
7. **变更预览**：复用 Action preview，逐项展示创建/修改/链接和不可逆 side effects。
8. **理由与审批**：rationale、提交、审核、拒绝、Apply、回执、Revert。
9. **高级区**：模型 DAG、Function versions、JSON、执行 trace；默认折叠。

## 7. 开源组件决策

| 组件 | 决策 | 用途 | 项目仍负责的语义 |
|---|---|---|---|
| TanStack Query 5 | adopt existing | API cache、失效、轮询 | revision key、权限变化、stale 状态 |
| TanStack Table 9 | adopt existing | 层级钻取、排序、筛选、列管理 | 对象引用、数量单位、逐行 Action 状态 |
| ECharts 6 | adopt existing | 需求/可交付柱图、库存曲线、constraint headroom、成本桥 | 指标计算、单位和证据 |
| Cytoscape 3 | adopt existing | 局部业务影响图 | 路径方向、授权投影、分页/预算 |
| XYFlow 12 | preserve narrow role | Study 计算 DAG | 不用于业务对象影响图 |
| SciPy MILP | preserve/spike against known optimum | 有界分配和排程 | 业务约束、状态、provenance、不可行解释 |
| OR-Tools | defer pending measured gate | 更复杂 CP-SAT 排程候选 | 只有 SciPy/现算法无法满足已知规模和约束时引入；先做许可证/镜像评审 |
| 新 Gantt 库 | defer | L3 主案例不要求完整拖放 Gantt | 先用 ECharts 时间条；Lear 复现进入后续阶段再评估 |
| 工作流引擎 | reject for current phase | package 状态机规模有限 | PostgreSQL 事务、Celery 和 outbox 足够；重复恢复缺陷达到架构复审门槛才重评 |

不新增图数据库、状态管理库或表单引擎。现有组件已经覆盖 UI 商品能力，项目代码专注 Ontology、权限、版本、影响和治理语义。

## 8. 分阶段实施

### Phase 0：基线审计和兼容边界

交付：

- 固定当前 commit、Docker 镜像、迁移版本和现有 Study/Scenario API 清单。
- 记录新版/legacy scenario 路由调用量和数据计数；确定 canonical versioned Study 路径。
- 为现有 supply-chain template 跑真实 PostgreSQL/FalkorDB/Redis/Celery 基线。
- 保存 lint/build/test、100k snapshot 和资源指标基线。

门禁：现有主制造 Study 可在真实 Docker 环境完成 baseline/candidate run；失败按 introduced、blocking-existing、unrelated、environment、unclassified 分类。不得为了过门禁修改无关历史债。

### Phase 1：类型化影响和库存语义

交付：

- 扩展制造 Ontology 模板：InventoryBalance/Lot、Reservation、SupplyCommitment、SupplyIssue。
- `material_shortage_decision_v1` adapter；复用现有 BOM/排程逻辑，增加时间分层净料、共享库存和收入风险。
- `ImpactPathArtifact/v1`、time-phased inventory、candidate action schemas。
- ConstraintResult v2：margin、subjects、path refs、remediations；保留 v1 读取兼容。
- artifact 分页、大小限制、完整/部分状态和 digest。

门禁：已知答案 fixture 证明无双重分配、单一关键件根因、共享物料取舍和收入风险可复算；旧模板仍能运行。

### Phase 2：Decision Package 与治理状态机

交付：

- migration 新增 package/action/evidence/event 资源和索引。
- submit/review/apply/revert 服务；ETag、幂等、自审禁止、权限重验和 stable errors。
- 复用 Scenario merge-preview/merge 与 Action preview/run/revert。
- transactional outbox 记录外部副作用；首期 connector 使用本地可控 fake ERP/采购适配器，不假装真实外部系统。
- 已批准并成功应用后关联最终 DecisionRecord。

门禁：状态迁移、并发审核、stale revision、partial external effect、重试、revert 和 migration rollback 在真实 PostgreSQL 通过；没有绕过 Action/Scenario 的 live 写路径。

### Phase 3：Decision Workspace

交付：

- 事件列表与 L3Harris 风格主工作区。
- KPI、需求/可交付图、层级钻取表、局部影响图、候选卡和约束余量。
- Action changes preview、rationale、审核、Apply、逐项回执和 Revert。
- URL 可恢复 issue/case/run/tab/selection；模型配置保持独立。
- 权限、partial/stale/unavailable、空结果、慢网和错误状态。

门禁：非 CS 用户无需编辑 JSON 或理解节点类型，即可从收入风险定位根因、比较候选、提交审核并查看执行结果；键盘关键路径和 1280px 窄屏通过。

### Phase 4：L3Harris 同等复杂度完整复现

fixture 至少包含：

- 3 个产品、2–3 层 BOM、20+ component items。
- 12+ 客户订单，优先级、不同 unit revenue 和 due date。
- 一个共享关键件造成多个订单阻塞；另一个表面短缺由在途量及时覆盖。
- 3 个库存位置、active reservations、在途供应、一个已失效承诺。
- 主供应商延迟、一个合格高价替代、一个未认证低价替代。
- 2 条生产资源、固定任务和能力冲突。
- baseline、expedite、reallocate、alternate+replan 至少四个候选。

用户演示链：收入风险首页 → 选择红色月份/产品 → 订单 → BOM 根因 → 库存/在途证据 → 四方案比较 → 查看获益与受损订单 → preview edits → rationale → 提交 → 另一用户批准 → Apply → 回执 → 审计/可逆回滚。

门禁：所有数值可由 fixture 独立计算脚本复算；浏览器录屏、截图、API trace、run/package/action digests 和数据库审计共同构成验收证据。

### Phase 5：跨案例复用验证

不新增专用 runtime：

- Wendy's mini-template：门店/DC 覆盖天数、部分削减订单、生产与原料缺口、批量执行。
- Heineken mini-template：未来订单、在途 ETA、唯一分配、container swap 和不可逆状态。
- Lear mini-template：紧急插单、资源不重叠、deadline validation、Apply/Revert。

门禁：三个模板共享 ImpactPath、CandidateAction、DecisionPackage 和审批 API；领域新增只发生在 Ontology 类型/模板/adapter，通用服务无 `if customer == ...` 分支。

### Phase 6：生产门禁和旧路径迁移

交付：

- 旧 supplier/dynamic API facade、双读/计数/摘要 digest 对齐和调用观测。
- 100k 与 1m 对象真实图导入、快照、影响查询和 Scenario Run。
- 100 Case 并发/取消、worker restart、Redis/PostgreSQL/FalkorDB outage、权限变化和 rebase/merge 冲突。
- 24 小时 soak、备份恢复、migration down/up 和 outbox 恢复。
- 两个发布周期无旧写入后才提出旧接口/表删除；保留数据迁移、验证和回滚方案。

门禁：真实基础设施报告包含 p50/p95/p99、吞吐、CPU、RAM、图 fan-out、队列等待、artifact 大小、错误率和恢复时间；未完成项目必须明确为未验收。

## 9. 测试与验证矩阵

### 9.1 单元与合同

- BOM effective revision、cycle/depth/fan-out、quantity/unit validation。
- 库存净可用量、预留、在途时间、过期和重复 allocation。
- revenue-at-risk、buildable quantity、stranded inventory 的已知答案。
- ImpactPath 稳定排序、路径保留、hash、partial/completeness。
- CandidateAction schema、hard/soft constraint、受益/受损订单守恒。
- DecisionPackage digest、状态机、ETag、idempotency、自审禁止。
- preview/apply 编译输出一致；query text 与 parameters 分离，拒绝注入。
- stable error code、权限投影、敏感字段不进入日志/artifact。

### 9.2 组件测试

- Scenario executor + real compiler + in-memory bounded snapshot：验证 artifacts 和 constraints。
- package service + real SQLAlchemy/PostgreSQL：验证事务、并发和 append-only events。
- API router + real service/authorization dependency：验证状态和错误响应。
- 前端 controlled API：钻取、候选切换、stale、partial、permission denied 和 apply receipts。

Mocks 只用于 UI 网络状态和外部 ERP connector；它们不能证明跨存储一致性、权限或生产性能。

### 9.3 真实集成

- PostgreSQL：migration、unique/check/FK、并发审核、outbox、backup/restore。
- FalkorDB：对象/关系读取、局部路径、>10k result pagination、空图和断开图。
- Redis/Celery：重复 dispatch、取消、worker kill/restart、queue backlog。
- API 权限：viewer/editor/reviewer/admin、提交后撤权、field projection。
- Scenario：tracking rebase、固定 RunPlan、merge conflict、原子合并。
- Action：preview、apply、side effect receipt、retry、revert/non-revertible。

### 9.4 浏览器用户模拟

Playwright 覆盖完整 Demo 以及：刷新、前进后退、分享 URL、快速切换订单、重复 Run/Apply、长表分页/筛选/排序、局部图扩展、慢网、500、session expiry、权限拒绝、stale ETag、queue unavailable、partial artifact、键盘操作、1280px 和移动窄屏降级。断言业务数字、路径和状态，不只断言页面出现。

### 9.5 真实公开数据与合成数据

完整 BOM、订单、库存、供应承诺和收入通常属于企业机密，本计划不把生成数据冒充真实数据。

- **真实数据 A**：[UCI Steel Industry Energy Consumption](https://doi.org/10.24432/C52G8C)，CC BY 4.0，35,040 行。继续验证真实生产负载、时间、班次和资源指标；保存检索日期、原文件、SHA-256、行列/缺失/时间分布。
- **真实数据 B**：[Open Supply Hub](https://info.opensupplyhub.org/faqs)，CC BY-SA 4.0，免费下载受当期额度限制。固定不超过 5,000 个 production locations 的导出，用于 Supplier/Facility 稳定身份、重复匹配、sector/product/processing type 和数据新鲜度；不使用地理 UI。记录导出日期、筛选、license、checksum 和实际计数。
- **业务正确性 fixture**：固定 seed 的 frePPLe-based synthetic manufacturing chain，明确标记 synthetic，用于可控 BOM、订单、库存和已知答案。
- **规模 fixture**：10k/100k/1m objects，控制 BOM 深度、扇出、共享组件和影响范围；只用于压力，不作为真实业务证据。

### 9.6 性能、稳定性和观测

先测基线再定最终 SLO，初始工程门槛：

- 100k 对象、局部影响 ≤10k nodes 时，workspace API p95 < 2s，首屏 p95 < 3s。
- 50 节点 Study compile p95 < 500ms；超出时先 profile。
- 100 并发 cases 无丢失、重复 Action 或不同 digest；记录 queue wait/run p50/p95/p99。
- 1m 图运行必须有内存/CPU/fan-out/artifact 报告，不以“能生成 JSONL”代替。
- 24 小时 soak 无无界内存增长、orphan artifact、卡死 package 或重复 outbox effect。
- structured logs 只含 IDs/digests/stable errors；trace 有界；metrics 包含 stage、queue、artifact、merge、outbox 和 recovery。

### 9.7 静态分析和构建

按当前仓库脚本独立执行：

```powershell
cd frontend
npm run lint:src
npm run lint:scripts
npm run build
npm run test:e2e:preflight
npm run test:e2e -- scenario-studies.spec.ts decision-workspace.spec.ts
```

用 ESLint config inspector 或等价方式检查代表性 TSX、Playwright TS、JS/MJS 的解析配置。changed files 的 error/warning 均为 0；不得用全局 disable、排除目录或 `|| true` 变绿。后端运行 scoped pytest、完整相关 v2 回归和真实服务标记测试；分开报告第三方 warning。

### 9.8 证据触发的架构复审

在相同 commit、fixture、依赖和认证模式下，若三个可比运行出现两个以上不同失败且共同指向同一边界，或同类缺陷修复后再次出现，暂停依赖该边界的补丁并审查：

- Study definition/execution 是否重复语义；
- metadata/data/scenario revisions 是否混淆；
- Object Query、Run、Action、merge 的权限是否一致；
- snapshot/cache/artifact invalidation 是否明确；
- generic Decision 与 Decision Package 是否形成双重权威。

记录复现、根因、责任边界、局部修复与共享重构对比、兼容/迁移/回滚和恢复门禁。单个环境失败或新增覆盖暴露旧缺陷不自动触发重写。

## 10. 风险、未决项与延期范围

| 风险 | 控制 | 解除/升级条件 |
|---|---|---|
| 舞台 Demo 隐藏真实复杂度 | 只复现可见流程；自定义明确契约和已知答案 | 新官方合同出现时更新 evidence ledger |
| 多层影响放大查询 | scope、depth/fan-out、pagination、artifact budget | 真实 profile 后分级提高 |
| 数量/单位/时间错误 | canonical unit、UTC、有效期和结构/领域双重校验 | 不允许静默转换 |
| 求解器给“最优”但业务约束缺失 | 明示目标/约束/solver status，人工审核 | known-answer 与真实规模通过后才能扩大自动化 |
| 外部写回部分失败 | outbox、idempotency、receipts、compensation | connector 逐个生产验收 |
| 旧路径长期共存 | facade、usage counters、双读 digest | 两发布周期无旧写后才移除 |
| Decision 模型重复 | package 是提案，DecisionRecord 是已完成决定 | migration 审计证明一对一/可追踪 |
| 真实全链数据不可得 | 真实局部数据 + 明示 synthetic golden chain | 获得合法真实事务数据后追加验收，不改写历史结论 |

延期：完整拖放 Gantt、自动 Pareto 前沿、商业 ERP connector、地理空间、多组织供应链数据交换、LLM/agent 自动决策。任何延期能力在实测缺口或新需求达到门槛后另开计划。

## 11. 决策摘要

- **保留**：versioned Study、Case、RunPlan、Scenario、Object Query、Action、Rule、现有图/表/图表组件和有界执行。
- **实施前必须修正**：库存/预留/在途语义、类型化影响路径、约束解释、Decision Package 状态机和方案级审批编排。
- **完整复现主案例**：L3Harris MRPSpeed 缺件闭环；复杂度包含收入风险、多产品、多级 BOM、共享物料、库存重分配、替代/催料、排程和订单交付。
- **组件决策**：继续使用 TanStack、ECharts、Cytoscape、XYFlow 和现有 SciPy 边界；不立即增加 Gantt 库、OR-Tools 或工作流引擎。
- **第一实施阶段**：Phase 0 基线与兼容审计，然后 Phase 1 类型化影响和库存语义。
- **进入后续阶段所需证据**：真实 Docker 回归、已知答案守恒、preview/apply 一致、权限/状态机测试、浏览器完整链、100k/1m 容量报告和 24 小时 soak；任何未完成项不得标为生产就绪。


## 12. 实施进度（2026-10-18）

| 阶段 | 状态 | 证据 |
|---|---|---|
| Phase 1 类型化影响与库存语义 | 核心完成 | `material_shortage.py`；`test_material_shortage_decision.py` |
| Phase 2 Decision Package 状态机 | 完成：服务端编译预览、`:apply` 走 Scenario revision + 既有 merge、outbox + 本地 fake ERP 回执、`:revert`、真实 PG 并发审核 | `decision_packages.py`、`decisions.py`；全新 PG 库 `upgrade head`→`downgrade -1`→`upgrade` 通过，ORM 与迁移后 schema 0 漂移 |
| Phase 3 Decision Workspace | KPI、钻取、证据、候选对比、服务端变更预览、提交/审核/执行/回执/重发/回滚 | `MaterialDecisionWorkspace.tsx`；lint/tsc/build 通过；**未做浏览器 E2E** |
| Phase 4 L3 同等复杂度 | fixture + 手算已知答案 + Study→Package 全链 API 测试完成 | `l3harris_fixture.py`；`test_l3harris_known_answer.py`；`test_material_decision_package_flow.py`（SQLite 与真实 PostgreSQL 均通过） |
| Phase 5/6 | 未开始 | — |

本轮实测发现并修复的真实问题：

1. **稀缺物料滞留在无法交付的订单上**：旧逻辑把最后的 CHIP-X 部分分给缺口订单（仍无法交付），导致恰好够用的订单拿不到。改为订单级全有或全无，缺口订单的暂定分配回滚。
2. **Study 产物摘要格式与 Package 校验不一致**：产物为 `sha256:<hex>`（71 字符），Package API 只收 64 位 hex，真实 Run 永远无法建包。改为接口层归一化。
3. 基线不再默认使用未下单的替代采购来源；替代采购作为候选动作单独计成本和效果。

已知答案（手算，见测试注释）：基线风险收入 550k；加急 PO-LATE 剩 140k（成本 3,200）；合格替代剩 480k（520）；按收入密度重分配剩 420k（受益 1010/1011，受损 1003/1006）；未认证低价来源 140k 但硬约束阻止；加急+合格替代+跨线改排为 0（3,720）。


### 12.1 第二轮（Apply 闭环）

- 新模板 `material_shortage_decision_objects`：7 类本体对象经 Object Query 从固定数据视图读取，替代 JSON 参数。
- 候选动作编译为**恰好是模型输入**的类型化编辑（加急改 `available_at`、替代采购新建 `SupplyCommitment`、改排改 `resource`、重分配写 `allocation_rank` 并释放预留），每条带 `expected_old_value`。
- 真实 FalkorDB 验收（`tests/v2/test_material_decision_apply_real_falkor.py`）：Apply 后在新的 live 数据视图重跑，风险收入 = 候选预测值（组合方案 0，重分配 420k）；Revert 后重跑回到 550k；审核后 live 漂移 → `merge_conflict`，无写入、无外部发送；外部回执部分失败 → `external_effect_partial`，重发后完成。
- 修复：`/transition` 不再接受 `begin_apply/apply_succeeded/revert`，执行只能走真正合并的 `:apply`。
- 并发审核测试经变异验证：去掉行锁后两人同时批准都成功（`['ok','ok']`），加锁后恰好一个成功。


### 12.2 浏览器完整链

- `frontend/src/test/e2e/decision-workspace.spec.ts` 在真实验证栈（PostgreSQL + Redis/Celery + FalkorDB + Vite）通过：收入风险 → 订单 → BOM 根因 → 候选对比（含受损订单、硬约束）→ 服务端变更预览 → 提交 → 自审被拒 → 第二人批准 → 刷新 → 执行与回执 → 刷新 → 回滚与不可逆提示。截图保存在 `test-results/`。
- 第二审核人由 `scripts/review_package_as_second_user.py` 模拟：本地单用户模式只有一个身份，这是模拟手段，不是生产审核路径。
- 本轮修复：决策包改为服务端状态（刷新/他人审核后可恢复）；带 `run` 的链接直接打开情景模拟页；对象 ID 全局唯一导致第二次播种冲突 → 播种按本体加命名空间；1280px 下候选表被截断 → 变更预览移至下方；读数节点折叠到“高级”。
- 验证栈已从 0040 升级到 0041 并重启 backend/celery/frontend（Windows 挂载下 Vite 不感知文件变化，改前端后需重启前端容器）。

未完成：Phase 5 跨案例模板；Phase 6 规模/soak/outage；键盘路径与移动窄屏未专门测试。

## 13. Phase 5 修订：用例必须由新用户在平台上自己搭建

依据：这些案例是客户团队（常与 Palantir 驻场工程师一起）在 Foundry/AIP 标准组件上搭出的应用（对象类型、Functions、Actions、Scenarios、Dynamic Scheduling），不是单独的定制软件。因此复刻标准是“空白本体 + 只用界面/API 即可搭出”，内置 L3 模板只算示例。用户已确认：先做第 0 步 + Heineken；用户函数隔离用方案 A（独立容器 + 子进程限制）；Lear 排程按“建议函数 / 校验函数”可插拔结构做，算法是用户函数的内容。

### 13.1 第 0 步交付

| # | 交付 | 复用 | 门禁 |
|---|---|---|---|
| 0.1 | `function-runner` 容器：只在无外网的内部网络上，接收源码+输入，子进程运行（不可联网、CPU/内存/时间/文件写入上限、非 root、空环境变量）；后端客户端不可用时返回 503，绝不回退到进程内执行 | — | 隔离实测：联网、超内存、超时、读环境变量、写文件均失败且被报告 |
| 0.2 | 用户函数：`executor_type=user_python`，源码与摘要存在不可变版本里；新增创建接口，发布新版本才能改源码；输出仍走既有 JSON Schema 合同校验 | Logic Asset 版本/合同 | 改源码不升版本被拒；同版本两次运行输出一致 |
| 0.3 | 通用决策输出 `decision/v1`：主指标、对象（订单/仓库/门店…）、候选（指标前后值、获益/受损、成本、硬约束、`{action_type_id, parameters}` 动作） | ImpactPath | Schema 校验；守恒检查（前值−后值 = 获益−受损） |
| 0.4 | 决策包按候选里的**用户配置情景 Action** 编译编辑（`compile_actions`），外部副作用由 Action 的 `side_effects` 声明 | 情景 Action、决策包、merge、outbox | 不再需要按案例写的编译代码 |
| 0.5 | 通用工作区：标题、指标、对象和动作名全部来自 `decision/v1` | 现有工作区 | 缺件专用判断删除 |

### 13.2 Heineken 验收（第 0 步的证明）

空白本体，只调 HTTP API：建映射导入集装箱/仓库/订舱数据 → 提交用户函数源码（`examples/heineken/`，后端不 import）→ 配置“改目的地”“调整提箱优先级”情景 Action → 发布 Study → 运行 → 决策包 → 第二人批准 → 执行 → 回滚。手算已知答案覆盖：唯一分配、不可逆状态不可换、换后原目的地缺口显示、截止时间。
