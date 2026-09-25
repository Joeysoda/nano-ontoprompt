# 人员 B 计划：决策运行时与操作型 UI

> 计划性质：责任边界与差距说明；不是工期承诺，也不授权实施。
> 负责人：人员 B
> 基准日期：2026-09-25
> 上游审计：`docs/PALANTIR_ONTOLOGY_GAP_AUDIT_2026_09_23.md`

## 1. 目标与不做事项

人员 B 负责把已有的查询、Object Set、Action、Logic、Scenario 和图能力组合成一条对普通用户可发现、对工程系统只有一个执行语义的决策闭环。

本责任包覆盖：

1. 统一主 Action 与 Scenario Action 的定义、预览、提交、日志和恢复语义；
2. 把 Logic Asset 提升为受类型和版本治理的 Function 能力，并守住“Function 计算、Action 写回”的边界；
3. 将 Object Query/Object Set 后端能力暴露为 Object Explorer 工作台，并在适用范围内高保真复刻 Palantir 的视觉布局与交互密度；
4. 将对象详情升级为可复用 full/panel Object View；
5. 统一 Scenario execution context、比较和合并体验；
6. 扩展图页为关系/事件/时间/Scenario 可协作的 Vertex 式工作台；
7. 建立真实用户和 Agent 的未预演问题验证。

本计划不负责重新定义 canonical metadata、对象/字段授权计算或底层 projection identity；这些由人员 A 提供共享合同。人员 B 不能在前端或执行器里复制一套临时类型/权限规则来绕过依赖。

## 2. 当前仓库事实与详细差距

### 2.1 两套 Action 语义并存

`backend/app/models/v2/action.py` 的主 Action Type 已有看起来较完整的字段：parameters、submission criteria、effects、side effects、permission rules、function binding、status/version。问题发生在执行语义：

- `backend/app/routers/v2/logic_actions.py` 的运行路径只理解少量 criteria，例如 required target、entity exists、field equals、required param；模型中更丰富的表达没有统一 compiler/validator。
- `permission_rules` 被存储和编辑，但运行时没有形成可观察的强制决策。
- effect 支持范围较小；未知 effect 被记录为 skipped，而不是在发布期或执行前以稳定错误拒绝。这会让 Action 显示成功却没有完整执行预期规则。
- Action 直接修改通用 `Entity.properties` 等旧表示，没有通过唯一 edit batch/object-data adapter。
- `OntologyActionRun` 有 before/after snapshot，但没有观察到与 Palantir “只允许撤销该用户最新 edit、side effect 不撤销”类似的明确 revert contract。
- SQL transaction 包裹部分 effect 不等于跨图、SQL、side effect 的 Action ACID；当前没有公开的 snapshot isolation、object conflict、retry、idempotency 和 write-skew 语义。

与此同时，`backend/app/services/v2/scenario_actions.py` 与通用 Scenario workbench 已经拥有顺序规则、preview、submit、input hash、immutable revision、merge preview/conflict 和 best-effort rollback 等更强能力。于是同一个产品中出现“live Action”和“Scenario Action”两个解释器。后果不只是重复代码：用户在 Scenario 中验证过的 Action，合并到 live 时可能因为另一套 criteria/effect/权限行为得到不同结果。

Palantir 的公开契约将一次 Action 定义为一个受控事务；规则顺序影响结果，preview/test run 返回 proposed edits，permissions 与 submission criteria 分离，Function rule 与普通 ontology rules 有互斥约束，成功执行形成 Action log。当前项目需要先统一这些可观察语义，而不是继续为两套 UI 分别补功能。

### 2.2 Logic Asset 是好的原型，但还不是 Function 平台

现有能力值得保留：

- `LogicAsset` 有 interface key/version、JSON Schema 输入输出、binding spec、deterministic/side-effect 标记和执行器类型；
- 执行前后做 schema validation 和单位 normalization；
- `LogicAssetRun` 记录输入、输出、错误、trace、耗时；
- `execute-plan` 能显式地把前序输出绑定到后序输入；
- `LogicDerivedFact` 为结构化输出保留 provenance。

差距在于它仍主要是源码内 `EXECUTORS` 注册的本地函数集合：

- 输入输出虽然有 JSON Schema，却没有与 canonical Object/Object Set/Interface/Struct/marking/scenario reference 对齐的领域类型签名。
- `interface_version` 和 `version` 是字符串，但没有 semver compatibility、依赖 range、pin/resolution 或发布兼容检查。
- Function 可以被直接运行并保存 derived facts；尚未建立“只读 Function 可直接执行，edit-producing Function 只返回 edit batch，只有 function-backed Action 可以应用”的强制边界。
- 没有编辑 provenance 声明来限定 Function 能创建/修改哪些类型，Action permission/log 也就无法预先推导影响面。
- 当前监控是 run list，不等于配额、timeout/cancellation、并发、retry、streaming、user-facing vs technical error、敏感 trace redaction 和版本退役治理。
- Logic Assets UI 是 JSON textarea + 本地执行结果，更像开发/演示控制台，不是类型驱动的业务函数消费体验。

Palantir 官方 Functions 类型参考和 OSDK 公开类型都把 Object、Object Set、Interface、Struct 等当作签名的一部分；Ontology edits 由 Function 产生但必须经 Action 应用。项目不必复制其语言运行时，但应采用同样清晰的执行边界。

### 2.3 Object Query 后端成熟度远高于当前 UI

后端已有 `filter`、`traverse`、`reachable`、interface traversal、union/intersect/subtract、nearest neighbors、with properties、aggregate、compare、cursor、saved Object Set、temporary/permanent 生命周期和 immutable data view。

`frontend/src/pages/ontologies/detail/tabs/ObjectQueryTab.tsx` 目前主要提供对象类型选择、加载首屏和通用结果表，分页入口仍受限。它没有让非开发者使用下列能力：

- 按 property 类型生成合适的 filter editor；
- 嵌套 AND/OR/NOT 与可编辑 chips；
- has-link、linked property、linked object 条件；
- 多跳 pivot/Search Around，并显示每一步当前类型；
- 聚合图表与图表反向筛选；
- static List 与 dynamic Exploration 的明确保存选择；
- compare 两个集合并让图表/表格/Action 共同响应；
- share 权限与 underlying data permission 的区别；
- 对当前集合执行 Action、导出或打开到 Graph/Scenario；
- 保存 URL、刷新/前进后退恢复、expired cursor/view 和 degraded capability。

这解释了为什么“功能分布”看起来缺失：大量能力存在于 schema 和 router，但用户必须手写结构或根本没有入口。差距的本质是缺一个 Object Set state model 驱动整页，而不是缺几个按钮。

Palantir Object Explorer 把搜索、filter chips、linked pivot、charts、results、compare、save exploration/list、Actions、Open in、Export 放在一个连续工作流里。**本项目默认应同时参考其视觉样式、交互细节、信息架构和语义，并以高保真复刻为第一选择**；只有当前业务语义、技术能力、可访问性、响应式要求或既有品牌体系确实不兼容时才偏离，而且偏离项必须在 UI review 中记录原因，不能用“参考信息架构”作为随意重画的理由。

#### 2.3.1 Object Explorer 全量截图视觉审计

本轮已从 Object Explorer 侧栏的 16 个页面读取官方 asset manifest，下载并逐张查看 **92 张官方 UI 截图**。覆盖范围包括首页、全局搜索、搜索结果分类、Search Around、SQL 分析、属性与关系筛选、嵌套逻辑、图表工作区、结果表、多对象预览、比较、pivot、保存 Exploration/List、Action 弹窗与管理员配置。`overview`、`search-syntax`、`understanding-text-search`、`apply-actions`、`generate-urls` 等页面没有独立截图或主要以文字说明行为；不是漏读。

