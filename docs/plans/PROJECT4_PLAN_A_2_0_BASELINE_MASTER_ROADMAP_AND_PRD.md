# Project 4：以 Plan A 2.0 为基线的主路线图与产品需求

| 项目 | 内容 |
|---|---|
| 版本 | v1.1 · 完整路线与需求展开稿 |
| 日期 | 2026-10-24 |
| 作者 | nihou |
| 状态 | 规划文件；范围、优先级、分工与估算须由双方评审后冻结；本文不代表功能已实现 |
| 开发起点 | GitHub fork 的 plan-a-2.0，提交 7ab1cd1acc7209516039b87220ac5646e48b46c0 |
| 目标 | 以现有 Plan A 2.0 为可复核起点，逐步建设完整的 Project 4 制造与供应链 Ontology 决策工作台 |
| 参考材料 | 本地 Project 4 主路线图、详细 PRD、目标架构、页面/任务矩阵、功能对照目录与 E2E 验收材料 |

> **基线原则：** 新阶段从 plan-a-2.0@7ab1cd1 的干净检出开始。当前本地 palantir@8005e66 及其未提交修改不作为共同基线，也不自动纳入本计划；只有经过逐项评估、测试和双方确认的改动，才通过独立提交或 PR 迁入。Plan A 2.0 中代码或路由存在，表示“可检查和复用的候选能力”，不等同于已通过运行、权限、恢复或端到端验收。

> **目标材料原则：** 主路线图与详细 PRD 描述的是期望目标和候选实现边界。本文吸收两者的产品目标、能力范围、功能需求、验收方向与风险，但不把其中针对本地 palantir 工作树的历史状态、分支处置或未签收结论当作 plan-a-2.0 的事实。所有外部产品资料仅作为可观察产品行为的参考；不推断其私有内部实现。

## 0. 决策摘要

Project 4 要逐步发展成一个面向制造与供应链业务的工作台：用户从数据源和业务对象开始，检查数据如何映射到本体，了解当前状态和历史变化，比较业务情景，获得带来源证据的分析建议，并在授权和人工审核后执行受控操作。Ontology、数据、查询、Scenario、Agent、Action、权限和审计应逐步收敛到同一组身份、版本与执行上下文。

本计划采用“先形成可重复的单条业务闭环，再扩展为平台能力”的推进方式：

1. 保留 Plan A 2.0 为代码起点，先确认其干净环境可以安装、启动和演示。
2. 以木材 Supplier Study 作为第一条端到端产品回归路径，保护既有页面结构、数据和 Baseline/B/C 操作体验。
3. 补齐数据到本体、版本和来源追溯、对象工作台、时序 What-if、受控 Action、持久 Agent 会话和平台治理之间的共享合同。
4. 按 M0–M8 路线建设能力；功能清单、页面覆盖率、公开参照、工作量与并行安排先由 M0 阶段确认。
5. 单独衡量 Project 4 Must-have 任务、Palantir 可比功能、页面相似度、组合参照对齐度和 Project 4 专属 UX；这些指标互不替代。

本计划的交付终点不是完整复制 Palantir Foundry/AIP 的全部产品，而是把目标文档中与 Project 4 制造/供应链任务相关的核心能力建设成可验证、可演示、具备权限与恢复边界的产品。完整低代码生态、任意外部连接器、3D Builder 和通用多租户平台属于后续选择，不作为本期默认承诺。

## 1. 计划基线与状态口径

### 1.1 唯一共同代码起点

| 项目 | 本计划约定 |
|---|---|
| 代码仓库 | Joeysoda/nano-ontoprompt |
| 分支 | plan-a-2.0 |
| 基线提交 | 7ab1cd1acc7209516039b87220ac5646e48b46c0 |
| 使用方式 | 新的共享开发分支从该提交建立；基线本身保留为可重建参照 |
| palantir 工作树 | 独立保留，不在开始阶段整体合并、重置或覆盖 |
| 计划文档 | 本文件是拟议目标计划，不是代码交付或阶段验收证明 |

### 1.2 基线能力的审慎解释

Plan A 2.0 中已经存在若干可以作为起点的源码和页面，包括 Supplier Study / What-if、Object Query、Logic Asset、Decision Analysis、图与对象浏览、数据工作台及相关 API。M0 必须在干净 checkout 中确认这些能力的路由可达性、所需服务、数据准备方式、权限行为和页面操作。只有通过对应验收的能力才标为已验证；需要修理的能力列为基线缺陷；不适用于目标流程的能力不因存在而扩成独立模块。

### 1.3 状态标签

| 标签 | 含义 |
|---|---|
| 基线候选 | 在指定代码提交中能找到实现入口，但尚未完成本计划的运行验收 |
| 已验证 | 有代码提交、可复现环境、明确输入和验收证据，且覆盖相应错误/权限边界 |
| 计划中 | 本计划拟开发的能力，尚未因文档描述而视为存在 |
| 条件纳入 | 需要在 M0 范围决策中确认后才进入本期承诺 |
| 延后 | 目标有价值，但当前阶段先不实施；不代表已从长期方向删除 |
| 不纳入本期 | 明确超出本计划本期边界；如重新纳入，需评估影响和资源 |

阶段或 PR 不能仅以“源码存在”“页面能打开”“健康检查为 200”标为已验证。每项已验证状态要注明代码 SHA、数据/环境版本、所用角色、操作或请求、结果和未覆盖边界。

## 2. 产品目标、用户与核心闭环

### 2.1 产品定位

Project 4 是面向制造、供应链和设备运营场景的语义决策工作台。它以本体对象和关系组织业务数据，用可追溯的数据加工和版本化模型提供查询、图、时间、分析及情景能力，并通过受控的 Action 和 Agent 帮助用户完成决策工作。它不是把若干演示页面简单并排，也不是不受控的 LLM 聊天界面。

### 2.2 主要用户

| 用户 | 主要工作 |
|---|---|
| 业务分析师 / 供应链规划人员 | 搜索供应商、物料、订单与工厂对象，比较方案，理解影响因素和数据证据 |
| 数据工程师 / Ontology 建模人员 | 接入来源、查看 schema、构建映射、维护对象/关系/规则/事件定义并发布版本 |
| 业务操作人员 | 查看任务、审核建议、批准或拒绝操作，跟踪执行状态与结果 |
| 管理员 / 治理负责人 | 管理身份、权限、资源发布、审计、运行健康、配额、恢复和系统策略 |
| Agent 使用者 / 运营者 | 在授权业务上下文中使用 Agent、检索知识、查看引用、接管或恢复会话 |

### 2.3 目标用户流程

最终目标流程应能从业务问题自然开始，而不要求用户先了解数据库表名：

1. 用户选择一个已发布的业务工作面，例如木材供应链韧性研究。
2. 用户通过 Object Explorer 找到供应商、物料、订单、工厂或设备对象，并打开完整对象页或侧栏对象面板。
3. 用户检查对象来源、关系、时间范围、数据质量、权限裁剪和本体版本。
4. 用户查看由来源数据构建而来的当前状态，或选择已固定版本的 Scenario / What-if 分支。
5. 页面比较基线与候选情景的指标、受影响对象、证据、风险与不确定性。
6. Query、分析或 Agent 可以解释发现，但每个重要结论都指向可访问的对象、来源和版本；数据不足时明确拒绝给确定建议。
7. 接受建议只生成 DecisionCase 或 Action Preview；适当角色进行审核后，系统再通过唯一 Action Runtime 执行。
8. 用户能够追踪结果、审批、审计、版本和失败恢复；系统运营者能观察队列、任务健康、错误与运行负载。

木材 Supplier Study 是第一条端到端产品路径和演示回归，不是全平台能力的替代品。FactoryNet 等时间流数据可用于时间语义和事件路径验收；其他行业来源按准入与许可状态逐步扩展。

## 3. 目标范围与优先级

### 3.1 八项产品目标

| 目标 | 目标结果 | 主阶段 |
|---|---|---|
| O1 数据到本体的可视化转换 | 从来源、转换、质量、映射到本体发布形成可回看工作流 | M2 |
| O2 大模型辅助本体构建 | LLM 产出有来源和版本的候选，由人审阅后发布 | M2 |
| O3 动态更新 | 数据变化可增量应用；schema 变化独立预览、审批和迁移，不默认全量重建 | M2、M5 |
| O4 What-if、时序知识图和向量检索 | 对同一明确时间、权限和版本上下文查询与比较 | M5、M7 |
| O5 Digital Twin 与决策推演 | 可重放地模拟状态、指标与影响，结果可解释且隔离写回 | M4、M5 |
| O6 可视化工作台和展示 | 为用户任务提供可复用页面；木材演示体验保持稳定且结果一致 | M3、M6 |
| O7 LLM / Agent 决策支持 | 有持久会话、受限工具、来源引用、人工审核和安全续接 | M7 |
| O8 稳定性、性能与生产准备 | 形成运行观测、故障恢复、容量、备份、升级与回滚证据 | M8 |

### 3.2 四项横向硬要求

1. 行业 Ontology 建模工具：可视化配置 Object、Property、Link、Rule、Event 和 Permission，并支持版本、校验、审核、发布及恢复。
2. 多源语义映射：覆盖 ERP、MES、SCADA、IoT、文档与图像来源族；每条路径如实标注真实连接器、适配器或 fixture。
3. 可执行业务规程和权限：查询、Agent 工具与 Action 使用共享服务端授权和约束；批准、拒绝、版本及执行者可审计。
4. 版本与血缘：业务结论和写入能追到数据、本体、计算定义、模型/Prompt/工具、权限决策、执行主体和输出摘要。

### 3.3 优先级

| 优先级 | 范围 |
|---|---|
| P0：核心产品闭环 | 基线保护；统一语义/版本/权限上下文；可视化数据到本体；LLM 候选与人工审核；增量和时间语义基础；对象浏览；Scenario/What-if；唯一 Action Runtime；持久 Agent Session 与工具边界；贯穿式审计和安全拒绝；木材 Supplier B 决策任务 |
| P1：产品化扩展 | Object Explorer 保存/分享与统一视图；版本化 Query/Function/Skill/API/MCP；受限应用组合器；Knowledge Base 与混合检索；多来源扩展；批量 Action；人工覆盖；运行队列、调度、指标、容量和运营管理；选定 TypeScript/Python SDK |
| P2：后续独立评估 | 任意低代码应用搭建器、完整 OSDK/多语言客户端生态、Marketplace、广泛商业连接器目录、全功能多租户平台、3D Twin Builder、通用插件/代码执行市场、复杂 Agent-to-Agent 网络编排 |

目标架构明确列出的批量 Action、人工覆盖、项目运营队列及人员负载管理先列为 P1 候选，不因缺少逐页 Palantir 对照而自动删除。它们的角色、语义和评分口径须在 M0.4 签收后纳入准确范围和估算。跨租户隔离、高级 Ontology 类型及 Agent-to-Agent 交接也在 M0.4 作单独裁决。

## 4. 产品需求（PRD）

以下 FR 为目标能力合同。各 FR 的实现顺序由第 5 节决定；某一局部页面或数据模型存在，不代表整条 FR 已完成。

### FR-01：统一业务语义与执行上下文

**目标：** 让对象、关系、属性、版本、调用者、权限和结果在查询、图、Scenario、Agent、Action 与应用之间保持同一含义。

**需求：**

- 形成稳定、可追踪的 Object、Object Type、Property、Link Type、Ontology Revision、DatasetVersion、Query/Function/Action、Scenario、Agent 和 Evidence 身份。
- 查询、图、情景、Agent 和 Action 共享明确的 Ontology revision、数据快照/视图、时间上下文、caller principal、权限裁剪摘要和 trace ID。
- 每个重要结果携带可复用 Result Manifest，提供授权范围内的数据来源、版本、查询/分析定义、时间窗口、刷新状态、输出摘要和关联运行记录。
- 旧模型可以在过渡期通过兼容层读取；新功能不得再建一套语义不同的对象、关系或写入解释器。
- 删除、tombstone、恢复、关系端点可见性和统计口径一致；来源数据行删除与 Ontology 类型定义删除是不同操作。
- 高级类型（如 Interface、共享属性、ValueType、Struct、Derived Property、Monitor/Alert）按 M0.4 冻结的子集逐步进入 UI；存有 schema 并不等于提供可用建模器。

**完成信号：** 同一业务对象从搜索、图、详情、Scenario、Agent 和 Action 中保持稳定 ID、revision 和权限含义；刷新或深链能恢复合同允许的上下文；未授权数据不会通过 manifest、计数、错误信息或引用泄露。

### FR-02：可追溯数据接入、构建、质量和 LLM 建议

**目标：** 让数据从来源进入业务本体的路径可审阅、可复现、可重放且可定位失败。

**需求：**

- 来源登记稳定 source ID、owner、连接类型、schema fingerprint、健康状态、刷新策略和凭证引用；秘密不写入日志、Run 结果或普通 manifest。
- Data Workflow 从 Source、DatasetVersion、Transform、Mapping、Quality 到 Ontology Publish 保存定义版本、输入 pin、输出摘要、运行状态、失败阶段、证据和操作主体。
- 通过可视化步骤展示字段/媒体片段到 Object、Property、Link、Rule 或 Event 的映射预览；错误、质量问题和人工修正可回看。
- LLM 可生成类型、关系、mapping、规则和 competency question 候选；每个候选绑定输入样例、来源、模型/Prompt/Tool/解析器版本、不确定性和拒绝理由。
- LLM 候选不会自动变成正式本体、Mapping 或执行逻辑；发布前需明确审阅和批准。AI 生成代码仅能在隔离、受限试运行和人工发布流程中使用。
- 缺模型、服务超时、无效输出或凭证不可用时，默认 fail closed；只有工作流明确许可时才可切换到已登记的规则降级路径，并将 Run 标注为 degraded。
- 每条数据来源族提供一个经准入和许可核实的代表性路径；真实连接器、适配器和 fixture 分开计数。

