# 部署指南

> 三种部署形态，按需要选：**一键编排**（推荐，平台 + 11 服务一起）、**单服务独立部署**（在别的机器上
> 只跑某一个服务）、**平台单独更新**（已有部署，只换平台镜像）。
>
> 镜像**默认从仓库拉取**（一次构建、处处拉取）；本地构建是开发态的可选路径。

---

## 零、镜像从哪来（先读这一节）

镜像名统一为 `bmc-<服务>` / `bmc-platform`，从哪个仓库拉由 `.env` 决定：

```dotenv
IMAGE_PREFIX=docker.io/aini123152008/     # 完整仓库前缀，**含结尾斜杠**
IMAGE_TAG=latest                          # 也可钉版本，如 0.6.2
```

| 仓库 | IMAGE_PREFIX | 目标机需要 |
| --- | --- | --- |
| Docker Hub | `docker.io/aini123152008/` | 公开仓库无需登录 |
| GitHub GHCR | `ghcr.io/aini123152011/` | 包为私有，需 `docker login ghcr.io`（PAT 带 read:packages） |
| 内网 GitLab | `<内网 GitLab 容器仓库>/` | 需 `docker login`（账号或部署令牌） |
| 本地镜像（开发态） | 留空 | 配 `compose.build.yaml` 本地构建 |

**发布镜像**（本地与 CI 同一个入口）：

```bash
# 推一家：凭据只走环境变量
REGISTRIES="docker.io/aini123152008/" TAG=latest   DOCKERHUB_USER=<账号> DOCKERHUB_TOKEN=<访问令牌> bash scripts/publish.sh

# 推多家（逗号分隔）
REGISTRIES="ghcr.io/<账号>/,docker.io/<账号>/" TAG=0.6.2   GHCR_USER=<账号> GHCR_TOKEN=<带 write:packages 的 PAT>   DOCKERHUB_USER=<账号> DOCKERHUB_TOKEN=<令牌> bash scripts/publish.sh
```

CI 侧：GitHub Actions 在 **main 或手动触发**时发布（GHCR 用内置 token；配了
`DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN` 两个仓库密钥就一并推 Docker Hub）；
GitLab CI 在默认分支用内置 `CI_REGISTRY_*` 推自己的容器仓库。

**本地构建**（开发态，改代码后想立刻验证时用）：

```bash
cd platform/frontend && pnpm build        # 平台镜像需要前端产物
docker compose -f compose.yaml -f compose.build.yaml up -d --build
# 或只构建服务镜像（支持换源加速）：BUILD_ARGS="APT_MIRROR=mirrors.aliyun.com" scripts/build.sh
```

---

## 一、一键编排（平台 + 11 个服务）

前置：Linux 宿主、Docker 24+ 与 Compose v2；宿主机需能拉取 `debian:bookworm-slim`、`postgres:18.4-alpine`
等基础镜像（国内可给 Docker 配 registry 镜像加速）。

```bash
cp .env.example .env          # 见下方「环境变量」；把 IMAGE_PREFIX 指向你要用的仓库
docker login <仓库主机>        # 仅私有仓库需要（如 ghcr.io）
docker compose pull           # 先把 12 个镜像拉下来（这一步不构建任何东西）
docker compose up -d          # 全部拉起
docker compose ps             # 确认都是 healthy
```

平台入口 `http://<宿主>:18080`，首次登录用 `.env` 里的 `FIRST_SUPERUSER` / `FIRST_SUPERUSER_PASSWORD`。

只起部分服务、或重启单个服务：

```bash
docker compose up -d chrony nginx       # 只起 NTP 与 HTTP
docker compose restart chrony           # 重启单个
docker compose logs -f platform         # 看平台日志
docker compose down                     # 停止（保留卷）
```

### 目录与卷

编排用**绑定挂载**，与平台部署目录的布局一致，两种部署方式之间可以直接复用配置与数据：

```
./volumes/<服务>-config/     配置卷（平台渲染产物写在这里，容器内挂到 manifest.config_dir）
./volumes/<服务>-data/       数据卷（日志、共享文件、校准数据等）
```

`bmc-platform-isolated-pg-data` 是**命名卷**且声明为 `external: true`——这样编排与「平台单独部署」
共用同一份数据库，不会被 Compose 的项目名前缀另建一个空卷（踩过：加了前缀的空库导致登录 500）。

### 端口

- 统一范围 **18101–18112**：每个服务一个宿主端口，便于防火墙放行与文档化；
- 标准端口 **123 / 69 / 162 / 514 / 25 / 445 / 2049 / 21**：被测 BMC 通常只能填 IP、改不了端口，
  这几个必须保留（chrony、tftpd-hpa、snmptrapd、rsyslog、postfix、samba、nfs-ganesha、vsftpd 是双发布）；
