# 本体构筑工作台（Nano-OntoPrompt）

> 当前可验收分支：`factorynet-temporal-workbench`。本分支提供 C-MAPSS FD001、FactoryNet CNC、I-BADAS 三类构筑闭环，并使用 Docker Compose 启动本地工作台。
>
> 部署、端口、Ollama、Celery、LiteLLM、数据库和后端启动说明请先阅读：[Docker 与后端部署说明](./DEPLOYMENT_WORKBENCH_ZH.md)。

**[English Documentation](./README.md)**

一个用于本地演示的领域本体构筑工作台。接入数据源后，经过数据选择、确定性处理、映射确认和后台构建，形成可查看的实体类型、真实实例、关系、逻辑规则与来源证据。

当前分支的主要构筑入口:

- **常规数据** — NASA C-MAPSS FD001（5 台设备 × 每台前 20 个 cycle，共 100 条读数）
- **时序数据** — FactoryNet CNC（`Ordinal + episode_id + time_s`）
- **多模态数据** — I-BADAS（12 组样例，RGB、深度、掩码、点云和元数据）

---

## 什么是本体(Ontology)?

本体是特定领域知识的形式化表示——一套共享的概念词汇及概念间的关系。它是把原始数据变成机器可读、可查询知识的结构化骨架。

在 nano-ontoprompt 中,每个本体由以下构件组成:

| 构件 | 含义 | 示例 |
|---|---|---|
| **实体(Object Type)** | 从 Curated 数据集映射出的核心概念,每行数据一个节点 | `Supplier`、`PurchaseOrder` |
| **关系(Link Type)** | 实体间的边,由外键检测与跨数据集值重叠推断 | `PurchaseOrder -[HAS_SUPPLIER]-> Supplier` |
| **逻辑规则(Logic)** | 规则层:从 schema 约束、质量报告、状态字段和图关系中发现的映射/校验/状态/推断/自动化规则 | `amount > 0`、`库存状态` 状态机 |

**典型场景:** 供应链知识建模、医疗概念提取、金融合规规则、法律文档结构化——任何需要把异构数据转化为结构化知识的领域。

---

## 功能特性

### 数据管道(v2)
- **可视化管道构建器** — 画布上编排连接器/存储器/转换器/输出节点,逐节点状态与数据预览
- **三条转换路径** — A:结构化(CSV/Excel,schema 推断 + 清洗);B:半结构化(JSON 拍平 / XML 解析);C:非结构化(文档 → Markdown → LLM 或规则结构化提取)
- **连接器** — 文件上传、MySQL/PostgreSQL、MongoDB、REST API(支持增量同步)
- **Curated 数据集** — 质量评分、人工审核(仅管理员可审批)、版本管理

### 本体构筑(v2)
- **五步工作流** — 数据集 → 内容选择 → 处理配置 → 本体映射 → 确认构建
- **统一物化** — 实体类型、真实实例、关系、逻辑规则和来源证据在同一修订中发布
- **本体查看** — 中央关系画布、右侧 Inspector、实体/属性/关系搜索和来源证据定位
- **严格分类** — 常规、时序、多模态本体只能追加同类数据

### 质量审查(ReAct Agent)
- **本地模型多步审查** — qwen3.5:0.8b 检查孤立实体、断链引用、缺失关系和低覆盖类型
- **工具调用架构** — 只读检查工具记录摘要、覆盖率、引用校验和规则模式观察结果
- **审查报告** — 按严重级别分类的问题及修复建议,持久化为审计任务

### 平台
- **LLM 提取** — 支持 OpenAI、Anthropic 及任何 OpenAI 兼容模型;多道防线杜绝模糊关系类型
- **LiteLLM 代理** — 可选 LiteLLM 集成,统一管理多 LLM 提供商的 API Key 与用量
- **提示词管理** — 领域提示词版本化管理,一键生成模板
- **数据管理** — 结构化数据浏览器,含 Curated 数据集详情面板、行级编辑与审核流程
- **导出** — JSON、YAML、CSV、Turtle (RDF)、HTML
- **优雅降级** — Neo4j / MinIO / ChromaDB / Redis 全部可选;缺失时自动回退 SQLite + 本地文件存储 + 同步执行
- **多语言界面** — 中英文切换
- **用户管理** — JWT 认证,admin/editor 角色;Curated 审批仅限管理员

---

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 18、TypeScript、Vite、Tailwind CSS、Cytoscape.js |
| 后端 | FastAPI、SQLAlchemy、Alembic |
| 元数据库 | SQLite(开发)/ PostgreSQL(生产) |
| 对象存储 | MinIO(可选,本地文件回退) |
| 图数据库 | Neo4j(可选,SQLite 回退) |
| 向量库 | ChromaDB(可选) |
| 任务队列 | Celery + Redis(可选,同步执行回退) |
| LLM 客户端 | OpenAI SDK、Anthropic SDK |
| LLM 代理 | LiteLLM(可选) |

---

## 架构指南

深入理解 Ontology-as-a-Service 架构设计 — 包括 Object/Link/Function/Governance 设计模式、多租户隔离、临床筛查工作流、生产部署 Checklist — 详见 **[ONTOLOGY.md](./ONTOLOGY.md)**（2727 行）。

---

## 快速开始

### 方式一 — Docker Compose（本地工作台）