从截图可提取的高保真目标如下：

| UI 表面 | 官方截图中可观察的视觉/交互模式 | Plan B 默认实现要求 |
| --- | --- | --- |
| 应用壳层 | 浅灰工作区、白色内容面板、细边框、低阴影；顶栏紧凑，Object Type 以图标、名称和标签页表达 | 延续当前品牌色和中文字体，但复刻空间层级、紧凑密度、面板比例、边框和工具栏结构 |
| 查询命令栏 | Object Type 图标在左；当前条件呈水平 chips；搜索框既能搜 property/value，也能加入 chart/filter；Clear 位于末端 | 作为 Explorer 的主状态入口，不另造传统“左侧表单 + 查询按钮”页面 |
| Filter popover | 左栏按 Properties/Linked Object Types 分组，中栏列属性或关系，右侧/下一层编辑值；AND/OR/NOT 以小型 token 与嵌套卡片呈现；底部整宽 Apply Filters | 复刻三段式选择、token 尺寸、层级缩进、底部主按钮和即时摘要；按字段类型替换输入控件 |
| Explore/Results | 顶部以相邻标签切换；Explore 是可拖拽/缩放的卡片网格，Results 是高密度数据表；切换不丢 Object Set | 复刻双模式结构和状态连续性，不拆成彼此无关的两个路由状态 |
| 图表卡片 | 白底、细边框、标题与聚合下拉在卡片头；histogram/listogram/grid/pie/statistics/map 使用统一容器；框选后底部出现 Apply filter CTA | 第一阶段先实现现有数据支持的 listogram/histogram/statistic；容器、工具条、选择反馈按截图复刻 |
| Layout | 图卡可添加、删除、拖动、横纵缩放；支持 undo/redo、layout picker、保存当前布局、设置个人/全局默认 | 先做固定但相同视觉的布局，再按后端能力开放编辑；禁用状态也应保留位置与解释 |
| Results table | 极紧凑行高、固定表头、首列选择框、对象 icon/title；列可排序、拖动、缩放和配置；右侧/下方可展开 preview | 使用虚拟化或分页维持密度；复刻列菜单、drag handle、选中态和 preview 比例，而不是通用 HTML 表格 |
| Object preview | 单选时显示一个 panel；多选时并排显示多个 compact panel；属性摘要、linked info 与 actions 保持一致 | 使用共享 `ObjectPanel`，确保 Explorer、Graph、Scenario 的视觉和语义相同 |
| Compare | 比较入口位于顶层工具条；第二集合用独立颜色；同一地图/图表并列或叠加展示两集合；仍保留 filter、Results、Action | 复刻双色集合标识、compare picker、图表叠加和共同过滤结构 |
| Pivot | Linked Objects 位于 Explore 结果侧边区域；pivot 后顶层 Object Type 改变，但历史筛选关系仍可理解 | 在 chips/breadcrumb 中显式显示 traversal path，并保留截图中的主类型切换感 |
| 保存与资源入口 | Exploration 与 List 是顶栏两个独立 dropdown；保存 modal 同时表达名称、说明、位置、Private/Public；资源搜索以卡片呈现 | 动态定义和静态成员不能共用一个含糊的 Save；视觉上保留两个资源入口和一致 modal |
| Action/Open in/Export | 位于 Results/Explore 共用的右侧操作区；Action 以菜单和 modal 执行，成功后顶部出现明确绿色 toast | 复刻操作分组、弹窗密度、Submit/Cancel 层级和成功反馈；权限不足时保留解释性 disabled state |
| 时间属性与 inline edit | 时间相关属性在单元格中使用微型 sparkline；可编辑字段直接弹出受约束选项 | 仅在 metadata 和 Action contract 支持时启用；不支持时保持只读视觉，不伪造修改成功 |

视觉复刻不意味着复制 Palantir 商标、专有图标素材或英文内容。验收以结构、间距、信息密度、组件层级、交互位置和状态反馈的相似度为主，同时保持本项目品牌、中文本地化、WCAG 可访问性和响应式能力。每个主要页面应保存 Palantir reference screenshot、当前实现 screenshot 与差异说明，作为 UI review 证据。

### 2.4 Entity detail 不是可复用 Object View

当前 `EntityDetailPage.tsx` 能展示实体配置、properties、instances、关系和关联逻辑/动作，但承担了类型管理与对象查看两种责任，更接近 builder/admin 页面。

Palantir 区分：

- 每个 Object Type 自动得到 standard Object View；
- full view 提供完整对象上下文；panel view 可嵌入 Search、Graph 等其他应用；
- prominent、normal、hidden property 按 metadata 自动呈现；media/time series/geo 有专用 renderer；
- linked objects 可按 link type 分组、内联预览并打开进一步探索；
- configured view 是工作流化覆盖层，用户仍可回到 standard view；
- tabs 可有条件可见、跨组件 filter 和权限对齐。

当前项目如果继续在每个页面各写一次对象卡片，会造成属性格式、隐藏字段、安全裁剪和 Action 入口不一致。真正缺的是 `ObjectView`/`ObjectPanel` 组件合同和配置模型，而不是给 Entity detail 增加更多区块。

#### 2.4.1 “configured Object View builder 是独立产品决策”的含义

这里必须区分三层，不能把它们都叫“做 Object View”：

1. **Standard Object View runtime：** 根据 Object Type metadata 自动显示 prominent/normal properties、links、actions、timeline 等。每个类型无需人工设计就能使用。
2. **Configured Object View runtime/schema：** 系统能够读取一个稳定配置，决定 tabs、组件布局、条件可见性、full/panel 形态和数据绑定。这使配置可以由代码、种子数据或未来 builder 生成。
3. **完整低代码 builder：** 面向管理员的可视化创作产品，包含组件 palette、drag/drop canvas、属性 inspector、数据绑定、条件表达式、undo/redo、draft/publish、版本、权限和预览。

原计划的意思不是“不做 configured Object View”，而是第一阶段先交付第 1、2 层，让所有对象有专业、统一、可复用的 full/panel view，并保证未来 builder 有稳定目标格式。第 3 层本身接近一个 Workshop/低代码产品，工作量和验收面显著大于对象详情页；如果没有单独立项就顺手做，容易得到一个不能安全发布、不能迁移配置的半成品。

修正后的产品建议是：**第一阶段必须做 standard view + panel reuse + configured schema/runtime；完整可视化 builder 列为后续明确阶段，而不是永久 defer。** 如果当前产品的主要用户就是非技术 Ontology 管理员，应把 builder 提前为人员 B 的下一阶段交付，但仍不能先于稳定配置 schema。

### 2.5 Scenario 后端有优势，但上下文没有贯穿所有能力

项目已有 Scenario resource/revision/change set/run/stage/metric/artifact/grant/audit，通用工作台支持 isolated objects、Action preview/submit、compare、merge review。这比“完全没有场景”强得多。当前 immutable base view 提供可重复分析，但不能因此省略 Palantir Beta 文档中的 tracking/rebase 工作流。

需要解决的差距：

- 产品文案和 API 必须明确 Scenario 是隔离 edit overlay/decision branch，还是历史数据快照；两者不能混用。
- Object Query、aggregate、Logic、Agent、Graph、Object View 的 scenario context 仍需一致传播，不能只有部分 widget 读取 scenario view。
- live 与 scenario 的 Action criteria/permissions 应通过 execution context 区分，但仍由同一 Action engine 解释。
- persisted Scenario metadata、作者、说明、审批状态和相关对象需要成为受治理资源；仅有后台 Scenario row 不等于业务协作对象。
- 当前只有 immutable/pinned 思路，尚未完整采用 Palantir 的默认 30 天 TTL、临时 session scenario、对象化 persisted scenario、定期 auto-rebase、execution-context criteria 和由 merge Action 控制写回等组合语义。
- compare 目前以有限指标和 change counts 为主，尚未把同一 Object Set/chart 在多个 scenario context 中并列计算推广为通用能力。