**完成信号：** 从一个已登记来源完成构建和审核发布，刷新页面后能查看输入/输出、映射、质量、审阅、版本、错误与恢复；重复执行语义明确，不会因模型重试重复发布。

### FR-03：增量更新、质量演进和时间语义

**目标：** 让持续变化的数据可靠进入已批准的语义模型，并支持说明“当时状态”和“系统何时知道”。

**需求：**

- Source event 有稳定 ID、来源 offset、event time、ingest time、业务有效时间、schema revision、operation 和内容摘要；事件重放幂等。
- 明确事件迟到、水位线、顺序冲突、patch/upsert、完整快照、删除 tombstone、恢复、回填与 schema drift 的策略；稀疏 patch 不得被误当作完整快照并删除缺失字段。
- 数据-only 更新复用已批准 Mapping ID/digest 与 Ontology revision，不触发新的本体建模；schema drift 走独立差异预览、审批和迁移。
- 查询和图支持当前态、as-of 和业务有效期查询；event time、valid time、system/ingest time、时区、精度和序号类型有显式合同。
- 失败工作项有有限重试、可查尝试历史、持久终态与授权人工重放；图投影可从权威数据重建并观察延迟。
- 时间序列值与 Event 对象区分建模，并可记录单位、采样率、质量和来源版本。

**完成信号：** 在固定 DatasetVersion 和 OntologyRevision 下执行新数据、重放、迟到和删除/恢复用例；生成预期 delta、不重复写入、不静默全量重建，并能重现历史时间点的状态。

### FR-04：Scenario、What-if、Digital Twin 与决策分析

**目标：** 提供隔离、可比较、可重放的业务模拟，并把推演结论连接到真实对象和证据。

**需求：**

- 区分 Tracking（跟随当前数据）和 Pinned（固定版本用于审计/复现）Scenario；定义创建、刷新、rebase、TTL、过期、冲突、分支、checkpoint 与合并语义。
- Scenario 固定输入快照、Ontology 与数据 revision、查询/Function/规则/模型版本、参数、时间上下文、principal/权限摘要和输出 digest。
- 用户能基于固定 checkpoint 创建分支、查看差异、重放运行、比较 Baseline 与候选方案，并解释逐对象影响。
- Digital Twin 展示观测值、推断值、期望值、模拟值和已批准值，不能把模拟结果伪装成实时事实。
- Supplier B 推荐卡应显示经批准的规则、输入和结果版本、关键指标、受影响对象、证据、替代方案、权衡、风险和数据缺口。依据不足、版本过期或权限不够时明确拒绝推荐并说明原因。
- 默认禁止 Scenario 直接写入生产数据或外部系统。任何写回都必须创建 FR-05 Action 请求并经过适用审批。
- 首阶段以可复现 2D 关系图、时间轴和指标面板为目标；3D 编辑器不是上线前提。
- Postgres/规范数据和原始来源为事实权威；向量结果只是召回候选，不能自行成为业务事实或替代证据。

**完成信号：** 同一固定输入可重放并获得可比较输出；当前态与历史态明确区分；Scenario 失败/冲突可恢复；所有推荐可以返回来源、规则和对象；外部写入只通过 Action Runtime。

### FR-05：唯一、受控、可审计的 Action Runtime

**目标：** 统一所有业务写入路径，使任何界面、Scenario 或 Agent 都不能绕过授权、审批与执行记录。

**需求：**

- 使用类型化 ActionRequest 和不可变 Action/Function 版本，标准化 Preview、Review/Approval、Commit/Run、Result、Reconcile 和补偿流程。
- Preview 固定目标 ObjectSet、输入/参数摘要、数据与 Ontology revision、权限范围、冲突检查和影响范围；提交时再次核验权限、对象版本和批准有效性。
- 人员、服务、后台 worker、请求者、审批者和执行者身份分离；系统重放不能冒用用户身份。
- Live、Scenario、DecisionCase、Agent 建议和 Supplier Study 等入口适配同一 Action 合同；重复旧路径逐步迁移、隔离或退役。
- 请求幂等、并发冲突、未知执行结果、部分成功、重试、取消、审计、跨存储投影与恢复有清晰状态。PostgreSQL 是权威写入记录；派生图投影可重建并由 durable reconciliation 收敛。
- 受控批量 Action 对每个对象单独记录授权和结果。人工覆盖必须有理由、前后值、发起/批准主体、关联 Case/Run 和可撤销审计；不得通过覆盖安全硬约束。
- 区分不可逆、可补偿与可撤回动作。补偿是新的、受授权且有审计的 Action；不得用数据库回滚宣称外部副作用可撤销。
- Agent 默认只读；所有写操作只能生成 Action Preview，实际提交走本 Runtime。

**完成信号：** 每个生产写入口进入同一状态机；撤权、过期批准、版本冲突和重复请求均有确定结果；故障注入或恢复用例证明不会出现状态谎报或重复副作用。

### FR-06：持久 Agent Session、Tool Registry 与 AI 治理

**目标：** 让 Agent 成为有边界、可继续、可审阅的产品运行能力，而不是单轮模型调用。

**需求：**

- Agent Definition 有稳定 ID、不可变版本、owner、模型/Prompt/Tool/Query/知识库绑定、授权范围、预算、评测结果和发布/撤回状态。
- AgentSession 固定会话 owner、参与者/角色、上下文引用、Ontology/data/Scenario revision、Definition/Model/Prompt/Tool 版本、预算、保留期及状态。
- Session、Event、Checkpoint 与 Run 状态可持久化；长任务有 worker、有限重试、heartbeat/lease/fencing、pause/cancel 确认和崩溃后恢复；恢复不能重复已完成的写操作。
- Tool Registry 记录 schema、read/write 类型、权限、版本、超时、限额、审批要求和输出格式；运行和恢复时重新检查权限及工具版本。
- 每次读取、检索、恢复和工具调用按 caller 与资源 ACL 的交集授权。参与者分享不扩大权限；撤权阻止后续读取、续跑和副作用。
- AgentEvent/trace 能关联真实 caller、owner、participant、model、tool、worker/system、requester/reviewer/executor；敏感数据按策略脱敏并受保留/删除控制。
- 重要回答引用来源对象、证据、时间和版本。证据不足、权限裁剪、工具错误时应明确说明。
- 定义离线评测与回归，包括工具选择、引用准确性、拒绝越权、恢复、延迟、成本和领域正确性；模型自评分不替代领域评估。
- Manager 式入口支持按权限管理已发布 Definition 与活动 Session、接管任务、查看运行和撤销访问。
- Agent-to-agent 编排是否进入本期由 M0.4 决定；如延期，不把 Session 参与者共享或人工接管说成多 Agent 编排。

**完成信号：** 持久会话可跨页面续接并在服务/worker 重启后恢复；对工具和对象权限的拒绝可审计；Agent 建议有证据并只通过 FR-05 触发写入。

### FR-07：Object Explorer、Object View 与演示一致性

**目标：** 让工作台以业务对象和用户任务组织信息，而不是在互不兼容的页面间复制上下文。

**需求：**

- 搜索、过滤、关系遍历、Pivot、排序、聚合、分页、保存查询/对象集和比较；权限与版本过滤由服务端执行。
- 同一 ObjectRef 可从搜索、表格、图、时间轴、DecisionCase、Agent 引用或分析页面打开 Full Object View 或侧栏 Panel。
- 对象详情可呈现可授权的属性、关系、来源、版本、时间、证据、质量和可用 Action；隐藏字段和被裁剪对象不可从页面总数/错误消息推断。
- 深链、页面切换和刷新按约定保留 Ontology、数据、Scenario、时间和查询上下文。
- 统一加载、空、错误、无权限、过期、部分成功和重试反馈；局部页面不得另造独立的状态含义。
- 木材 Supplier Study 保持用户熟悉的布局、Case 切换、图和 Baseline/B/C 指标步骤。历史截图缺失时明确建立新视觉基线，不声称像素级复原历史版本。

**完成信号：** 关键 ObjectRef 和上下文在工作面间不丢失；木材固定数据下的操作与显示一致；至少完成目标页面矩阵中该工作面对应的真实任务与视觉/状态验收。

### FR-08：平台治理、资源登记与运行运维

**目标：** 把身份、资源、执行、审计和运营信息作为共享控制面，而不是仅以菜单隐藏或日志拼接代替治理。

**需求：**

- 统一 human、group、service 和 system principal；所有 Query、Agent Tool、Action、调度任务和恢复任务记录 caller/scope/policy decision。
- 按资源、Ontology、Object、Property/Field、Link、Evidence、Tool、Action、Session 等层级执行服务器端授权；撤权须影响后续读取与执行。
- 为 Ontology、Dataset、Workflow、Query、Function、Action、Scenario、Agent、Knowledge Base 和 Application 建立稳定资源身份、owner、ACL、revision、生命周期和依赖关系。
- 审计事件可关联 trace/correlation、actor、资源、版本、请求摘要、审批、拒绝原因、结果摘要和重试历史；不把秘密和未授权正文写进审计。
- 持久任务队列支持状态、取消、重试、失败终态、人工接管和权限过滤。运营队列读取权威任务状态，不创造第二套任务事实。
- 持久调度定义 timezone/DST、misfire、overlap、版本 pin、幂等 Run、暂停/取消和重启后续作语义。
- 提供健康/readiness、依赖状态、日志、指标、trace、队列/投影延迟、限额、阈值告警和容量观察。
- 备份/恢复、schema 升级/前向修复、投影重建、发布/回滚和故障演练有可复现记录。
- Tenant/Project/Use-case 隔离边界须在 M0.4 决策。未决定前不扩展为完整多租户平台，但所有资源和授权合同应为未来范围保留稳定扩展点。
- 人员工作量分配可先提供基于权威队列、资格和容量数据的建议及人工接管；没有可靠数据时不得自动宣称公平或自动排班。

**完成信号：** 代表用户/服务/worker 路径通过授权正向和负向矩阵；运行状态、审计、队列、告警和恢复在真实服务中可查；安全和容量边界有证据。

### FR-09：可复用 Query、Function、Skill、API 与 MCP 发布

**目标：** 让经批准的业务查询和逻辑可以由工作台、应用、Agent、HTTP API 与 MCP 复用，而不产生权限或语义分叉。

**需求：**

- Query/Function/Skill 有类型化输入输出、稳定 ID、不可变版本、权限合同、错误行为、运行限额和生命周期。
- 同一已发布 Query 在 Explorer、业务应用、Agent 和 API/MCP 中使用一致的调用者、过滤、版本和 ResultManifest。
- Function/Logic 可有草稿、测试、评估、审阅、发布、撤回、监控与历史版本；只纳入确定性或明确受控的执行类型，不发展成任意代码沙箱。
- 为纳入范围的 API 生成或维护基础 TypeScript/Python typed clients，统一分页、错误处理和 caller scope。
- Provider/API Key 以凭证引用和非秘密指纹进入资源配置；秘密不进入 Query、Session 或 Run 文档。

**完成信号：** 一个经批准的 Query/Function 可在至少两个已纳入工作面复用；结果与权限一致；版本或权限改变时有明确的失效/兼容策略。

### FR-10：可视化 Data-to-Ontology Workflow 与应用组合

**目标：** 让用户以可理解、可审阅的工作流组织数据加工和业务工作面。

**需求：**

- Workflow Definition、Node Definition 和 Workflow Run 版本化；Source、Transform、Mapping、Quality、Ontology Publish 节点有明确输入输出和权限合同。
- 节点支持配置、预览、验证、dry-run、运行日志、失败定位、重试和版本差异；所有输出均绑定输入版本与节点参数 digest。
- 生成式处理逻辑先作为候选审阅，再进入隔离执行/正式发布流程；未知依赖或权限默认禁止。
- 在 M2 建立数据到本体流程后，M6 的应用组合器复用 Object View、Query、Analysis、Scenario 和 Action 组件，配置可预览、版本化发布、撤回和回滚。
- 发布后的 2D 页面支持用户/角色、数据范围和权限反馈；不通过复制组件来形成平行语义。

**完成信号：** 代表性工作流可重新打开、审阅、重跑并恢复；应用页主要组合现有资源，并沿用它们的版本、权限和状态合同。

### FR-11：Knowledge Base 与混合检索生命周期

**目标：** 把文档和其他非结构化证据作为受治理的来源接入 Query、Explore 和 Agent。

**需求：**

- KnowledgeBase、Document、Chunk、IndexRevision 有稳定 ID、来源、解析版本、权限、刷新和删除状态。
- 导入、解析、切分、索引、更新、重建和删除任务有 Run 历史和可诊断失败状态。
- 向量、全文和图邻居可作为候选召回通路组合；召回前后都执行 caller/resource 权限过滤。
- 每个 EvidenceRef 可返回可授权来源、文档/数据版本、片段定位、时间和索引版本；内容更新或删除能让旧索引失效或重建。
- 向量索引可重建；它不能作为事实权威，不能以相似度分数代替规则或人工验证。

**完成信号：** Agent/Explore 能从已授权知识库返回可验证引用；权限撤销、文档更新和删除后旧内容不会继续通过过期索引泄露。

## 5. 以 Plan A 2.0 为起点的路线图

M0–M8 是计划阶段，不是预先固定的日历排程。必须先完成 M0 范围和风险盘点，再结合两位开发者实际投入、许可和数据准备情况给出低/基准/高估算。

### M0：冻结新阶段的起点和验收范围

**目标：** 证明共同基线能重建，弄清可复用能力、木材演示状态、目标工作面和当前目标边界。

**主要内容：**

