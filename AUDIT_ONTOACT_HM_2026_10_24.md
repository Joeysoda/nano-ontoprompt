# OntoAct：NG AI SDE 招聘视角审视与修改清单

审视日期：2026-10-24。对象：当时的本地工作树，含尚未提交的文件；不是对 GitHub 默认分支或线上部署的确认。此文保留修改前的问题快照；后续实现与实测结果见 `docs/showcase/VERIFICATION.md`。

## 判断与审视预算

如果我是招 NG AI SDE 的 HM：这个项目有值得面试的工程内容，但首页使它看起来像一个不断扩张的 AI 生成平台。最大问题是作者的判断力被功能清单掩盖，部分实现又没有落实文档承诺的边界。不能凭这些推断作者不懂或代码来自 AI；它们是降低工程可信度的证据。

以下概率仅是本次审视采用的主观工作假设，不是招聘行业统计；分母是已经点进项目的技术评审者。各阶段可重叠。

| 评审行为 | 假设发生概率 | 本次努力占比 | 评审者需要立即得到什么 |
|---|---:|---:|---|
| 看首页、README 前几屏 | 约 90% | 45% | 谁遇到什么问题、实际结果、能否快速看懂 |
| 抽查 2–4 个实现/测试文件 | 约 35% | 35% | 边界、失败处理、独立验证、作者取舍 |
| 点演示或看短视频 | 约 15% | 15% | 一个完整且可理解的业务闭环 |
| 自己部署 | 约 3% | 5% | 一条确定的命令、固定版本、可复现结果 |

部署发生概率低，但失败的负面影响很大；应把成本集中在一条可靠的标准演示路径。代码抽查重点选缺料计算、Decision Package、LLM 解释、NL→Cypher、Function runner 和对应测试。

## 已有的可信工程内容

- `backend/app/services/v2/material_shortage.py` 将计算与持久化分开，明确 all-or-nothing 分配、候选复用相同模拟器、不宣称全局最优，并限制输入规模和 BOM 深度。
- `backend/unit_tests/test_material_shortage_decision.py` 验证共享物料不能重复分配、失败订单释放暂存物料、有效承诺和 BOM 环。这些比截图和测试数量更能说明理解业务不变量。
- `backend/tests/test_material_decision_package_flow.py` 覆盖证据 digest 篡改、幂等重试、拒绝自审、过期 ETag、过期场景版本，并有真实 PostgreSQL 并发审核测试定义。注意本次没有成功运行这一整套测试。
- README 主动说明模拟数据、fake ERP、非因果推断和授权迁移未完成。这种诚实应保留，并前置与主线直接相关的限制。
- Function runner 确实独立容器运行，配置了只读文件系统、非特权用户和资源限制；它是有价值的设计，但现有约束不足以支持笼统的强安全承诺。

## P0：投递前优先完成

### 1. 首页从内部交付说明改成可验证的产品说明

**证据：** `README.md:3` 开头介绍 feature 分支、codex 分支、Plan A/B、Plan A 2.0 和多个集成模块。标题是 Ontology Decision Workbench，目录/clone 地址是 nano-ontoprompt，用户称为 OntoAct。第一张表仍以内部计划组织。

**HM 的疑虑：** 我不知道实际做成了什么，只看到一串架构术语。作者是否只是在追着 AI 生成的计划扩写？

- [ ] 统一对外项目名为 OntoAct；旧仓库名可以保留，但说明一次即可。
- [ ] 首屏放一句用户问题、一张自有产品截图、一个短视频或演示入口、三个能在代码中找到证据的能力。
- [ ] 将 Plan A/B、合并历史、分支状态移到贡献者或维护文档。
- [ ] README 先讲缺料决策这一条闭环；其他域放 Examples，不并列争抢主线。
- [ ] 建议定位：`OntoAct is a decision workbench for material shortages: trace affected orders, compare feasible responses, and apply reviewed changes with an audit trail.`

**验收：** 不熟悉 Palantir 的工程师 30 秒能说出用户、输入、输出和一个关键工程难点；前三屏不需要理解内部计划名。

### 2. 修复 LLM 解释校验的语义过度承诺

**证据：** `backend/app/services/v2/decision_explanations.py:93` 的 `verify()` 只遍历 reasoning/risks，对证据 ID 和数值近似匹配；不检查 summary，跳过部分小整数，也不校验数值的字段归属、方向、单位和实体。前端 `WidgetView.tsx` 当前标签是“数字与引用已核验”，比“事实已验证”克制，但仍不足以说明语义限制。

