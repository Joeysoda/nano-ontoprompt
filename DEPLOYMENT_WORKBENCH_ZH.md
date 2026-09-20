# 本体构筑工作台：Docker 与后端部署说明

本文档对应分支 `factorynet-temporal-workbench`，用于本地完整演示。当前工作树的前端、后端、Celery、LiteLLM、数据库和本地模型均按本说明启动。

当前版本入口：

- GitHub 分支：[factorynet-temporal-workbench](https://github.com/Joeysoda/nano-ontoprompt/tree/factorynet-temporal-workbench)
- 本地工作台：`http://127.0.0.1:15173/overview`
- 本地后端健康检查：`http://127.0.0.1:18080/health`

注意：`127.0.0.1` 只对启动服务的这台电脑有效，不是公网链接。同学需要先按本文档在自己的电脑上部署，不能直接打开你电脑上的 localhost。

## 1. 运行边界

- 目标：电脑浏览器本地演示，不做公网部署。
- 认证：`docker-compose.local.yml` 显式启用 `AUTH_MODE=local_single_user`，打开网站后不需要用户名和密码，也不会出现登录页。
- 绑定：演示端口只绑定 `127.0.0.1`，不会把数据库、对象存储或 LiteLLM 暴露到局域网。
- 数据：不执行 `down -v`，不删除既有 PostgreSQL、Neo4j、MinIO 或 ChromaDB 数据卷。

## 2. 需要安装的内容

### 必需

1. Docker Desktop（启用 Linux containers）。
2. Git。
3. Ollama。安装后准备本地审查模型：

```bash
OLLAMA_HOST=0.0.0.0 ollama serve  # Docker 访问宿主机时使用；已作为系统服务运行则不要重复启动
ollama pull qwen3.5:0.8b
curl http://127.0.0.1:11434/api/tags
```

最后一个请求应能看到 `qwen3.5:0.8b`。网页不会自动安装模型，只会在“模型与审查”页面检测服务。当前本地演示已验证该模型存在；模型约 1 GB，不需要再次下载。

### 可选

- Python 3.12：只在不使用 Docker、需要直接运行后端或测试时使用。
- Node.js 22：只在不使用 Docker、需要直接运行前端时使用。
- MiniMax M3 凭证：标准数据构建才需要。凭证必须先轮换，再写入本地 `.env`；不得提交到 Git，也不要把凭证写进本文档。

## 3. 获取代码与配置密钥

```bash
git clone --branch factorynet-temporal-workbench \
  https://github.com/Joeysoda/nano-ontoprompt.git \
  nano-ontoprompt-workbench
cd nano-ontoprompt-workbench
cp .env.example .env
```

`.env` 已被 `.gitignore` 忽略。至少应修改以下值：

```env
ENVIRONMENT=development
SECRET_KEY=<随机的长字符串>
ENCRYPTION_KEY=<Fernet 密钥>
FIRST_ADMIN_PASSWORD=<仅在 JWT 部署中使用的强密码>
LITELLM_MASTER_KEY=<本地网关密钥>
LITELLM_SALT_KEY=<本地网关盐值>
MINIMAX_API_KEY=<轮换后的 M3 凭证；不用标准模式时留空>
```

生成 Fernet 密钥（只把输出写入自己的 `.env`，不要复制到 Git）：

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

本地 Compose 会覆盖数据库、Ollama、LiteLLM 和端口配置。不要把 `.env` 中的密码、令牌或 API Key 粘贴到 issue、截图或提交记录中。

## 4. 启动完整 Docker 栈

在工作树根目录执行：

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  up -d --build
```

本地 Compose 会启动这些服务：

| 服务 | 作用 | 本机端口 |
|---|---|---:|
| `frontend` | React/Vite 工作台 | `15173` |
| `backend` | FastAPI API、迁移、数据服务 | `18080` |
| `celery_worker` | 下载、映射、构建、审查等后台任务 | 无外部端口 |
| `celery_beat` | 定时任务调度 | 无外部端口 |
| `litellm` | 统一模型网关 | `14000` |
| `db` | PostgreSQL 元数据 | `15432` |
| `redis` | Celery 队列 | `16379` |
| `neo4j` | 图存储兼容服务 | `17474` / `17687` |
| `minio` | 对象存储 | `19000` / `19001` |
| `chromadb` | 向量检索兼容服务 | `18001` |

检查容器：

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml ps
curl http://127.0.0.1:18080/health
```

健康检查应返回 `status=ok`，并显示 `db`、`neo4j`、`falkordb`、`minio`、`chroma` 均正常。后端启动时会自动执行 Alembic 迁移；需要手动核对时可运行：

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml \
  exec backend alembic current
```

打开：

- [本体构筑工作台](http://127.0.0.1:15173/overview)
- [后端健康检查](http://127.0.0.1:18080/health)
- [模型与审查](http://127.0.0.1:15173/models)

### 4.1 FalkorDB 说明

FalkorDB 不是 Compose 文件中的公共端口服务；没有它时，本体和动态数据模型仍可通过 PostgreSQL 事实回退运行。要启用原生图投影，可在宿主机额外启动一个本地实例：

```bash
docker volume create nano-ontoprompt_workbench_falkordb_data
docker run -d --name nano-ontoprompt-falkordb --restart unless-stopped \
  -p 127.0.0.1:6381:6379 \
  -v nano-ontoprompt_workbench_falkordb_data:/data \
  falkordb/falkordb:latest
```

然后在 `.env` 中确认：

```env
FALKORDB_HOST=host.docker.internal
FALKORDB_PORT=6381
```

重启后端和 worker：

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml restart backend celery_worker
```

检查 `/health` 中 `falkordb` 是否为 `ok`。如果不需要原生图投影，可以不执行这一节，系统会继续使用 PostgreSQL 回退。

## 5. 本地模型与模型路由

“模型与审查”页面应显示：

- Ollama：在线。
- `qwen3.5:0.8b`：可用于审查。
- LiteLLM：可达。
- MiniMax M3：已配置但需要显式云端验证。

安全探测本地模型：

```bash
curl http://127.0.0.1:18080/api/v1/models/local/probe
curl 'http://127.0.0.1:18080/api/v2/model-routes/status?probe_local=true'
```

要同时显式验证本地模型和 MiniMax 路由：

```bash
curl 'http://127.0.0.1:18080/api/v2/model-routes/status?probe_local=true&probe_cloud=true'
```

只有 `available=true` 才表示实际探测通过；“已配置”或“网关可达”不等于模型调用成功。标准数据构建使用 MiniMax M3，私密数据禁止走云端；本地审查使用 `qwen3.5:0.8b`。

标准数据的 M3 请求统一经过 LiteLLM，并且只发送用户确认的范围。私密数据不会发送到云端；审查默认使用本地 qwen。网页打开或刷新模型页不会自动调用 M3。

## 6. 三类数据构建

三类入口共用五步工作流：数据集 → 内容选择 → 处理配置 → 本体映射 → 确认构建。

- 常规数据：老师提供的 NASA C-MAPSS FD001 下采样案例，当前演示为 5 台设备、每台前 20 个 cycle，共 100 条读数。
- 时序数据：FactoryNet CNC，使用 `Ordinal + episode_id + time_s`，不凭空生成日期。
- 多模态数据：I-BADAS，安装器只准备 12 组样例，包含 RGB、深度、掩码、点云与元数据。

新建本体在最终构建时创建；追加只能写入同一数据分类的已有本体新修订。构建完成后可在本体页面查看实体类型、真实实例、关系、逻辑规则、证据和质量审查。

后台构建任务会持久化。常规和多模态页面的构建链接包含 `?run=<run_id>`，刷新后会恢复任务状态和结果卡片；时序构建使用独立任务详情页。

## 7. 任务与日志核验

最近模型调用可在“模型与审查”页面查看，也可通过接口读取（接口只返回当前用户有权限的日志）：

```bash
curl 'http://127.0.0.1:18080/api/v2/model-invocations?model=qwen3.5%3A0.8b&status=completed&limit=10'
```

审查页展示只读工具轨迹、观察结果和最终结论，不展示模型隐藏思维。图片只记录资产引用和哈希，不复制 Base64。

## 8. 动态本体与 What-If

打开 FactoryNet 本体后，右上角的“编辑本体”支持新增、修改或删除实体类型、属性、类型关系和逻辑规则。每次只保存一个修改，后端会在同一事务中校验引用并生成新的不可变修订；真实数据实例保持只读。删除有实例、关系、规则或证据引用的对象会被明确阻断。旧修订可在“修改记录”中恢复，恢复会重新物化本体结构并创建新的当前修订，不覆盖历史记录。

模型建议支持批量导入：在建议列表左侧勾选多项，点击“批量填入表单”后逐项检查字段，再点击“检查冲突”。后端会在保存点中模拟整批新增/修改/删除，检查重复标识、对象引用、关系端点、基数和逻辑规则；校验通过后“确认批量导入”才会把整批操作作为一个新修订提交，任意一项失败都不会留下半批修改。对应接口为 `POST /api/v2/ontologies/<ontology_id>/changes/batch/validate` 和 `POST /api/v2/ontologies/<ontology_id>/changes/batch`。

FactoryNet 的“数据模型”页面可以选中一个 Observation，再进入 “What-If 推演”。推演固定 `episode_id + Ordinal`、数据集版本和本体修订，流程是：选择基线 → 设置属性/关系/规则假设 → 运行推演 → 查看差异。结果只保存于情景，不写回正式本体或 FalkorDB；新增、消失和未变化关系在画布中分别用绿、红、灰标识，右侧显示规则前提、引擎版本和证据。

### 8.1 FactoryNet 逐事件动态演化

打开 FactoryNet 本体后选择“动态演化”，点击“新建动态运行”。文件回放会先建立事件索引，随后支持：

- 每次只提交一条 Observation；
- 单步、播放、暂停、继续、取消和 0.5/1/2/5/10/20 条每秒；
- `Ordinal` 水位、当前状态/完整历史切换；
- `LATEST_OBSERVATION`、工序、刀具状态和检测状态的事实迁移；
- 旧事实保留有效区间、来源事件和 EvidenceRef；
- 播放结束后人工确认“发布快照”，不会自动写回正式本体。

也可以用推送模式接入未来的传感器或脚本：

```bash
curl -X POST http://127.0.0.1:18080/api/v2/temporal-streams/<run_id>/events \
  -H 'Content-Type: application/json' \
  -d '{
    "event_id": "demo-0001",
    "episode_id": "episode-a",
    "entity_key": "CNC_Mill_3_Axis",
    "ordinal": 1,
    "source_sequence": 0,
    "payload": {"ctx_process_phase": "roughing", "ctx_tool_condition": "unworn"},
    "source_ref": {"source_row_id": "demo-0001"}
  }'
```

重复的 `event_id + 相同载荷` 会幂等处理；相同 `event_id` 但载荷不同、或早于水位的事件会返回明确错误，不会偷偷重算历史。

推演使用已核验的 Semantica 提交 `3a69721abf72d7188a0d6fd72c8462261b2c44eb`，并受两跳、500 节点、2,000 条事实、50 条规则和 50 次迭代限制。当前内置演示把真实观测的 `ctx_tool_condition=unworn` 改为 `worn`，由 `IN_PHASE` 与 `HAS_TOOL_CONDITION` 推出 `PHASE_TOOL_STATE`；它是规则情景推演，不代表因果反事实或维护效果预测。

常用接口：

```bash
curl http://127.0.0.1:18080/api/v2/ontologies/<ontology_id>/editor
curl http://127.0.0.1:18080/api/v2/ontologies/<ontology_id>/changes
curl 'http://127.0.0.1:18080/api/v2/ontologies/<ontology_id>/what-if/context?target_instance_id=<instance_id>&episode_id=<episode_id>&at=<ordinal>'
curl http://127.0.0.1:18080/api/v2/ontologies/<ontology_id>/what-if/scenarios
```

## 9. 常用运维命令

只重启本工作树的某个服务：

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml restart backend
docker compose -f docker-compose.yml -f docker-compose.local.yml restart celery_worker
```

查看日志：

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml logs -f backend
docker compose -f docker-compose.yml -f docker-compose.local.yml logs -f celery_worker
```

停止本工作树但保留数据卷：

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml stop
```

不要使用 `docker compose down -v`，它会删除卷并可能清空本地演示数据。

## 10. 不使用 Docker 时直接部署后端

仅用于开发或测试，生产环境仍建议使用容器编排：

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-reasoning.txt
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开终端启动任务进程（需要可访问 PostgreSQL、Redis、Neo4j、MinIO、ChromaDB 和 Ollama）：

```bash
cd backend
source .venv/bin/activate
celery -A app.tasks.celery_app worker --loglevel=info
celery -A app.tasks.celery_app beat --loglevel=info
```

直接运行时请把 `DATABASE_URL`、`REDIS_URL`、`NEO4J_URI`、`MINIO_ENDPOINT`、`CHROMA_HOST`、`LITELLM_API_BASE` 和 `OLLAMA_API_BASE` 改为实际地址，并将 `AUTH_MODE` 设为 `jwt` 后配置反向代理和强密码。

## 11. 常见问题

### 页面打不开或 API 代理失败

确认前端和后端都在同一份 Compose 工作树中运行，并重建前端：

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build frontend backend
```

### 本地模型显示未连接

依次检查 `ollama serve`、`ollama list`、`curl http://127.0.0.1:11434/api/tags`，再在模型页点击“重新探测”。Docker 后端通过 `host.docker.internal` 访问宿主机 Ollama。

### 数据预览提示对象存储缺失

先检查 MinIO 容器和 `/api/v2/storage/integrity`。不要直接删除卷；旧数据记录可能仍指向原对象存储。

### M3 显示等待验证

这是标准数据的安全状态，不会自动切换到本地模型。先在 `.env` 中写入轮换后的凭证，再由管理员在明确操作下验证网关和上游授权。

### I-BADAS 重复键或安装中断

重新打开多模态数据页并继续同一个安装任务。安装器按数据集版本和样本键幂等写入，不要同时创建第二个安装任务。

## 12. 生产部署提醒

本地 Compose 仅用于演示。生产部署至少需要：关闭 `local_single_user`、启用 JWT 与强密码、使用独立密钥管理、限制 LiteLLM/数据库/对象存储网络访问、配置 HTTPS 反向代理、备份 PostgreSQL 和对象存储，并在轮换 M3 凭证后重新验证模型路由。

## 13. 关于登录密码

本交付链接对应的是本地演示配置：`AUTH_MODE=local_single_user`，因此不需要用户名或密码，直接打开 `/overview` 即可。仓库中保留 JWT 登录代码是为了部署到共享或生产环境；如果要启用 JWT，请在自己的 `.env` 中设置强随机 `SECRET_KEY` 和 `FIRST_ADMIN_PASSWORD`，不要使用或转发任何共享默认密码。
