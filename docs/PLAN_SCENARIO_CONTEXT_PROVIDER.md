# ScenarioContextProvider 详细设计计划

状态：设计稿，2026-09-21；本文件不代表功能已实现。范围限定于现有 Supplier Study 页面及其可复用的上下文契约，不重做整个 What-if Workbench。参考 [Vertex Supplier 总计划](PLAN_VERTEX_SUPPLIER_SCENARIO_WORKBENCH.md)的功能目标，但本文件只以当前 Vertex、Ontology Scenarios、公开 API 和仓库代码为设计依据；原计划中不同来源的假设不能自动继承。

## 1. 目标、边界与前置决策

目标是让 Graph、Selection Card、Action、Summary、Models、KPI、Impacts/Object Table、Compare、Run details 读取**同一个经过授权、版本固定的业务上下文**。用户快速切换 Baseline/B/C、提交 Action、运行、刷新或浏览器回退时，不得出现“B 标题 + C 图/指标/Action target”的混搭。上下文切换是一组视图的相干提交，不声称 PostgreSQL、FalkorDB、Redis 间存在分布式原子事务。

不做：新查询 DSL、新对象存储、复制 Palantir 私有后端、把真实 Run 换成前端动画、导入官方截图当产品资产、为当前供应商场景引入通用 Scenario Builder。**不做本体时间轴、历史回放、关系随时间演变、多时间窗口对比或新的时序查询**；这部分归其他同学。保留 Object Set / Object Query / QueryDataView / Logic Asset、既有异步 Run 和 frePPLe fixture。当前页已有的 `scenario_time` 仅作为本次 Supplier Study 推理配置和兼容性标识，修改后旧 Run 不可冒充新配置的结果；Provider 不增加任何时间可视化能力。

前置需求澄清与领域建模结论：用户要求聚焦 `ScenarioContextProvider`，排除旧版 Workshop 依据和另一位同学负责的时间轴；当前只有 Supplier Study 消费这一上下文，因此本期只接入该页面，抽出可复用的纯类型，不预做跨产品全局状态。未提交 Action 是表单草稿，不属于服务器 Scenario/Run；本期按 Case ID 保存在当前页面内存里，切换时保留且显示“未提交”，刷新后消失，绝不提交到其他 Case。这是明确的本项目交互约定，验收按此测试，不再作为待决定项。

## 2. 当前仓库事实与缺口

- `frontend/src/pages/what-if/SupplierStudyPage.tsx` 在单组件内通过 URL、React `useState`、三类 React Query 和 mutation callback 管理 Case、drawer、图、指标与草稿；`latestSuccess` 由前端从 runs 推出，仅校验 `status`、`compatibility_hash`、`scenario_revision`，未校验 `case_definition_revision`。Graph 仅按 `case_id` 门控节点本体，顶部数量仍直接读取 `graph.data`；Selection/指标读 run manifest，而图端点自行挑选 run。回退时 drawer 本地 state 不一定与 URL 同步。
- `backend/app/routers/v2/supplier_studies.py::_view` 返回 `scenario_id`、`scenario_revision`、`revision_view_id` 与最近 10 个 runs，**尚未返回**总计划第 338 行所称的规范化 `scenario_context`。`case_graph` 自行选最近 completed run，缺少 `case_definition_revision` 条件；`compare` 已使用更严格的定义版本筛选。必须解决读路径分叉。
- `backend/app/schemas/v2/object_query.py::ExecutionContext` 已有 `ontology_id`、`revision_policy`、`revision_id`、`consistency`、`metadata_digest`、`data_view_id`、`projection_token`、`scenario_id`、`scenario_revision`；现有 Object Query 能校验 Scenario/View 授权和 snapshot。不要设计第二套同义查询参数。
- Run 已有 `case_id`、`case_definition_revision`、`revision`、`compatibility_hash`、`result_view_id`、input/output digest、stages、warnings。Study 的 `etag` 与 Case 的 `etag` 可供写入冲突检查。现有 Playwright Demo 流程覆盖基本运行和图，但缺少快速切换、旧响应落地、回退恢复、单个视图身份一致性断言。
- 工作树当前有大量用户未提交修改和未跟踪文件；实施必须保留，不得 reset/checkout/clean/stash。本规划仅新增本文件。

## 3. 证据台账：现行 Vertex/Ontology 文档、公开 API 和截图