**本次最小复现：** 证据为 `E1.value = {revenue_protected: 550000}`；claim 写 `Revenue at risk increases to 550000` 并引用 E1，返回 `verified: True`。数字存在，业务意思却变了。这不是完整 prompt-injection 或生产漏洞测试。

- [ ] 将事实断言结构化为实体、指标、值、单位、baseline/candidate、方向及 evidence reference；由确定性代码逐字段验证。
- [ ] 自由文本 summary 要么只表达已验证的结构化事实，要么明确标为模型生成且未做语义核验。
- [ ] 在完成语义校验前，把结果命名和界面说明收窄为“引用存在性及数值匹配检查”。
- [ ] 加入金额字段对调、增减反转、供应商/订单错配、小整数幻觉、summary 单独幻觉、单位混用的反例。

**验收：** 上述复现不再得到整体“已验证”状态；断言失败时保留错误原因，不能把重新措辞当验证。

### 3. 关闭 NL→Cypher 的边界缺口和错误成功

**证据：** `backend/app/services/v2/graph/nl2cypher.py:103` 用关键词黑名单限制写操作；ontology 参数和 LIMIT 只写在提示词里。直接调用验证器，`MATCH (n) RETURN n` 和 `CALL db.labels()` 均通过。`backend/app/routers/v2/graph.py:1623` 的 `/graph/ask` 执行生成查询；此路由有全局登录依赖，但函数没有调用同文件的 `_graph_access` 或应用字段投影。翻译失败会退回通用节点查询，执行错误则返回带 results 空数组的普通响应。

**边界：** 已确认的是验证器和路由实现缺口；本次未连接 Neo4j，未证明实际跨租户读取，也不能把“有登录”写成“无鉴权”。

- [ ] 优先让模型输出受限查询 DSL，复用已有 Object Query 授权与编译路径；不让任意 Cypher 成为模型工具接口。
- [ ] 如果暂时保留原始 Cypher，默认禁用该遗留端点，直到落实 ontology/object/field 权限、只读数据库凭据、查询超时和结果上限。
- [ ] 不支持的问题返回明确 unsupported/clarification；模型不可用返回 unavailable，不默默换成“返回全部节点”。
- [ ] 加跨 ontology、隐藏字段、无界查询、过程调用、模型不可用等边界验证。

**验收：** 用户 A 无法通过自然语言查询用户 B 的资源；无法执行无界查询；失败不会被界面呈现成成功但空结果。

### 4. 让 quick start 实际进入首页承诺的核心演示

**证据：** README 命令有 `git checkout <branch>` 占位符；`bootstrap_deployment.sh --seed-demo` 导入 frePPLe，而核心缺料 Decision Workspace 的 E2E 需要独立 seed、ontology/study/run 参数和第二用户审核命令。默认 local_single_user 模式与双人审批存在体验摩擦，E2E 使用外部命令完成第二人审批。

- [ ] 提供固定 release/tag 的复制即用命令，不要求读者选择交付分支。
- [ ] 增加明确的缺料演示入口，自动准备所需 study、case、run，并输出可以直接打开的深链接。
- [ ] 提供两个本地演示身份及可理解的切换方式；不为演示绕过自审禁令。
- [ ] 启动后验证数据及关键服务就绪，而不是仅以 HTTP `/health` 可访问判定业务演示就绪。
- [ ] 明确无 API key 可用的确定性流程，以及哪些 AI 功能需要 key；模型不可用时应有清楚的界面状态。

**验收：** 从干净环境照 README 操作，能完成“风险→候选→审核→应用→刷新后仍存在→回滚”，中途无需手工 SQL、容器名或隐藏脚本。

### 5. 将测试存在转为当前提交可复现的测试证据

**证据：** 未发现仓库内 `.github` 工作流目录。`backend/tests/conftest.py` 顶层导入整个 app，自动 fixture 每个测试建/删所有表，使独立计算测试也依赖应用及 MCP 加载。runner 测试和核心 E2E 均有环境条件 skip。当前本地 `docs/reports/resume-metrics-2026-10-19/` 记录过非全绿全量运行，但 `git ls-files docs/reports` 返回 0；不能把本地报告当作 GitHub 读者可见的证据。