不应在 Palantir-compatible 与当前 reproducible design 之间二选一。目标合同应明确提供两种模式：

- **Tracking Scenario（默认交互模式）：** 尽量按 Palantir Beta 公开契约实现，基于 live branch、默认 TTL 30 天、定期 auto-rebase、支持 temporary 与 object-backed persisted metadata、通过 execution context 区分 Action criteria，并用 merge Action 写回。
- **Pinned Scenario（项目扩展）：** 固定 base data view/revision，适用于可重复评估、审计、长时间运行模型和 decision evidence；不会自动吸收 live 变化，用户必须显式 rebase/clone/merge。

之前没有直接建议完全切换到 auto-rebase，是因为项目当前的供应链研究、input/output digest、immutable result view 和比较证据依赖固定 baseline；强制每 10 分钟改变 base 会让相同输入不再可重复。这不是 Palantir 设计“不适用”，而是项目还有审计型使用场景。修正后的计划应完整采纳 Palantir 可观察的 Tracking 模式，同时把 Pinned 明确标为扩展，而不是用扩展替代标准模式。

### 2.6 当前 Graph 与 Vertex 式关系工作台仍有层级差距

当前工作分支已有 Cytoscape/XYFlow 等图能力和关系展示，但 Palantir Vertex 的关键价值不只是画节点边：

- 从选中对象打开与其他页面一致的 panel Object View；
- 按关系 Search Around，并在大集合前先过滤；
- 多步、参数化 traversal，可保存并复用；
- histogram 根据当前选中/图内对象实时筛选；
- events、time series、time scrub 改变节点状态和事件 badge；
- layers/style/layout 是可保存图资源的一部分；
- graph 有分享、版本、只读历史和 Scenario/model 叠加。

如果 Graph 自己定义查询和对象卡片，会再次绕过 Object Set 与 Object View。Graph 应是同一 query/view/action/scenario contract 的消费者。

这里还存在一个重要的分支事实：2026-09-24 fetch 后确认，`origin/factorynet-temporal-workbench` 有 4 个当前工作分支尚未包含的提交：

- `d1637e2`：persistent FactoryNet temporal replay；
- `a5159b3`：event-at-a-time FactoryNet evolution；
- `ba6567d`：接入 Dynamic Evolution 页面；
- `fd90622`：blind live FactoryNet data construction。

远端代码已经包含 `TemporalReplay`、`TemporalStreamEvent`、`TemporalFact`、`DataModelSnapshot`、独立 graph namespace、watermark、valid-from/to facts、事件幂等、发布快照和 `DynamicEvolutionTab` 的时间轴/当前与历史关系视图。这些文件在当前 working tree 中均不存在。因此“可能需要新的时间序列与事件模型”这一表述已经过时；正确差距是：**成熟实现存在于另一个已分叉分支，但尚未集成和复核，Plan B 不得另造第二套模型。**

#### 2.6.1 四个未合入提交的逐项审查

| 提交 | 实际能力，不只是提交标题 | 应保留 | 合并后仍需补齐或收敛 |
| --- | --- | --- | --- |
| `d1637e2` | 增加动态本体 change/impact/batch、可编辑 Data Model、FactoryNet 分批 replay、独立图 namespace、持久化 batch、时间轴图、基于固定 revision 的 What-if runner；54 个文件，约 6.4k 行 | ontology change audit、impact preview、replay checkpoint、隔离图、固定 revision 推理和 UI 时间轴均是可复用资产 | `WhatIfScenario/WhatIfRun` 是未 present、未形成团队验收证据的原型，并与当前更完整的 `Scenario/ScenarioRevision/ScenarioRun` 重叠；动态本体编辑属于人员 A 的 canonical metadata 边界；不能把 FactoryNet 专用 schema/字段当通用 temporal contract |
| `a5159b3` | 在 replay 上增加逐事件 stream、push/file 两种来源、事件幂等、watermark、valid-from/to temporal facts、SSE、当前/历史关系读取和 immutable published snapshot；19 个文件，约 2.7k 行 | `TemporalStreamEvent`、`TemporalFact`、`DataModelSnapshot`、run-scoped namespace、publish gate 与 snapshot hash 应作为统一时间数据底座 | 第一版明确拒绝 late event；时间主键主要是 numeric ordinal；尚未定义 event-time/ingestion-time、时区、乱序容忍窗口、correction/retraction、schema evolution、retention；这些是扩展差距，不是重写理由 |
| `ba6567d` | 增加 Ontology detail 的 Dynamic Evolution tab，提供 run 选择、时间滑块、current/history relation 和事件列表 | 作为 temporal workbench 的入口与可视化原型保留 | 节点详情仍是页面自有 key/value UI，未复用 `ObjectPanel`；查询也未统一为 Object Set/execution context；需在共享合同完成后重接，而不是删除页面 |
| `fd90622` | 增加从空白开始的 simulated-live FactoryNet 会话、readiness/自动准备、未知未来 horizon、逐事件图增长及独立 Dynamic Data 页面；10 个文件，约 1k 行 | blind/live 演示、未知终点、单步/暂停/发布和来源耗尽状态是很好的验收夹具 | `dynamic_data` router 会自动创建 FactoryNet ontology，并调用 service 私有函数；这是 demo bootstrap，不应成为通用 ingest API。前端还包含轮询与 SSE 双刷新、全图分页后客户端布局、`any`/lint 豁免，需治理性能和类型边界 |

#### 2.6.2 合并后的领域归属与重复模型裁决

这四个提交不是“已经完成整个 Vertex/Scenario”的证据。代码审查只能证明功能存在，不能替代 present、产品验收和使用证据；其中 temporal 部分是值得集成验证的数据平面原型，What-if 部分则是待收敛的实验实现。合并后按以下边界使用：

- `TemporalReplay/TemporalStreamEvent/TemporalFact/DataModelSnapshot` 只作为时间数据到达、幂等处理、内部有效期投影、证据和发布 checkpoint 的数据平面；Plan B 可复用该底座，但不能把这些表直接暴露成 Palantir-compatible Ontology 时间语义。
- 当前分支的 `Scenario/ScenarioRevision/ScenarioChangeSet/ScenarioRun` 在 revision、change set、digest、grants、audit、Action preview/submit、compare/merge 上明显更完整，因此是用户可协作的决策分支与 edit overlay canonical 模型。Joey 的 `WhatIfScenario/WhatIfRun` 只作为待提取算法的 source code 随历史保留，不注册正式 router、不展示第二个 What-if tab；其固定 revision 推理 diff 经测试后迁到统一 Scenario runner，旧表/model 再退役。
- `OntologyChange`、Data Model editor、batch impact 属于 canonical ontology schema 编辑链，应由人员 A 审核 revision、identifier、authorization 和 migration aliases；人员 B 只消费发布后的 schema revision，并负责把影响/历史正确展示在运行时 UI。
- `DynamicDataPage` 的 FactoryNet 自动准备是 demo adapter；通用产品入口必须要求显式 source、mapping、ontology、schema revision、event-time policy 和授权，不允许靠名字或实体集合猜 ontology。
- published `DataModelSnapshot` 应成为 Object Query data view、Pinned Scenario base 和 Graph history 的可引用资源；不能只更新 `OntologyProject.current_data_snapshot_id` 后让其余运行时继续读取隐式 live 图。

