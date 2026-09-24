#!/usr/bin/env bash
# 在目标机上跑后端 pytest 全套（开发机没有 PostgreSQL，整套测试跑不起来）。
#
# 为什么必须到目标机跑：`tests/conftest.py` 的 `db` fixture 是 session 级 autouse，会真的建表、
# 灌种子、跑完再清理——开发机上没有库，`pytest tests/` 连 conftest 都过不去（实测：连接超时 4 分钟）。
#
# **绝不能用已部署的平台库跑**：conftest 的清理会删掉 AuditLog / UserRole / User / Role 全表，
# 跑一次就把平台登录账号和审计日志清空。所以这里另起一个一次性 PostgreSQL + 一次性测试容器，
# 与部署库完全隔离。
#
# 用法（在目标机上，仓库已同步到 DEPLOY_DIR）：
#   bash scripts/run_backend_tests_remote.sh                 # 跑全部
#   bash scripts/run_backend_tests_remote.sh tests/test_service_seeds.py -q   # 透传 pytest 参数
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/servicesmgt-deploy}"
IMAGE="${IMAGE:-fx-platform:latest}"
PG_IMAGE="${PG_IMAGE:-postgres:18.4-alpine}"
PG_NAME="fx-pytest-pg"
NET="fx-pytest-net"
DB_USER="postgres"
DB_PASSWORD="pytest-only-not-a-secret"
DB_NAME="bmc_platform"

cleanup() {
  docker rm -f "$PG_NAME" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> 一次性 PostgreSQL（$PG_IMAGE）"
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
docker rm -f "$PG_NAME" >/dev/null 2>&1 || true
docker run -d --name "$PG_NAME" --network "$NET" \
  -e POSTGRES_USER="$DB_USER" -e POSTGRES_PASSWORD="$DB_PASSWORD" -e POSTGRES_DB="$DB_NAME" \
  "$PG_IMAGE" >/dev/null

echo "==> 等待数据库就绪"
for _ in $(seq 1 30); do
  if docker exec "$PG_NAME" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "==> 跑 pytest（容器内；tests/ 与 services/ 用部署目录里的当前版本挂进去）"
# - tests 用部署目录的当前版本（镜像里的是构建时的旧副本）
# - app 也挂进去：uv 把项目装成 editable，挂载后测的就是当前源码而不是镜像里的旧代码
# - pyproject.toml / uv.lock 也挂进去：镜像里的依赖清单是构建时的快照，新增依赖
#   （如验证码用的 pillow）不挂这两个文件，容器里 `uv sync --frozen` 装不上，
#   测试会直接 ImportError。挂了之后测试环境始终与工作树一致
# - SERVICES_DIR 指向挂载的服务目录，test_service_schemas/seeds 才能扫到全部服务
# - UV_* 走国内镜像，避免容器里再拉一遍依赖超时
# - pytest 用**绝对路径**调用：`bash -lc` 会加载 /etc/profile 重算 PATH，镜像里 ENV 设的
#   /app/backend/.venv/bin 会被覆盖掉，直接写 `pytest` 会报 command not found（实测踩到）
# - **先跑迁移再跑测试**：空库没有表，conftest 的 init_db 一查 user 表就报
#   `UndefinedTable: relation "user" does not exist`（271 个用例全 ERROR）。仓库自己的入口
#   scripts/prestart.sh 也是「先 alembic upgrade head 再起服务」，这里对齐。
docker run --rm --network "$NET" \
  -v "${DEPLOY_DIR}/platform/backend/tests:/app/backend/tests:ro" \
  -v "${DEPLOY_DIR}/platform/backend/app:/app/backend/app:ro" \
  -v "${DEPLOY_DIR}/platform/backend/pyproject.toml:/app/backend/pyproject.toml:ro" \
  -v "${DEPLOY_DIR}/platform/backend/uv.lock:/app/backend/uv.lock:ro" \
  -v "${DEPLOY_DIR}/services:/app/services:ro" \
  -e DATABASE_URL="postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@${PG_NAME}:5432/${DB_NAME}" \
  -e SERVICES_DIR=/app/services \
  -e VOLUMES_MOUNT_ROOT=/tmp/platform-volumes \
  -e SECRET_KEY=pytest-only-secret-key \
  -e PROJECT_NAME=pytest-only-project \
  -e FIRST_SUPERUSER=admin@example.com \
  -e FIRST_SUPERUSER_PASSWORD=pytest-only-password \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e UV_DEFAULT_INDEX="${UV_DEFAULT_INDEX:-https://mirrors.aliyun.com/pypi/simple/}" \
  "$IMAGE" bash -lc "cd /app/backend && uv sync --frozen --dev >/dev/null && /app/backend/.venv/bin/alembic upgrade head >/dev/null && /app/backend/.venv/bin/pytest -p no:cacheprovider $*"