- [ ] 建立小而必跑的 CI：纯计算、状态机、授权负例、前端检查和 build。
- [ ] 增加独立的真实 PostgreSQL/FalkorDB/runner 集成任务；明确 pass/fail/skip，不用全 skip 的绿色结果表示集成通过。
- [ ] 将纯计算测试移出导入整个 app 的 fixture 作用域。
- [ ] 发布当前提交的检查链接；报告注明 commit、命令、环境、原始计数及已知失败。
- [ ] 选择少量重要证据纳入 Git 或 CI artifacts；确认 `.gitignore` 不会让首页引用的报告只存在本机。

**验收：** 新 checkout 有确定的快速验证命令；集成服务未启动时明确报缺前置条件；演示发布提交的核心流程检查真实通过。

## P1：提高面试深挖时的可信度

### 6. 为 AI 能力提供真正的 held-out 评估

**证据：** 当前展示很强的是计算、规则与治理；这并不能直接证明模型工具选择、证据使用或回答质量。`agent_decision.py` 明确区分确定性路径与真实 Agno/LLM 模式，这是正确区分，应对外保持。

- [ ] 选择一个核心 AI 任务，不把整个系统包装成 agent：例如对象证据检索与解释，或从问题编译受限查询。
- [ ] 预先固定一个小型 held-out 集，包含歧义、无答案、权限拒绝、错误证据、模型超时和正常问题。
- [ ] 针对具体假设选 baseline：普通检索、无 ontology 上下文、无校验等；控制数据、模型、提示词预算和请求条件。
- [ ] 记录任务正确率、证据正确率、拒答/错误/超时、延迟和模型成本；保留失败样例。
- [ ] 将确定性回归与真实模型评估分开报告，不用前者的通过率证明 agent 可靠。

**验收：** 能解释 AI 相比替代方案在哪里有收益、在哪里会失败；未跑出的指标标为待测。

### 7. 展示作者做过的三个困难判断

- [ ] 公开三篇短工程记录，分别写清问题、两个可行选项、选择依据、代价和证据。
- [ ] 优先素材：共享物料分配为何 all-or-nothing；并发审核如何只允许一个胜者；场景回滚为何无法撤回已经发出的外部通知。
- [ ] 至少一个来自真实遇到的 bug，给出最小输入和修复前后行为；不要编造事故或开发历史。
- [ ] 引用具体实现/测试和提交；计划中的设计不要用完成时态。

**验收：** HM 能看出你理解哪些不变量，以及你主动舍弃了什么，而不是只看到技术名词。

### 8. 收窄 Function sandbox 的承诺并补资源边界

**证据：** `function_runner/server.py:48` 使用 `capture_output=True` 收集输出，`MAX_OUTPUT_BYTES` 在子进程结束后才检查 stdout；stderr 也被完整收集。ThreadingHTTPServer 接收线程与等待队列不等于执行槽位限制。Compose 的 internal network 仍连接 backend/worker，因此 README 的“no network”表述过宽。

- [ ] 将 stdout/stderr 改为读取过程中执行有界限制；触顶终止该次执行并返回稳定错误码。
- [ ] 限制连接、等待请求、请求读取时间和并发总资源，而不只限制子进程数。
- [ ] 明确 runner 可达的内部服务和信任边界；不能把无互联网出口表述为没有网络。
- [ ] 增加输出洪泛、stderr 洪泛、并发耗尽、同容器请求间干扰及后续请求仍可用的验证。
- [ ] 准确说明它服务于何种信任级别，不将单容器资源限制等同于任意敌对多租户代码安全。

**验收：** 一个异常 Function 不会因父进程缓冲输出拖垮所有后续请求；安全说明可逐条对应配置和测试。

### 9. 减少默认基础设施并解释每项存在理由

**证据：** 默认 compose 同时启动 PostgreSQL、Redis、FalkorDB、Neo4j、MinIO、ChromaDB、LiteLLM、worker/beat、runner、前后端。多个镜像使用 latest/main-latest。README 把它们并列列为架构能力，却未先说明核心演示真正依赖什么。

- [ ] 为核心闭环定义最小启动组合，将兼容图后端、向量检索、模型网关等按真实依赖放入 profiles。
- [ ] 所有默认依赖要能解释“支持主流程哪一步、移除会失去什么”。
- [ ] 发布演示固定经过验证的镜像版本/摘要及依赖；避免固定 Python 包却让数据库/网关随 latest 改变。
- [ ] 实测后写清内存、磁盘、首次 build 时间；未测量不填漂亮数字。

**验收：** 技术栈看起来是工作负载推导出来的选择；读者不用启动与目标演示无关的服务。

### 10. 用一条闭环统一旧路径与新治理承诺

