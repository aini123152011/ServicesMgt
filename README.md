# BMC 服务管理平台（ServicesMgt）

把 **11 个 BMC 测试用服务**（NTP / TFTP / SNMP Trap / Syslog / SMTP / HTTP(S) / FTP / SFTP / SMB / NFS / WebDAV）
容器化，并用一个平台统一管理它们的**配置下发、生命周期与故障注入**。

这组服务的定位是**被测 BMC 的「测试仪器」**：BMC 固件与带外管理在真实服务行为下表现如何、
遇到异常（丢包、拒认证、时间偏移、内容损坏……）时怎么处理，靠这组夹具来复现和验证。
所以每个服务除了正向功能，还带一组**可开关的故障模式**，且都要求「客户端可观测的行为变化」作为验收依据。

---

## 快速开始（一键编排）

前置：Linux 宿主、Docker 24+ 与 Compose v2。

```bash
cp .env.example .env          # 填 SECRET_KEY / POSTGRES_PASSWORD / FIRST_SUPERUSER_PASSWORD
                              # 并把 IMAGE_PREFIX 指向你要用的镜像仓库
docker compose pull           # 拉取 12 个已构建好的镜像（不构建）
docker compose up -d          # 拉起平台（含 PostgreSQL）与 11 个服务
docker compose ps             # 看健康状态
```

镜像名统一为 `bmc-<服务>` / `bmc-platform`，支持 Docker Hub 与 GHCR 两家仓库
切换，详见[部署指南](docs/deployment.md)。本地开发改代码时再走构建：
`docker compose -f compose.yaml -f compose.build.yaml up -d --build`。

平台入口：`http://<宿主地址>:18080`，用 `.env` 里的 `FIRST_SUPERUSER` 登录。

只起其中几个：`docker compose up -d chrony nginx sftp`

> 端口方案：每个服务都在**统一范围 18101–18112** 内分配一个宿主端口（便于文档化与防火墙放行），
> 同时**保留 BMC 侧必须的标准端口**（123/69/162/514/25/445/2049/21）——被测 BMC 通常只能填 IP，
> 改不了端口，去掉就失去测试意义。

---

## 平台能做什么

| 能力 | 说明 |
| --- | --- |
| 配置下发 | 表单 → Jinja2 渲染 → 写入服务配置卷 → 触发 `/reload.sh`；**以服务真实生效为准**，不是「改了文件就算」 |
| 故障注入 | 每个服务一组故障模式（共 29 个），全部经真实协议客户端验证，详见 [验证矩阵](docs/verification-matrix.md) |
| 配置版本与回滚 | 每次下发（含回滚）留存版本，可查看内容（secret 脱敏）并一键回滚；回滚走同一条渲染→生效→审计链路 |
| 生命周期 | 起停重启、状态与健康检查、容器日志、数据卷浏览（日志按 IP/日期归档、支持关键字过滤） |
| 外部用法提示 | 每个服务详情页给出「BMC / Linux 客户端 / 浏览器怎么连」的命令示例，可直接照抄 |
| RBAC 与审计 | 只读 / 操作员 / 管理员三种角色；所有写操作留审计日志（可按动作、服务、关键字查询） |
| 平台自更新 | 检测新版本后可在界面内触发更新，更新失败自动回滚（健康检查不通过即回退） |

---

## 服务一览

