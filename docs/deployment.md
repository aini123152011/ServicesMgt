# 部署指南（占位）

> 状态：占位 —— 在阶段 4（一键编排与收尾）完善。当前有效的部署事实如下。

## 环境变量

- 复制根目录 `.env.example` 为 `.env`（platform/backend 读取 `../.env`）
- 必填：`SECRET_KEY`、`PROJECT_NAME`、`DATABASE_URL`、`FIRST_SUPERUSER`、`FIRST_SUPERUSER_PASSWORD`

## 平台后端（本地开发）

```bash
cd platform/backend
uv sync                        # 安装依赖
# 本地 PostgreSQL（或用已有的实例，改 .env 的 DATABASE_URL）
docker run -d --name bmc-pg -e POSTGRES_PASSWORD=changethis -e POSTGRES_DB=bmc_platform -p 5432:5432 postgres:16
bash scripts/prestart.sh       # 迁移 + 初始数据
bash scripts/tests-start.sh    # 或直接跑测试
uv run fastapi dev app/main.py
```

## 前端（本地开发）

```bash
cd platform/frontend
pnpm install
pnpm dev                       # http://localhost:5173
```

## 镜像构建

```bash
make check                              # 多架构(arm64+amd64)构建校验
make push REGISTRY=ghcr.io/<owner>      # 构建并推送全部服务镜像
```