- 在全新目录检出 plan-a-2.0@7ab1cd1，记录依赖、服务、迁移链、数据准备和启动过程；不使用当前 palantir 工作树作为替代。
- 盘点目标功能的代码入口、实际路由、数据依赖、运行证据、角色边界和已知缺陷；标为基线候选/已验证/未验证/需修复/条件纳入。
- 复核 Wood Study Supplier Study 的 Baseline/Supplier B/Supplier C 数据、布局、页面入口、指标、图片/演示材料和正常操作路径；保留健康体验，不新建空白流程替代既有演示。
- 冻结 11 个目标工作面、代表角色、主用户任务、Must-have、页面/功能参照来源、截图采集条件和证据责任人。
- 准入候选数据集、fixture 与 benchmark；确认许可证、来源 provenance、适用范围和再分发限制。
- 裁决本期是否需要 Tenant/Project/Use-case 隔离、Agent-to-Agent、Ontology 高级类型、批量 Action、人工覆盖和运营队列，并确定分母与安全要求。
- 逐项审阅本地 palantir 线中值得复用的改动；未签收的本地改动保持隔离。
- 按冻结范围重估 M1–M8、并行关系、owner、集成缓冲和主要风险。

**交付物：** Plan A 2.0 可复建说明；基线能力与缺陷清单；木材演示回归材料；页面/任务矩阵；准入数据表；范围决策记录；更新后的估算和负责人。

**退出条件：** 两位开发者对基线 SHA、目标功能范围、第一条业务闭环、可复用能力和范围外项签收；主要未知项有 owner 和关闭条件；关键依赖可复建。

### M1：统一身份、语义、版本和执行合同

**目标：** 在新增跨模块行为前，确定各服务如何表示相同对象、数据、调用者、权限和结果。

**主要内容：** 稳定 ObjectRef/ResourceRef；Ontology/Data/Scenario/Query/Function/Action/Agent 版本；Principal 与授权摘要；ScenarioContext 与 ResultManifest；trace 与审计事件；系统 worker 身份；统一错误、分页、幂等、重试、删除和部分失败语义；旧模型兼容边界。

**交付物：** 合同文档、API/schema 更新策略、迁移/兼容计划、权限模型、事件和 manifest 定义、跨模块示例。

**退出条件：** 查询、图、Scenario、Agent 和 Action 对同一对象/版本/权限返回一致语义；兼容路径不会额外赋予权限；迁移路线可以在干净数据库中验证。

### M2：本体建模、来源接入、Workflow、质量与增量

**目标：** 完成一条可视化、可追溯、受审阅的数据到本体路径，并形成可扩展至不同来源族的模板。

**主要内容：**

- Data/Source Catalog 与连接状态；支持项目中准入的数据库、文件、API 和事件来源。
- Workflow/Transform/Mapping 的预览、质量检查、Dry-run、失败诊断和版本记录。
- Object/Property/Link/Rule/Event/Permission 的受控建模、引用校验、版本差异、影响预览、独立审阅、发布和恢复。
- LLM 本体/关系/字段映射候选、评估和人工确认；版本化模型/Prompt/Tool 与证据引用。
- 数据-only 增量更新、事件 checkpoint、迟到事件、schema drift、删除/恢复、重放和投影恢复。
- 六类来源族 ERP/MES/SCADA/IoT/文档/图像逐类准入代表样例；未接真实数据时如实记录 fixture/adapter，不冒充连接器。

**交付物：** 一个可复现的来源到 Ontology 发布流程；版本/lineage；质量与 LLM 候选面板；增量与故障恢复合同；逐来源准入记录。

**退出条件：** E2E-01 和 E2E-02 通过；发布前后可还原；重复数据不重复写入；失败范围隔离；schema 变更不会被数据-only 更新静默接受。

### M3：Object Explorer、Object View 与共享可视化工作区

**目标：** 让用户通过真实业务对象完成搜索、关系钻取和上下文保持。

**主要内容：** 全局搜索、筛选、关系导航、pivot、对象集合、保存/比较；表格/图/时间视图；Full Object View 与侧栏 Panel；来源/版本/权限/质量/时间证据面板；跨页深链；响应式布局和一致的加载/错误/无权限状态。

**交付物：** 一组共享 Object View/Explorer 组件和查询接口；页面任务脚本；Wood Study 视觉/功能回归样本。

**退出条件：** E2E-04 的对象发现与钻取子路径通过；同一 ObjectRef 跨页面一致；角色差异由服务端过滤；木材演示没有非预期回归。

### M4：DecisionCase 与唯一 Action Runtime

**目标：** 建立所有业务写入共同的受控预览、审核、执行与审计路径。

**主要内容：** 类型化 Action Definition/Request；Preview 与影响确认；审阅者、审批和过期授权；单对象/批量 ObjectSet；冲突和版本条件；幂等提交；Run/attempt/event 状态；失败恢复、投影对账与补偿；人工覆盖；旧路由迁移；Agent/Scenario 只创建该合同允许的请求。

**交付物：** 统一 Action API 和运行账本；审批 UI；决策案例与执行结果链；审计查询；批量和补偿语义。

**退出条件：** E2E-05 通过；未授权、撤权、过期批准、对象变化和重复请求均不产生未审核副作用；中途故障可恢复或给出明确 outcome-unknown 状态；所有实际写入口完成登记。

### M5：时间语义、Temporal KG、Scenario 与 Digital Twin

**目标：** 把实时/历史对象状态、图、证据、推演和决策结果绑定到同一时间和版本上下文。

**主要内容：** event/valid/system/ingest 时间模型；watermark、历史快照和时间游标；图谱与对象时间视图；向量证据候选；Tracking/Pinned Scenario；branch/checkpoint/compare/rebase/replay；Baseline/B/C 指标、影响链和推荐卡；权限下的来源追踪。

**交付物：** 可重复的时间查询和 What-if 工作面；情景隔离、重放和比较；Supplier B 指标→对象→证据→推荐链；时序完整性提示。

**退出条件：** E2E-03 与 E2E-04 通过；同一输入可重放；观测和模拟分离；数据或证据不足时不作确定建议；Scenario 无绕过 Action 的生产写入。

### M6：可组合的业务应用和运营工作页

**目标：** 让已发布的业务工作面复用 Object、Query、Analysis、Scenario 和 Action，而不是复制每个领域的后端语义。

**主要内容：** 受限应用配置、组件组合、草稿预览、版本发布/撤回、应用权限、对象/指标/趋势/告警/影响链卡片；供应链、设备维护、质量审核等代表工作面；查看器和编辑器状态。

**交付物：** 可复用 2D 工作页组件和应用版本生命周期；目标页面矩阵中的组合/专属工作面。

**退出条件：** 代表应用使用统一服务与权限合同；用户不因页面组合而绕开 Action 或复制本体语义；错误、空数据、加载和拒绝状态可验收。

### M7：Agent Definition、持久 Session、Knowledge 与共享服务

**目标：** 使 AI 能在授权对象和工具范围内持续协助业务决策，并把查询/逻辑作为治理资源复用。

**主要内容：** Agent Definition 的版本、评估和发布；Tool/Function/Query/Skill Registry；持久 Session/Event/Checkpoint；参与者、撤权和人工接管；工作队列与 worker 恢复；Knowledge Base 导入、解析、chunk、索引、权限与引用；混合检索；Agent 跨工作面续接；选定 TS/Python 客户端；Tool/Agent 行为与成本评估。

**交付物：** 有限工具目录、可恢复 Agent 工作台、知识库生命周期、共享 Query/Function 发布入口、评测集和安全矩阵。

**退出条件：** E2E-06 通过；重启后续接不重复执行已完成副作用；权限撤销立即阻止续跑；重要回答有可访问证据；所有写建议经 M4。

### M8：平台治理、运营与生产准备

**目标：** 验证系统在约定数据量、角色、故障与恢复条件下可安全运行和发布。

**主要内容：** Resource Registry；身份/角色/策略管理；项目运营队列和人工接管；耐久 Scheduler/Monitor；统一日志、指标和 trace；限额/告警；P50/P95、吞吐与投影滞后；备份/恢复、迁移/回滚、重启与故障演练；发行版本与配置发布/撤回。

**交付物：** 运维工作面、告警与审计、容量/恢复报告、部署/升级/回滚手册、生产 Gate 证据。

**退出条件：** E2E-07 和 E2E-08 通过；故障后的系统状态、权限边界和恢复过程可解释；性能数据绑定明确环境与数据档位，不用本机演示结果外推生产容量。

## 6. 面向使用者的版本演进规划

以下是产品增量的提议名称，用于沟通范围，不是发布日期或已承诺工期。具体 release tag 在 M0.5 后由双方确定。

| 版本增量 | 覆盖阶段 | 用户可感知的结果 | 退出重点 |
|---|---|---|---|
| R0：Plan A 2.0 基线版 | M0 | 可从指定 SHA 重建已有系统；木材演示和已有模块有状态清单 | 共同基线与验收分母签收 |
| R1：可信 Ontology 与数据流 | M1–M2 | 来源、映射、质量、LLM 候选与本体发布可追溯；支持首批准入来源和增量 | E2E-01/02 |
| R2：业务对象工作台 | M3 | 搜索、对象详情、图和来源证据共享同一 ObjectRef/权限/版本上下文 | Object Explorer 与木材回归 |
| R3：受控决策与情景 | M4–M5 | What-if 和影响分析可比较；建议先预览/审批，再通过统一 Runtime 执行 | E2E-03/04/05 |
| R4：AI 协作工作台 | M6–M7 | 可组合业务页面、持久 Agent 会话、知识检索、工具治理和结果引用 | E2E-06 与应用组合 Gate |
| R5：可运营平台版 | M8 | 资源、策略、队列、调度、观测、容量、备份和发布恢复达到约定门槛 | E2E-07/08 与生产 Gate |

若阶段验收未通过，可以交付范围受限的内部版本，但必须标出缺口；不得只因页面或菜单已出现就称为对应产品版本完成。

## 7. 端到端验收与木材纵向任务

| 场景 | 主路径 | 主要证明 |
|---|---|---|
| E2E-01 数据到 Ontology 发布 | 来源 → DatasetVersion → Workflow/Mapping → 质量 → LLM 候选 → 人工审阅 → OntologyRevision | 输入 pin、候选和审批分离、来源血缘、坏引用拒绝、发布/恢复可重现 |
| E2E-02 数据增量更新 | 已批准映射 → 新数据/事件 → 增量 delta → 对象/关系更新 → 对账/回放 | 幂等、checkpoint、迟到/删除语义、schema drift 独立批准、无静默全量重建 |
| E2E-03 时序查询与证据 | 固定 Object/Replay/时间窗 → 图/时间属性查询 → 语义/向量证据召回 | 同一时间语义和权限上下文；引用指向来源/版本；向量不是事实源 |
| E2E-04 Scenario 与 DecisionCase | 搜索对象 → Baseline/Scenario → 指标/影响链 → 证据/推荐 → DecisionCase/Preview | 固定基线和结果可重放；观测/模拟区分；接受建议不直接写入 |
| E2E-05 Action 生命周期 | Preview → 审批 → Commit/Run → 结果/审计 → 失败恢复/补偿 | 全入口统一授权、幂等、冲突、身份、重试、部分成功及无重复副作用 |
| E2E-06 Agent 会话 | 创建/共享 Session → 获取授权上下文 → 工具/证据 → 暂停/重启/恢复 → Action Preview | 持久化、撤权、checkpoint、工具版本、actor 和 trace；不绕过 Action |
| E2E-07 平台权限 | user/group/service/system 与资源/字段/工具策略组合 → 允许/拒绝/撤权 | 服务端权限一致；计数、搜索、图、向量、错误和导出均不泄漏 |
| E2E-08 运行与恢复 | 任务/队列/投影/服务故障 → 恢复/重试 → 备份还原/升级/回滚 | 健康、性能、容量、告警、恢复目标和审计可复现 |

### Supplier B 纵向集成 Gate

在 M3–M7 的组件分别达到稳定状态后，把 E2E-04、E2E-05、E2E-06 串成同一条用户任务：从 Object Explorer 打开固定 Wood Supplier B；确认同一个 ObjectRef、来源证据和权限上下文；打开 Baseline/Supplier B/Supplier C 并比较；沿指标进入受影响对象和规则依据；生成 DecisionCase 与 Action Preview；Agent 在相同授权上下文下解释建议并引用相同证据；由适当角色审批后运行一次受控 Action；从 ActionRun/Audit 返回原始供应商与数据来源。刷新或恢复 Session 后仍能定位同一个对象、情景、Case 和证据版本。

通过条件是跨页面 ObjectRef、Ontology/Data/ScenarioContext digest、principal/policy scope、DecisionCase、ActionRun 和 evidence lineage 相符；不能在某页悄悄换成同名异 ID、latest 数据或更宽权限。

## 8. 产品比较、页面验收与质量指标

### 8.1 相似度声明边界

本计划不承诺对整个 Foundry/AIP 产品线达到 90% 相似。只对 M0.4 选定的 Project 4 制造/供应链范围、公开可观察且可逐项比较的功能和页面评分。预计页面工作面共 11 类：Ontology Modeler、Source/Data Workflow、Object Explorer、Object View、Graph/Temporal Explorer、分析/影响链、Scenario/What-if/Twin、Decision/Action Center、Explore/Agent/Knowledge、应用组合与 2D 工作页、Governance/Monitor。

### 8.2 分开报告的指标

| 指标 | 规划目标 | 约束 |
|---|---:|---|
| 目标页面覆盖 | 11/11 | 每页必须有一个真实代表任务，菜单或静态 mock 不计 |
| Project 4 Must-have 任务通过率 | ≥90% | P0 与权限/写入安全任务 100% 通过 |
| Palantir 可比功能覆盖率 | ≥90% | M0.4 冻结原子功能、官方来源和权重；按实际 E2E 行为计，不以源码存在计 |
| 直接可比页面相似度 | 平均 ≥90/100；至少 90% 纳入页面单页 ≥90 | P0 页面 ≥90；其余直接可比页 ≥85；只对有逐页官方 UI 参照者计分 |
| 组合参照工作面一致性 | 平均 ≥90/100 | 单独报告，不混入 Palantir 直接页面相似度 |
| Project 4 专属 UX | 平均 ≥90/100 | 单独报告，不伪称与 Palantir 逐页相似 |
| 核心闭环 | E2E-01—08 全通过 | 每条单独报告；P0 与安全路径不允许以平均数抵消失败 |
| 来源/版本/策略追溯 | P0 结果 100% | 不能追溯的结果视为不通过或显式标注不可重放 |

