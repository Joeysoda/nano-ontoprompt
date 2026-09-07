# 本体构筑工作台：Docker 与后端部署说明

本文档对应分支 `factorynet-temporal-workbench`，用于本地完整演示。当前工作树的前端、后端、Celery、LiteLLM、数据库和本地模型均按本说明启动。

## 1. 运行边界

- 目标：电脑浏览器本地演示，不做公网部署。
- 认证：`docker-compose.local.yml` 显式启用 `AUTH_MODE=local_single_user`，打开网站后不需要登录。
- 绑定：演示端口只绑定 `127.0.0.1`，不会把数据库、对象存储或 LiteLLM 暴露到局域网。
- 数据：不执行 `down -v`，不删除既有 PostgreSQL、Neo4j、MinIO 或 ChromaDB 数据卷。

## 2. 需要安装的内容

### 必需

1. Docker Desktop（启用 Linux containers）。
2. Git。
3. Ollama。安装后准备本地审查模型：

```bash
ollama serve                 # 如果 Ollama 已作为系统服务运行，不要重复启动
ollama pull qwen3.5:0.8b
curl http://127.0.0.1:11434/api/tags
```

最后一个请求应能看到 `qwen3.5:0.8b`。网页不会自动安装模型，只会在“模型与审查”页面检测服务。

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

## 8. 常用运维命令

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

## 9. 不使用 Docker 时直接部署后端

仅用于开发或测试，生产环境仍建议使用容器编排：

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
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

## 10. 常见问题

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

## 11. 生产部署提醒

本地 Compose 仅用于演示。生产部署至少需要：关闭 `local_single_user`、启用 JWT 与强密码、使用独立密钥管理、限制 LiteLLM/数据库/对象存储网络访问、配置 HTTPS 反向代理、备份 PostgreSQL 和对象存储，并在轮换 M3 凭证后重新验证模型路由。
