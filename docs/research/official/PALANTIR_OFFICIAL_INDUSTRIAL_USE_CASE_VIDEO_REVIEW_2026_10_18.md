# Palantir 官方工业案例视频审计：供应链、生产与工厂情景推理

> 审计日期：2026-10-18  
> 选片规则：Palantir 官方 YouTube 频道；优先 2026，最早 2025；必须有明确业务用例；只提取 Ontology 平台和情景决策相关能力。  
> 证据边界：截图证明画面中可见的 UI；字幕和演讲说明业务流程；客户成果陈述不等同于可复现性能测试。AIP 的聊天或自治能力不进入近期建设范围，但其背后的对象、约束、Action、审批和审计契约可以进入 Ontology 平台设计。

## 1. 入选视频

| 年份 | 官方视频 | 行业用例 | 本报告用途 |
|---|---|---|---|
| 2026 | [Agentic Supply Chain for Data Center Delivery — Nscale](https://www.youtube.com/watch?v=oSsEaz36DLU) | 数据中心建设、BOM 变更、采购和安装里程碑 | 多角色供应链协同、变更传播、交付排程 |
| 2026 | [Sovereign AI for the Compute Supply Chain — NVIDIA](https://www.youtube.com/watch?v=xZBaOenSZ9I) | 芯片/计算设备短缺下的受限物料分配 | 多层 BOM、供应承诺、库存滞留最小化、what-if |
| 2026 | [Delivering the Power Infrastructure for AI — Eaton](https://www.youtube.com/watch?v=m8v0lu2Ygyk) | Engineer-to-order 配置、材料和供应商可行性 | 需求到 BOM、供应、工厂、报价和交期的影响链 |
| 2026 | [Connecting Chemistry, Operations and Commercial — Hexion](https://www.youtube.com/watch?v=Qi9PsPw8OXM) | VMI 补货、化工原料采购、危险原料成本优化 | 跨销售、运营、生产和采购传播决策 |
| 2026 | [Trinity Industries at Palantir Sovereignty Bootcamp](https://www.youtube.com/watch?v=5VEWFMFCd5Y) | 轨道车辆工业运营 | 仅作为候选背景；无可用字幕和本地产品画面，证据不足，不参与 UI 结论 |
| 2025 | [AIP for Just-in-Time Production — Lear](https://www.youtube.com/watch?v=-DPdyQR1bG4) | 汽车座椅 JIT 生产、动态排程、物料与产线异常 | 2026 样本未完整覆盖的生产排程操作界面 |
| 2025 | [Enhancing Production Efficiency with MRPSpeed — L3Harris](https://www.youtube.com/watch?v=EX1T2CEJzL4) | 复杂装备生产、缺件、库存解锁和收入保护 | 生产可行性逐层钻取与建议动作 |
| 2025 | [Revolutionizing Supply Chain Agility for the Frosty — Wendy's QSCC](https://www.youtube.com/watch?v=8wX9YjrR_1k&t=297s) | 易腐品库存、门店订单、DC 调拨、生产与原料计划 | 网络库存异常到批量执行的完整闭环 |
| 2025 | [AI-Driven Supply Chain Transformation — Heineken](https://www.youtube.com/watch?v=uo6y_0DG29I) | 进口集装箱、仓库需求、客户订单和装柜调整 | 预测性物流与最后时刻的订单/容器重分配 |

被排除的 2023 及更早视频包括 PENA Connected Manufacturing、Integrity Tool & Mold、Eaton Material Shortages 和旧版 Supply Chain Resilience。它们没有用于本报告的主结论。

## 2. 跨视频反复出现的产品结构

```mermaid
flowchart LR
  A[业务事件或需求变化] --> B[Ontology 对象与关系]
  B --> C[影响范围和证据]
  C --> D[候选动作/方案]
  D --> E[约束、成本、交期与风险检查]
  E --> F[人工复核和发布]
  F --> G[Action 写回与后续任务]
  G --> B
```

五个有效案例虽然行业不同，但 UI 和运行方式高度一致：

1. 用户从一个业务问题进入，例如 BOM 改版、物料分配、订单配置或产线异常。
2. 系统用 Ontology 展示受影响的订单、物料、供应商、工厂、任务和承诺。
3. 用户看到候选动作及其依据，而不是直接面对底层函数节点。
4. 系统同时检查交期、库存、产能、认证、价格和优先级等约束。
5. 候选方案先预览，再由业务人员复核、发布或应用。
6. 决策结果写回 Ontology，并触发采购、排程、通知或下游工作流。

这意味着本项目的 `StudyGraphCanvas` 适合模型设计者；业务用户的主页面应围绕事件、对象、候选方案、影响、约束和提交状态组织。

## 3. Nscale：数据中心交付供应链

### 3.1 业务问题

建设数据中心需要协调设计 BOM、采购申请、供应商、合同、PO、运输、安装和调试。一个 BOM revision 或延期部件可能影响多个 PO 和安装里程碑。人工跨系统排查容易漏掉变更传播和替代方案。

### 3.2 可见 UI

![Nscale BOM 变更工作区](evidence/palantir_official_industrial_videos/oSsEaz36DLU/screenshots/02-agent-workspace.jpg)

左侧不是通用功能菜单，而是按领域分组的对象与流程：Infrastructure、Supply Chain、Design、Operations。主区域以一个 **BOM Update Detected** 事件为中心，分为变化、受影响 PO 和待复核项。多个专用角色（Supply Chain、Procurement、Schedule）共享同一业务上下文。

![Nscale 交付排程与推荐动作](evidence/palantir_official_industrial_videos/oSsEaz36DLU/screenshots/04-project-schedule.jpg)

页面同时展示在途货物、准时率、风险货物、平均提前期、里程碑甘特图、推荐动作、解释、替代路径和 Apply 按钮。它解决的是“某个部件延期会影响哪个里程碑，以及现在应该改供应商还是改计划”。

### 3.3 功能解决了什么

| 功能 | 解决的问题 |
|---|---|
| BOM revision diff | 明确 Rev 5 → Rev 6 改了哪些部件和数量 |
| 影响 PO 列表 | 找出仍按旧版 BOM 执行的采购单 |
| 供应链对象导航 | 从部件跳到供应商、合同、PO、运输和站点 |
| 甘特图与 milestone impact | 将物料延期转成可理解的施工/安装日期影响 |
| 推荐替代路径 | 比较加急、替代供应商、调整工序等方案 |
| Apply + confidence/evidence | 人工确认后才执行，并保留建议依据 |

### 3.4 转成我们的测试

- BOM v1 改为 v2：删除一个部件、增加一个替代部件、数量翻倍，准确找出受影响 PO。
- PO 已部分到货时改 BOM，不能把全部数量当作未采购。
- 替代供应商可满足交期但未认证时，必须阻止 Apply。
- 上游交付推迟 12 天，传播到安装里程碑并显示关键路径和浮动时间。
- Apply 前基础 PO 被别人修改，必须检测 revision 冲突。

## 4. NVIDIA：受限计算供应链的物料分配

### 4.1 业务问题

复杂计算系统由多层 BOM、数千供应商和多座工厂共同完成。同一种受限芯片可能被多个产品和客户争抢。目标不只是提高产量，还要减少因为缺少另一种物料而形成的 excess 和 stranded inventory。

### 4.2 可见 UI

![NVIDIA 供应链运营视图](evidence/palantir_official_industrial_videos/xZBaOenSZ9I/screenshots/01-chip-ontology.jpg)

首页把网络流向、风险任务队列、客户需求、库存趋势和约束热力图放在同一屏。演讲明确列出 Ontology 中的 materials、manufacturers、sites、commits、capacity 和 allocations。

![NVIDIA 方案复核和发布](evidence/palantir_official_industrial_videos/xZBaOenSZ9I/screenshots/03-what-if-scenario.jpg)

Review & Publish 页面显示材料满足率、客户/承诺约束、库存风险和按客户或产品的分配表。演讲给出的 what-if 包括供应减少 10% 和增加另一个工厂站点，并强调把分析员的分配决策保存为后续评估样本。

### 4.3 功能解决了什么

| 功能 | 解决的问题 |
|---|---|
| 多层 BOM 和供应承诺模型 | 判断某一芯片短缺最终影响哪些成品和客户承诺 |
| constrained material allocation | 在物料不足时明确分给谁、为什么 |
| stranded inventory 指标 | 避免只追求产量而制造大量无法完工的半成品 |
| 供应风险热力图 | 让分析员先处理高风险供应商、工厂和物料 |
| what-if 比较 | 快速评估供应下降、工厂切换和产能变化 |
| Review & Publish | 将方案从模拟变为可复核、可发布的业务决策 |
| 决策结果留存 | 比较系统建议与分析员最终决定，形成可审计反馈 |

### 4.4 转成我们的测试

- 关键芯片供应减少 10%，验证受影响产品、订单和 stranded inventory。
- 两种产品共享同一芯片，在客户优先级和最低服务约束下验证分配。
- 增加新工厂但缺少认证工艺或运输路线时，产能不能被直接计入。
- “最大化产量”和“最小化滞留库存”给出不同方案时，显示目标权衡和 Pareto 候选。
- 用户手工覆盖系统分配，必须保存理由、修改前后 delta 和约束状态。

## 5. Eaton：Engineer-to-order 的需求、BOM 与交期

### 5.1 业务问题

在 engineer-to-order 环境里，一个客户需求变化会决定产品配置、BOM、可用供应商、制造工厂、成本、风险和承诺交期。问题不是更快读取规格文档，而是把需求转成结构化、可追踪、可复核的设计与供应决策。

### 5.2 可见 UI

![Eaton 需求到 BOM 的影响链](evidence/palantir_official_industrial_videos/m8v0lu2Ygyk/screenshots/03-impact-chain.jpg)

左侧保留 Chain of Thought / evidence 步骤，右侧 BOM Availability Planning 汇总可用项、长提前期项、关键项、建议动作和详细物料表。字幕说明决策者可以在一个地方看到从需求到配置、BOM、供应商、工厂和承诺的完整影响链。

![Eaton 多方案报价与供应替代](evidence/palantir_official_industrial_videos/m8v0lu2Ygyk/screenshots/04-supplier-mitigation.jpg)

Quotation Options 同时比较预工程、推荐和按原规格三个候选，展示价格、预计交期、设计/供应状态和成本增量。案例中第二供应商可以消除长提前期，但需要结合技术与商业约束复核。

### 5.3 功能解决了什么

| 功能 | 解决的问题 |
|---|---|
| requirement traceability | 证明某个 BOM 和供应决定源自哪条客户要求 |
| similar build / exception 检查 | 复用历史经验并发现新配置异常 |
| BOM availability planning | 在承诺交期前识别长提前期和关键物料 |
| 第二供应商方案 | 将替代来源对交期、成本和风险的影响一起展示 |
| 多报价候选 | 让销售、工程、采购和客户在同一证据上权衡 |
| evidence + human accountability | 人对决定负责，系统保留步骤和证据 |

### 5.4 转成我们的测试

- 客户把额定参数提高一级，验证配置、BOM、工艺、认证和交期联动。
- 第二供应商交期短但单价高，输出成本—交期—风险三维比较。
- 历史相似项目使用旧版规范时，不得直接复用为当前批准依据。
- 一条需求同时影响多个子组件，必须显示所有路径并去重聚合成本。
- 报价发布前某个关键物料变为不可用，方案应自动标记 stale 并重新验证。

## 6. Hexion：化工 VMI、生产与原料采购

### 6.1 业务问题与流程

Hexion 案例把客户、产品、工厂、原料、供应商、合同、价格和运输放进共同 Ontology。字幕描述了两条具体链路：

1. **Vendor-managed inventory**：预测客户消耗，必要时加急现有订单，重新安排客户交付；模型考虑消耗率、交付窗口、船期和订单合并，然后确认订单并写回 Ontology 和下游系统。
2. **危险原料采购**：产品需求变化传播到原料需求；系统比较供应协议、全球指数等价格来源，为某工厂的 phenol 生成采购方案，并用 cost bridge 解释供应商分配如何形成预期节省。

本地无法取得该视频的产品画面，因此这里不把字幕描述转换成具体 UI 复刻要求。

### 6.2 功能解决了什么

| 功能 | 解决的问题 |
|---|---|
| 消耗预测 + VMI 规则 | 在客户断货之前调整订单和交付 |
| 运输与合单约束 | 避免只按库存阈值补货造成低效运输 |
| 需求向原料传播 | 将销售变化及时转换为原料采购计划 |
| 多价格来源与合同 | 防止用错误或过期价格比较供应商 |
| cost bridge | 解释节省来自价格、数量、分配还是运输变化 |
| evidence-backed Action | 决策写回时保留数据、规则和人工确认 |

### 6.3 转成我们的测试

- 客户消耗突然增加 25%，比较加急旧订单、新建订单和合并运输。
- 交付窗口、船期和最低装载率互相冲突时，返回不可行原因。
- 产品需求变化只传播到实际使用的原料及对应工厂。
- 危险原料只能从合规供应商采购，价格更低不能覆盖 hard constraint。
- 价格指数过期、合同币种不同或单位不一致时阻止成本比较。
- cost bridge 各项必须能复算到总成本 delta。

## 7. Lear：JIT 生产和动态排程（2025 补充案例）

### 7.1 业务问题

汽车座椅从固定客户订单到装车窗口时间很短。系统需要连续接收订单、Tier-2 来料、产线吞吐、库存容量和异常，并动态生成生产顺序；计划必须经车间团队判断后才执行。

### 7.2 可见 UI

![Lear 产线排程工作区](evidence/palantir_official_industrial_videos/-DPdyQR1bG4/screenshots/02-scheduling-pipeline.jpg)

主视图把不同产线或工位组织为并列列，卡片代表具体生产任务。它更接近可操作排程板，不是函数节点图。

![Lear 排程验证与 Ontology edits](evidence/palantir_official_industrial_videos/-DPdyQR1bG4/screenshots/03-schedule-simulation.jpg)

Schedule Lot Job 对话框显示 deadline、priority、建议任务、阶段进度、冲突验证和最终 Ontology edits；成功后仍提供 Revert。字幕强调 Foundry 提供决策层，车间人员提供实际判断。

### 7.3 功能解决了什么

| 功能 | 解决的问题 |
|---|---|
| 动态生产排程 | 应对持续进入的订单、来料和产线状态变化 |
| 库存分配 | 避免排了任务却没有正确物料 |
| deadline validation | 确保插单或换线后仍满足装车窗口 |
| production/inventory alerts | 让人员先处理会影响时间敏感交付的异常 |
| Ontology edits + Revert | 把排程结果变成可审计业务状态，并允许撤销 |
| human-on-the-loop | 现场团队确认计划是否符合真实车间条件 |

### 7.4 转成我们的测试

- 紧急订单插入后重排同一资源，不能造成任务重叠。
- 来料延迟、吞吐下降和成品库存已满三种异常分别验证传播。
- 换线固定时间、单件时间和批量必须正确计算总周期。
- 人工固定一个不可移动任务后，求解器只能调整剩余任务。
- Apply 后执行 Revert，排程、库存占用和审计记录必须一致恢复。

## 7A. L3Harris MRPSpeed：从收入风险钻取到缺件动作

### 7A.1 展示的 UI

![L3Harris 产品组合与生产状态](evidence/palantir_official_industrial_videos/EX1T2CEJzL4/screenshots/01-production-portfolio.jpg)

Demo 先从生产组合和收入风险进入，而不是先要求用户选择模型。Demand Explorer 顶部用 KPI 表示风险收入、需求、可生产数量和缺件数，中间用红蓝柱状图比较计划需求与实际可交付，底部是受影响产品或订单。用户点击异常后逐层进入产品、需求、BOM 和缺料。

![L3Harris BOM 缺件钻取](evidence/palantir_official_industrial_videos/EX1T2CEJzL4/screenshots/03-bom-shortage-drilldown.jpg)

这个视图解决“延期是因为许多零件，还是一个关键零件”的问题。它把聚合指标与行级证据放在一页：上方显示需求/交付差距，表格显示产品、客户、数量、日期和状态；继续钻取后可查看库存、在途量、BOM 缺口和建议动作。

![L3Harris 建议动作](evidence/palantir_official_industrial_videos/EX1T2CEJzL4/screenshots/04-suggested-actions.jpg)

后段不止报告缺件，还列出可以催料、加急工单、重新分配库存或避免继续采购其余 BOM 的具体动作。其价值在于防止“因为缺一个关键件，其他所有零件仍继续到货并形成滞留库存”。

### 7A.2 UI 对应解决的问题

| UI 设计 | 解决的问题 |
|---|---|
| 收入/需求/可生产量 KPI | 把物料问题翻译成管理者能判断优先级的业务影响 |
| 计划与实际柱状图 | 快速找到哪个月份、产品或客户承诺出现缺口 |
| 层级 drill-down | 从收入风险一路定位到订单、产品、BOM 和单个短缺件 |
| inventory availability | 区分现有库存、在途、已分配和真正可重分配库存 |
| suggested actions | 告诉用户该催哪个供应商、加急哪个工单或调拨哪批库存 |
| 避免无效 BOM 到货 | 缺关键件时减少其余组件继续采购形成的现金占用 |

### 7A.3 可复现测试

- 一个成品包含 20 个组件，其中 19 个充足、1 个短缺；系统必须定位唯一瓶颈，并计算受影响收入和订单。
- 同一短缺件在库、在途和已预留数量不能重复计算。
- 从低优先级订单重分配库存给高优先级订单时，必须显示被牺牲订单和净收入变化。
- 催料无法赶上交期时，建议动作不能显示为可行。
- 关键件缺失后，其余 BOM 的新采购建议应暂停或解释继续采购的理由。

## 7B. Wendy's QSCC：易腐库存的网络级闭环

### 7B.1 展示的 UI

![Wendy's 门店库存异常与建议](evidence/palantir_official_industrial_videos/8wX9YjrR_1k/screenshots/02-store-inventory-recommendation.jpg)

左侧是异常队列；中间地图定位 DC 和门店；右侧解释订单、现有库存、未来配送和行动建议。案例不是统一削减订单：库存超过三天的门店被削减，库存不足的门店仍保留部分订单，因此 UI 必须展示逐门店的规则结果和例外。

![Wendy's 门店级订单调整结果](evidence/palantir_official_industrial_videos/8wX9YjrR_1k/screenshots/03-order-reallocation-result.jpg)

操作后页面保留原始数量、建议数量、覆盖天数和状态，使用户能核查哪些门店被完全取消、部分保留或没有变化。批量动作必须允许展开行级结果，不能只显示“节省了多少箱”。

![Wendy's 供应商与网络短缺](evidence/palantir_official_industrial_videos/8wX9YjrR_1k/screenshots/04-network-shortage.jpg)

第二阶段从门店/DC 问题扩大到供应商网络：展示可用 8,300 箱、缺口 10,200 箱和必须立即订购的 3,500 箱，并列出供应点和替代路径。

![Wendy's 预测库存与生产需求](evidence/palantir_official_industrial_videos/8wX9YjrR_1k/screenshots/05-projected-inventory.jpg)

库存曲线明确显示在哪一天跌为负数，并把缺口继续展开为成品生产量、原料缺口、生产日期、DC 发运和到货时间。视频 04:57–07:58 的核心是一个跨层情景，而不是单独的库存告警。

![Wendy's 批量执行建议订单](evidence/palantir_official_industrial_videos/8wX9YjrR_1k/screenshots/06-execute-recommended-orders.jpg)

最终通过 Execute Recommended Orders 批量下单并更新状态，随后执行原料采购和生产动作。这说明 UI 需要“预览全部修改 → 确认 → 逐项执行状态 → 最新状态重算”的事务式体验。

### 7B.2 UI 对应解决的问题

| UI 设计 | 解决的问题 |
|---|---|
| 异常队列 | 从数千门店和订单中先处理会造成浪费或断货的问题 |
| 地图 + 站点详情 | 理解 DC、门店和替代配送节点的关系；本项目近期可用关系图和分组表替代地图 |
| 规则建议与逐店例外 | 根据库存覆盖天数削减订单，同时保护库存不足门店 |
| 网络库存曲线 | 预测何时跌破零，而不是只看当前库存 |
| 原料—生产—配送时间线 | 确保补货动作在需要日期前真正完成 |
| 批量执行与状态更新 | 把建议转成真实订单，同时保留行级执行结果 |

### 7B.3 可复现测试

- 100 家门店中分别存在过量、刚好和不足库存；规则不得一刀切。
- 门店下一次卡车到达时间晚于库存耗尽时间时，不能取消全部订单。
- 一个 DC 短缺、另一个 DC 有余量时，比较调拨和新生产的成本与交期。
- 成品缺口 5,800 箱、现有原料只能生产 3,000 箱时，剩余 2,800 箱缺口不能消失。
- 生产、原料采购和 DC 发运必须按时间依赖排序，任一步延迟都要重新计算断货日。
- 重复点击批量执行不得创建重复订单；部分失败必须显示逐项回执。

## 7C. Heineken：未来需求、在途运输与装柜重分配

### 7C.1 展示的 UI

![Heineken 未来订单与在途供应](evidence/palantir_official_industrial_videos/uo6y_0DG29I/screenshots/01-forward-supply-view.jpg)

案例把 distributor inventory、未来三周订单、船舶位置、集装箱和九个需求仓库统一起来。主页面用时间视图和状态表让用户看到“现在有什么、海上有什么、什么时候到、未来会卖多少”。

![Heineken 订单优先级操作](evidence/palantir_official_industrial_videos/uo6y_0DG29I/screenshots/02-order-prioritization.jpg)

操作对话框要求选择具体订单/需求点和优先级，再生成或提交动作。这解决了多个客户争用有限库存时的执行顺序问题；演讲说明系统根据下单先后、三周 lead time 和供应状态决定拉前哪些订单。

![Heineken 港口容器优先级](evidence/palantir_official_industrial_videos/uo6y_0DG29I/screenshots/03-container-priority.jpg)

港口停留会产生 demurrage。界面把集装箱、到港日期、仓库需求和积压风险放在同一张列表，让用户选择优先提取哪些箱，而不是简单按“停得最久”排序。

![Heineken 装柜前目的地交换](evidence/palantir_official_industrial_videos/uo6y_0DG29I/screenshots/04-late-swap-decision.jpg)

生产完成到装柜前仍允许把货物从一个目的地换到另一个需求更紧急的仓库。弹窗列出可选订单/目的地并要求人工确认；算法必须同时检查现有 booking、产品、数量、截止时间和新目的地需求。

### 7C.2 UI 对应解决的问题

| UI 设计 | 解决的问题 |
|---|---|
| forward-looking supply view | 合并未来订单、库存和海运 ETA，提前发现断货 |
| 订单优先级 Action | 有限库存下决定先满足哪个客户或仓库 |
| container action queue | 综合滞港费与业务紧迫度安排提箱 |
| late swap dialog | 装柜前利用最新需求调整目的地，减少错误库存 |
| 执行工作流与状态 | 让建议进入运输和仓库流程，并跟踪是否执行成功 |

### 7C.3 可复现测试

- 船舶 ETA 变化和仓库需求上涨同时发生，重新计算三周供需缺口。
- 最早到港容器不是最高优先级时，解释业务优先级与滞港费的权衡。
- 同一库存不能同时分配给两个客户订单。
- 目的地交换必须验证 booking cut-off、运输路线、数量和仓库接收能力。
- 交换后原目的地不得静默短缺，必须生成新的影响和候选补救动作。
- 已进入不可逆运输阶段的 container 不得继续显示可交换。

## 8. 对当前系统的综合判断

### 8.1 可以直接复用

- `ScenarioStudyDefinition → Version → Case → RunPlan → Run` 已提供模型版本和运行复现基础。
- Object Query、Scenario revision、Action preview/apply、merge-preview 和 revert 已覆盖关键写入边界。
- 多级 BOM、替代供应、产能排程、hard/soft constraints、solver 可行性和异步运行已有实现或测试。
- XYFlow 计算图可继续用于模型设计；对象浏览和关系图可以承载影响路径。
- 规则 proposal review 和禁止 self-approval 可以作为方案级审批的底层能力。

### 8.2 最需要新增或整合

| 优先级 | 能力 | 来自哪些案例 | 当前缺口 |
|---|---|---|---|
| P0 | 业务事件中心工作区 | Nscale、Lear | 当前入口以 Study/Case 为中心，缺少 BOM change、shortage、late shipment、production alert 等一等事件 |
| P0 | 可解释影响路径 | 全部 | 结果指标和对象图存在，但缺少 action → object edges → constraints → KPI 的统一 artifact |
| P0 | Decision Package | NVIDIA、Eaton、Lear | 缺少方案级输入、证据、结果、理由、审批和写回 digest |
| P0 | 方案级 review/publish/apply/revert | NVIDIA、Lear、Hexion | 底层能力分散在 Rule、Action 和 Scenario 页面 |
| P1 | 资源余量和瓶颈视图 | NVIDIA、Lear | 当前约束表缺少 capacity headroom、关键资源和时段解释 |
| P1 | 时间表与关键路径 | Nscale、Lear | 有排程计算，但缺少业务甘特图、milestone impact 和固定任务交互 |
| P1 | 多候选商业比较 | Eaton、Hexion | 当前 delta 表缺少成本桥、交期、风险和合规的并列解释 |
| P1 | 决策覆盖与理由 | NVIDIA、Eaton | 缺少用户 override、rationale、约束复验和后续效果追踪 |
| P1 | stale/freshness 门禁 | 全部 | 数据、BOM、供应承诺或价格变化后，旧方案应自动失效 |

### 8.3 不应照搬

- 不把 AIP 聊天框或多个 agent 卡片作为 Ontology 平台的核心交付。
- 不根据舞台 Demo 推断没有展示的后端安全、事务或性能能力。
- 不把地图作为近期要求；当前项目已决定暂缓地理空间能力。
- 不把每个行业案例做成专用 router 和专用数据表。它们应共享 Study、Scenario、Object、Link、Constraint、Action、Decision Package 和 Evidence 契约。

## 9. 新的用例测试库

### 9.1 六条端到端业务链

| ID | 问题 | 端到端链路 | 主要断言 |
|---|---|---|---|
| UC-01 | 供应商停供两天，会缺哪些料？ | supplier → material → BOM → work order → order | 完整影响路径、缺口、恢复时间、无关对象不受影响 |
| UC-02 | BOM 改版会影响哪些采购和里程碑？ | revision diff → affected PO → shipment → install milestone | 数量/版本正确、部分到货正确、关键路径正确 |
| UC-03 | 关键芯片少 10% 应分给谁？ | constrained material → products → customers → commits | hard constraints、目标权衡、滞留库存、人工覆盖理由 |
| UC-04 | 第二供应商能否缩短 ETO 交期？ | requirement → configuration → BOM → supplier → quote | 认证、价格、lead time、工厂能力和报价 delta |
| UC-05 | 客户消耗增加是否要加急 VMI？ | consumption → inventory → orders → vessel/window → delivery | 断货时间、合单、运输约束和写回幂等 |
| UC-06 | 紧急订单能否插入 JIT 产线？ | order → materials → tasks → resource schedule → delivery | 无资源重叠、换线时间、deadline、Apply/Revert |

### 9.2 跨用例边界

- 环状 BOM、断链关系、重复边、超过最大深度和极端扇出。
- 零/负库存、未知单位、币种不一致、时区跨日、价格或承诺过期。
- 供应商有容量但认证失效；工厂有理论能力但缺少工艺或人员资格。
- 候选方案在审批期间变 stale；发布前必须重新绑定基线并复验。
- 同一事件产生重复消息时，Action、PO 和排程不得重复创建。
- 人工覆盖最优解时必须保存理由，且不能绕过 hard constraint。
- 部分写回失败时显示逐项回执，支持重试、补偿或人工处理。
- Apply 后 Revert 要恢复对象属性、关系、库存占用和时间表，并保留两次审计记录。

## 10. 推荐的产品页面

```text
┌ 事件：关键物料 M-42 短缺 ─ 状态 / 优先级 / 数据新鲜度 / 负责人 ┐
├ 业务上下文 ───────────┬ 候选方案 ───────────┬ 约束与风险 ───┤
│ 物料、BOM、订单、客户   │ A 原计划 / B 替代采购 │ 认证、产能、交期 │
│ 供应商、工厂、库存      │ C 改排程 / D 分配优先 │ 成本、库存、质量 │
├ 影响路径 ─────────────┴────────────────────┴───────────────┤
│ 事件 → 物料 → 工单 → 资源 → 订单；可切换时间表和指标比较     │
├ 证据、假设、人工修改理由 ───── 保存 · 提交审核 · 发布/应用 ─┤
└─────────────────────────────────────────────────────────────┘
```

业务页面默认显示对象、影响、候选和约束。模型 DAG、函数版本、端口、JSON 和预算继续放在“模型配置”中。这既保留当前已经完成的通用建模能力，也更接近这些官方案例的实际操作方式。

## 11. 证据与限制

- 已核对所有入选视频的官方频道和上传日期；主样本为 2026，Lear 为 2025 补充。
- 已下载并检查 Nscale、NVIDIA、Eaton、Lear 的本地视频画面；Hexion 使用官方视频字幕和元数据，未取得可用视频文件，因此没有截图结论。
- Nscale 没有可用字幕，相关 UI 结论只来自画面中可读内容；没有把听不清的演讲内容补写成事实。
- Trinity 缺少字幕和可用画面，保留在候选表但没有进入功能或 UI 结论。
- 本报告复用前一份 NHS 报告对当前仓库的代码审查结果，并进一步对照这些案例；没有重跑缺少 `pytest` 的本地 Python 环境。
- 视频文件和截图位于 `docs/evidence/palantir_official_industrial_videos/`，作为本地分析证据，不应提交完整视频。

