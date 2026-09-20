# BMC Services Platform 文档

> BMC 服务聚合管理平台 —— 部署、开发与服务接入文档。

## 规划中的文档

| 文档 | 内容 | 状态 |
|------|------|------|
| [deployment.md](./deployment.md) | 独立部署 / 一键部署 / 环境变量 | 占位（阶段 4 完善） |
| [adding-a-service.md](./adding-a-service.md) | 如何新增一个服务插件（目录契约） | 占位（阶段 1 样板完成后撰写） |

## 快速指引（当前）

- 本地开发环境变量：复制 `.env.example` 为根目录 `.env`
- 镜像构建：`make help` / `scripts/build.sh --help`
- 代码规范：见 `.trellis/spec/backend/` 与 `.trellis/spec/frontend/`