- [ ] 列出核心演示实际经过的读写入口，逐项确认 ontology/object/field 授权和写入审计。
- [ ] 未完成治理迁移的遗留功能标为 experimental 或在默认演示中禁用；仅在 README 末尾承认缺口不够。
- [ ] 对关键状态转移说明事务边界、重试、崩溃后恢复和回滚补偿。
- [ ] 展示一次重复 apply、一次过期 revision、一次外部效果失败后的处理结果。

**验收：** “governed”能落到整条演示路径，不能靠某个新模块已经安全来代表所有旧接口。

### 11. 重构优先看职责和失败路径，不看代码行数

**证据：** 图 router、temporal/mapping/semantic 服务及若干前端页面体量较大；仅凭文件大不能判质量差，但评审更难快速定位关键边界。

- [ ] 首先统一路由 DB 依赖；当前 conftest 要扫描所有 app 模块并 override 各自 get_db，是依赖不统一的实际维护成本。
- [ ] 优先拆分授权、查询编译、外部副作用和持久化事务；不要只是将巨型文件机械切成多个文件。
- [ ] 错误状态不要普遍回 HTTP 200 + 空数组，保持模型不可用、无权限、无结果和执行失败可区分。

**验收：** 新贡献者能沿一条调用链找到权限检查、计算和提交边界；测试不需要猜哪些路由会写到真实配置数据库。

## P2：完成度与呈现

- [ ] **补 LICENSE 文件。** README 写 MIT，但本次 `git ls-files '*LICENSE*'` 未列出许可证文件。第三方数据/材料另写来源及许可边界，不把外部产品参考截图当自有 UI。
- [ ] **统一前端包管理器。** 同时跟踪 bun.lock/package-lock.json，而 Docker/README 使用 npm；选择主路径并保证 CI 使用 lockfile 安装。
- [ ] **修正文档与安装一致性。** requirements-reasoning.txt 要求 `--no-deps`，Docker 确实如此安装，README 手动启动却直接 pip install；给出同一套已验证方案，并运行依赖一致性检查。
- [ ] **整理文档入口。** 招聘入口指向 demo、architecture、verification、limitations；内部研究/PRD 可以保留，减少读者必须理解的前置背景。
- [ ] **简短说明 AI 使用。** 只写真实做过的事：哪些用于生成实现、哪些边界由自己设计、如何用独立样例和负例审核输出。不要编造“人工审核每一行”。
- [ ] **英文演示路径一致。** 面向英语岗位，首页示例、主流程标签和视频应能独立看懂；中文文档可继续保留。

## 本次运行证据与限制

1. 运行 `python -m pytest tests/test_material_shortage_decision.py tests/test_material_decision_package_flow.py -q`，在 conftest 导入 app/MCP 时失败。当前本机 mcp=1.29.0，而仓库固定 mcp=1.12.4；不能据此断言干净安装必然失败，但揭示快速测试与整个应用加载耦合。
2. 使用 runpy 加载原始 `test_material_shortage_decision.py`，每次重新构造原 fixture，直接执行其中 4 个测试函数，均通过。此结果仅证明这 4 个断言集合通过，不冒充 pytest 套件通过。
3. 直接执行 LLM 解释反例，得到 `verified=True`；直接执行 Cypher 验证器，两条无 ontology 限制的查询均被接受。未调用真实模型或数据库。
4. `npm.cmd run build` 在 tsc 写入现有 node_modules/.tmp 的 tsbuildinfo 时遇 EPERM；未完成构建，不能将权限问题归因于 TypeScript 源码错误。
5. Docker 配置和引擎管道当前不可访问；未成功部署，也未实际浏览运行 UI。演示体验问题基于脚本、配置和 E2E 代码审视，不是冒充实际使用结论。
6. PowerShell 默认解码曾让 UTF-8 中文显示异常；显式 UTF-8 读取正常，不能将终端显示异常当作仓库乱码缺陷。

## 建议执行顺序

第一批先形成可投递版本：改 README 首屏；修解释语义与 NL 查询边界；打通固定缺料演示和双人审核；让最小 CI 在固定版本上真实通过。这些优先于新增功能。

第二批形成面试深度：公开三个取舍与 bug 记录；完成一个小而真实的 AI 评估；补 runner 资源边界与默认部署版本固定。

第三批做维护收口：默认服务精简、遗留路径隔离、依赖和文档整理、LICENSE。

目标印象：作者用 AI 加速实现，同时自己定义业务不变量、控制模型权限、验证失败路径，并能用可复现证据解释质量。项目范围可以有限，但主路径应当完整可靠。