- nginx / sftp / webdav 没有标准端口需求，只发布范围内端口。

### 环境变量

`.env`（同目录，`chmod 600`，**不进仓库**）：

| 变量 | 说明 |
| --- | --- |
| `SECRET_KEY` | 平台 JWT 签名密钥（必填） |
| `POSTGRES_PASSWORD` | 平台数据库口令（必填） |
| `FIRST_SUPERUSER` / `FIRST_SUPERUSER_PASSWORD` | 初始管理员账号（必填） |
| `POSTGRES_USER` / `POSTGRES_DB` | 默认 `bmc` / `bmc_platform` |
| `PROJECT_NAME` / `FRONTEND_HOST` | 界面标题与前端地址（默认 `http://localhost:18080`） |
| `UPDATE_REGISTRY` | 平台自更新用的镜像仓库地址；留空则关闭在线更新入口 |

---

## 二、单服务独立部署

每个服务都能脱离平台单独跑（用于「只想在别的机器上提供一个 NTP/TFTP/…」）：

```bash
cd services/<name>
docker compose up -d          # 用服务自身的规范端口（nginx 80/443、sftp 22、…）
```

首次启动时配置卷为空，`entrypoint.sh` 会从镜像内置的 `defaults/` 播种一份初始配置，
因此**开箱即用**；接上平台后，平台渲染的配置会覆盖同一份卷（布局与一键编排一致）。

> 种子文件必须等于「模板按 schema 默认值的渲染结果」，由 `platform/backend/tests/test_service_seeds.py` 守着；
> 改 schema 默认值或模板后要重新生成种子，否则测试失败。

---

## 三、平台镜像构建与更新

平台镜像两种定义：

- `Dockerfile.platform` —— 权威定义（含 `uv sync`），首次构建用；
- `Dockerfile.platform.fast` —— 分层增量（`FROM bmc-platform:latest` 再覆盖 app 与前端产物），日常更新用。

增量部署到目标机（本地打包 → ssh 传包 → 远端构建/迁移/重建）：

```bash
tar --exclude='__pycache__' -czf /tmp/platform-update.tgz \
  platform/backend/app platform/backend/scripts platform/frontend/dist \
  scripts/verify_bmc_platform_e2e.py scripts/deploy_platform_remote.sh
ssh root@<目标机> 'cat > /tmp/platform-update.tgz' < /tmp/platform-update.tgz
ssh root@<目标机> 'VERSION=<x.y.z> bash -s' < scripts/deploy_platform_remote.sh
```

脚本要点（都在 `.trellis/spec/deploy/platform-deployment.md` 里展开）：

- **运行参数从现有容器读回**（环境变量、挂载、端口、网络、重启策略），脚本里不写任何密钥；
- 排除 `PLATFORM_VERSION` / `PLATFORM_BUILD`（镜像自带元数据，照抄旧值会让新镜像继续报旧版本）；
- **先迁移再切流**：用新镜像跑一次 `alembic upgrade head`，再替换容器——容器 `CMD` 不会自动迁移；
- 分层增量构建在层数超过阈值时先压平（overlay2 下层上限 128 层）。

平台界面里的「系统更新」走的是同一条链路（`app/container_rebuild.py`），更新后健康检查不过会自动回滚。

---

## 四、排障

| 现象 | 原因与处理 |
| --- | --- |
| 登录报 500 | 数据库是空的或连错库：确认 `bmc-platform-isolated-pg-data` 卷是 `external` 的同一份，别被项目名前缀另建 |
| 配置保存成功但服务没变 | 看平台返回的 `applied`；`applied=false` 表示容器没运行（配置已落盘，启动后生效）。仍不对就查容器内 `/reload.sh` 的输出 |
| reload 报 409 / 502 | 容器正在重启：平台会等待重试；持续失败看容器日志（配置语法错误会让守护进程反复退出） |
| 服务容器反复重启 | 渲染出的配置非法。先看容器日志里的 parse 报错，再对照模板与 schema 默认值 |
| `docker compose up` 报端口占用 | 标准端口（25/445/2049…）与宿主机自带服务冲突，改 `.env` 或先停宿主服务 |
| 平台更新后版本号没变 | 部署时把旧容器的 `PLATFORM_VERSION` 也带过去了；见上面的「排除镜像自带元数据」 |

更多容器层面的坑（权限、`/var/run` 空 tmpfs、被动端口段、降权守护进程的 reload 陷阱等）见
[容器运行时踩坑清单](../.trellis/spec/services/container-runtime-guidelines.md)。