| 服务 | 用途 | 容器内端口 | 宿主发布（统一范围 + 标准端口） | 生效方式 | IPv6 |
| --- | --- | --- | --- | --- | --- |
| chrony | NTP 时间同步 | 123/udp | 18101、123 | 热 | ✅ 双栈（`bindaddress` 两族） |
| nginx | HTTP / HTTPS 文件服务 | 80、443 | 18102、18103 | 热 | ✅ 双栈（`listen [::]`） |
| rsyslog | BMC 日志归集 | 514/tcp+udp | 18104、514 | 重启 | ✅ 双栈（默认） |
| webdav | WebDAV 文件共享 | 8080 | 18105 | 重启 | ✅ 双栈（`Listen [::]`） |
| sftp | SFTP 文件传输 | 22 | 18106 | 重启 | ✅ 双栈（默认） |
| vsftpd | FTP 文件共享 | 21、40000-40100 | 18107、21、40000-40100 | 重启 | ✅ 双栈（`listen_ipv6`） |
| tftpd-hpa | TFTP 文件传输 | 69/udp | 18108、69 | 重启 | ✅ 双栈（`[::]:69`） |
| samba | Samba / CIFS 文件共享 | 445 | 18109、445 | 重启 | ✅ 双栈（默认） |
| nfs-ganesha | NFS 网络文件系统 | 2049 | 18110、2049 | 重启 | ✅ 双栈（默认） |
| snmptrapd | SNMP Trap 接收 | 162/udp | 18111、162 | 重启 | ✅ 双栈（`udp6:162`） |
| postfix | SMTP 邮件中继 | 25 | 18112、25 | 重启 | ✅ 双栈（默认） |

每个服务都能**脱离平台单独部署**（`cd services/<name> && docker compose up -d`），
这时用服务自身的规范端口（nginx 80/443、sftp 22 等）。

---

## 仓库结构

```
services/<name>/            每个服务一个插件目录：Dockerfile、compose、manifest、schema、
                            templates/*.j2（配置模板）、defaults/（空卷种子）、entrypoint、reload
platform/backend/           FastAPI + SQLModel + PostgreSQL + Alembic（配置渲染、生命周期、审计、RBAC）
platform/frontend/          React 19 + Vite + TanStack + shadcn/ui
compose.yaml                一键编排：平台 + PostgreSQL + 11 个服务
scripts/build.sh            服务镜像统一构建入口（本地/CI 共用，支持多架构）
scripts/verify_bmc_platform_e2e.py   实机验收套件（117 条用例，真实协议判定）
docs/                       部署、服务接入、容器踩坑与验证矩阵
```

---

## 文档

- [部署指南](docs/deployment.md) —— 一键编排、单服务独立部署、平台更新与排障
- [如何新增一个服务插件](docs/adding-a-service.md) —— 目录契约、manifest/schema 字段、验收清单
- [容器运行时踩坑清单](docs/container-runtime-guidelines.md) —— 11 类「配置写对了但服务没按配置工作」的坑与修法
- [验证矩阵](docs/verification-matrix.md) —— 11 服务 × 4 类验证项 × 29 个故障模式的实测结论

---

## 构建与 CI

```bash
scripts/build.sh                 # 构建全部服务镜像（当前架构）
scripts/build.sh chrony nginx    # 只构建指定服务
make check                       # 多架构（linux/arm64,linux/amd64）构建校验，不产出镜像

# 发布到镜像仓库（多架构 manifest；凭据只走环境变量）
REGISTRIES="docker.io/<账号>/" TAG=latest   DOCKERHUB_USER=... DOCKERHUB_TOKEN=... bash scripts/publish.sh
```

CI 只做「准备环境 + 调用同一个 `scripts/build.sh`」，构建逻辑不在流水线里重复：

- GitHub Actions：`.github/workflows/build.yml`（现役 CI：main 或手动触发时构建多架构并推 GHCR，
  配了 `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN` 则一并推 Docker Hub）

---

## 安全说明

- 仓库里的服务默认口令（如 nginx Basic 认证、samba 共享账号）都是**夹具值**，只应在隔离的测试网段使用。
- **不要把真实 BMC / 宿主机的账号口令写进仓库**（文档、任务记录、提交信息都不要）——仓库只要可能被
  共享或发布，提交即等于公开，且 `git log -S` 能翻出历史版本，事后改文件撤不回。
- 平台自身的密钥（`SECRET_KEY`、数据库口令、超管口令）只放在部署机的 `.env`（`chmod 600`），不进仓库。