M0.4 冻结全部页面分类、功能分母、来源 URL/核对日、截图条件、权重和评审规则。页面评分候选维度为信息架构/任务层级 25%、布局/密度/响应 25%、核心控件/交互 25%、加载/空/错误/权限/成功状态 15%、视觉层级/可读性 10%；采用前先由双方签收。页面截图需固定角色、视口、数据 fixture、页面状态和代码 SHA。不能通过删除难项、将组合页面归类为不可比或更换分母抬高分数。

功能覆盖率、页面相似度、组合对齐度、Project 4 专属 UX 和本项目任务完成率是不同指标。一个指标通过不能替代另一个；当前计划本身没有任何达标结论。

### 8.3 质量与运行指标

M0.4/M0.5 冻结适用目标后，M8 报告至少包括 P50/P95 延迟、并发档位、吞吐、错误率、队列等待/积压、数据新鲜度与投影延迟、任务恢复时间、备份还原耗时、升级/回滚结果和资源成本。所有数字注明代码 SHA、硬件、配置、数据量、调用角色和试验方法。历史 benchmark 只作参考，不自动证明当前代码或生产容量。

## 9. 数据、基准与演示策略

- **业务演示主线：** 保持 Wood Supplier Study 的 Baseline/B/C 案例为第一条业务闭环；指标和合成数据明确标记，不称作真实供应商事实。
- **时间与制造样例：** FactoryNet 等已存在的时间序列/事件样例可用于验证事件和时态合同，需固定来源、版本、数据规模和构建方式。
- **Ontology/查询基准候选：** LUBM 用于可扩展 RDF/本体查询基准；CQ4OE 与经固定版本/许可核对的 OntoURL 用于 competency question 与本体查询评估。它们是互补的候选，不应把一个 benchmark 当成全产品验收。
- **时序图基准：** TGB 2.0 单独用于时间知识图 link prediction/预测类评测；不得把预测成绩替代 What-if、权限或业务决策验收。
- **其他数据：** EITKG、C-MAPSS、BTS、MES/SCADA/IoT 候选和多模态资料须核实 license、provenance、再分发和任务匹配；未完成核验时不纳入正式分母。
- **复现规则：** 数据集记录来源、版本、许可、下载日期/哈希、预处理脚本、切分、模型和指标；测试/试点优先用可合法分享的样例和 fixture。不能把 fixture 宣称为在线连接器，也不能未经审阅把生成数据写入正式演示库。

## 10. 双人协作与版本治理

### 10.1 建议分工

| 责任 | 开发者 A：语义核心与数据平面 | 开发者 B：运行时与产品工作台 |
|---|---|---|
| 主责 | Ontology/Object/Revision、来源与数据版本、映射/质量/增量、权限与语义查询合同、数据 lineage | Supplier Study/Explorer/应用交互、Scenario/DecisionCase、Action Runtime、Agent/Knowledge UI、运行状态与用户任务体验 |
| 共同 | 架构决策、跨模块 API/状态/权限合同、E2E、数据与风险签收、Wood 回归、发布 Gate | 架构决策、跨模块 API/状态/权限合同、E2E、数据与风险签收、Wood 回归、发布 Gate |

该分工是建议，不预设两位开发者投入相同。每项接口有单一 owner 和 reviewer；涉及权限、身份、版本、跨存储提交与 Action 的改动必须双方评审。

### 10.2 Git 协作规则

1. 保留 plan-a-2.0@7ab1cd1 作为只读、可重建基线。
2. 双方同意本计划后，从准确 SHA 创建新的共享开发分支；分支名由双方确认，并避免与现有本地 palantir 分支冲突。
3. 本地 palantir 变更保持独立；只有被选中且通过验收的改动经小型 PR 移植，不复制整份混合工作树。
4. 每个 PR 对应一个可描述的用户任务、一个主要接口/行为改动和可重现的验证证据；PR 写明迁移影响、权限边界、Wood 演示影响和回滚方法。
5. 数据库 migration 要验证干净升级、适用的回退/防误回退规则、schema 与 ORM 一致性；任何本地数据库状态不能替代迁移验证。
6. 计划文件和代码提交分开记录；合并计划不代表实现开始，合并代码也不自动关闭验收 Gate。

### 10.3 当前协作的下一步

双方首先分别审阅本计划和对方计划，提出范围、基线、分工、用户流程、估算和验收的差异；确认共同版本后，完成 M0.1–M0.5，再建立共享开发分支和首批工作包。此顺序是建议，不要求立即推送现有本地改动或覆盖任何分支。

## 11. 依赖、风险与明确边界

| 风险 | 影响 | 计划控制 |
|---|---|---|
| 代码基线混淆 | 当前本地实验被误当成所有人已有实现，或 Plan A 回归难以定位 | 固定 SHA；M0 干净重建；本地变更逐项移植 |
| 新旧语义/执行双轨 | 查询、权限、Scenario 和 Action 行为不一致 | M1 冻结共享合同；M4 收敛写入口；旧路径只读或适配后退役 |
| 只做页面没有业务闭环 | 页面看起来完整但数据/权限/恢复不可用 | 以 E2E 与真实数据任务验收，静态入口不计完成 |
| 事件时间含义混乱 | 历史查询和 Scenario 结果不可复现 | 统一 typed time contract、watermark、版本 pin 和时间测试 |
| 跨存储状态漂移 | API 结果与图投影或运行状态不一致 | PostgreSQL 权威记录、持久 outbox/reconcile、投影健康与重建 |
| Agent 越权或恢复重做 | 泄露信息、重复副作用 | caller 权限取交集、每次执行重验、lease/checkpoint/fencing，写入只经 Action |
| 来源许可或质量不明 | 计划无法合法分享或结果不可信 | M0 数据准入、许可证/哈希/样例质量记录；必要时改用可分享 fixture |
| 范围膨胀 | 平台模块多而首条流程不能完成 | P0/P1/P2 与 Must-have 冻结；任何新增项绑定用户任务、证据与资源 |
| 过早声称 90% 或生产就绪 | 对外表述超出实际证据 | 分母冻结后分别评分；标出未评分/未运行；M8 绑定真实容量和恢复证据 |

### 本期暂不承诺

- 完整复制 Palantir Foundry/AIP 的所有产品页面、数据连接器或私有系统架构。
- 未经准入的真实 ERP/设备控制写入、无人审查的高权限 AI 脚本或 Agent 直接数据库写入。
- 完整 3D Twin Builder、任意拖拽低代码平台、Marketplace、完整开发者 SDK 生态和全面跨租户 SaaS。
- 以页面数量、功能点文字描述、源码、单次健康检查或历史截图代替端到端验收。
- 在两人实际投入、范围和数据状态未核实前给出确定日历完成日。

## 12. 完成定义与阶段签收

目标产品只有同时满足下列条件才可称为本计划完成：

1. 双方确认的 M0 范围、任务和 11 个工作面矩阵有最终版本；P0 与安全目标没有未决例外。
2. M1–M8 的纳入目标均有实现或经双方签署的范围外决定；每个阶段退出证据可在指定版本和环境中复现。
3. E2E-01 至 E2E-08 达到各自通过条件；Supplier B 纵向集成 Gate 通过。
4. 每项 Must-have、Palantir 可比功能和页面分数按冻结分母单独报告；没有把未纳入、不可比或未运行项计作通过。
5. 权限撤销、隐私/脱敏、幂等、失败恢复、迁移/备份及生产写入路径通过明确的负向和恢复场景。
6. 对全部 P0 结果可追溯到授权范围内的来源、版本、规则/模型和执行记录；无法追溯的结果明确失败或限制展示。
7. 木材演示在批准的数据和页面基线下通过回归；如果只能重建新截图基线，记录这一事实而不声称恢复了历史像素画面。
8. M8 的性能、容量、告警、备份、恢复、升级和回滚报告绑定目标环境、数据档位和代码 SHA；生产结论不从本地 Demo 外推。
9. 未关闭的条件项、P2 项和已知限制有清单、owner、影响、重估触发条件和对外说明。

## 13. 评审后要冻结的决策

以下事项不阻止编写本计划，但进入实施前须由双方在 M0 作出记录：

1. 11 个页面中哪些有充分的 Palantir 直接 UI 参照，哪些为组合参照或 Project 4 专属。
2. 公开功能目录中原子功能的最终分母、Must-have 任务和权重。
3. Tenant/Project/Use-case 隔离的本期范围与主体/策略模型。
4. 是否纳入 Agent-to-Agent handoff，以及它与多人共享 Session、人工接管的边界。
5. 本期可视化 Ontology 高级类型子集。
6. 批量 Action、人工覆盖、人员工作量分配和运营队列的详细角色、交互与验收分母。
7. 六类数据来源各自采用真实连接器、适配器还是 fixture；每条的许可和数据负责人。
8. 各 benchmark 的适用问题、版本、许可、指标和训练/评估切分。
9. 木材演示是否能找到并签收历史截图；若不能，如何批准一个新的视觉基线。
10. 两位开发者的实际投入、职责比例、集成时间、依赖和低/基准/高工期。
11. 本地 palantir 线上哪些改动适合按独立 PR 迁入，哪些保留、重写或放弃。

## 14. 参考与材料解释

本路线图汇总并收敛以下项目内材料：

- Project 4 目标系统主路线图：总体目标、M0–M8、页面和指标验收、生产 Gate。
- Project 4 详细 PRD：FR-01 至 FR-11、跨模块合同、基准候选、双人分工、E2E 和风险。
- PALANTIR_PROJECT4_COMPLEMENT_AND_TARGET_ARCHITECTURE：目标架构与能力参考；不作为直接实现命令或现状证明。
- PROJECT4_SCREEN_AND_TASK_ACCEPTANCE_MATRIX、PROJECT4_PALANTIR_FUNCTION_PARITY_CATALOG、PROJECT4_M0_ACCEPTANCE_SCENARIOS：页面、功能与端到端证据候选，需在 M0 由双方冻结。
- GitHub plan-a-2.0@7ab1cd1：本计划唯一指定的新开发起点。

外部产品资料只支持对公开可观察行为的比较。对厂商宣传或缺少可复核实现的能力，标注为公开描述/待验证；不根据公开页面推断内部实现、安全强度或生产成熟度。

---

**计划状态：** 本文件是从 Plan A 2.0 基线到目标产品的完整提议版。它汇总目标文档并提出路线，不代表任何新增能力已经开发、上传或通过验收。双方评审与 M0 范围签收后，才形成可排期的执行版本。

## 15. 目标能力总目录

本节将主路线图和详细 PRD 中分散的能力整理为可讨论的产品目录。表中“阶段”表示建议落点；M0.4 才会冻结原子项范围、owner、优先级和评分分母。当前可找到源码的条目仍需按 M0 重新验收。

### 15.1 来源、数据和本体建设

| 能力 | 目标功能 | 阶段 | 优先级 | 主要依赖 / 完成观察 |
|---|---|---|---|---|
| 来源目录 | 登记数据库、文件、API、事件流、文档和媒体来源；提供 owner、schema 指纹、凭证引用、健康、刷新方式和权限 | M1–M2 | P0 | 来源信息稳定且秘密不进入日志；连接可测试、可禁用、可审计 |
| 数据集版本 | 对每次导入生成不可变 DatasetVersion，保存 schema、内容摘要、行数/文件清单、来源 offset 或 snapshot 标识 | M1–M2 | P0 | 同一版本可复查；“latest”不用于已排队任务的隐式输入 |
| 连接器与适配器 | 适配已选来源协议并显式标注原生连接器、项目适配器或离线 fixture | M2 | P0 | 每类来源有独立验收用例；不得按连接器数量代替语义映射覆盖 |
| 可视化转换 | 通过受限节点执行 parse、clean、join、map、quality 和 publish；支持配置、预览与差异 | M2 | P0 | WorkflowDefinition 与每次 Run 都有版本和 manifest；节点失败可定位和重试 |
| 数据质量 | 校验完整性、类型、唯一性、引用、时效、质量阈值和异常分布 | M2 | P0 | 发布前显示错误/警告和影响范围；质量规则有版本和结果证据 |
| Ontology Modeler | 编辑 Object Type、Property、Link、Rule、Event、Permission；验证引用、约束和基数 | M2 | P0 | 编辑/审核/发布分离；差异预览、影响对象及恢复入口清晰 |
| 语义类型扩展 | Interface/shared property、ValueType、Struct/field、受限 Derived Property | M2 | 条件 P1 | 按 ONT-05 单项裁决；派生属性使用安全、无副作用、可验证的有限表达式 |
| LLM 建模建议 | 建议类型、属性、关系、映射、约束和 competency questions；给出来源、版本、不确定性 | M2 | P0 | 候选不自动写入已发布 Ontology；模型失败不静默转成成功 |
| 本体发布与恢复 | 草稿、验证、影响预览、审核、版本发布、撤回/恢复和下游兼容检查 | M2 | P0 | 发布记录绑定 Dataset、Mapping、规则和授权；历史版本保持可追溯 |
| 动态增量更新 | 对获批 Mapping 应用数据变更，支持 offset、幂等、迟到事件、checkpoint、回填和恢复 | M2、M5 | P0 | 数据-only 更新不触发重新建模；变更范围及失败对象可查询 |
| Schema drift | 对新字段、类型变化、约束冲突形成独立变更提案与影响预览 | M2 | P0 | 未审批不改变 canonical Ontology；审批后通过明确迁移/回填过程上线 |
| 删除与恢复 | 支持来源行、对象和关系的追加式 tombstone，以及按时间恢复/查询 | M2、M5 | P0 | 不混淆“来源记录删除”和“Ontology 类型定义删除”；历史 as-of 结果可重现 |

