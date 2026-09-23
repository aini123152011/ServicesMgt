# BMC Services Platform 文档

> BMC 服务聚合管理平台 —— 部署、开发与服务接入文档。

| 文档 | 内容 |
|------|------|
| [../README.md](../README.md) | 项目总览：能力、服务一览、快速开始、构建与 CI |
| [deployment.md](./deployment.md) | 一键编排 / 单服务独立部署 / 平台更新与排障 |
| [network-plan.md](./network-plan.md) | 网络规划：宿主网口、14 张 Docker 网络、13 服务端口映射、BMC 侧地址对照 |
| [adding-a-service.md](./adding-a-service.md) | 如何新增一个服务插件（目录契约、manifest/schema 字段、验收清单） |

容器踩坑与验证结论（改服务前值得先读）：

| 文档 | 内容 |
|------|------|
| [container-runtime-guidelines.md](./container-runtime-guidelines.md) | 11 类「配置写对了但服务没按配置工作」的坑与修法 |
| [verification-matrix.md](./verification-matrix.md) | 13 服务 × 4 类验证项 × 38 个故障模式的实测结论 |

## 快速指引

- 环境变量：复制 `.env.example` 为根目录 `.env`（含镜像来源 `IMAGE_PREFIX`/`IMAGE_TAG`）
- 一键起全部：`docker compose pull && docker compose up -d`
- 镜像构建：`scripts/build.sh --help` / `make help`（多架构校验用 `make check`）
- 镜像发布：`REGISTRIES=... bash scripts/publish.sh`（凭据走环境变量）
- 实机验收套件：`scripts/verify_bmc_platform_e2e.py`（162 条用例，按阶段可单独跑 `--phase <name>`）