```bash
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

本地演示端口为前端 `15173`、后端 `18080`；同时启动 PostgreSQL、Redis、Neo4j、MinIO、ChromaDB、Celery worker/beat 和 LiteLLM。

打开 [http://127.0.0.1:15173/overview](http://127.0.0.1:15173/overview)。Ollama 与 `qwen3.5:0.8b` 的安装、M3 凭证和安全配置见 [Docker 与后端部署说明](./DEPLOYMENT_WORKBENCH_ZH.md)。

### 方式二 — 手动启动(最小化,无需外部服务)

**前置要求:** Python 3.11+、Node.js 18+

```bash
# 后端
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head                                  # 开发模式也可依赖启动时自动建表
uvicorn app.main:app --reload --port 8000

# 前端(另开终端)
cd frontend
npm install
npm run dev
```

完整本地栈建议直接使用 Docker；手动启动时外部服务地址必须在环境变量中显式配置。

---

## 使用流程

1. 打开“数据构筑”，选择常规、时序或多模态入口。
2. 选择内置案例或导入来源，再选择字段、时间范围、样例和模态。
3. 选择 `standard` 或 `private`，确认发送范围后生成映射。
4. 逐项确认实体类型、属性、关系和逻辑规则。
5. 启动构建任务；完成后进入“本体”查看关系、实例、证据和质量审查。

---

## 项目结构

```
nano-ontoprompt/
├── backend/
│   ├── alembic/               # 数据库迁移 (0001_full_baseline 覆盖全部表)
│   ├── app/
│   │   ├── routers/           # v1 + v2 REST API 端点
│   │   ├── models/            # SQLAlchemy ORM 模型 (v1 + v2)
│   │   ├── services/
│   │   │   ├── connection/    # 文件 / SQL / Mongo / REST 连接器
│   │   │   └── v2/
│   │   │       ├── pipeline/  # 转换引擎、A/B/C 三路径、处理步骤
│   │   │       ├── mapping/   # 自动映射、外键与备用键关系推断
│   │   │       ├── graph/     # Neo4j 服务、Cypher 校验、图分析
│   │   │       ├── curated/   # 质量评分、审核流程
│   │   │       └── vector/    # ChromaDB 服务
│   │   └── tasks/             # Celery 任务 (管道运行、同步、提取)
│   ├── scripts/               # 维护脚本 (孤儿数据清理、迁移)
│   └── tests/                 # 300+ pytest 用例
├── frontend/
│   ├── scripts/                # 一次性调试/演示/测试脚本
│   └── src/
│       ├── pages/pipelines/    # 管道列表 + 画布构建器
│       ├── pages/ontologies/   # 本体关系 / 实体 / 逻辑规则 / 质量审查
│       ├── pages/data-management/  # 常规 / 时序 / 多模态五步构筑
│       └── api/                # Axios 客户端 (v1 + v2)
├── scripts/
│   └── data/                   # 数据导入与实体关联脚本 (SNOMED、供应链)
├── docker-compose.yml          # 基础服务定义
├── docker-compose.local.yml    # 本地端口、单用户、Celery、LiteLLM 和 Ollama 配置
├── litellm_config.yaml         # LiteLLM 代理配置
├── ONTOLOGY.md                 # 架构设计指南
└── test_data/                  # 示例数据集与 E2E 验收脚本
```

---

## 环境变量

完整列表见 `.env.example`,核心配置:

```env
ENVIRONMENT=development        # 设为 production 时, 默认密钥未修改将拒绝启动
DATABASE_URL=sqlite:///./ontoprompt.db
SECRET_KEY=change-me
ENCRYPTION_KEY=                # Fernet 密钥, 用于加密存储的 API Key
FIRST_ADMIN_USER=admin
FIRST_ADMIN_PASSWORD=admin123

# 可选服务 (缺失时优雅降级)
REDIS_URL=redis://localhost:6379/0
NEO4J_URI=bolt://localhost:7687
MINIO_ENDPOINT=localhost:9000
CHROMA_HOST=localhost

# 上传限制
MAX_UPLOAD_MB=200
ALLOWED_UPLOAD_EXTENSIONS=csv,xlsx,xls,json,xml,pdf,docx,doc,pptx,ppt,md,txt

# 可选: LLM 辅助语义外键检测 (需先配置模型)
ENABLE_LLM_FK_DETECTION=0
```

---

## 故障排查

**前端容器报 `AggregateError [ECONNREFUSED]`。**
确认使用 `docker-compose.yml` 与 `docker-compose.local.yml` 的组合，并重建：

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build frontend backend
```

**已有部署用 `admin / admin123` 登录失败。**
admin 用户用旧的默认密码 seed,需要重置:

```bash
# Docker
docker compose exec backend python scripts/reset_admin_password.py

# 手动启动
cd backend && python scripts/reset_admin_password.py
```

可选参数: `--user <username>` (默认 `admin`)、`--password <new_pwd>` (默认 `admin123`)。

**LLM 提取被 OOM-kill(macOS 或低内存环境)。**
并行 LLM 提取在内存有限的机器上可能耗尽资源。代码已默认改为串行提取(`max_workers=1`)。如仍遇到问题,可逐域提取,或减少每次提取上传的文件数。

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Joeysoda/nano-ontoprompt&type=Date)](https://star-history.com/#Joeysoda/nano-ontoprompt&Date)

---

## 许可证

MIT