### 15.2 对象、查询、分析和页面工作面

| 能力 | 目标功能 | 阶段 | 优先级 | 主要依赖 / 完成观察 |
|---|---|---|---|---|
| Canonical Object Backend | 统一对象身份、属性、链接、聚合和版本化查询入口 | M1–M3 | P0 | SQL 权威事实和图投影的行为可对照；每项查询执行权限投影 |
| Object Query | 以类型化过滤、排序、分页、关系遍历、聚合和查询摘要读业务对象 | M1、M3 | P0 | query digest、data view、ObjectSet 和权限摘要可追踪；禁止任意查询绕过策略 |
| ObjectSet / DataView | 表示一组经查询得出的对象和筛选条件，可在页面、Scenario、Agent 和 Action 间传递 | M1、M3 | P0 | 固定选择快照或明示动态语义；分享不能扩大成员对象/属性权限 |
| Object Explorer | 搜索、筛选、Search Around、Pivot、排序、保存探索、比较和深链 | M3 | P0 | 以业务对象完成任务，无需先知道表名；跨页状态可恢复 |
| Full Object View | 可直接深链到 canonical ObjectRef，显示对象属性、关系、来源、时间、证据和可用操作 | M3 | P0 | 打开、刷新、浏览器导航及跨页返回保持合法上下文 |
| Object Panel | 侧栏查看对象，保留所在图、表格或分析页上下文 | M3 | P1 | 与 Full View 引用同一对象身份和授权；面板关闭后可回到原位置 |
| Graph Explorer | 展示本体关系、可选择对象、边/关系文字、邻域、筛选和查询上下文 | M3、M5 | P0 | 图节点/边遵守对象与关系 ACL；边只能连接当前可见的合法端点 |
| Temporal Explorer | 按明确时间语义查看事件、属性变化、关系有效期和事件顺序 | M5 | P0 | 对象、图、证据、Scenario 的时间指针与 data revision 一致 |
| Analysis / Quiver-like 工作面 | 定义指标、聚合、影响链、过滤、趋势与基线比较 | M5 | P0 | 分析定义有稳定 ID/version，指标结果可追到来源和计算版本 |
| Scenario 工作台 | Tracking/Pinned 视图、情景分支、差异、checkpoint、compare、rebase 和冲突处理 | M5 | P0 | 默认隔离；任何 live 写回进入 Action Runtime |
| DecisionCase 工作面 | 汇总问题、对象、证据、推荐、风险、审核和关联 ActionRun | M4、M5 | P0 | Case 的事实、结论、审批状态和执行状态可区分且可追溯 |
| 应用工作页 | 把已批准 Object View、Query、Analysis、Scenario、Action 和 Agent 模块组合成业务页 | M6 | P1 | 页面引用资源 ID/version 而不复制业务语义；支持草稿、预览、发布、撤回 |
| 木材 Supplier Study | 保留现有研究、Baseline/B/C、图、指标、运行详情和用户熟悉的操作方式 | M0、M3–M5 | P0 | 固定数据与显示结果相符；历史图缺失时只签署新基线，不宣称像素级历史复原 |

### 15.3 执行、Agent、知识和平台运营

| 能力 | 目标功能 | 阶段 | 优先级 | 主要依赖 / 完成观察 |
|---|---|---|---|---|
| Action Definition | 稳定 Action ID/version、类型化输入、约束、权限要求、可逆性与服务端执行定义 | M1、M4 | P0 | 未知参数/效果拒绝；已发布版本不可被并发修改覆盖 |
| Action Preview | 展示目标、前后值、关系影响、条件、授权、版本和 digest | M4 | P0 | 任何批准都绑定当前 preview digest；输入变化必须重新预览/审批 |
| Review / Approval | 区分请求人、审批人、执行者；支持批准、拒绝、过期、撤回和理由 | M4 | P0 | 过期或撤权后提交被拒绝；决定有不可覆盖审计 |
| Action Commit / Run | 把批准的 EditBatch 以幂等方式提交并生成持久运行/状态记录 | M4 | P0 | 同一 request key 不重复副作用；结果明确区分完成、失败、未知、部分完成 |
| Outbox 与投影恢复 | PostgreSQL 记录权威运行事实，工作进程重试/对账图投影和受控外部效果 | M4、M8 | P0 | 投影延迟可观测，能从权威状态重建，重试预算有限且可审计 |
| 批量 Action | 对固定对象集逐对象预览、授权、执行和返回结果 | M4 | P1 / 目标架构必做 | 批量语义、大小上限、部分成功和逐项重试由 M1/M0.4 冻结 |
| 人工覆盖 | 有权人员基于明确理由覆盖某类建议/决策 | M4 | P1 / 目标架构必做 | 覆盖范围受限、可撤销、审批链明确；不能豁免硬性安全约束 |
| Agent Definition | 配置和发布有版本的 Prompt、模型、工具、Query、知识库、范围、预算和评测状态 | M7 | P0 | 运行绑定不可变 Definition 和 Model/Tool 版本；发布、暂停、撤回可审计 |
| Agent Session | 多轮会话及上下文可持久化、跨授权入口续接和恢复 | M7 | P0 | Session ACL 与 caller 数据权限取交集；撤权立即阻断后续访问 |
| Agent Run / Checkpoint | 队列、worker lease、heartbeat、fencing、暂停/取消、恢复和逐工具 attempt | M7 | P0 | 重启后从有效 checkpoint 续跑；过期 worker 无法提交；已成功 Action 不重做 |
| Tool Registry | 类型化工具输入输出、读写级别、授权范围、超时、限额、版本和人工批准策略 | M7 | P0 | 只执行注册且已发布工具；所有调用记录 actor、scope、输入/输出摘要 |
| Agent Manager | 按权限管理 Agent Definition、活动 Session、失败运行、接管、恢复和撤销 | M7 | P1 | 运营者只能管理自身 scope；每项操作有主体、原因、前后状态和审计 |
| Knowledge Base | 文档、解析、Chunk、索引版本、权限继承、刷新/重建/删除和可定位 Evidence | M7 | P1 | 索引可重建；旧权限、旧版本或被删除文件不能经向量召回泄露 |
| Hybrid Retrieval | 组合向量、全文和图邻居候选召回并回到权威来源核验 | M5、M7 | P1 | Evidence 带 source/version/time；相似度不能替代事实或规则判定 |
| Query / Function / Skill 发布 | 一份版本化逻辑供 Explorer、App、API、MCP 和 Agent 复用 | M7 | P1 | 输入输出、权限、超时、分页、兼容和撤回行为一致 |
| TypeScript / Python clients | 为冻结 API 提供选定、类型安全、尊重 caller scope 的客户端 | M7 | P1 | 来自 OpenAPI 或受同步检查；不继承 SDK 作者权限 |
| Resource Registry | 为本体、数据集、Workflow、Query、Action、Scenario、Agent、知识库和 App 提供统一目录 | M8 | P1 | 稳定 ID、owner、scope、ACL、revision、依赖和 lifecycle 与权威对象一致 |
| Principal / Policy | 用户、组、角色、服务和系统主体，以及资源/对象/字段/工具的授权策略 | M1、M8 | P0 | 默认拒绝、scope、撤权缓存失效、系统主体限权和拒绝审计可验收 |
| Schedule / Monitor | 持久任务定义、触发、Run 历史、阈值、告警、暂停、取消与重试 | M8 | P1 | cron/timezone/DST/misfire/overlap 和幂等语义明确；不只保存表达式字符串 |
| Ops Queue / 人员负载 | 展示待审、失败/阻塞、投影异常和 Agent handoff；按权限分派/人工接管 | M8 | P1 / 目标架构必做 | 队列引用权威 Run/Case，不另造业务状态；容量不足或资格未知时回退人工 |
| Platform Observability | readiness、日志、trace、延迟、队列、错误、配额、投影新鲜度和告警 | M8 | P0 | 依赖健康和业务可用性分开；单次 HTTP 200 不代表生产就绪 |
| Backup / Recovery | PostgreSQL/MinIO 备份恢复与图/向量索引重建，记录 RPO/RTO | M8 | P0 | 独立环境演练成功；数据量/摘要一致且结果可查询 |

## 16. M0 规划门展开：进入开发前需要完成什么

M0 是完整规划和基线确认阶段，不是额外的产品功能期。M0.1–M0.5 完成并由双方签收后，才建立新的共享开发分支并正式排 M1–M8。各项输出可并行准备；M0.4 范围冻结依赖 M0.1–M0.3，M0.5 工期依赖 M0.4。

### M0.1：Plan A 2.0 能力、环境与风险盘点

| 审阅主题 | 必须回答的问题 | 交付证据 |
|---|---|---|
| Git 基线 | 干净检出是否指向唯一 SHA；代码是否与公开 plan-a-2.0 分支一致；新分支是否从该 SHA 派生 | refs/SHAs、远程引用、干净工作树清单和固定检查点 |
| 构建与运行 | 安装、compose、环境变量、DB/Redis/FalkorDB/MinIO/Chroma 和前端/后端能否启动；依赖未就绪时是否安全失败 | 可复建命令、服务/版本清单、依赖 readiness 和常见失败处理 |
| 数据库迁移 | Alembic 链是否从新 DB 升级；schema 与 ORM 是否一致；空库和演示数据初始化是否可重复 | fresh DB upgrade/check、必要 downgrade/防误回退证据；不以本机旧数据库替代 |
| 模块路线 | Ontology、mapping、query、graph、Scenario、Supplier Study、DecisionCase、Action、Agent 的当前入口、状态、权限和数据依赖是什么 | 逐项 capability inventory，标“源码、可访问、已跑通、E2E 已过、未验证” |
| 演示完整性 | Wood Study 入口、Baseline/B/C Run、指标、关系图、对象来源及允许操作能否重现 | 稳定操作步骤、数据/Run 标识、健康截图或明确的新基线提案 |
| 写入面 | 哪些 API/UI/worker 能写对象、Scenario、Action、DecisionCase 或外部系统；哪些只是测试/fixture helper | 路由清单、调用者、写目标、权限依赖、幂等/审批状态表 |
| 历史改动候选 | 当前 `palantir` 本地工作树的哪些独立改动可能值得复用；是否有迁移、依赖或演示副作用 | adopted / reconsider / parked 列表和各自源码 SHA；不整体复制 |

**M0.1 退出条件：** 两位开发者能从指定提交重建系统；关键数据路径和服务依赖已确认；源码存在与运行通过分开登记；所有可能写数据的入口、数据库/migration 风险和 Wood 演示状态有负责人和下一步。

### M0.2：页面、任务、角色和视觉基线

对第 18 节列出的 11 个工作面逐页冻结：主要用户、主要业务任务、角色、默认输入、成功状态、空/加载/错误/无权限状态、主要证据、直接/组合/Project 4 专属参照类型、是否计入 Palantir 页面相似度、截图和验收 owner。

**页面证据 manifest**至少记录：应用版本/浏览器、视口和 device scale factor、用户角色、fixture manifest/hash、页面 URL、对象/Scenario/Run 标识、UI 状态、代码 SHA、截图 hash、参考页面 URL/标题/核对日及评审人。

**Wood Study 特别约定：** 先确认历史可复原内容和目标图；健康状态下锁定 Baseline、Supplier B、Supplier C 的同一数据/Run/指标/图/证据关系。历史截图如果找不到，允许业务负责人签一个新的健康基线，但必须标为新建基线；故障页只作诊断，不能当目标。功能回归和像素/布局相似度分别验收。

**M0.2 退出条件：** 11 个工作面都可映射到可执行用户任务；每页有真实权限状态和成功结果；视觉 rubric、采集环境与页面类别由双方签收；Wood Study 的目标状态和截图处理方式书面确认。

### M0.3：E2E 任务、数据和许可准入

为 E2E-01 至 E2E-08 分别冻结主角色、初始状态、输入版本、操作序列、预期结果、拒绝/失败路径、重放命令、日志/证据位置和 owner。数据侧逐类审查 ERP/MES/SCADA/IoT/文档/图像候选的来源、版本、hash、license、PII/商业限制、预处理、规模及 fixture 是否可分享。

**数据准入原则：**

- Wood fixture 用于产品演示和固定回归，合成 profile 和单位必须在 UI/文档中说明。
- FactoryNet bounded fixture 用于事件/时间/生产工作流回归；锁定 release、采样方式和来源 hash。
- CQ4OE/OntoURL/LUBM 分别评估 Ontology/问题映射和规模查询；固定版本与评分实现。
- TGB 2.0 单独评估 temporal link prediction，不代替业务 What-if 或决策质量。
- 所有候选数据集先核 license/再分发条件；不能因出现在目标文件清单中就视为已获准使用。

**M0.3 退出条件：** 每条 E2E 可用固定 fixture 和角色重放；每种来源族注明真实连接器/adapter/fixture/尚未准入；成功、拒绝、故障与恢复结果都有可核对的预期；外部数据使用合规边界清楚。

### M0.4：候选范围、相似度和决策卡签收

功能对照目录里曾形成约 170 个候选原子项、覆盖约 38 个父项的盘点快照。该数量受目录版本影响，仅作审阅清单，不是本计划正式承诺或正式分母。双方要以 Plan A 2.0 干净基线重新逐项确认，去重并保留排除理由；父项只汇总子项，不能重复计分。

分别签署以下结果分母：

1. 本项目 Must-have 用户任务和 P0/P1/P2 权重。
2. Palantir 公开、可逐项比较的功能行为及官方依据。
3. 直接可比页面相似度的页面集合。
4. 组合参照工作面的产品对齐度。
5. Project 4 专属页面的 UX 质量。
6. E2E-01—08、跨来源追溯、安全/拒绝和木材回归门槛。