#### 2.6.3 以 Palantir 公开设计为准的采纳裁决

提交时间、作者和已有代码量都不是采纳依据。Palantir 的公开合同给出三条不能混淆的资源模型：

1. **Event 是 Object Type。** Vertex 文档要求 event object 至少有 start/end 两个 timestamp，并通过标准 link 与业务对象相连。因此 Joey 的 `TemporalStreamEvent` 是 ingestion envelope，不是最终 Event Object；它被处理后应 materialize 成标准 object/link/property，才能被 Object Query、Object View、Action、权限和 Vertex 一致消费。
2. **连续测量是 Time Series Property。** TSP 是 object property，通过 string series ID 连接一个或多个 time-series sync；sensor object type 用标准 link 关联 root object，并具有 default TSP、unit、interpolation 等 capability metadata。因此 `TemporalFact(subject,predicate,object,valid_from/to)` 可以支持内部状态投影，却不能替代 TSP/sensor/time-series sync 合同。
3. **Ontology Scenario 是 edit overlay，不是历史 snapshot。** Palantir Scenario 默认 30 天 TTL、每 10 分钟 auto-rebase、按 execution context 执行 Function/Action，并由 merge Action 写回；官方明确说它不是 historical data version。`DataModelSnapshot` 只能作为本项目 Pinned/审计扩展或查询 checkpoint，不能被命名或解释成 Palantir Scenario base 的标准行为。

据此逐项裁决：

| Joey 实现 | Palantir 对照 | 裁决 |
| --- | --- | --- |
| event key 幂等、watermark、pause/resume、file/push replay | 属于 streaming ingestion/runtime，Palantir Ontology 文档不规定内部实现 | **采纳为内部底座**，但补 event-time/ingestion-time、乱序与 correction policy |
| `TemporalFact` valid-from/to 与 run graph namespace | 可支撑历史查询，但不是官方 Event Object 或 TSP 类型表面 | **仅保留为内部 projection**；新增标准 Event Object materialization 与 TSP adapter，所有产品 UI 经 canonical object/property/link contract 读取 |
| `DataModelSnapshot` 和 snapshot hash | 有利于可重复测试；官方 Scenario 明确不是 snapshot | **保留为 Pinned/审计扩展**，名称、banner 和 API 不伪装成 Tracking Scenario |
| `DynamicEvolutionTab`/Dynamic Data graph | 部分对应 Vertex time selection/events，但节点 panel、event object、TSP chart、search-around 尚未按官方合同统一 | **保留为实验入口并重接共享组件**，不能据此宣称 Vertex 已完成 |
| `WhatIfScenario/WhatIfRun/WhatIfTab` | Vertex model scenario 强调 configured model inputs/outputs、time window、baseline run、actions/overrides；Ontology Scenario 另有 overlay/rebase/merge 合同 | **不作为正式入口采纳**；仅提取有证据价值的 fixed-revision reasoning diff 到当前 Scenario/Logic runner |
| ontology change impact/editor | 对应 builder 对 object/link/property schema 的治理，不属于最终用户 Scenario | **由人员 A 审核后采纳**；schema revision/authorization/identifier 不通过则不发布 |

因此“合并 Joey 分支”只表示把代码历史放到共同集成点，并不自动授予任何模块 canonical 地位。正式路由、导航、数据库读写路径和 Plan B ownership 以上表为准；未通过裁决的模块可以保留源码和测试，但默认不注册、不展示，直到适配完成。

#### 2.6.4 何时合并、怎样合并

合并窗口是**当前 208 文件工作区先形成一个可回滚提交之后、人员 B 开始按本计划继续实现之前**；不是等两人各自再做完一轮。两条分支共同祖先为 `72396a2`，当前分支独有 2 个提交，temporal 分支独有上述 4 个提交。采用保留历史的 `--no-ff` integration merge，不 squash，也不逐文件复制；这样 blame、回滚和四个提交的验收边界仍清楚。

已知直接重叠文件有 9 个：`backend/alembic/env.py`、`backend/app/main.py`、`backend/app/tasks/celery_app.py`、`backend/tests/v2/models/test_v2_schema.py`、`frontend/src/App.tsx`、`frontend/src/components/Layout.tsx`、`frontend/src/pages/data-management/DataManagementPage.tsx`、`frontend/src/pages/data-management/multimodal/MultimodalDataPage.tsx`、`frontend/src/pages/ontologies/detail/OntologyDetailPage.tsx`。这些冲突必须做语义合并：同时保留双方 router/model 注册、Celery tasks、路由、导航和 tab，不能用 `ours`/`theirs` 整文件覆盖。

迁移图也必须显式收敛：temporal 链从 `0015_ontology_mapping_v2` 分出 `0016_dynamic_ontology_what_if → 0017_temporal_replays → 0018_temporal_stream_facts`，当前链则是 `0016_reasoning → … → 0030_supplier_run_evidence`。合并后新增 Alembic merge revision，以这两个 head 为 `down_revision`；随后分别验证空库 `upgrade head`、从每条已部署 head 升级、`alembic heads` 只有一个 head。不得改写已经可能被组员运行过的历史 migration ID。

Integration merge 的完成定义不是“Git 无冲突”，而是：应用能导入选定 routers/models；migration 单 head；现有 Scenario 与 temporal tests 同时通过；ingestion event 可 materialize/适配为标准 Event Object 或 TSP；历史 checkpoint 与 Scenario overlay 在命名和执行语义上分离；Joey 的旧 What-if router/tab 默认不注册；前端同时可达 Object Query、当前 Scenario 和标为实验性的 Dynamic Evolution，且共享导航没有丢项。

### 2.7 现有测试不能单独证明 Ontology 可被发现和正确使用

仓库已有大量 pytest、Object Query contract tests 和 Playwright Scenario 测试，这些对工程回归很重要。但官方设计验证特别要求使用未预演的真实业务问题，让新用户和 Agent 在通用工具中寻找答案，并记录正确性、路径、耗时、困惑和求助。

由实现者预先编码的 happy path 会验证“我们写的流程能跑”，却不能发现：用户选错同名类型、误解 linked direction、把 static list 当 dynamic exploration、在 Scenario/live 间执行错 Action，或 Agent 因 metadata 描述不清选择错误 property。人员 B 需要把这种产品验证纳入验收，而不是仅增加截图测试。

## 3. 参考语义与必须守住的不变量

1. Action definition、preview/test、submit、log、revert 和 Scenario application 必须共享一个 compiler/executor contract。
2. Submission criteria 解释“此时是否允许提交并给出用户原因”；permission decision 解释“该 principal 是否被授权”；两者不可互相替代。
3. Rule 顺序是语义的一部分；未知 rule/effect 在发布或验证阶段失败，不能静默 skipped。
4. Function edit 只产生 typed edit batch；只有 Action 可以应用。External side effects 不属于 ontology edit ACID，必须有独立结果和幂等策略。
5. Object Set definition 与 read options、execution/scenario context、materialization 分开；UI URL/state 也保持这一区分。
6. Dynamic Exploration 保存定义，Static List 保存成员；分享任一资源都不扩大 underlying data permission。
7. 同一对象在 Explorer、Graph、Scenario 中使用同一个 Object Panel；standard view 永远可用，configured view 是覆盖层。
8. Scenario context 显式、可见、可比较，不允许用户误把 scenario edit 当 live write。
9. 所有异步操作展示 empty、loading、partial、expired、permission denied、unavailable、conflict 和 failed 的不同状态。
10. 用户与 Agent 验收必须使用未预演问题和真实数据，不把内部演示脚本当作可发现性证据。
11. Ingestion event、internal temporal fact、published checkpoint 与 Scenario overlay 是不同层资源；任何 UI 或 API 不得把 replay 进度、历史时点和 scenario revision 混成同一个“版本”。
12. 合并后的正式 API 不依赖 FactoryNet 私有字段、按名称猜 ontology 或 service 私有函数；demo adapter 必须与通用 contract 分层。
13. 对外 temporal ontology 只暴露标准 Event Object、link、TSP/sensor/time-series capability；内部 `TemporalFact` 不绕过 Object Query、Object View、Action 和授权合同。

