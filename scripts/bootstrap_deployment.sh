#!/usr/bin/env bash
# Create a private deployment configuration and start the complete workbench.
#
# This script deliberately generates secrets on the target machine.  It never
# prints them, stores them in Git, or asks a user to copy a password from this
# repository.  Re-running it keeps existing values and Docker volumes.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SEED_DEMO=false
case "${1:-}" in
  ""|--no-seed) ;;
  --seed-demo) SEED_DEMO=true ;;
  --help|-h)
    cat <<'USAGE'
用法：
  ./scripts/bootstrap_deployment.sh             生成本机密钥并启动工作台
  ./scripts/bootstrap_deployment.sh --seed-demo  启动并导入公开 frePPLe 演示数据
  ./scripts/bootstrap_deployment.sh --no-seed    只启动服务，不导入演示数据

脚本只在当前服务器的 .env 和 Docker 数据卷中保存运行时密钥；仓库不含
密码、API Key 或数据库备份。重复运行会复用已有配置和数据，不执行删除操作。
USAGE
    exit 0
    ;;
  *)
    echo "未知参数：$1" >&2
    echo "运行 ./scripts/bootstrap_deployment.sh --help 查看用法。" >&2
    exit 2
    ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  echo "未找到 Docker。请先安装 Docker Desktop 或 Docker Engine。" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "未找到 Docker Compose 插件。请先升级 Docker。" >&2
  exit 1
fi

ENV_FILE="$ROOT_DIR/.env"
ENV_WAS_CREATED=false
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ROOT_DIR/.env.example" "$ENV_FILE"
  ENV_WAS_CREATED=true
fi
chmod 600 "$ENV_FILE"

# Never guess a PostgreSQL password for an existing external volume.  The
# original .env is the only place where that credential is intentionally kept.
if [[ "$ENV_WAS_CREATED" == true ]] && docker volume inspect nano-ontoprompt_pg_data >/dev/null 2>&1; then
  echo "检测到已有 nano-ontoprompt_pg_data 数据卷，但本机没有原来的 .env。" >&2
  echo "为避免损坏或误连现有数据库，请先恢复对应 .env，再重新运行此脚本。" >&2
  exit 1
fi

read_env() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

random_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    if ! command -v od >/dev/null 2>&1; then
      echo "需要 openssl 或 od 才能生成本机密钥。" >&2
      exit 1
    fi
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
  fi
}

random_fernet() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n'
  else
    # 32 random bytes encoded as URL-safe base64.  The fallback is only used
    # on minimal systems where openssl is absent.
    if ! command -v od >/dev/null 2>&1; then
      echo "需要 openssl 或 od 才能生成本机密钥。" >&2
      exit 1
    fi
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n' | xxd -r -p | base64 | tr '+/' '-_' | tr -d '\n'
  fi
}

set_if_empty() {
  local key="$1"
  local value="$2"
  local tmp
  tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { found = 0 }
    $0 ~ ("^" key "=") {
      current = substr($0, length(key) + 2)
      if (current == "" || current == "<generated>" || current == "<generated-postgres-password>" || current == "change-me" || current == "sk-master-change-me" || current == "sk-salt-change-me") {
        print key "=" value
      } else {
        print
      }
      found = 1
      next
    }
    { print }
    END { if (!found) print key "=" value }
  ' "$ENV_FILE" > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$ENV_FILE"
}