签署目标页面矩阵和原子功能目录；目标架构要求的批量 Action、人为覆盖、运营队列与负载建议必须有单独裁决，不能因缺少直接 Palantir 同名功能被悄悄删去。

#### GOV-06：治理隔离范围决策卡

| 选项 | 范围 | 收益与成本 | 必需的验收声明 |
|---|---|---|---|
| A 单项目 | 本期只验证单项目资源权限；租户隔离明确列为长期缺口 | 初期较轻；未来 tenant-aware 迁移可能更大 | E2E-07 不得宣称多租户通过 |
| B tenant-aware 合同、单 tenant 部署（建议讨论） | M1 数据合同支持 tenant scope；首期只部署一个 tenant；M8 对两个 tenant 做跨范围隔离验收 | 当前投入与未来扩展折中；需要 M0.5 估迁移和跨服务传播 | 两个 tenant 的 list/read/search/graph/vector/session/queue/Action/Mapping 做零泄漏/零副作用矩阵后才可声明隔离 |
| C 首期完整多租户运营面 | 加组织/租户生命周期、委派、管理员 UI、队列/配额和恢复 | 范围最大、工期与运维负担显著增加 | M0.5 单列评估，完整跨租户数据面和控制面验收 |

无论选择哪项，普通用户、服务主体与 system worker 都必须有显式身份和 scope；不能把全局 ID 或前端菜单隐藏视为租户边界。

#### AGT-05：Agent-to-Agent 决策卡

| 选项 | 范围 | 必需条件 |
|---|---|---|
| A 延后 | 本期单 Agent、多人 Session、人工接管；多 Agent 以后独立立项 | 不把 Session 分享称为 agent handoff；开放缺口列入目标记录 |
| B 受限 handoff | 仅支持顺序式、有界 Agent 子任务，不做无界 fan-out/自治循环 | 定义父子 Run/Trace、定义版本、权限交集、预算/跳数/循环限制、取消/恢复、证据传递和 Action 边界；纳入 M7/E2E-06 并重估 |

两者均需区分 Session participant、人工 operator 和另一个 Agent 的执行身份；一个主体的授权不能被委派过程扩大。

#### ONT-05：高级 Ontology 类型决策卡

| 选项 | 范围 | 必需条件 |
|---|---|---|
| A 基础类型 | Object/Property/Link/Rule/Event/Permission；高级类型列为开放项 | 不把高级类型模型字段计作编辑器功能通过 |
| B 有限子集（建议讨论） | Interface/shared property、ValueType、Struct/field、受限 Derived Property 分别验收 | 每类独立版本、diff、影响预览、发布、恢复及 Query/Mapping 消费；Derived Property 只用无副作用表达式，依赖无环 |
| C 通用类型/表达式系统 | 任意类型组合和函数/表达式生态 | 需要额外的沙箱、兼容、权限、测试和发布计划；另行估算，不继承 B 的预算 |

#### 其他 M0.4 决策

- Scenario 的 Tracking/Pinned 生命周期、TTL、rebase cadence、冲突解决和写回权限；主参考行为可供比较，但具体默认值按项目数据和业务风险冻结。
- Workflow 节点集及哪些代码/Prompt 节点允许用户自定义；未审核生成逻辑不能运行于高权限进程。
- 批量 Action 的原子/逐项部分成功策略、大小限制和人工重试预算。
- 工作量分配是否自动推荐，所需资格/容量数据是否存在；缺数据时回退人工。
- 公开 Palantir 页面参照的可验证程度和木材演示视觉基线来源。

### M0.5：估算、依赖、投入和 Go/No-Go

按照 M0.1—M0.4 的签收范围，拆分每阶段的 owner 时间、设计/实现/集成/复验/缓冲，标明能否并行、外部数据许可依赖、基础设施依赖、迁移风险和演示/产品切换风险。输出低/基准/高三档估算及置信度，而不是一个没有边界的“还要几周”。

主路线图和详细 PRD 中曾存在约 15–22 周与 16–23 周的旧阶段区间；这些数字所用功能范围和统计口径不同，而且不包括完整 M0、许可等待、集成缓冲和返工。本计划不直接继承为项目承诺。M0.5 需要基于实际人员投入重新估算；未完成前没有可信的完成日期。

**M0 完成包：** Plan A 2.0 baseline manifest、能力/证据矩阵、Wood Study 验收/截图方案、11 页页面矩阵、E2E 与数据准入 manifest、正式功能分母、三个决策卡结果、阶段预算/依赖图、风险清单和双方签收记录。

## 17. M1–M8 工作包与阶段依赖

本节细化第 5 节各阶段，但保持在产品工作包粒度。每个工作包都须挂到实际 PR/提交、接口合同或 E2E；表中的 A/B 是职责建议，不要求同步开发所有子项。

### M1：共享语义、身份和结果合同

| 工作包 | 主要内容 | 主责建议 | 交付物 / 阶段证据 |
|---|---|---|---|
| M1.1 ResourceRef 与 ObjectRef | 稳定资源 kind/id、对象类型/对象 ID、归属、URI/deep-link 和适配旧 ID | A | canonical schema、兼容映射、唯一性/不存在/权限测试 |
| M1.2 Revision 与快照 | Ontology、Dataset、Workflow、Query、Function、Scenario、Action、Agent、Knowledge Base、App 的版本/生命周期 | A | revision 规则、不可变版本合同、diff 和回滚策略 |
| M1.3 Principal 与 Policy | user/group/service/system principal、scope、默认拒绝、政策版本、撤权即时生效 | A | 权限矩阵与 API 决策摘要合同；系统主体不会冒充请求人 |
| M1.4 ExecutionContext / ScenarioContext | 绑定本体、数据、时间、对象集、情景 branch/checkpoint、caller、policy、evidence 和 trace | A+B | 跨模块 JSON/schema、稳定摘要算法、旧路由迁移/适配表 |
| M1.5 ResultManifest | 统一表达来源、版本、查询/计算定义、筛选 digest、权限裁剪、freshness、输出摘要和 trace | A+B | UI/API 共同消费的样例；无权字段不会通过 manifest 泄漏 |
| M1.6 事件和错误合同 | 错误类型、部分失败、重试、幂等键、超时、取消、状态转换、系统 actor | B+A | API 约定、前端错误状态和后台 Run 状态机 |
| M1.7 兼容与合同 Gate | 识别重复 Scenario、query、Action、Agent 入口，决定 canonical 路由和兼容边界 | 共同 | 旧入口保留/适配/只读/退役计划；跨入口契约样例 |

**M1 退出 Gate：** 至少选取一个只读 Query/ObjectSet 和一个仅 Preview 的 Action 薄片，从 API 到 UI 验证对象、版本、caller、权限和 ResultManifest 一致；共享 schema 不再存在未解释的并行定义。

### M2：数据平面、Ontology Modeler 和增量更新

| 工作包 | 主要内容 | 主责建议 | 交付物 / 阶段证据 |
|---|---|---|---|
| M2.1 来源与快照 | Source Registry、连接健康、凭证引用、schema fingerprint、DatasetVersion 和内容/行/文件摘要 | A | 来源目录、版本选择器、数据来源 manifest |
| M2.2 传输模式 | 关系型/表格/API；watermark/event ID 时间流；文档/图像 manifest 三类公共适配模式 | A | 六个业务来源族独立映射到这些模式；清楚标 real connector/adapter/fixture |
| M2.3 Workflow/Run | Workflow/Node 定义与版本、持久 Run、步骤状态、权限、日志、取消、重试、checkpoint | A+B | 页面画布/配置与后端 Run 共用同一状态；刷新可恢复运行详情 |
| M2.4 Ontology Modeler | Object/Property/Link/Rule/Event/Permission 建模；高级类型按 ONT-05 | A | 建模器、校验、引用/端点验证、草稿、diff、影响预览 |
| M2.5 LLM Candidate | Source/Dataset/Model/Prompt/Tool/parser/normalizer 版本 pin 和候选证据 | A+B | 候选、拒绝、人工修改和模型失败的记录；不直接 publish |
| M2.6 Quality/Review/Publish | 数据质量、mapping 审查、本体独立审批、版本发布与恢复 | A+B | 质量门与审批人分离；发布只写入批准 revision |
| M2.7 Incremental Apply | 复用已批准 Mapping；区分新增/更新/删除；offset、幂等键、水位线和回填 | A | E2E-02 增量 Run/attempt/delta 账本 |
| M2.8 Projection/Reconciliation | 权威事实入库、派生图投影、有限自动重试、人工重放和 dead-letter | A+B | 单对象失败隔离；人工重放需理由/权限/独立预算；重建一致 |
| M2.9 六类来源验收 | ERP、MES、SCADA、IoT、文档、图像分别选样和验证来源到语义对象 | A+B | 来源族覆盖报告、许可/质量/限制；不能把一个传输适配器算六个真实连接器 |

**M2 退出 Gate：** E2E-01 与 E2E-02 通过；Source→DatasetVersion→Workflow/Mapping→Quality→Ontology Revision→Object 的血缘完整；候选审批与正式发布分离；增量重放不重复写，schema drift 不隐式改本体。

### M3：Explorer 与标准对象体验

| 工作包 | 主要内容 | 主责建议 | 交付物 / 阶段证据 |
|---|---|---|---|
| M3.1 Search / Filter | 按对象类型、属性、关系和权限搜索；稳定排序/分页/空结果/错误 | B，A 提供 Query | 真实 API 驱动的搜索；query/filter digest 和权限一致 |
| M3.2 Search Around / Pivot | 沿 Link Type 导航到目标对象集，显示路径和条件 | B+A | 从当前对象可解释地沿链接钻取/返回；不将同名关系错误合并 |
| M3.3 ObjectSet / Saved View | 保存筛选/结果集、重复打开、比较和受控分享 | A+B | 保存的数据 view 不扩大对象/字段授权；固定/动态结果语义清楚 |
| M3.4 Full Object View / Panel | 统一属性、关系、来源、历史、证据、相关 Action 和分析入口 | B | Full URL、侧栏 Panel 和页面 handoff 共用同一 ObjectRef |
| M3.5 Graph / Timeline Surface | 节点/边筛选、时间选择、关系标签显示、selection 联动 | B+A | 节点/边数量和对象/API 一致；边不能泄漏隐藏端点 |
| M3.6 Context Recovery | 深链、新标签、刷新、前进/后退和跨来源返回 | B+A | ObjectRef、ObjectSet、Scenario、时间和可见权限按合同恢复 |
| M3.7 Workbench States | Loading、empty、stale、partial、unauthorized、error、retry、success | B | 关键页面有一致的状态文案和操作；不以空数据冒充成功 |

**M3 退出 Gate：** 用户用真实工作任务从业务问题找到并打开对象、Pivot 到相关对象、查看可访问来源和版本；Full/Panel/图/表的上下文一致；分享视图不扩大权限。

### M4：Action Runtime 与 Case

| 工作包 | 主要内容 | 主责建议 | 交付物 / 阶段证据 |
|---|---|---|---|
| M4.1 写入口地图 | 盘点 UI、API、Scenario、DecisionCase、Wood、Agent 和后台的全部 write path | B，A 验证 data write | 每条入口标定 source principal、target、审批、旧/fixture 状态 |
| M4.2 Action Definition/Compiler | 类型化 Action 与约束，解析请求生成 EditBatch | B+A | 只处理白名单字段/效果；未知输入、未知关系、未知目标失败关闭 |
| M4.3 Preview 与 Digest | 对象、修改前后值、边影响、目标版本、权限和冲突预览 | B+A | digest 固定输入和结果；审核后 payload/权限/版本变化必须失效 |
| M4.4 Review / Approval | 审批角色、理由、拒绝/过期、撤权、人工覆盖和审计 | B | 审批人不能被请求人字段伪造；安全规则不能被一般人工覆盖 |
| M4.5 Commit / Idempotency | 同一 request key 单一逻辑 Run；并发 ETag/revision；批量逐对象结果 | B+A | 重复请求复用结果/Run；版本冲突明确；部分成功合同可追踪 |
| M4.6 Cross-store Recovery | PostgreSQL authority、outbox、FalkorDB rebuild、reconciliation、补偿 | A+B | 事务窗口异常/进程退出测试；未知结果状态可查；无重复副作用 |
| M4.7 旧路径收敛 | 迁移、适配或退役 Scenario merge/Action endpoints/Agent confirmed route | B+A | 每个实际 HTTP 写路由走统一 ActionRequest；旧路由不绕过权限/审批 |
| M4.8 DecisionCase UI | 关联业务问题、证据、规则、Preview、审批和结果 | B | Case 与 ActionRun/输入/审批人/执行人有可回溯关联 |

**M4 退出 Gate：** E2E-05 通过。安全拒绝、撤权、并发冲突、故障窗口、重复请求、超时未知结果、补偿和人工重放均有覆盖；自动重试和人工尝试账本分别保留。

### M5：时间、Scenario、分析与 Digital Twin

| 工作包 | 主要内容 | 主责建议 | 交付物 / 阶段证据 |
|---|---|---|---|
| M5.1 Typed Time Model | event/valid/ingest/system time、timezone、precision、watermark、ordinal | A | 强类型 ScenarioContext 和查询 API；UI 不混用序号/时间戳 |
| M5.2 Temporal Facts/Graph | 历史对象属性、Link 有效期、事件时间线、tombstone/restore | A | current/as-of 对照、端点约束和事件来源可复查 |
| M5.3 Scenario Lifecycle | Tracking/Pinned、baseline snapshot、TTL、rebase、clone、branch、checkpoint | A+B | 输入固定、期限/更新语义透明、冲突处理有授权/审计 |
| M5.4 Compare / Replay | 基线和情景 KPl、对象差异、逐步执行/重放、故障恢复 | A+B | 相同快照可得相同比较；运行失败和历史状态可查 |
| M5.5 Analysis / Impact Chain | 指标定义、规则、对象传播、聚合和供应商影响链 | A+B | 每个指标/边有定义版本和 source/evidence；未知链路不补猜 |
| M5.6 Evidence Retrieval | 向量、全文、图邻居混合候选与权威来源复核 | A | ACL 前/后过滤、引用版本、索引修订和删除语义 |
| M5.7 Wood Supplier B Decision Card | 按签收规则生成建议/备选/权衡/风险/数据不足状态 | B+A | 输入、规则、metric/analysis version 和输出 digest 显示；不足时显式拒答 |
| M5.8 2D Twin Surface | 趋势、图、状态、Run、时间游标和 compare | B | 观测/推断/模拟/批准状态分离；3D builder 不作为 Gate |

