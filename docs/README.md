# BMC Services Platform 文档

> BMC 服务聚合管理平台 —— 部署、开发与服务接入文档。

| 文档 | 内容 |
|------|------|
| [../README.md](../README.md) | 项目总览：能力、服务一览、快速开始、构建与 CI |
| [deployment.md](./deployment.md) | 一键编排 / 单服务独立部署 / 平台更新与排障 |
| [adding-a-service.md](./adding-a-service.md) | 如何新增一个服务插件（目录契约、manifest/schema 字段、验收清单） |

工程知识库（踩坑与契约，改代码前值得先读）：

| 文档 | 内容 |
|------|------|
| [容器运行时踩坑清单](../.trellis/spec/services/container-runtime-guidelines.md) | 11 类「配置写对了但服务没按配置工作」的坑与修法 |
| [服务插件契约](../.trellis/spec/services/index.md) | manifest / schema / 模板 / 种子的硬性要求 |
| [验证矩阵](../.trellis/spec/services/verification-matrix.md) | 11 服务 × 4 类验证项 × 29 个故障模式的实测结论 |
| [部署层规范](../.trellis/spec/deploy/platform-deployment.md) | 镜像发布与拉取式部署、平台重建与迁移的契约 |
| [后端规范](../.trellis/spec/backend/index.md) / [前端规范](../.trellis/spec/frontend/index.md) | 分层结构、错误语义、质量门禁 |

## 快速指引

- 环境变量：复制 `.env.example` 为根目录 `.env`
- 一键起全部：`docker compose up -d`
- 镜像构建：`scripts/build.sh --help` / `make help`（多架构校验用 `make check`）
- 实机验收套件：`scripts/verify_bmc_platform_e2e.py`（117 条用例，按阶段可单独跑 `--phase <name>`）
- 代码规范与门禁见 `.trellis/spec/backend/` 与 `.trellis/spec/frontend/`