# Keep an existing database password when upgrading an older checkout that
# only had DATABASE_URL.  A fresh checkout receives a random password.
POSTGRES_PASSWORD="$(read_env POSTGRES_PASSWORD)"
DATABASE_URL_CURRENT="$(read_env DATABASE_URL)"
if [[ -z "$POSTGRES_PASSWORD" ]]; then
  if [[ "$DATABASE_URL_CURRENT" =~ ^postgresql://[^:]+:([^@]+)@ ]]; then
    POSTGRES_PASSWORD="${BASH_REMATCH[1]}"
  fi
  if [[ -z "$POSTGRES_PASSWORD" || "$POSTGRES_PASSWORD" == \<*\> ]]; then
    POSTGRES_PASSWORD="$(random_hex)"
  fi
fi

set_if_empty ENVIRONMENT development
set_if_empty BACKEND_AUTH_MODE local_single_user
set_if_empty FRONTEND_AUTH_MODE local_single_user
set_if_empty POSTGRES_DB ontoprompt
set_if_empty POSTGRES_USER ontoprompt
set_if_empty POSTGRES_PASSWORD "$POSTGRES_PASSWORD"
set_if_empty DATABASE_URL "postgresql://$(read_env POSTGRES_USER):$POSTGRES_PASSWORD@db:5432/$(read_env POSTGRES_DB)"
set_if_empty REDIS_URL redis://redis:6379/0
set_if_empty SECRET_KEY "$(random_hex)"
set_if_empty ENCRYPTION_KEY "$(random_fernet)"
set_if_empty FIRST_ADMIN_USER admin
set_if_empty FIRST_ADMIN_PASSWORD "$(random_hex)"
set_if_empty UPLOADS_DIR /uploads
set_if_empty CMAPSS_FD001_HOST_DIR ./data/cmapss_fd001_demo
set_if_empty CORS_ORIGINS http://localhost:15173,http://127.0.0.1:15173
set_if_empty LITELLM_MASTER_KEY "sk-$(random_hex)"
set_if_empty LITELLM_SALT_KEY "sk-$(random_hex)"
set_if_empty UI_USERNAME admin
set_if_empty UI_PASSWORD "$(random_hex)"
set_if_empty OLLAMA_API_KEY "local-$(random_hex)"
set_if_empty NEO4J_URI bolt://neo4j:7687
set_if_empty NEO4J_USER neo4j
set_if_empty NEO4J_PASSWORD "$(random_hex)"
set_if_empty FALKORDB_HOST falkordb
set_if_empty FALKORDB_PORT 6379
set_if_empty MINIO_ENDPOINT minio:9000
set_if_empty MINIO_ACCESS_KEY "minio$(random_hex | cut -c1-16)"
set_if_empty MINIO_SECRET_KEY "$(random_hex)"
set_if_empty MINIO_USE_SSL false
set_if_empty MINIO_DATA_VOLUME nano-ontoprompt_workbench_minio_data
set_if_empty CHROMA_HOST chromadb
set_if_empty CHROMA_PORT 8001

# These provider variables intentionally stay empty until the operator adds a
# key locally.  A standard-data M3 call is never silently replaced by Ollama.
set_if_empty MINIMAX_API_KEY ""
set_if_empty OPENAI_API_KEY ""
set_if_empty ANTHROPIC_API_KEY ""
set_if_empty DASHSCOPE_API_KEY ""

if ! docker volume inspect nano-ontoprompt_pg_data >/dev/null 2>&1; then
  docker volume create nano-ontoprompt_pg_data >/dev/null
fi
if ! docker volume inspect nano-ontoprompt_workbench_falkordb_data >/dev/null 2>&1; then
  docker volume create nano-ontoprompt_workbench_falkordb_data >/dev/null
fi

COMPOSE=(docker compose --env-file "$ENV_FILE" -f docker-compose.yml -f docker-compose.local.yml)
"${COMPOSE[@]}" config >/dev/null
"${COMPOSE[@]}" up -d --build

echo "等待后端健康检查……"
if command -v curl >/dev/null 2>&1; then
  healthy=false
  for _ in $(seq 1 180); do
    if curl --fail --silent --show-error http://127.0.0.1:18080/health >/dev/null 2>&1; then
      healthy=true
      break
    fi
    sleep 1
  done
  if [[ "$healthy" != true ]]; then
    echo "后端在等待时间内没有通过健康检查。请运行：" >&2
    echo "  ${COMPOSE[*]} ps" >&2
    echo "  ${COMPOSE[*]} logs --tail=120 backend" >&2
    exit 1
  fi
else
  echo "未找到 curl，已启动容器；请手动检查 http://127.0.0.1:18080/health。"
fi

if [[ "$SEED_DEMO" == true ]]; then
  echo "导入公开 frePPLe 演示数据（幂等操作，不覆盖其他本体）……"
  "${COMPOSE[@]}" cp data/frepple_demo/manufacturing_demo.json backend:/tmp/manufacturing_demo.json
  "${COMPOSE[@]}" cp scripts/import_frepple_demo.py backend:/tmp/import_frepple_demo.py
  "${COMPOSE[@]}" exec -T backend python /tmp/import_frepple_demo.py /tmp/manufacturing_demo.json
  "${COMPOSE[@]}" cp scripts/verify_frepple_demo.py backend:/tmp/verify_frepple_demo.py
  "${COMPOSE[@]}" exec -T backend python /tmp/verify_frepple_demo.py /tmp/manufacturing_demo.json
fi

echo
echo "工作台已启动： http://127.0.0.1:15173/overview"
echo "后端健康检查： http://127.0.0.1:18080/health"
echo "配置文件仅保存在本机： ${ENV_FILE}（已设置为 600 权限）"
echo "不会把 .env、密码、API Key 或数据库卷提交到 Git。"