**M5 退出 Gate：** E2E-03 与 E2E-04 通过；Scenario、图、对象查询和证据使用同一 revision/time/policy；推荐能定位到依据并仅创建 Case/Action Preview。

### M6：受限应用组合层

| 工作包 | 主要内容 | 主责建议 | 交付物 / 阶段证据 |
|---|---|---|---|
| M6.1 可复用模块目录 | Object View、Query、Analysis、Scenario、Action、Agent 和 Evidence 模块注册 | B+A | 模块携带类型、resource ID/version、权限和必要上下文 |
| M6.2 页面配置 | 绑定对象集、过滤、参数、指标、布局和用户角色 | B | 只配置已发布资源，不在 UI 画布中暗藏业务执行规则 |
| M6.3 Draft/Preview/Publish | 应用草稿、预览、验证、审核、发布、撤回和回滚 | B+A | 页面版本不可变；依赖变更可提示影响 |
| M6.4 工作页模板 | 供应链、设备维护、质量审核 2D 工作页 | B | 三类工作面复用同一业务组件和权限/Action 语义 |
| M6.5 用户运营状态 | 公布加载、运行、stale、错误、告警、责任人与更新时间 | B+A | 运营视图消费权威 Run/Monitor 状态，不复制任务状态 |

**M6 退出 Gate：** 至少三类代表性页面由共享模块组合而成；预览、发布、撤回、权限和错误状态可验证；复用证据可从 resource dependency 清单确认。

### M7：Agent、知识库和可复用能力发布

| 工作包 | 主要内容 | 主责建议 | 交付物 / 阶段证据 |
|---|---|---|---|
| M7.1 Agent Definition | 草稿、配置验证、离线评测、批准、发布、撤回、版本回滚 | B，A 提供 semantic/ACL | 不可变定义 pin；模型/Prompt/工具/知识库版本确定；秘密只用凭证引用 |
| M7.2 Shared Session | Owner/participant/operator、邀请/撤权、共享上下文和跨应用入口 | B+A | 权限取交集；撤权对读、续跑、工具和 Action 生效 |
| M7.3 Durable Run | Queue/worker、lease、fencing、checkpoint、tool attempts、pause/cancel/recovery | B+A | 崩溃/重复投递不丢 Run 或重复副作用；旧 worker 不能提交 |
| M7.4 Tool Registry | typed query/logic/tool、读写声明、范围、版本、超时、预算、approval | B+A | Agent 只调用发布工具；执行期和恢复期重新授权 |
| M7.5 Knowledge Base | Document/Chunk/IndexRevision、解析、权限、更新/删除/重建 | A，B 建工作台 | 引用可打开/定位；删除/撤权后旧索引不泄漏 |
| M7.6 Query/Function/Skill Publish | 输入输出、版本、评估、批准、撤回，绑定 UI/API/MCP/Agent | A+B | 相同定义在多个调用面行为一致 |
| M7.7 Manager / Evaluation | Agent/Session 列表、查看 trace、接管、配额、运行质量/成本评估 | B+A | scoped operator；模型质量和平台可靠性分开评分 |
| M7.8 API Clients | 为 M0 冻结接口生成受限 TS/Python typed clients | B+A | OpenAPI 同步；客户端不携带服务端或作者权限 |
| M7.9 Agent Handoff（条件） | 父子任务、有限委派和上下文/证据传递 | B+A | 只有 M0.4 选择 AGT-05=B 时实施；强制预算、循环/跳数上限和权限交集 |

**M7 退出 Gate：** E2E-06 通过；Session 和 Definition 生命周期完整；跨应用续接、会话分享与撤权有真实 HTTP/浏览器证据；Agent 结论带引用，写入不离开 M4。

### M8：平台控制面、调度和生产准备

| 工作包 | 主要内容 | 主责建议 | 交付物 / 阶段证据 |
|---|---|---|---|
| M8.1 Resource Registry | 核心资源目录、owner/steward、scope、version、依赖、状态、保留期 | A+B | 对象资源目录与领域 API 对齐；访问和管理按 scope 过滤 |
| M8.2 Identity / Policy UI | 用户、组、角色、service/system principal、策略变更预览/撤销 | A 模型，B UI | 权限决策跨入口一致、拒绝有原因、撤权及时生效 |
| M8.3 Scheduler / Run History | 持久调度、timezone/DST、misfire/overlap、run pin、恢复和取消 | A+B | Beat/worker 重启、重复 tick 和失败可重放；cron 字符串不是唯一证据 |
| M8.4 Ops Queue / Workload | 审批、失败/阻塞 Run、Agent handoff、投影异常及授权人工分派 | B+A | 队列项只投影权威 Case/Run/Session；分派/接管留 actor 与理由 |
| M8.5 Observability / Limits | readiness、trace、metrics、队列、投影滞后、资源使用、配额/告警 | A 服务，B 工作台 | 真实依赖和业务任务状态可区分；SLO 绑定环境和时间窗口 |
| M8.6 Backup/Recovery | PostgreSQL/MinIO 恢复、FalkorDB/Chroma 重建、migration/rollback | A+B | 隔离环境恢复，校验行数/hash/关键 query；真实记录 RPO/RTO |
| M8.7 Load/Failure Gate | 交互查询、source-to-mapping、temporal、batch Action、Agent 与队列容量 | A+B | 固定 seed/输入/环境/并发；冷启动和稳态；每档重复运行并检查正确性/ACL |
| M8.8 Release Gate | 生产风险、操作手册、发布/撤回、备份/回滚、审计保留 | 共同 | 负责人接受残余风险；E2E-08/G5 通过才可称为生产准备完成 |

**M8 退出 Gate：** E2E-07/08 通过，资源、身份、审计、调度、运营队列、可观测性、限额、备份和故障恢复有可复现证据；未签收 SLO 或未完成跨范围权限矩阵时不得声称生产就绪。

### 17.1 阶段依赖和可并行边界

| 依赖 | 允许的并行方式 | 禁止的并行方式 |
|---|---|---|
| M0 → M1 | M0.2 页面参照和 M0.3 数据/许可可并行盘点 | 范围分母、数据许可未冻结就承诺正式功能覆盖率/日期 |
| M1 → M2/M3 | 合同初步冻结后，A 可推进数据/本体，B 可推进 Explorer 交互 | 两边各自定义 ObjectRef、权限、版本或 ResultManifest |
| M2 → M5/M7 | M2 的来源/版本/证据合同稳定后，M5/M7 可建立受限只读集成样例 | Scenario/Agent 绑定 mutable latest 输入或私有查询模型 |
| M4 → M5/M7 | Action Preview 合同完成后并行接入 Scenario/Agent 写提案 | 在 Action Runtime 未定前从 Scenario 或 Agent 实现直接写入 |
| M3/M4/M5 → M6 | 已发布组件的输入输出稳定后构建页面组合 | M6 复制一套数据/权限/Action 逻辑来赶页面数量 |
| M1/M4/M5 → M7 | Agent Definition 和只读工具可先做；受控写入等 M4 | 模型调用使用 Agent author 权限或跳过审批 |
| M1/M4/M7 → M8 | 可先约定 Resource Registry/metrics 字段；最终 Ops Gate 等代表路径存在 | 只做运营列表但底层 Run/Case 状态不是权威来源 |

## 18. 关键跨模块接口与数据合同

| 合同 | 必须说明的内容 | 主要消费者 | 冻结阶段 |
|---|---|---|---|
| ResourceRef | resource kind/id、scope、owner/steward、revision、lifecycle、dependency | 管理、分享、审计、应用和 API | M1 |
| ObjectRef | Ontology ID/revision、Object Type ID、Object ID、稳定 URI 和可授权显示标识 | Explorer、Graph、Scenario、Agent、Action | M1 |
| DatasetVersion | source ID、精确 version/snapshot、schema/content hash、采样/offset/完整性标记 | Mapping、Workflow、Scenario、Evidence、Agent | M1/M2 |
| PrincipalContext | caller、service/system actor、delegation、role/group、scope、policy decision version/digest | 所有读写路由、worker 和 SDK | M1 |
| ScenarioContext | Ontology/schema revision、DataModelSnapshot、DatasetVersion/hash、时间类型/值/时区/watermark、branch/checkpoint、principal/policy、query/evidence digest | Graph、What-if、Analysis、Agent、Action | M1/M5 |
| ResultManifest | sources/versions、query/function/metric version、filter digest、ObjectSet/DataView、time range、permission trim、freshness/run status、output digest、trace | UI、API、导出和审计 | M1 |
| WorkflowRun | workflow version、node config/code/prompt digest、input pins、output pins、caller/policy、状态/attempt/checkpoint | Pipeline、Mapping、quality、发布和重试 | M1/M2 |
| ActionRequest | action definition version、目标 ObjectSet snapshot/digest、参数、expected revision、caller、policy/permission digest、scenario context、idempotency key | UI、API、Scenario、DecisionCase、Agent | M1/M4 |
| ActionRun / EditBatch | preview digest、批准、执行 actor、每个目标结果、状态迁移、错误/unknown effect、投影和补偿信息 | Audit、Ops Queue、Scenario、Agent | M4 |
| AgentRun | Agent Definition/ModelConfig revision、非秘密指纹、模型 artifact 可验证版本、Prompt/Tool/Knowledge pins、caller/participant、预算、lease generation | Session、Manager、worker、audit | M1/M7 |
| EvidenceRef | source/document/object ID、revision、locator、time、permission scope、index revision、content digest | Query、Scenario、DecisionCase、Agent | M1/M5/M7 |

任何内容引用只能在服务端权限确认后返回可见字段。权限 digest 便于判断输入是否改变，不能反向替代每次请求的授权校验。凭证本体从不序列化到这些 manifest；只保存安全引用或版本指纹。

## 19. E2E 验收剧本展开

### E2E-01：数据到 Ontology 发布

- **准备：** 具备来源管理员和本体编辑者；选定一个许可清楚、schema 可校验的结构化来源和固定 DatasetVersion。
- **操作：** 登记来源 → 预览 schema → 建 Workflow/Mapping → 生成 LLM 候选（若模型在本地/免费测试环境可用）→ 质量验证 → 人工审核 → 独立发布 Ontology revision。
- **成功检查：** 每个输出指向 source/version、transform/mapping、prompt/model/tool、审阅人、质量结果和新 revision；打开对象可定位源行/文件证据。
- **失败/拒绝：** 模型超时或输出非法、质量门失败、权限不足、引用不存在、审批后输入变化；应停止发布并留下可理解状态。
- **重放：** 使用同一输入与 Workflow revision 再跑，结果摘要一致或明确说明非确定性。

### E2E-02：已批准 Mapping 上的数据增量

- **准备：** 对同一 mapping/schema revision 固定 v1 数据和 offset；准备重复事件、迟到事件、一次合法更新和一个 schema drift 候选。
- **操作：** 应用 v1 → 增加 v2 → 重投一次相同事件 → 注入合法修改/删除 → 触发投影恢复。
- **成功检查：** 数据-only update 沿用 mapping ID/digest，不产生本体建模调用；变化列表、offset、attempt 和投影状态完整。
- **失败/拒绝：** 相同事件 ID 不同 payload、完整快照不完整、未审核 schema drift；拒绝并说明原因，不能静默删除/重建。
- **恢复：** 失败对象有限重试/进入终态，授权人工重放不覆盖自动历史；当前态与 as-of 结果符合删除/恢复约定。

### E2E-03：Temporal Query 与 Evidence Retrieval

- **准备：** 固定具有明确 event/valid/ingest 时间和来源引用的时间数据，至少包含权限可见和不可见对象。
- **操作：** 选对象/时间范围 → 看对象属性/关系快照 → 检索向量/全文/图邻居证据 → 打开来源。
- **成功检查：** 同一时间上下文贯穿查询和检索；结果标时间类型/时区、水位和索引版本；证据可回到权威来源。
- **失败/拒绝：** 不存在的时间点、过期快照、撤权、索引过期、证据被删除；无结果或拒绝态清楚，不返回隐藏对象/片段。

### E2E-04：Scenario、指标和 DecisionCase

- **准备：** 固定 Ontology revision、DatasetVersion、Scenario baseline、经签收的规则与角色。
- **操作：** 从 Object Explorer 找 Supplier B → 打开 Baseline/B/C → 调整一个授权参数 → Run/Compare → 钻取受影响订单/物料/供应商 → 检查建议 → 生成 DecisionCase/Action Preview。
- **成功检查：** 比较结果有版本、指标定义、数据、规则、对象集合和 evidence 链；观测/模拟区分；推荐限制在确定规则允许的范围。
- **拒绝态：** 规则未签收、数据缺失/过期、权限裁剪导致证据不完整时显示“无法形成推荐”及理由。
- **安全边界：** Scenario run 不写 live 数据；通过接受建议也只生成 Case/Preview。

### E2E-05：统一 Action Preview 到审计/恢复

