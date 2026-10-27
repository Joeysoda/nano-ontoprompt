# 本体决策工作台（Nano-OntoPrompt）

> 本文件与 [英文 README](./README.md) 同步，描述 `feature/l3-decision-workspace` 这条开发线：在 [`codex/merge-impact-chains`](https://github.com/Joeysoda/nano-ontoprompt/tree/codex/merge-impact-chains) 集成（Plan A 语义治理底座 + Plan B 运营工作台）之上，加入 Plan A 2.0、类型化规则运行时、Azure PdM 设备（Machinery）路径、约束求解器、Foundry 风格导航，以及决策层（缺料决策、受治理的决策包、隔离执行的用户 Function）。

**[English Documentation](./README.md)** · **[Docker 与后端部署说明](./DEPLOYMENT_WORKBENCH_ZH.md)** · **[文档目录说明](./docs/README.md)** · **[Plan A / Plan B 集成合同](./docs/plans/PLAN_A_PLAN_B_INTEGRATION_CONTRACT.md)**

这是一个参照 Palantir Foundry 设计的开源平台：把结构化、时序和多模态数据构建成受治理的本体，用户可以在上面查询、查看、执行操作，并用 What-if 情景做推演和决策。原有的数据管道与 LLM 抽取路径保留不变，在此之上围绕稳定的语义身份、来源证据、对象集、Action、Function 和情景构建了完整的运营层。

**本体（Ontology）是什么**：一套把业务数据组织成"对象类型（如 `Machine`、`Order`）、对象之间的关系（如 `Order -[USES]-> Material`）、以及作用在对象上的规则和操作"的结构，让人和 AI 都能按业务语义而不是按数据库表来理解和操作数据。

## 当前范围

当前交付由三层组成：

| 层 | 职责 | 当前能力 |
|---|---|---|
| **Plan A — 语义与数据底座** | 定义本体资源的含义、谁能看到、结果由哪个数据版本产生 | 版本化语义资源、稳定资源 ID、来源映射、权限投影、执行/结果清单、Schema 迁移规划、快照与图投影状态 |
| **Plan B — 运营工作台** | 让用户探索对象，把受治理的数据变成决策 | 对象查询、对象集与已保存视图；对象浏览器（Vertex 风格关系图）；Action 与 Function 治理；类型化规则与告警；约束求解器；情景创建、预览、比较、跟踪与合并；时序与时间序列分析 |
| **决策层** | 把一次情景研究变成经过审核、可回滚的变更 | `decision/v1` 结果契约、候选方案比较、决策包（提交 → 审核 → 经情景合并应用 → 回滚）、外部副作用经 outbox 投递、用户自写 Function 在隔离环境执行 |

原有的构筑平台也全部保留：连接器、数据转换、Curated 数据集、本体映射、质量审查、时序回放、多模态证据、规则推理和决策建议。

## 端到端流程

```text
源数据
   -> 连接 / 上传
   -> 转换与整理（Curated）
   -> 本体映射与语义审核
   -> 不可变修订版本 + 来源证据
   -> 实时或固定版本的数据视图
   -> 对象查询 / 对象浏览器
   -> Action、Function、规则或 What-if 情景
   -> 情景研究候选方案 -> 决策包 -> 审核
   -> 经受治理的合并应用（带审计记录，可回滚）
```

内置演示数据：

- **frePPLe 供应链**（`data/frepple_demo/`）— 公开数据集，演示对象集、Action 和已保存的情景研究。
- **缺料决策** — 合成的 MRPSpeed 复杂度数据（多级 BOM、带库位的库存、预留、双产线排程），带人工推导的标准答案，驱动缺料决策工作区。
- **Azure PdM 设备** — 微软的模拟预测性维护样例，映射为带时间序列属性的 `Machine` 对象，以及故障/维护/错误事件对象。80 MB 的 CSV 不进 git：放在 `data/azure_pdm/`（已被 git 忽略），或用 `AZURE_PDM_DIR` 指定位置。
- **C-MAPSS FD001**、**FactoryNet CNC**、**I-BADAS** — 常规、时序（逐条事件回放）和多模态三类构筑路径。

## 主要能力

### 数据构筑与本体建模

- 可视化数据管道，支持文件、SQL、MongoDB、REST 数据源。
- 结构化、半结构化、文档三条转换路径。
- Curated 数据集：质量评分、审核、版本管理与来源追溯。
- 候选实体/属性/关系映射，人工逐项确认。
- 关系发现：外键、归一化值、备用键，可选 LLM 辅助。
- 动态本体编辑：不可变修订、依赖检查、批量校验、历史恢复。

### 语义治理与安全

- 对象类型、属性、关系、接口、值类型、结构类型的稳定语义资源。
- 版本化定义绑定到本体修订和元数据摘要。
- `AuthorizationContext -> AuthorizationProjection` 契约，控制对象、关系和字段级可见性。
- 显式区分实时、固定版本、快照、情景和降级执行状态。
- 结果清单携带元数据、权限、来源、适配器和一致性身份。
- 可预演、可恢复的 Schema 迁移：重命名、类型转换、删除、替换数据源、迁移编辑记录、重建图投影。

### 对象查询与对象浏览器

- 类型化筛选、嵌套布尔表达式、排序、游标分页、聚合与比较。
- 已保存的对象集、查询视图、不可变数据视图和后台查询任务。
- 对象目录、可配置列、分布、透视、已保存的探索和多选操作。
- 对象详情面板：属性、关系、图上下文、时序快照和时间序列图表。
- 权限、上下文、能力和一致性失败都返回稳定的错误码，而不是含糊的空结果。

### Action、Function 与逻辑

- 实时操作和情景操作共用同一套执行上下文。
- Action 校验、冲突预览、执行、审计和回滚。
- Function 输入/输出契约，以及与对象集和对象的受治理绑定。
- 版本化 Logic Asset，运行时做契约检查。
- 基于 Semantica 的有界规则推理和可解释的决策建议。
- 约束求解器（经 SciPy 调用 HiGHS 求解混合整数规划）作为编辑型 Function 运行；只有由 Function 驱动的 Action 才能应用它产生的修改。
- 用户 Function：用户提交的 Python 代码存为不可变版本，只在 `function_runner` 中执行——这是一个独立容器，没有网络、没有可写文件系统，并限制 CPU、内存和进程数。

### 规则、告警与自动化

- 类型化规则运行时，采用 Foundry Rules 的结构（对象集输入 → 有序逻辑块 → 输出行），只能通过审核过的提案修改。
- 按计划在固定版本的数据视图上评估，支持暂停/静音；告警按事件段去重，可以引用一次已完成的 Action 运行来关闭。
- 界面上有告警中心和规则页面。

### 决策

- 缺料决策模型：候选方案（加急、买替代料、重新分配、认证替代、综合重排）通过重跑同一个模拟来评估，给出受保护/受损订单、增量成本和硬约束。
- 决策包：状态机、ETag、幂等状态转换、禁止自审。应用时把候选方案重新编译成类型化修改、校验预览摘要，再通过现有的情景合并生成修订；回滚恢复基线，并列出无法撤销的外部影响。
- 外部副作用经 outbox 投递（演示中是本地的模拟 ERP 连接器），带回执和重试。

### 情景与时序运营

- What-if 情景固定在本体修订、数据源/视图身份和执行上下文上。
- 假设编辑、Action 预览、图对比、合并预览和可跟踪的情景运行。
- FactoryNet 回放：单步、播放、暂停、继续、取消，事件速度可调。
- 幂等的事件推送、水位线、有效期区间、证据引用和手动发布的快照。
- 情景结果在显式的受治理合并之前保持隔离；模拟不会悄悄改动实时图。

### 审查与验证

- ReAct 风格的本体质量审查，使用有界的只读检查工具。
- 单元、契约、迁移、集成、浏览器、重启和视觉证据测试套件。
- Plan B 持久化与运行时基准测试脚本。
- 生成的 TypeScript 语义契约与后端 Pydantic 定义自动比对。

## 架构

| 关注点 | 实现 |
|---|---|
| 前端 | React 19、TypeScript、Vite、Tailwind CSS、Cytoscape.js、ECharts |
| API 与服务 | FastAPI、Pydantic、SQLAlchemy、Alembic |
| 权威元数据与事实账本 | PostgreSQL；轻量开发和契约测试可用 SQLite |
| 图投影 | FalkorDB；兼容路径仍可用 Neo4j |
| 对象与文件存储 | MinIO，开发时回退到本地 `storage/` |
| 分析处理 | DuckDB、pandas、PyArrow |
| 向量检索 | ChromaDB |
| 后台任务 | Celery + Redis（含每分钟一次的规则调度） |
| 用户 Function 沙箱 | `function_runner/` 容器，只连内部网络 |
| 优化求解 | HiGHS（经 SciPy `milp`） |
| 模型网关 | LiteLLM；支持 OpenAI、Anthropic 及兼容接口 |
| 本地推理模型 | Ollama，当前演示使用 `qwen3.5:0.8b` |

PostgreSQL 保存修订、事实、策略、映射和执行记录。FalkorDB 是可重建的图投影，用于图遍历和运营读取。单凭一个修订标签不算数据快照；固定版本的读取还会带上数据源、视图、元数据和权限身份。

更深入的 Ontology-as-a-Service 设计模式（Object/Link/Function/Governance、多租户隔离、生产部署 Checklist）见 **[ONTOLOGY.md](./ONTOLOGY.md)**。

## 快速开始

### 方式一 — 完整 Docker 工作台

前置要求：Git、带 Compose 的 Docker、Bash。只有需要本地模型做审查/推理时才要装 Ollama。

```bash
git clone https://github.com/Joeysoda/nano-ontoprompt.git
cd nano-ontoprompt
git checkout <分支名>   # 例如 codex/merge-impact-chains，或你要交付的分支
./scripts/bootstrap_deployment.sh --seed-demo
```

脚本会在本机生成带随机密钥的 `.env`，准备 PostgreSQL 和 FalkorDB 的外部数据卷，构建服务、执行数据库迁移，并导入公开的 frePPLe 演示数据。可以重复运行，不会提交凭证或运行数据。

打开：

- 工作台：[http://127.0.0.1:15173/overview](http://127.0.0.1:15173/overview)
- 后端健康检查：[http://127.0.0.1:18080/health](http://127.0.0.1:18080/health)
- API 文档：[http://127.0.0.1:18080/docs](http://127.0.0.1:18080/docs)

本地 compose 覆盖配置使用 `local_single_user` 模式，打开即用、没有登录页。不要在共享或公网环境使用这个模式。端口、模型配置、数据恢复和服务器运维见 [DEPLOYMENT_WORKBENCH_ZH.md](./DEPLOYMENT_WORKBENCH_ZH.md)。

### 方式二 — 手动开发

前置要求：Python 3.12、Node.js 22。

```bash
# 后端
cd backend
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-reasoning.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# 前端（另开终端）
cd frontend
npm install
npm run dev
```

手动开发可以用 SQLite 和同步执行跑小任务，但快照、图投影、恢复和部署验收必须用 PostgreSQL/FalkorDB 全栈。

## 演示路径

侧边栏是平台级导航：首页、本体库、应用（对象浏览器、告警中心、情景研究、通用情景）和系统。数据在本体内部添加（数据 → 数据源），没有单独的数据工具入口。

1. 从 **本体库** 打开一个本体，展示总览、语义资源、来源证据和修订历史。
2. 在 **对象浏览器** 中筛选、聚合或保存对象集；打开一个对象，展示属性、关系、关系图（Search Around、图层、时间游标）、告警、事件和时间序列图表。
3. 在 **告警中心** 展示定时规则产生的告警，并引用一次已完成的 Action 运行来关闭其中一条。
4. 在 **情景研究** 中运行一次研究（如缺料决策），比较候选方案，创建决策包、审核、经情景合并应用，再回滚。
5. 展示结果清单、审计记录和显式的合并边界。

FactoryNet 的 **动态演化** 页面还可以演示逐条事件更新、回放控制、水位线、历史有效期和手动发布快照。

## 项目结构

```text
nano-ontoprompt/
├── backend/
│   ├── alembic/                 # 版本化数据库迁移
│   ├── app/
│   │   ├── models/v2/           # 语义、安全、查询、情景、时序等数据表
│   │   ├── routers/v2/          # 当前 REST API
│   │   ├── schemas/v2/          # 共享的请求/响应契约
│   │   ├── services/v2/         # 构筑、治理、查询、图、Action、情景与决策逻辑
│   │   └── tasks/v2/            # Celery 任务
│   ├── scripts/                 # 契约导出、验证、基准测试、演示数据灌入
│   └── tests/                   # 单元、契约、集成和迁移测试
├── frontend/
│   └── src/
│       ├── pages/apps/          # 应用启动页（/apps/:app）
│       ├── pages/data-management/
│       ├── pages/ontologies/
│       ├── pages/what-if/       # 情景研究、决策工作区、决策包
│       └── test/e2e/            # 浏览器验收流程
├── function_runner/             # 用户 Function 的隔离沙箱服务
├── data/                        # 演示数据：frepple_demo、cmapss_fd001_demo、bts_demo；azure_pdm、dataco 仅本地
├── docs/                        # 见 docs/README.md：research、plans、adr、guides、reports、archive
├── scripts/                     # 部署引导、数据导入和部署验证
├── storage/                     # MinIO 不可用时的本地文件存储（git 忽略）
├── docker-compose.yml           # 基础服务拓扑
├── docker-compose.local.yml     # 本地端口与单用户演示模式
├── ONTOLOGY.md                  # 架构设计指南
└── DEPLOYMENT_WORKBENCH_ZH.md   # 完整中文部署指南
```

## 验证

按改动的范围运行对应检查：

```bash
# 后端测试
cd backend
pytest

# 生成的 Plan A 契约是否最新
python scripts/export_plan_a_contract.py --check

# 前端静态检查与构建
cd ../frontend
npm run lint:src
npm run build

# 浏览器流程（需先启动全栈）
npx playwright test
```

仓库还提供分支接线、重启持久化、供应链数据、对象查询/FalkorDB 集成和 Plan B 运行时基准等专项脚本。

## 环境变量

完整列表见 `.env.example`，核心配置：

```env
ENVIRONMENT=development        # 设为 production 时，默认密钥未修改将拒绝启动
DATABASE_URL=sqlite:///./ontoprompt.db
SECRET_KEY=<由部署脚本在本机生成>
ENCRYPTION_KEY=                # Fernet 密钥，用于加密存储的 API Key
FIRST_ADMIN_USER=admin
FIRST_ADMIN_PASSWORD=<由部署脚本在本机生成>
BACKEND_AUTH_MODE=local_single_user   # 共享环境必须改为 jwt

# 可选服务（缺失时优雅降级）
REDIS_URL=redis://localhost:6379/0
FALKORDB_HOST=falkordb
FALKORDB_PORT=6379
NEO4J_URI=bolt://localhost:7687
MINIO_ENDPOINT=localhost:9000
CHROMA_HOST=localhost

# 上传限制（未写在 .env.example 中，可按需添加）
MAX_UPLOAD_MB=200
ALLOWED_UPLOAD_EXTENSIONS=csv,xlsx,xls,json,xml,pdf,docx,doc,pptx,ppt,md,txt

# 可选：LLM 辅助语义外键检测（需先配置模型）
ENABLE_LLM_FK_DETECTION=0
```

## 当前边界

- Docker 配置面向演示和开发。共享的生产部署必须启用 JWT、HTTPS、外部密钥管理、网络限制、备份和监控。
- Plan A 的核心契约和 SQLite 测试已具备；真实 PostgreSQL/FalkorDB 上的迁移恢复、投影对账和性能分位数仍需要按环境补充验收证据。
- 部分旧接口和 Plan B 接口尚未完全迁移到统一的 Plan A 权限投影和稳定语义资源 ID，见 [集成合同](./docs/plans/PLAN_A_PLAN_B_INTEGRATION_CONTRACT.md) 中未勾选的条目。
- 基于规则的情景展示的是有界的逻辑后果，本身不能证明因果效应，也不能预测维护结果。
- 决策包的外部副作用投递到本地模拟 ERP 连接器；本仓库不包含真实 ERP 集成。
- 缺料决策数据是合成的（MRPSpeed 复杂度），Azure PdM 是微软的模拟样例，都不是生产数据。
- 运行中的数据库卷、上传文件、`.env` 和 API 凭证都刻意排除在 git 之外。
- 独立的 `codex/next-stage-ontology-modules` 分支不属于本次集成交付，除非单独合并。

## 安全与数据处理

- 不要提交 `.env`、API Key、密码、数据库卷、上传文件或复制来的生产数据。
- 隔离的本地演示以外，一律使用 `AUTH_MODE=jwt`。
- 共享对象集或情景只代表共享该资源本身，不能扩大对底层对象或字段的访问权限。
- 脱敏值不得出现在 trace、日志、错误信息和模型提示词中。
- 需要保留数据卷的环境，不要执行 `docker compose down -v`。

## 故障排查

**前端容器报 `AggregateError [ECONNREFUSED]`。**
确认使用 `docker-compose.yml` 与 `docker-compose.local.yml` 的组合，并重建：

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build frontend backend
```

**已有 JWT 部署登录失败。**
不要在文档或命令中使用固定密码；为已有 JWT 部署重置一个只在本机输入的新密码：

```bash
# Docker
docker compose exec backend python scripts/reset_admin_password.py

# 手动启动
cd backend && python scripts/reset_admin_password.py
```

可选参数：`--user <username>`（默认 `admin`）、`--password <new_pwd>`。不要把新密码写入脚本、截图或 git。

**LLM 抽取被 OOM-kill（macOS 或低内存环境）。**
并行 LLM 抽取在内存有限的机器上可能耗尽资源。代码默认串行抽取（`max_workers=1`）。如仍有问题，可逐个领域抽取，或减少每次上传的文件数。

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Joeysoda/nano-ontoprompt&type=Date)](https://star-history.com/#Joeysoda/nano-ontoprompt&Date)

## 许可证

MIT
