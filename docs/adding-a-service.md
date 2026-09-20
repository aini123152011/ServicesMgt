# 如何新增一个服务插件

> 范本实现：`services/chrony/`（第一个竖切样板，包含全部支撑文件）。

## 目录契约

```
services/<name>/
  Dockerfile              # debian:bookworm-slim + apt 官方包；锁定版本（注释记录版本号与升级方法）
  docker-compose.yml      # 可独立 up 的最小 compose：独立 network + 命名卷 + healthcheck
  reload.sh               # 统一生效入口：热加载（SIGHUP 等）优先，否则重启进程；无进程时非 0 退出
  entrypoint.sh           # 空卷播种：配置卷为空时从镜像内置 defaults/ 复制初始配置，再 exec 主进程
  defaults/<主配置>       # 镜像内置种子配置，内容 = schema 默认值的渲染产物（两者必须保持一致）
  schema.json             # 可配置项定义，平台据此生成表单与校验
  templates/*.j2          # 原生配置文件模板（Jinja2），平台据此渲染
  manifest.yaml           # 元数据，平台 Service Registry 据此识别（字段缺失会被跳过并告警）
```

## manifest.yaml 字段（平台固定契约）

| 字段 | 说明 |
|------|------|
| `name` | 服务名 = 目录名，平台主键 |
| `display_name` | 中文显示名 |
| `category` | `time` \| `file-share` \| `log-monitor` |
| `description` | 一句话说明 |
| `container_name` | 容器名，约定 `bmc-<name>` |
| `config_dir` | 配置卷在容器内的挂载点（如 `/etc/chrony`） |
| `config_files` | 渲染产物相对 config_dir 的路径列表 |
| `ports` | `[{port, protocol, description}]` |
| `reload_mode` | `hot`（/reload.sh 不中断）\| `restart` |

## schema.json 字段格式

顶层 `{"fields": [...]}`；每个 field：`name`、`type`（`string`|`integer`|`boolean`|`enum`|`list`）、`label`（中文）、`default`、`required`、`help`（均可选）；类型专属：enum 加 `options`，string 加 `pattern`（正则），integer 加 `min`/`max`，list 加 `item_pattern`（每项正则，防注入，必填）。**所有默认值必须能通过自己的校验规则。**

## 平台侧行为（新增服务零改动）

- Service Registry 扫描 `SERVICES_DIR`（`app/core/config.py`）加载 manifest+schema
- 配置渲染：Jinja2（`trim_blocks=True + lstrip_blocks=True`，模板头部勿删该约定注释）渲染到 `{VOLUMES_MOUNT_ROOT}/<name>-config/`，原子写入
- 生效：`docker exec bmc-<name> /reload.sh`；容器未运行时保存配置但 `applied=false`（启动后生效）
- 模板渲染环境说明见 `services/chrony/templates/chrony.conf.j2` 头部注释

## 验收清单

- [ ] `cd services/<name> && docker compose up -d` 独立可用（AC1）
- [ ] Dockerfile 架构无关，`make check` 多架构构建通过（AC6）
- [ ] `defaults/<主配置>` 与 schema 默认值渲染产物逐字节一致
- [ ] reload.sh 在容器内可执行（LF 行尾、exec 位）