- **准备：** 一个可预览写操作，一个有权审批人，一个可撤权/冲突路径和一个故障注入环境。
- **操作：** Preview → 检查目标/差异/条件 → 审核 → Commit/Run → 查看结果/审计 → 对可补偿动作执行新的补偿 Run。
- **成功检查：** ActionRun/ChangeSet/ScenarioRevision 的权威状态可解释；所有写入路径使用统一 request；成功结果包含实际 actor 和 policy digest。
- **负向：** 审批后目标变化、权限撤销、重复 request key、未知 effect、并发修改、worker timeout；不写或呈 outcome unknown，不悄悄返回成功。
- **恢复：** 对数据库提交与图投影、Scenario merge、重试和人工重放的窗口执行恢复用例；禁止重复副作用。

### E2E-06：Agent 发布、Session 分享与恢复

- **准备：** 已发布 Agent Definition、受限只读工具、受邀 viewer、非成员、可撤权主体和领域证据。
- **操作：** 运行对话 → 查看证据 → 分享 Session → viewer 尝试查看 → owner 撤权 → 追加 turn/工具调用 → 重启 worker → 恢复 Session → 请求一个写建议。
- **成功检查：** Run pin 住 Definition/Model/Prompt/Tool/Knowledge/Ontology/Data 版本；输出引用同一上下文；Session event/checkpoint 顺序可重建。
- **拒绝/恢复：** 撤权后操作被拒绝；worker 重启从有效 checkpoint 继续；旧 lease worker 不能写；写建议只生成 DecisionCase/Action Preview。
- **评测：** 工具选择、证据引用、越权拒绝、人工批准、恢复和成本分别计量；模型回答质量不能代替 runtime gate。

### E2E-07：权限、撤权与资源隔离

- **准备：** user/group/service/system 四类主体（项目范围获批时），至少两个授权 scope 和多个对象/字段/关系/evidence。
- **操作：** 在 Explorer、Graph、Vector/Knowledge、Scenario、Agent Session、Workflow、Action、Queue 和 API/MCP 逐面验证允许、拒绝与撤权。
- **成功检查：** 后端决定一致；隐藏对象/字段不从总数、报错、引用、日志或相似召回泄漏；system worker 只执行已登记 scope。
- **如果 GOV-06=B/C：** 加两个 tenant/project scope 的跨入口隔离、撤权和运营队列认领检查；无“只测列表接口”就宣称租户隔离。

### E2E-08：生产准备、运行容量和灾备

- **准备：** M0.5 锁定的硬件、S/M/L 代表数据档、权限角色、并发和读写 mix。
- **操作：** 交互查询/图、Source-to-Workflow、temporal ingest、向量检索、批量 Action、Agent Session/Tool、队列/投影在固定条件下运行；执行服务/worker/依赖故障、备份还原、升级和回滚。
- **成功检查：** 冷启动和稳态分别记录 P50/P95、吞吐、错误率、队列等待、CPU/内存、数据库连接、投影滞后、结果/ACL 正确性和恢复时间。
- **复现：** 固定 seed、fixture/hash、代码 SHA、镜像/依赖、配置、测量窗口和重复次数；稳态每档至少三次，绝对阈值由项目负责人在 M0.5 签收。

## 20. 十一类页面工作面规划

| 页面 / 工作面 | 代表用户问题 | 主要功能 | 关键验收状态 | 主要阶段 |
|---|---|---|---|---|
| 1 Ontology Modeler | “这条关系和属性是否符合业务语义？” | 对象/关系/规则/事件/权限编辑、校验、diff、影响预览、发布 | 合法草稿、引用错误、审批拒绝、发布、恢复 | M2 |
| 2 Source/Data Workflow | “这份数据如何变成业务对象？” | Source、Dataset、Transform、Mapping、Quality、LLM 候选、运行历史 | 预览、质量失败、模型不可用、人工审核、重放 | M2 |
| 3 Object Explorer | “哪些供应商或设备符合条件？” | 全局搜索、属性过滤、关系搜索、Pivot、保存/比较 ObjectSet | 有结果、无结果、数据过期、无权限、部分结果 | M3 |
| 4 Full/Panel Object View | “这个对象是什么、来自哪里、关联什么？” | 属性、链接、事件、时间、来源、证据、Case/Action | 深链、刷新、历史对象、字段裁剪、失效链接 | M3 |
| 5 Graph/Temporal Explorer | “对象之间如何关联，过去某时刻状态如何？” | 图邻域、关系显示、时间游标、事件序列、有效期 | 图空/错误、隐藏端点、as-of、重放、过期快照 | M3/M5 |
| 6 Analysis / Quiver-like | “哪个因素影响 KPI，变化传播到哪里？” | 指标定义、聚合、趋势、影响链、对比和证据 | 正常指标、数据不足、定义变更、不可重放 | M5 |
| 7 Scenario/What-if/Twin | “更换方案后会发生什么？” | Tracking/Pinned、参数、分支、checkpoint、compare、rebase、2D Twin | Run 状态、冲突、过期、恢复、无 live side-effect | M5 |
| 8 Decision/Action Center | “建议影响什么，谁批准，执行结果如何？” | DecisionCase、Preview、审批、执行、拒绝、补偿、审计 | 权限拒绝、审批过期、冲突、失败重试、部分成功 | M4/M5 |
| 9 Explore/Agent/Knowledge | “能否基于权限内的对象和资料解释问题？” | 持久 Session、Knowledge Base、Tools、Evidence、Manager | 证据引用、撤权、暂停/恢复、worker 故障、模型错误 | M7 |
| 10 Application Composer / Published Apps | “能否将这些对象和操作组织成业务工作页？” | 受限组件组合、参数绑定、预览、发布/撤回 | 草稿、发布版本差异、权限、组件失败、回滚 | M6 |
| 11 Governance/Monitor | “当前资源、任务、权限和系统健康如何？” | Resource Registry、审计、队列、调度、指标/告警、备份/恢复状态 | 用户/服务 scope、告警、积压、撤权、恢复 | M8 |

**页面共用要求：** 每一页有真实 Must-have 任务；都消费同一组 ResourceRef/Principal/ResultManifest 状态；共享链接不能扩大权限；关键按钮有明确副作用预览；每页对加载、空数据、未授权、过期、错误、部分成功和恢复进行验收。

## 21. 跨领域架构不变量细化

1. **事实权威：** PostgreSQL 保存权威业务对象、版本、Scenario/Action/Agent 运行和审计状态；MinIO 保存原始/媒体资产；FalkorDB 作为可重建图投影；Chroma/向量索引仅用于候选召回。
2. **版本锁定：** 排队任务不能只保存可变资源 ID 后再取 latest；Workflow、Agent、Scenario、Action、Query 和 Evidence 消费者必须绑定确切版本或在版本漂移时 fail closed。
3. **读写分离：** 查询/Agent 建议/Scenario 推演不自动带来写权限；任何真实业务副作用必须是 FR-05 可识别的请求、审批、执行和 audit。
4. **权限不是 UI 状态：** 菜单隐藏、前端 disabled、ObjectSet 签名或 permission digest 不能代替每个服务端读取/写入的当前 caller authorization。
5. **系统主体受限：** 后台 rebase、projection、schedule 和 repair 使用可登记/可撤销的 system principal，具有窄 scope、触发任务 ID、输入版本和独立审计；不用布尔开关普遍绕过授权。
6. **派生索引可丢弃：** FalkorDB、vector index、cache 和 query result 可由权威数据重建；它们的滞后、失败或版本必须可观测，不可在无数据来源下单独定义事实。
7. **AI 输出是候选：** LLM/Agent 的类型、映射、证据链和建议均附模型/Prompt/Tool/source version；没有来源或授权证据的关键陈述不作为已验证业务事实。
8. **演示不是生产：** Synthetic fixture、离线数据、单机性能、mock 工具和手动截图明确标记；不向客户/同学宣称真实 connector、生产规模或端到端完成。
9. **跨存储最终一致：** SQL 与图/对象存储/外部系统不假装一个数据库事务；每个阶段有权威记录、可重放命令、幂等键、对账状态和补偿边界。
10. **故障状态不粉饰：** 未知是否提交时显示 outcome unknown 并提供安全查询/恢复路径；未验证时显示 not verified；不可将 `running` 或 HTTP 200 推断成业务完成。

## 22. 需要保留的长期候选与本期边界

| 候选能力 | 本计划处理 | 进入本期的条件 |
|---|---|---|
| 租户管理面与完全多租户 SaaS | 条件项，不默认承诺全套管理面 | M0.4 决定 GOV-06；M0.5 估算；身份/资源/队列的隔离 Gate 有 owner |
| Agent-to-Agent 自治编排 | 可延后；多人会话和人工接管不能代替它 | 只有 M0.4 纳入 AGT-05=B 才排 M7.9；受限顺序 handoff，预算/深度/循环受控 |
| Ontology 高级类型 | 有限类型编辑为 P1 候选 | M0.4 选择 ONT-05=B 后拆成四个独立 feature gate |
| 完整 3D Digital Twin Builder | 本期延后 | 2D/关系/时间/指标 Twin 稳定，需求和公开参照明确后另立版本 |
| 任意脚本、Function 或插件运行市场 | 不纳入本期默认范围 | 需另有隔离沙箱、权限、资源预算、依赖供应链与发布控制计划 |
| 广泛商业连接器市场 | 不按数量作为目标 | 每个 connector 有客户需求、授权、维护责任和真实端到端测试 |
| Marketplace/完整 OSDK/多语言生态 | 后续产品化方向 | 先稳定 Query/Function 发布合同和受限 TS/Python client |
| 无界自动分派或算法排班 | 不纳入 | 有经业务批准的可解释资格、可用性、公平和申诉合同；无数据时保留人工派单 |
| 训练/部署自有 foundation model | 不属于本路线主线 | 另行科研/产品立项；本期评测已有/许可可用的模型并 pin 版本 |

## 23. 风险、观测与缓解措施补充

| 风险类别 | 具体情形 | 缓解与发现信号 |
|---|---|---|
| 基线与协作 | 两位开发者从不同 SHA 开工；plan 文件随本地代码却未传给协作者 | 每个 PR/验收记录 baseline SHA；计划附件单独同步；共享分支从指定提交创建 |
| 语义漂移 | Entity/Relation、Ontology Object/Link、Scenario snapshot、Agent context 使用不同标识 | M1 canonical contract 与跨入口一致性用例；迁移旧模型时保留旧 ID 和 adapter mapping |
| 来源错配 | Mapping 使用 latest 数据；LLM 候选没有精确输入来源 | 固定 DatasetVersion/hash；模型任务输入 manifest 与输出绑定；无法读取 pin 时停止 |
| 工作流误发布 | LLM 或 Transform 代码未经审批写入 Ontology | 默认候选/预览；工作流权限分离；schema/quality/revision Gate；不明代码不执行 |
| 增量覆盖 | patch 被当快照，缺字段造成数据删除；同 ID 冲突覆盖历史 | 输入模式标记 patch/full snapshot；完整性证明；事件冲突拒绝；tombstone/restore 测试 |
| 权限绕过 | 只限制菜单；Graph/Vector/Agent 或 system worker 使用过宽 scope | 按 caller 逐服务端检查；对象/字段/证据负向矩阵；系统主体 scope 登记与撤权 |
| Action 二阶段提交 | SQL、图投影、审批或审计窗口中崩溃，Run 状态与实际效果不一致 | ActionRequest digest、持久状态机、Outbox/reconciliation、故障注入、outcome unknown 和补偿流程 |
| Agent 重复副作用 | worker 重试/恢复重复创建 Case 或 Action | lease generation/fencing；每次 tool/action 有 attempt key；恢复跳过已成功副作用并复核权限 |
| Scenario “假隔离” | 情景 UI 看似独立但写入 live graph 或沿用旧 baseline | 固定 ScenarioContext、执行目标 isolation、M4 写回唯一门和 live side-effect 检查 |
| 证据检索泄露 | 旧向量索引在删除/撤权后仍返回敏感文档片段 | 每次检索服务端 ACL、索引 revision 失效、来源再核验、canary 测试 |
| 依赖范围膨胀 | 为模仿平台新增多数据库/agent 框架/插件带来运维负担 | 先做对照 PoC；迁移需可回退且通过权限/性能/恢复任务，不以新技术本身为目标 |
| 许可与隐私 | benchmark/企业 fixture 无再分发权或含敏感字段 | M0 数据准入、manifest/license decision、脱敏/删除流程；未批准数据不进入公共仓库 |
| 生产声明过度 | 本机 Demo 或一条健康检查被说成生产级 | 使用 Gate 表和环境绑定指标；按用户授权、E2E、容量、灾备分层报告 |
| 团队容量不足 | M0 决策后范围仍超出两人可用时间 | P0/P1/P2 明确；低/基准/高工期；每次纳入新功能需评估退出项或额外资源 |

## 24. 计划文档自身的维护规则

- 计划中的能力使用统一状态标签；新进展只更新对应 capability/phase/gate，不把“已加代码”直接改成“通过”。
- 每次改 scope/priority/estimate 要记录原因、批准者、影响到的 E2E、页面分母和责任人。
- 源主路线图与 PRD 的历史实现快照不要整段复制为 Plan A 2.0 事实；验证结果要写明所运行 SHA。
- 外部参考链接需记录来源页面/版本和核查日期；厂商未公开的实现保持未知，不做假设。
- 新增路线项要回答：哪个用户任务需要它、复用哪项合同、什么证据能判定完成、失败或缺数据时如何呈现、对演示和已发布资源的影响是什么。
- 需求缩减必须登记为 out-of-scope/conditional，而非从功能目录删除后继续宣称原目标已达到。

**本版扩展结论：** 上述第 15–24 节将目标 PRD 的能力目录、M0 决策门、M1–M8 工作包、共享数据合同、E2E、页面矩阵、治理候选、风险和维护方法展开到可用于双方评审的产品/版本粒度；更细的 API 字段、数据库迁移、页面像素稿、逐测试用例和日历排程留待双方签收后另出接口设计、验收脚本和项目排期，不在本路线图中假装已经决定。