按用户要求，本计划不使用旧版 Workshop 文档，也不使用时间轴/关系演变资料。Vertex 文档说明可见的 Scenario 交互，Ontology Scenarios Beta 文档说明公开的沙盒语义，Object Set API 示例仅说明请求边界。截图结论只描述可见 UI，绝不反推 Palantir 私有服务如何存储或事务化。

| 证据、版本语境 | 逐项读取后可支持的契约或可见事实 | 对本设计的影响 | 证据级别 |
| --- | --- | --- | --- |
| [Vertex Scenarios Getting started](https://www.palantir.com/docs/foundry/vertex/scenarios-getting-started)；[Add Action 图](https://www.palantir.com/docs/resources/foundry/vertex/simulate-system-12.jpg) | 文本规定 Add scenario → Add Action → 配参数 → Submit → Run 的操作顺序；截图可见左侧选中对象卡、中心系统图、右侧 Case Study/Actions/Run。 | Action target 来自当前 Case；选中对象卡与图须同 Case；未 Submit 的草稿不能进入 Run。图中旧式 Model 选择流程已被官方标记 sunset，本项目只参考可见布局，不继承其技术路径。 | 现行 Vertex 文档 + 实际查看截图 |
| 同一 [Vertex Getting started](https://www.palantir.com/docs/foundry/vertex/scenarios-getting-started)；[完成 Run 图](https://www.palantir.com/docs/resources/foundry/vertex/simulate-system-9.jpg)、[override 图](https://www.palantir.com/docs/resources/foundry/vertex/simulate-system-10.jpg) | 文本规定完成 Run 有绿色 check/耗时，override 输入高亮，新输出与 baseline 比较；截图可见右侧 Actions 与 Models 表格列、左侧对象属性和节点 readout。 | 同一 Run 身份供状态、参数表、图节点 readout/Selection 消费；Baseline/B/C Compare 列分别 pin 各自 Run。截图不证明服务如何实现共享上下文。 | 现行 Vertex 文档 + 实际查看两图 |
| [Vertex Scenario options](https://www.palantir.com/docs/foundry/vertex/scenarios-options)；[Scope/参数图](https://www.palantir.com/docs/resources/foundry/vertex/simulate-system-7.jpg) | 文本说明对象图 scope、自动 baseline 和输入/输出参数；截图可见右侧 Scope=Objects on Graph 与参数添加区。 | 只沿用当前 Supplier Study 已有 scope/配置；这些设置是 Run 的输入身份，不在 Provider 内扩展为时序/关系演变功能。 | 现行 Vertex 文档 + 实际查看截图 |
| [Ontology Scenarios Overview（Beta）](https://www.palantir.com/docs/foundry/ontology/overview-ontology-scenario) | 现行 Beta 场景是隔离的 Action edit overlay，可同时创建多个场景做比较；文档说明 30 天 TTL 和约 10 分钟自动 rebase，不是历史快照工具。 | 本项目 pinned QueryDataView 是自己的确定性运行结果存档，并非复制 Beta 场景生命周期；`activeCase` 和三个比较 Run 分开，是结合当前仓库数据模型作出的项目设计。 | 公开文档；与本项目实现有明确差异 |
| [Load Object Set API](https://www.palantir.com/docs/foundry/api/ontologies-v2-resources/ontology-object-sets/load-object-set)、[Aggregate Object Set API](https://www.palantir.com/docs/foundry/api/ontologies-v2-resources/ontology-object-sets/aggregate-object-set) | API 请求示例把 `scenarioRid` 放在独立 query 参数，与 Object Set/aggregation body 分开；Load 的 `snapshot` 是另一种读取选项，分页以 `nextPageToken` 为准；Aggregate 提供精度选项/响应信息。 | 后端调用/前端 Query key 分开表示 Query Definition、Execution Context、Read Options；精确 KPI 不能把近似或不完整聚合标成完整；不能从 API 示例臆测私有 revision 协议。 | 公开 API 文本/代码示例，**非**后端源码截图 |
| [palantir/osdk-ts React contributing](https://github.com/palantir/osdk-ts/blob/main/packages/react/CONTRIBUTING.md)、[React Getting Started](https://github.com/palantir/osdk-ts/blob/main/docs/react/getting-started.md)、[Querying Data](https://github.com/palantir/osdk-ts/blob/main/docs/react/querying-data.md) | 公开源文档/示例显示 React provider + client observable/cache，list/aggregate 等按身份缓存，刷新时允许旧数据存在。 | 参考“缓存与视图相干性分离”原则；不得直接引入 OSDK，因为本项目后端非 Foundry 服务，也未证实它能管理本项目的 Run/ChangeSet。具体 Provider 结构仍是项目实现推断。 | 公开源码仓库文档；未取得私有实现 |

截图审读限制：Vertex 图能证明 Actions/Run、参数表与图/对象卡的可见布局，不证明它们背后的服务事务。未发现可信的“私有后端代码截图”；上述 API 示例只可反推**公开请求边界**，不能反推 Palantir 数据库设计。

## 4. 领域契约与不可破坏的不变量

### 4.1 身份层次

```text
Study (study_id, ontology_id, base_view_id, study_etag, compatibility_hash)
  └─ Case (case_id, case_key, definition_revision, case_etag)
       └─ ScenarioResource (scenario_id, scenario_revision, revision_view_id, changeset_id)
            └─ selected result = latest compatible completed Run
                 (run_id, case_definition_revision, revision, result_view_id,
                  input_digest, output_digest, compatibility_hash)

URL selection = study/case/object/drawer (navigation intent, not business authority)
Compare selection = pinned Baseline/B/C run identities (not activeCase)
Draft = local per-case form state (not submitted revision or result)
```

定义 `ResolvedScenarioContext`，由后端 Study 响应给出逐 Case 的规范化 `context_ref`，前端 Provider 只选择并验证，不在各 widget 重算：`ontology_id, study_id, case_id, case_key, study_etag, case_etag, case_definition_revision, compatibility_hash, scenario_id, scenario_revision, changeset_id, revision_view_id, selected_run_id?, selected_result_view_id?, selected_run_status?, input_digest?, output_digest?, context_token`。`context_token` 为稳定、可比较的身份签名/编码，须包含所有影响读取语义的字段；不包含 UI drawer 或 draft。不可把其当授权凭证；服务器每次仍校验用户、Study、Case、Run、View 的关联与权限。可复用 `stable_hash` 实现，但避免把 token 当缓存一致性的唯一服务器校验。

`revision_view_id` 表示 Action 后的 Scenario 输入快照；`selected_result_view_id` 表示成功 Run 的模型结果快照，二者绝不能混名。没成功 Run 时 Selection/Input 可以读 revision view，Output/KPI 应显示“尚无当前结果”，不能把历史结果当作当前；旧兼容 Run 可以单独展示为“历史”，不占据 active result。`latest_attempt` 供进度和失败诊断，`selected_completed_run` 供业务结果，两者可同时存在。

从该结构生成既有 Object Query `ExecutionContext`：`revision_policy=pinned, consistency=snapshot, data_view_id=(选定读层的 view), scenario_id, scenario_revision, ontology_id`；Query Definition、读取选项（排序/分页/准确性）和 context 分层传递。无 Snapshot/权限时返回显式错误，不静默回落到 live。精确 KPI 要求完整性标记，图有裁剪时 `truncated=true` 并显示范围，不把“空结果”“没运行”“服务不可用”“partial”合并为空列表。

### 4.2 状态所有权与转换

- 服务器是 Study/Case/Scenario/Run/View/权限/兼容性的权威来源。URL 只表示希望打开哪一个 Study/Case/Object/Drawer；Provider 解析 URL 后用服务器返回的 Case 列表归一化，无效或无权限的 ID 显示明确错误或跳到经确认的默认 Case，绝不悄悄显示另一个 Case 的数据。
- Provider 唯一维护 `activeCaseId` 对应的**已解析**上下文引用；Compare 单独维护 `compareSet`，不是 Provider active 字段的数组版。selected object 用 `{type,id}` 与当前 context token 绑定；它可以进入 URL，但必须重新验证。drawer 可由 URL 驱动；未提交草稿、筛选词、临时展开状态为本地 UI state。
- 页面上下游依赖为 `study/case -> context_ref -> active graph/object query/metrics/summary/action target`；`study -> compatible run set -> compare`。Case 切换先置新 identity，再发布一组 loading/ready 状态；旧异步响应即便到达也不能越过 `context_token` 检查。React Query 可缓存旧 Case 以便返回，但旧数据不得在新标题下短暂显示。`cancelQueries` 只是减少无用请求，不是正确性条件。
- Action Submit / Run 的 target、etag、definition revision 和 request id 在点击瞬间固定；onSuccess 只能在当前活动 identity 仍相同时展开对应 drawer，否则只刷新对应 Case 缓存。运行中切 Case 后旧 Run 的 stage 更新不应推进新 Case UI。冲突 409 明示“Case 已改变，请刷新/重试”，403 不降级，503 保留故障提示。
- Time/Scope/模型配置的变动由后端生成新 compatibility hash；旧 Run 仍可审计，但新 active context 输出为空，Compare 对不兼容列显示不可比较和原因。改变选项后 Provider 不应从旧响应构造“已完成”状态。
- 对 Browser back/forward、刷新、重启：从 URL 选择 + 后端 Study 响应重建；不依赖内存 token 计数器或草稿持久化。书签不包含权限或敏感输入。

## 5. Design corrections（源驱动反转审查）

| Previous assumption | Evidence | Corrected design | Impact on existing code | Action |
| --- | --- | --- | --- | --- |
| 总计划所说的 `scenario_context` 已可直接供前端使用 | `_view` 当前只返回分散字段；Graph、Compare、前端选择 Run 各有自己的筛选规则 | 服务器补一个规范化 `context_ref`，统一“最新兼容成功 Run”解析函数；所有读取以同一身份校验 | Study API、Graph、Compare、前端类型 | extend + refactor |
| `scenario_revision` 足以确定当前业务结果 | 同一 revision 可以有新定义版本/Time/Scope/模型兼容组；Run 另有 `case_definition_revision` 和 `compatibility_hash` | result 身份必须包括 Case 定义版本、兼容 hash、Run ID、result View、digest | `latestSuccess` 与 graph endpoint 漏筛 | refactor |
| Graph endpoint 自选 latest 是无害的 | `case_graph` 未筛 `case_definition_revision`，与 Compare 路径不同 | Graph 接受显式 pinned run/context，服务器检验其属于当前 Case/定义版本/兼容组；未提交时只读 revision view | Graph API 增加可选 pin 参数，无 pin 的既有请求也改用统一 resolver | extend + refactor |
| “当前 Case”和“三列 Compare”是一个状态 | Vertex Getting started 的截图可同时展示 Baseline/Scenario 列；仓库 UI 当前把单个 `active` 与三列 `compare` 分别请求 | 独立 active context 与比较 run set；Compare 点击跳转时才显式更改 active case | Provider 与 Compare 组件接口 | refactor |
| 修改 Study 推理配置后还能继续展示先前 output 为当前结果 | 现有 `_compatibility` 对 Study 的 `scenario_time`/scope/模型配置生成 hash，Run 保存 `compatibility_hash` | 这些已有配置改变时旧 output 不再被选为“当前”，显示需重跑；不引入时间轴或历史关系查询 | 保存设置后清除/标注旧结果 | refactor |
| React Query 的旧缓存可以直接渲染 | OSDK public React 文档提示缓存刷新期可持有旧数据；本页 Graph 数量未门控 | 缓存保留但每个可见区必须核对 context token，包括 header、Selection、指标、表、chart | 各可见区读一个 typed query wrapper | refactor |
| 官方 Scenario 一律是不可变历史快照 | Ontology Scenarios Beta 明确 TTL/自动 rebase，且声明并非历史快照工具 | 本项目 pinned QueryDataView 是自有的可重复运行结果，不导入 TTL/rebase | 产品说明、API provenance | preserve + document |
| 可以仅凭 Vertex 截图设计更多 Action/撤销能力 | 现有 Supplier 后端仅提供特定 Action 与 revision，截图只证明可见的 Add/Submit/Run | Summary 只展示真实的当前 Action/ChangeSet，不添加尚无后端语义的撤销能力 | Summary UI | preserve |

递归模型、操作顺序和类别复核：这里无需新递归查询语法；关键顺序为“提交 Action → 新 revision view → 运行 → result view → 选择 completed run”。Object Set definition、read options、execution context、result materialization 必须分开。上述修正均由具体身份错配风险驱动，不因外观相似而重写模块。

## 6. 目标架构及 API 契约

### 后端

1. 在现有 Supplier Study service/router 中提取 `resolve_case_context(study, case, runs, scenario, revision)`；`_view` 每个 Case 新增 `context_ref`，保留旧字段供现有调用兼容。解析成功结果时完整校验 `status=completed`、`case_id`、`case_definition_revision`、`revision`、`compatibility_hash`、`result_view_id`、QueryDataView ready/ontology/snapshot；记录 `selection_reason`（current success / no run / running only / incompatible / view unavailable），避免前端猜测。
2. Graph 端点增加可选的 pinned `run_id` 或 `context_token` 参数，调用同一 resolver 并拒绝不匹配请求（409 stale context、403 unauthorized、404 absent、503 projection unavailable）。响应 `context` 回显完整身份与实际 `view_id`、`read_layer`、`truncated/completeness`；不创造第二套图查询语言，继续用 QueryDataView/FalkorDB 适配层。新页面始终传 pin；现有不传 pin 的调用仍可工作，但服务端也必须用同一 resolver，不能保留旧的错误筛选逻辑。目前仓库搜索仅发现一个前端调用点，没有证据需要制定“旧客户端淘汰期限”。
3. Compare endpoint 与 `resolve_case_context` 共用兼容检查，响应明确每列 run/view/digest/完整性；不兼容返回列级状态，不能给出误导性 delta。Models grid 与 Compare drawer 以相同已解析 run 集合为数据源。Metrics/Impacts 阶段性沿用现有 result manifest，但必须核验 manifest 的 run/view/digest；进一步迁移到既有 Object Query aggregate/load 时需保持单位、精度和输出一致，禁止引入第二查询体系。
4. HTTP 读不信任前端 token；调用现有 `_study` owner/admin 鉴权、`_snapshot` View/ontology 校验和 Object Query context resolver。错误码和可重试性标准化；不泄漏其他用户的存在/Run ID。无需新增数据库表；若最终发现持久草稿需求，再独立设计 migration，不在本期预做。

### 前端

1. 新建 `frontend/src/pages/what-if/scenario-context/` 的类型/纯 resolver/Provider/hook（建议 `ScenarioContextProvider.tsx`）；Provider 包住 Supplier Study 主体，接收 URL selection 与 Study query 数据。区分 `activeContext`, `compareContexts`, `latestAttempt`, `selectedResult`, `selectedObject`, `draftByCase`, `navigation`，只在相关字段变化时更新订阅值；外部 API 与已存在的 `ExecutionContext` 类型对齐，类型由后端 schema 生成/复用优先，避免手写并行版本。
2. 建立 `useContextBoundQuery` 封装，Query key 至少包含 `ontology/study/case/context_token/read_layer/queryDefinition/readOptions`；`enabled` 由可见性和 ready 状态决定，响应回显身份比对后才 render。禁止跨 Case `keepPreviousData`。统一 Study polling：运行中阶段短轮询，静止时降频或显式 refresh；不要每个区块独立重复 polling。
3. 将 `SupplierStudyPage.tsx` 逐步拆为 `StudyShell/CaseSelector/GraphPane/SelectionCard/ActionPanel/ModelsGrid/DetailsTabs` 消费同一 hook。Graph 标题、计数、节点与 Selection 卡绑定同一 Graph response token；Summary/指标和 Impacts 绑定同一 selected result；Compare 用独立 pinned 三列。drawer 的唯一状态跟 URL 同步，并允许刷新/回退还原。Action 草稿按 Case ID 保存，提交时重验目标 etag；草稿不进入 Run。
4. UI 仅参考 Vertex 场景截图的左侧对象属性卡、中心图、右侧 Case Study/Actions/Run/Models 列以及 override 高亮、绿色完成标识和耗时；沿用本项目颜色/组件。Supplier B/C 是方案对比，不增加时间对比 UI。窄屏中图与信息栏堆叠；Selection 不遮挡 graph，键盘可选择 Case/Tab/节点。关键截图采用同尺寸对照，评估信息层级与交互，不追求像素级复刻。

## 7. 组件选择

| 候选 | 决定 | 理由及改变条件 |
| --- | --- | --- |
| 现有 TanStack Query v5 | adopt | 已安装；负责请求缓存、失效、轮询、取消。上下文身份及授权一致性仍由项目代码负责；如真实测试显示缓存键难以保证相干，再评估替代。 |
| React Context + reducer / pure selectors | adopt | 页面范围内足够，状态转换可单测，避免新依赖；如果实际 profile 证明大图因 context 导致频繁全量 rerender，拆分 Context 或采用选择器。 |
| 现有 Zustand v5 | defer pending measured threshold | 已安装，但本期再开全局 store 易产生第二份状态；仅在有量化 rerender 问题且纯 Context 拆分仍不足时用于 UI 层，服务器事实仍不入长期 store。 |
| palantir OSDK React provider | reference only | 依赖 Foundry API/类型体系；本项目不是 Foundry 后端，不适合直接引入。 |
| React Router URL search params | adopt for navigation only | 承载 study/case/object/drawer，可分享/回退；不能承载凭证、草稿或权威 revision。 |
| 现有 Object Query / QueryDataView / FalkorDB | preserve | 现成权限、snapshot、对象投影路径；Graph 做 adapter，不另建查询层。 |

## 8. 分阶段实施与 Gate

1. **契约/回归基线**：冻结当前 API 示例、UI Demo 和失败样本；给 `ResolvedScenarioContext` 与兼容选择写 schema/纯函数测试，针对“同 revision 不同定义版本”“旧 run view”“Study options 改变”出红测试。Gate：身份/错误/加载状态表经测试覆盖，现有代码与用户改动未被覆盖。
2. **服务器单一 resolver**：将 Study `context_ref`、Graph pin/回显、Compare 复用和鉴权接通，保留已有字段和不带 pin 的 Graph 请求；用真实 PostgreSQL + FalkorDB 验证每个 View。Gate：B/C 并发/重复运行后 graph/compare 的 run/view/digest 完全相同，失配请求拒绝，不混用旧结果；现有前端请求形式仍正常返回。
3. **Provider 与 UI 迁移**：Provider 管理活动上下文，组件改用 context-bound hooks；按可见性读取，URL 回退/刷新同步；Action mutation pin target；Summary/Graph/Selection/KPI/对象明细/Compare 信息层级整理。Gate：受控慢响应 B→C→B、Run 中切换、options 改变、浏览器前进后退均无跨 Case 一帧错配；草稿不会跨 Case 提交。
4. **完整真实验收**：执行既有 [Supplier Demo 操作稿](DEMO_SUPPLIER_SCENARIO_WORKBENCH.md) 并在其基础上增加 Provider 一致性步骤，保持原步骤不删；Playwright 从准备好数据起完整重放，截图 Graph、Selection、Summary、Models、Compare、现有推理配置、重启恢复。Gate：同一稿连续完整通过，真实后端回归、build、lint 和环境故障注入通过；缺陷则修复后从第一步重跑。若可比运行持续出现同类跨组件错配，先做限界架构复盘（身份来源/缓存失效/视图所有权），记录证据、影响和最小修复，再继续，不因一次孤立失败重写。

## 9. 验证矩阵与验收证据

| 不变量/用户流程 | 最低测试层 | 具体断言和证据 |
| --- | --- | --- |
| Context identity、hash、空/坏组合、View 与 Run 归属 | backend unit + API contract | 定义版本、兼容组、revision、view、digest 任一变化改变 token；跨 Case/跨 ontology 的 Run ID 拒绝；无结果与不可用错误分开。 |
| 真实写入/执行/投影一致 | PostgreSQL + FalkorDB + Redis/Celery integration | Submit Action → revision view → 运行 → result view；Graph/Object Query/Compare 同 run/view/digest；重复 Run、失败/重试/取消、服务中断恢复后不错误复用；迁移到最新并重启验证。 |
| 前端快速切换/旧响应 | React component test with controlled deferred API | B→C→B 乱序响应；每次 Graph 标题/计数/节点、Selection、Action target、Summary、KPI、Impacts 只显示同 token 数据；Run 回调在别的 Case 不改其 drawer。 |
| 导航与交互 | Playwright real browser/real API | sidebar → Supplier Study → B/C/基线 → Action/Run → stages → Summary/Graph/Selection/对象表/Compare；back/forward、刷新、后端重启；既有 Demo 稿逐步截图并检查 console/network/溢出。 |
| 现有推理配置、比较精度 | API contract + E2E | 修改当前 `scenario_time`/Scope 后旧 Run 标历史且 KPI 不冒充当前；Compare 非兼容列无 delta；完整值、单位、精度标志一致；真实空表、部分数据、图截断各有专属状态。不测试或实现本体时间轴。 |
| 授权与故障 | API integration + browser fault injection | 403/404/409/503、过期会话、慢网络、FalkorDB/Redis 断连、并发 etag 冲突；不泄漏对象或自动 fallback live，重试 UI 正确。 |
| UI/可访问性/稳定性 | Playwright + profiling | 1440×900/1280×720/窄屏不溢出；键盘导航、Tab 焦点、selected state/ARIA；记录切 Case p50/p95、请求数、内存/CPU、一次 Run 的恢复耗时；比较优化前后同 fixture。 |
| 静态门禁 | ESLint + TypeScript + build | `cd frontend; npm run lint:src; npm run lint:scripts; npm run build`，分别 0 errors/0 warnings；核查 TSX/test/JS/MJS parser。现有验收记录称全仓 `lint:src` 138 errors/12 warnings，实施前须重测按规则/文件分组；新改文件绝不能增量产生警告，直接阻塞本功能的历史问题修复，其他历史债逐阶段清零，不通过禁规则或 `|| true` 伪造绿灯。 |

数据：复用 [frePPLe 固定公开源及许可证记录](../data/frepple_demo/README.md)（官方仓库 [frePPLe](https://github.com/frePPLe/frepple)，commit `73e5be3d1573db043209111325dd921d68cf4b88`，fixture SHA-256 `d7eb98078882b234c395fd053c5f6fbda33810cb90add2adb4bf7d62f28637ef`；384 源记录、363 业务对象、903 关系、220 Demand、16 open）；synthetic supplier 扩展明确单独标注。重用现有导入/核验脚本，不新造“真实数据”。已准备数据默认不重复导入；独立测试库使用脚本导入并仅清理其自身新建的测试 ID。验收写出本次命令、版本、耗时、截屏路径、Run/Context token 对照和真实限制。

故障分类：新增或本次变更引入、旧代码但直接阻断上下文正确性、无关历史债、环境/fixture/migration/auth 模式不匹配，原因不明时先记 unclassified；不能拿浏览器或 Docker 启动失败当功能通过或失败。性能门槛先测现有真实基线，再设不得增加无意义重复请求、切 Case p95 不显著退化的数值预算；不以 mock 延迟证明生产性能。日志只记录 ID、token 摘要、阶段、耗时、错误码，不记录完整参数或敏感对象属性。若新增索引/迁移，再补 migration/rollback 演练；本期预期无 schema migration。

## 10. 需要防止的问题、明确取舍与结论

- **一个 Run 指向两份不同的结果怎么办？** 后端目前把结果 View ID 同时写入 `ScenarioRun.result_view_id` 和 `result_manifest.provenance.result_view_id`（见 `backend/app/tasks/v2/scenario.py`）。Graph 现在优先读 manifest 中的 ID；若旧数据/故障令两处不一致，图可能读到另一份结果。实施时核对二者；不一致就显示“结果资料不一致，无法展示当前图”，记录 Run ID 供排查，不能猜一处当作正确。正常 Run 的两处必须相等，并以真实库测试验证。这就是原文“上下文完整性”，不是要求另建数据层。
- **接口改动会不会把现有页面弄坏？** 当前 Graph API 在仓库里只发现 `SupplierStudyPage.tsx` 一个调用点，它没有传 Run ID。新页面会传明确的 Run/context pin；服务端仍接受原来的无 pin 请求，只把内部选 Run 逻辑修正确保它选同一份结果。Study 响应只增加字段、不删除旧字段。因此本期没有“淘汰旧 API”项目，也不需要设迁移期限；用原请求和新请求各做一次回归测试即可。
- **草稿和页面范围怎么定？** 本期 Provider 只服务 Supplier Study；未提交 Action 草稿仅保存在此页面内存中、按 Case 隔离，切换后可回来继续填，刷新后消失，Run 不读取草稿。该决定已写入交互与测试，不是悬而未决的风险。跨页面 Provider、草稿持久化、多 Action 撤销和本体时间轴均不在本期。
- **不要把 Beta Ontology Scenario 当成本系统的历史快照。** 官方 Beta 文档说明它有 TTL/自动 rebase 且不是历史快照工具；本项目的 pinned Run/View 是当前仓库为可重复 Demo 设计的结果存档。UI 和文案须说明其来源，不宣称与官方场景生命周期等价。

决策摘要：保留既有查询/视图/运行管线；首先修正服务器统一 context 解析与 Graph/Compare 选 Run 不一致，再加页面 Provider 和组件绑定；React Query + React Context 足够，OSDK 仅作参考。进入 UI 阶段的证据是后端真实库里三个 Case 的 Run/View/Digest 一致性与错误语义测试通过；完成依据是同一 Demo 全流程、故障/重复运行、lint/build/回归和截图均通过，而不是 Provider 文件存在。