## 4. Design corrections

| 之前的假设 | 证据 | 修正后的设计 | 对现有代码影响 | 动作 |
| --- | --- | --- | --- | --- |
| 主 Action 与 Scenario Action 可分别演进 | 两者 criteria/effect/preview/merge 行为已经不同 | 一个 Action compiler 产生 typed edit batch，live/scenario 只是不同 execution target | `logic_actions.py`、`scenario_actions.py` 与两个 UI 需收敛 | refactor |
| 保存了 `permission_rules` 就等于实现权限 | 主运行路径未强制执行；官方区分 criteria 与 permissions | 发布验证和执行都调用人员 A 的 authorization contract | model 可保留，runtime 必须重接 | extend |
| Logic Asset 等同于 Function | 当前是本地 registry + JSON Schema；官方 Function 有领域类型、版本和 edit boundary | 保留资产/run/trace，增加 typed signature 与 read/edit execution class | Logic models/router/UI 扩展，不重写算法 | preserve + extend |
| Object Query UI 只需展示结果 | 后端 AST/Object Set resource 已远超类型下拉和表格 | 建立以 Object Set expression 为中心的 Explorer state model | 重构 `ObjectQueryTab`，复用现有 APIs | replace UI shell |
| Entity detail 可同时当类型管理和对象工作台 | 当前页面职责混合；官方 full/panel views 可跨应用复用 | 拆 Type Manager 与 reusable Object View/Panel | 页面路由和组件边界改变 | refactor |
| Scenario revision 可以被描述成历史版本 | 官方明确 Scenario 不是历史 data version；本项目 immutable view 是自有扩展 | 同时提供 Palantir-compatible Tracking 与项目扩展 Pinned；二者都仍是 edit overlay，不冒充历史仓库 | 工作台创建流程、context、TTL、rebase、compare/merge 合同调整 | extend + clarify |
| 图页面自己查询和展示对象最直接 | 会复制 Object Set、权限和 Object View 语义 | Graph 消费统一 Object Set + Object Panel + Scenario context | 图数据加载和 selection sidebar 重接 | refactor |
| Vertex 时间/事件模型尚未实现 | 远端 4 个提交实现了内部 replay/event/fact/snapshot 和 Dynamic Evolution UI，但不等于 Palantir 的 Event Object + TSP/sensor 模型 | 保留 ingestion data plane，增加 canonical Event/TSP adapter、late-event/time policy；禁止把内部 fact table 当产品 ontology | 涉及跨分支 integration、双 Alembic head、9 个重叠文件与领域适配 | preserve substrate + adapt surface |
| 远端 What-if 可与当前 Scenario 并列上线 | Joey 的实现未 present/验收；两者都有 scenario/run/base revision，却使用不同表、API、执行器和 UI；当前实现的治理、Action 和 merge 更完整 | 当前 `v2_scenarios` 为唯一产品 catalog；远端 router/tab 默认不注册，只迁移其固定 revision reasoning diff | 需要算法提取与 model/table 退役，不创建第三套 UI | converge |
| Dynamic Data demo 就是通用实时接入 | 它按 FactoryNet 实体集合猜 ontology、可自动建 schema，并调用私有 helper | 保留为 demo adapter；正式 API 显式声明 source/mapping/revision/time policy/auth | router/service 分层并补 contract tests | preserve + isolate |
| “信息架构参考”足以指导 Explorer UI | 92 张官方截图给出明确布局、密度、控件位置和状态反馈 | 默认高保真复刻，偏离必须有记录和理由 | `ObjectQueryTab` 需要按 reference screenshot 进行视觉验收 | replace UI shell |

## 5. 目标边界（简要行动建议）

0. 当前工作区提交后完成基于 Palantir 合同的 selective integration merge：解决重叠文件、双 migration head 和双 Scenario 模型裁决；只注册通过裁决的 temporal runtime，Joey What-if 保留源码但不上正式入口。
1. 建立统一 Action compiler/edit batch，并让 preview、live submit、scenario submit 共用；旧 endpoint 做兼容 facade。
2. 给 Logic Asset 增加 typed signature/version policy；把 edit-producing function 的唯一提交口绑定到 Action。
3. 用现有 Object Query/Object Set APIs 建 Object Explorer state model，并以 92 张官方截图为默认视觉基准开放 filter、pivot、charts/results、save、compare、Action/open-in。
4. 提取 standard `ObjectView` 与 `ObjectPanel`；Explorer、Graph、Scenario、Dynamic Evolution 先复用 panel，再做 configured views。
5. 让统一 `ExecutionContext` 贯穿 Query/Function/Action/View/Graph；实现 Tracking/Pinned 两种 Scenario，并让 Pinned 显式引用 published temporal snapshot。
6. 在已合并 temporal data plane 上补通用 source/time/late-event contract，并 materialize/adapter 到 Event Object、TSP/sensor/time-series sync，再扩展 Vertex 式 events/time/layers/version 和开展盲测。

### 5.1 开源组件决策矩阵（第一阶段前置审查）

这张表只评估“通用基础设施/组件”是否值得复用，不把组件能力当成 Palantir 领域合同。许可证和能力以 2026-09-25 访问到的官方仓库/文档为准；引入前仍需锁定版本、跑本项目构建与 license 检查。

| 组件 | 对本项目的准确作用 | 决策 | 仍由项目负责的部分 | 许可证/证据 |
| --- | --- | --- | --- | --- |
| React Query Builder | Object Explorer 的嵌套 AND/OR/NOT、rule group、可拖拽条件编辑；支持自定义 field/operator 和导入导出 | **adopt narrow role**，先做小型 spike | Object Set AST、linked traversal、权限裁剪、参数绑定、稳定错误和 Palantir 视觉样式；不能直接把其 query 输出当后端合同 | MIT；官方仓库说明支持可定制 query builder、DND、SQL/MongoDB 等 formatter，但这些 formatter 不是本项目语义：[repo](https://github.com/react-querybuilder/react-querybuilder)、[DND docs](https://react-querybuilder.js.org/docs/dnd) |
| TanStack Table | Object Explorer dense result table、排序、筛选状态、分组、row selection、server-side pagination 的 headless state/adapter | **adopt narrow role**；保留自有 table markup | Object Set cursor、snapshot/live consistency、field authorization、compare selection、inline Action 状态和截图级布局 | MIT；官方文档明确是 headless、可完全自定义并提供 React adapter：[docs](https://tanstack.com/table/latest/docs/overview)、[repo](https://github.com/TanStack/table) |
| Apache ECharts | Explore charts、aggregate card、time-series plot、compare overlays | **adopt narrow role**，只负责渲染 | 聚合语义、数据权限、时间范围、scenario context、loading/partial/degraded 状态和 Palantir card layout | Apache-2.0；官方仓库和许可证说明其为浏览器可视化库：[repo](https://github.com/apache/echarts)、[license](https://github.com/apache/echarts/blob/master/LICENSE) |
| JSON Forms | Configured Object View 的 JSON Schema + UI schema runtime；自定义 renderer 可承载 ObjectPanel widget | **spike first**，不在第一阶段引入 | canonical view schema、Object/Object Set/Action binding、权限和版本兼容；完整 low-code builder 不由 JSON Forms 自动提供 | MIT；官方仓库说明支持 React/Angular/Vue、JSON Schema 和自定义 renderer：[repo](https://github.com/eclipsesource/jsonforms)、[React API](https://jsonforms.io/api/react/) |
| Cytoscape.js / XYFlow | 现有 Graph/Vertex 原型的布局、selection、关系交互 | **preserve**，不更换引擎 | Search Around、Object Panel、Event Object/TSP、Scenario overlay、授权和图资源保存 | 已在当前仓库使用；组件只提供图交互，不拥有 Ontology 语义 |
| Temporal.io | 长任务、重试、取消、恢复、补偿和跨服务 durable execution 的候选 | **defer / threshold-gated**；第一阶段继续 Celery | Action transaction、edit batch、权限、审计和 Scenario merge；引入会增加独立服务和 worker 运维 | MIT server；官方仓库定位为 durable execution，但不能替代领域编排：[repo](https://github.com/temporalio/temporal)、[official site](https://temporal.io/) |
| OpenFGA | relationship-based authorization 的参考或人员 A 的受控 spike | **reference / person A owns**，B 不直接接入 | 当前 ontology/object/property projection、field masking、Action criteria 与 permission 分离；不能把 FGA tuple 当完整权限合同 | Apache-2.0 SDK/模型生态；官方概念是 relationship tuple + authorization model：[concepts](https://openfga.dev/docs/concepts) |

第一阶段只批准 React Query Builder、TanStack Table、ECharts 的 narrow spike，不立即安装依赖；JSON Forms、Temporal.io、OpenFGA 先不进入运行时。每个 spike 必须有小样例、bundle/运行时成本、许可证记录、可替换边界和“失败时回退到现有实现”的结论。不得为了使用组件而改变 Object Set、Action、Scenario 或授权语义。

### 5.2 第一阶段实施范围与停止线

本次只启动 Phase 1：

- 接受已有 `ExecutionContext` 作为 Action runtime 的输入合同，并校验 ontology、scenario、revision/data view 的一致性；
- live Action 明确进入 `live` target；带 Scenario context 的请求在统一 compiler 尚未接通前必须 fail-closed，不得写入 live Entity；
- 为后续 shared Action compiler 保留稳定错误码和契约测试；
- 不实现 Object Explorer UI、不安装上表组件、不实现 Tracking Scenario auto-rebase、不实现 Event Object/TSP materializer、不实现 configured builder。

Phase 1 的完成标准是：同一个 Action 请求能明确报告 live/scenario target；伪造或越权 Scenario context 在执行前失败；任何失败都没有 live write；现有 live Action 和 Scenario action 测试保持通过。达到这些标准后，才进入 Object Explorer 组件 spike 和 shared compiler 重构。

本次启动的代码落点：`backend/app/services/v2/action_context.py`（解析与稳定错误合同）、`logic_actions.py`（执行前 fail-closed 门禁与响应回显）、`OntologyActionRun.execution_context` 及迁移 `0032_action_run_execution_context`（持久化上下文），并配套 `tests/test_action_context.py`。

### 5.3 三个开源组件 spike 结果

Spike 页面位于 `/component-spikes`，代码在 `frontend/src/pages/component-spikes/`，只使用静态样例数据：

- React Query Builder `8.24.3`：通过。嵌套 rule group、AND/OR 和 JSON AST preview 可用；生产接入仍必须由项目维护 Object Set AST adapter、字段权限和视觉覆盖。
- TanStack Table `9.2.4`：通过。headless table、selection 和自有 markup 可用；后续纵切面已切换到 v9 current `useTable` API，不再依赖 legacy compatibility API。
- Apache ECharts `6.1.0`：通过。Actual/Scenario overlay、tooltip 和 resize 可用；构建输出出现大 chunk 警告，因此正式页面必须 dynamic import/code splitting，不能把 ECharts 直接并入主 bundle。

三个 spike 均没有接生产 API，也没有改变后端语义。下一步只有在确认 bundle 预算、版本锁定和 adapter contract 后，才可把其中任一组件接入 Object Explorer。

### 5.4 Object Explorer 第一条生产纵切面（已完成）

Spike 后的接入门槛已经落地：三个依赖在 `package.json` 精确锁定为 React Query Builder `8.24.3`、TanStack Table `9.2.4`、ECharts `6.1.0`；ECharts 使用 `echarts/core` 按需模块并由 `React.lazy` 拆成独立 chunk，组件实验页也从主入口拆包。主 bundle 仍超过 Vite 默认 500 kB 预算，因此后续还要继续拆分 Ontology detail，但不再因 ECharts 把完整图表运行时直接并入入口。

`frontend/src/pages/ontologies/detail/object-explorer/contract.ts` 是正式 adapter 边界：它只把受控 Query Builder rule group 转换为项目自己的 `ObjectSetExpression/FilterExpression`，完成数字/布尔值类型转换、未知字段拒绝、AND/OR/NOT 映射和 URL 无损恢复；第三方 AST 不进入后端。契约测试覆盖嵌套过滤、URL 往返和不在已发布 query metadata 中的字段拒绝。

`ObjectQueryTab` 已成为第一条真实 Object Explorer 纵切面：使用 ontology property metadata 限制字段/操作符，点击 Apply 后调用既有 `/object-query/load`；TanStack Table v9 渲染高密度 Results，ECharts 仅渲染明确标注为“不完整”的当前页分布，cursor pagination 仍由后端拥有；Explore/Results 切换、Object Type、filter state 写入 URL；选择对象时打开共享 `ObjectPanel`。List/Exploration save、Compare、Pivot、Action/Open in Graph 保留在正确位置但 disabled，并解释尚缺合同，避免伪成功。

本纵切面没有跨越下一停止线：完整 aggregate 仍要求 pinned data view；当前页图表不能作为业务统计；`ObjectPanel` 尚未在 Graph/Scenario 复用；shared Action compiler、save resource、compare 与 pivot 仍是后续阶段。

### 5.5 Shared Action compiler 纵切面（已完成）

本节更新并 supersede 5.2 中“统一 compiler 尚未接通前”的临时 fail-closed 停止线：该停止线在 Phase 1 验收时成立，现已由下面的共享 compiler 纵切面取代；其余未实现项仍保持停止线。

`backend/app/services/v2/scenario_actions.py` 现在是 live 与 Scenario 共用的 target-neutral edit-batch compiler。它统一执行 action authorization、参数绑定、submission criteria、目标对象解析与 typed edit 生成；canonical `op` effects 继续走既有 schema，旧的 `action` effects 则只在 compiler 边界内归一化为 `invoke_action`、`set_property`、`create_object`、`add_link`/`remove_link` 等 typed edits。未知 effect、未知参数、缺失 target、criteria 不满足和权限不匹配都会在任何写入前稳定失败。

`logic_actions.py` 的 live preview/run 与 `scenario_workbench.py` 的 Scenario compile 均调用同一 compiler。live 路径将 edit batch 原子应用到 SQL snapshot，并保留 expected-old-value 检查；Scenario 路径要求 `expected_etag` 与 `client_request_id`，再交给现有 revision/idempotency contract。这样 preview、live execution 与 Scenario application 不再各自维护一套 action 语义。

本纵切面仍有明确边界：目前不支持外部副作用型 effect（review/repair/writeback 等）、不实现 configured Object View builder、不做自动 rebase，也不把页面图表或 legacy action shape 直接升级为长期 metadata contract。下一步应补齐 action metadata migration、ObjectPanel 在 Graph/Scenario 的复用，以及 save/compare/pivot 合同；这些不应通过重新分叉 compiler 实现。

### 5.6 ObjectPanel 在 Scenario graph 的复用（已完成）

Scenario study 的选中对象现在直接复用 Object Explorer 的 `ObjectPanel` 视觉壳、属性列表、关闭行为和操作区；Scenario-specific 的 model inputs、model outputs、warnings 作为 panel 内扩展内容注入。这样 Graph/Scenario 不再维护另一套 selection-card 外观，同时保留 pinned revision、run provenance 和模型结果的领域字段。

本步只接入 Scenario object graph；ontology schema graph 的节点仍是 entity type metadata，不应伪装成 object instance，因此暂不强行套用对象 panel。下一步可在后端提供 instance-level graph contract 后，再接 Graph 的对象 panel adapter。

## 6. 与人员 A 的接口和文件所有权

### 人员 B 主要拥有

- `backend/app/routers/v2/logic_actions.py`
- `backend/app/services/v2/scenario_actions.py`
- `backend/app/services/v2/logic_assets.py` 与 `routers/v2/logic_assets.py`
- Action/Function/Scenario 的 compiler、preview、execution orchestration 与 logs
- `frontend/src/pages/ontologies/detail/tabs/ObjectQueryTab.tsx`
- `frontend/src/pages/ontologies/detail/entity/EntityDetailPage.tsx`
- `frontend/src/pages/ontologies/detail/action/ActionDetailPage.tsx`
- `frontend/src/pages/ontologies/detail/tabs/LogicAssetsTab.tsx`
- `frontend/src/pages/what-if/GenericScenarioWorkbenchPage.tsx`
- Graph/Object View/Explorer 新共享组件及其 Playwright tests
- temporal replay/stream 的运行时呈现、snapshot 与 Scenario/Object Query/Graph 的集成 adapter；不拥有 canonical ontology schema editor

### 从人员 A 消费，不自行复制

- canonical type/property/link/interface identifiers 与 generated client types；
- authorization projection 和 permission digest；
- metadata/data/view/scenario revision identity；
- edit batch 可引用的字段、关系与 migration aliases；
- stable error/capability envelope。

双方可并行：B 可以先做 Action semantic inventory、Explorer state reducer、Object Panel 无数据壳层和 Playwright fixtures；涉及真实 metadata/security/edit commit 的集成需等 A 的合同冻结。

## 7. 验证与验收证据

| 层级 | 最小验收证据 |
| --- | --- |
| Action contract | 相同 Action definition 在 preview、live、scenario 得到同序 edit batch；criteria/permission/unknown rule/conflict 有稳定结果；side effect 与 ontology commit 分开报告。 |
| Function contract | typed Object/Object Set/Interface/Struct 参数被验证；版本 range/pin 可重复；edit function 不能被直接应用；trace 不泄漏隐藏字段。 |
| Explorer component | filter chips、nested logic、pivot、set arithmetic、save static/dynamic、compare、pagination 和 URL restore 对 controlled API 响应产生正确 expression。 |
| Explorer visual fidelity | 每个核心状态保存 Palantir reference、实现截图和差异清单；评审壳层、查询栏、popover、Explore/Results、图卡、表格、preview、compare、modal/toast 的布局与交互。无理由偏离为未通过。 |
| Real integration | Postgres+FalkorDB+真实 authentication 下，从 Exploration 执行 Action 到 live/scenario，重启后 log、view、Object Panel 一致。 |
| Browser E2E | 覆盖键盘操作、刷新/前进后退、慢网/失败/expired view、权限拒绝、重复提交、conflict、窄屏；断言业务结果而非仅页面存在。 |
| Graph/Scenario | 同一 Object Set 在 live 与两个 scenario 中结果可比较；Graph selection 使用共享 panel；多步 traversal 与保存/恢复一致。 |
| Temporal integration | `alembic heads` 单 head；空库与两条旧 head 均可升级；file/push 相同事件产生相同 snapshot hash；重复事件幂等；late event 得到明确政策结果；snapshot ID 可被 Pinned Scenario、Object Query 和 Graph history 一致引用。 |
| Palantir temporal surface | Event 至少有 start/end timestamp 并作为标准 Object Type 被查询；TSP 通过 series ID + sync 解析，sensor link/default TSP/unit/interpolation 可发现；内部 TemporalFact 不能成为 UI 的专用旁路。 |
| Product validation | 10–15 个未预演问题，由新用户、领域用户、Agent 分别完成；记录答案、选择路径、耗时、困惑、求助和错误类型。 |
| Performance/soak | 记录 Explorer load/pivot/compare 和 Action preview/submit 的 p50/p95/p99、请求 fan-out、内存、取消与恢复；无重复请求和无界 trace。 |
| Static gates | `frontend`: `npm run lint:src`、`npm run lint:scripts`、`npm run build`、相关 `npm run test:e2e`；changed files 零 error/零 warning。Backend 执行相关 pytest contract/integration suites。 |

测试数据与人员 A 共享同一固定版本真实公开数据集和 checksum；B 额外为每个盲测问题保存 known-answer evidence，但不能把答案编码进 Agent prompt。Synthetic scale fixture 单独标注，用于大集合、分页、图 fan-out 与 compare 压力。

若相同夹具下反复出现 preview/live/scenario 三者不一致，或 Object Set 在 Explorer/Graph/Scenario 三入口结果不一致，触发有界 architecture review；优先检查是否仍存在第二个 compiler、context 丢失或 UI 自行解释 AST，而不是继续打入口专用补丁。

## 8. 风险与未决项

- Action 的外部 side-effect executor 当前能力有限；在没有幂等键、重试分类和安全凭据边界前，不应扩大到真实外部系统。
- configured Object View 第一阶段交付 standard view、panel reuse 和 configured schema/runtime；完整低代码 builder 必须独立估算，并作为明确后续阶段而非含糊 defer。
- Vertex 时间/事件能力已在 `origin/factorynet-temporal-workbench` 存在；首要风险是双 Alembic head、9 个重叠注册/UI 文件、双 Scenario catalog 和 FactoryNet demo 泄漏进通用 API，禁止另造第二套模型。
- Palantir Scenarios 仍是 Beta；实现其 Tracking 模式时要记录 2026-09-24 契约，同时保留 Pinned 作为明确扩展，避免把扩展伪装成 Palantir 行为。
- UI 大改容易与工作区现有未提交修改冲突，实施时必须按文件确认 ownership 并避免覆盖用户改动。

## 9. 差距参考文档

### Actions 与 Functions

- [Action types overview](https://www.palantir.com/docs/foundry/action-types/overview/)
- [Action rules](https://www.palantir.com/docs/foundry/action-types/rules/)
- [Parameters](https://www.palantir.com/docs/foundry/action-types/parameter-overview/)
- [Submission criteria](https://www.palantir.com/docs/foundry/action-types/submission-criteria/)
- [Permissions](https://www.palantir.com/docs/foundry/action-types/permissions/)
- [Read/write authorizations](https://www.palantir.com/docs/foundry/action-types/read-write-authorizations/)
- [Consistency guarantees](https://www.palantir.com/docs/foundry/action-types/consistency-guarantees/)
- [Test run](https://www.palantir.com/docs/foundry/action-types/test-run/)
- [Action log](https://www.palantir.com/docs/foundry/action-types/action-log/)
- [Action reverts](https://www.palantir.com/docs/foundry/action-types/action-reverts/)
- [Functions overview](https://www.palantir.com/docs/foundry/functions/overview/)
- [Functions types reference](https://www.palantir.com/docs/foundry/functions/types-reference/)
- [Ontology edits from Functions](https://www.palantir.com/docs/foundry/functions/edits-overview/)
- [Functions versioning](https://www.palantir.com/docs/foundry/functions/functions-versioning/)

### Explorer、Views、Scenarios 与 Vertex

- [Object Explorer overview](https://www.palantir.com/docs/foundry/object-explorer/overview/)
- [Getting started](https://www.palantir.com/docs/foundry/object-explorer/getting-started/)、[Search objects](https://www.palantir.com/docs/foundry/object-explorer/search-objects/)、[Explore charts](https://www.palantir.com/docs/foundry/object-explorer/explore-charts/) 与 [View results](https://www.palantir.com/docs/foundry/object-explorer/view-results/)：全量 UI 截图审计的主要来源。
- [Filter results](https://www.palantir.com/docs/foundry/object-explorer/filter-results/)
- [Pivot linked objects](https://www.palantir.com/docs/foundry/object-explorer/pivot-linked/)
- [Compare object sets](https://www.palantir.com/docs/foundry/object-explorer/compare-object-sets/)
- [Save explorations](https://www.palantir.com/docs/foundry/object-explorer/save-explorations/) 与 [Save lists](https://www.palantir.com/docs/foundry/object-explorer/save-lists/)
- [Object Views overview](https://www.palantir.com/docs/foundry/object-views/overview/)
- [Standard Object Views](https://www.palantir.com/docs/foundry/object-views/standard-object-views/)
- [Configured Object Views](https://www.palantir.com/docs/foundry/object-views/config-overview/)
- [Configure tabs](https://www.palantir.com/docs/foundry/object-views/config-tabs/)
- [Ontology Scenarios](https://www.palantir.com/docs/foundry/ontology/overview-ontology-scenario/)
- [Temporary scenarios](https://www.palantir.com/docs/foundry/ontology/temporary-scenario/)
- [Persisted scenario metadata](https://www.palantir.com/docs/foundry/ontology/persisted-scenario/)
- [Merge scenarios](https://www.palantir.com/docs/foundry/ontology/merge-scenario/)
- [Vertex overview](https://www.palantir.com/docs/foundry/vertex/overview/)
- [Explore object relationships](https://www.palantir.com/docs/foundry/vertex/explore-object-relationships/)
- [Vertex save/share/version](https://www.palantir.com/docs/foundry/vertex/save-share/)
- [Vertex scenarios](https://www.palantir.com/docs/foundry/vertex/scenarios-overview/)
- [Vertex events and time series](https://www.palantir.com/docs/foundry/vertex/events-overview/)
- [Time series overview](https://www.palantir.com/docs/foundry/time-series/time-series-overview/)
- [Time series properties](https://www.palantir.com/docs/foundry/time-series/time-series-properties/)
- [Time series concepts glossary](https://www.palantir.com/docs/foundry/time-series/time-series-concepts-glossary/)
- [Ontology design validation](https://www.palantir.com/docs/foundry/ontology/ontology-design-validation/)

### Palantir 官方公开源码

- [OSDK ActionDefinition](https://github.com/palantir/osdk-ts/blob/main/packages/api/src/ontology/ActionDefinition.ts)：公开 Action 参数类型包括 object、object set、interface、struct 等，并记录 modified entities。
- [OSDK ObjectSet](https://github.com/palantir/osdk-ts/blob/main/packages/api/src/objectSet/ObjectSet.ts)：where、pivot、set arithmetic、aggregate、derived properties、pagination 的类型化表面。
- [OSDK ObjectSet tests](https://github.com/palantir/osdk-ts/blob/main/packages/api/src/objectSet/ObjectSet.test.ts)：公开类型约束、interface narrowing、linked traversal 与 nearest-neighbor 限制的测试证据。
- [Foundry Platform TypeScript SDK Ontology package](https://github.com/palantir/foundry-platform-typescript/tree/develop/packages/foundry.ontologies/src/v2/public)：公开 Action、Object Set、Scenario、Interface 与 Value Type API 客户端。公开客户端形状不等于 Palantir 私有后端实现。

### 当前仓库证据

- `backend/app/models/v2/action.py`
- `backend/app/routers/v2/logic_actions.py`
- `backend/app/models/v2/logic_asset.py`
- `backend/app/services/v2/logic_assets.py`
- `backend/app/routers/v2/logic_assets.py`
- `backend/app/services/v2/scenario_actions.py`
- `backend/app/models/v2/scenario.py`
- `backend/app/services/v2/scenarios.py`
- `backend/app/schemas/v2/object_query.py`
- `backend/app/services/v2/object_query/*`
- `frontend/src/pages/ontologies/detail/tabs/ObjectQueryTab.tsx`
- `frontend/src/pages/ontologies/detail/entity/EntityDetailPage.tsx`
- `frontend/src/pages/ontologies/detail/action/ActionDetailPage.tsx`
- `frontend/src/pages/ontologies/detail/tabs/LogicAssetsTab.tsx`
- `frontend/src/pages/what-if/GenericScenarioWorkbenchPage.tsx`
- `origin/factorynet-temporal-workbench:backend/app/models/v2/temporal_replay.py`
- `origin/factorynet-temporal-workbench:backend/app/models/v2/dynamic_ontology.py`
- `origin/factorynet-temporal-workbench:backend/app/routers/v2/dynamic_data.py`
- `origin/factorynet-temporal-workbench:backend/app/routers/v2/temporal_replays.py`
- `origin/factorynet-temporal-workbench:backend/app/routers/v2/temporal_streams.py`
- `origin/factorynet-temporal-workbench:backend/app/services/v2/dynamic_ontology_service.py`
- `origin/factorynet-temporal-workbench:backend/app/services/v2/temporal_replay_service.py`
- `origin/factorynet-temporal-workbench:backend/app/services/v2/temporal_stream_service.py`
- `origin/factorynet-temporal-workbench:backend/app/services/v2/what_if_service.py`
- `origin/factorynet-temporal-workbench:frontend/src/pages/data-management/dynamic/DynamicDataPage.tsx`
- `origin/factorynet-temporal-workbench:frontend/src/pages/ontologies/detail/tabs/DynamicEvolutionTab.tsx`

## 10. 决策摘要

- **保留：** Object Query/Object Set 后端、Scenario immutable revision/data view、Logic Asset contracts/runs/traces、现有图组件，以及 Joey 的 temporal ingestion/replay/checkpoint 底座；内部 temporal fact 不等于 canonical Ontology 时间表面。
- **实施前必须改变：** Action 只能有一个 compiler/edit batch 语义；Function edit 必须经 Action；Explorer/View/Graph 不得各自解释类型和权限。
- **视觉决定：** Object Explorer 默认高保真复刻 Palantir 官方截图；保持本项目品牌、中文、可访问性和响应式，所有其他偏离都需记录。
- **组件决定：** 继续采用 React Query、Cytoscape/XYFlow、Playwright 的现有角色；低代码 builder 在稳定配置 runtime 后独立立项，不引入新的图数据库产品。
- **Integration gate：** 当前工作区形成可回滚提交后，把 `origin/factorynet-temporal-workbench` 以保留历史的 `--no-ff` 方式选择性集成；解决双 migration head、重叠注册/UI 文件和双 Scenario catalog，只启用符合 Palantir 资源边界的入口，并通过 temporal + Scenario 联合验证。
- **第一阶段：** 在 integration gate 通过后，完成 Action semantic inventory 与统一 edit-batch contract，同时按官方 UI 搭建 Explorer shell/state reducer 和共享 Object Panel。
- **进入后续阶段的证据：** preview/live/scenario 对同一 Action 产生一致 edits；Object Set expression 能从 URL/UI 无损往返；核心 Explorer 状态通过 reference screenshot review；Object Panel 在 Explorer/Graph/Scenario 三处使用同一组件和授权结果。
