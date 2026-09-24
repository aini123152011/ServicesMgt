# WebDAV 文件共享服务镜像：debian:bookworm-slim + 发行版官方 apt 包，架构无关，
# 可直接用 docker buildx build --platform linux/arm64,linux/amd64 多架构构建
FROM debian:bookworm-slim

# 锁定版本：Apache 2.4（编写时 bookworm 包版本为 2.4.68-1~deb12u1）。
# NOTE: bookworm 点发布会跟随上游 2.4.x 新版本（历史上 2.4.57→2.4.62→2.4.68），
# 精确锁定 2.4.68* 会在下次点发布后构建失败，故只锁 2.4 大版本、放行安全升级。
# 升级方法：确认 bookworm 存档已有目标版本后重建镜像；上游 3.x 发布时改为 3.* 并做回归验证。
# apache2-utils 提供 htpasswd（entrypoint 把用户清单转换为 Basic 认证凭据文件）。
# APT_MIRROR：默认官方源；境内构建可用 --build-arg APT_MIRROR=mirrors.aliyun.com 加速
ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        apache2=2.4* \
        apache2-utils=2.4* \
    && rm -rf /var/lib/apt/lists/*

# NOTE: 不改包自带配置——entrypoint 以 apache2 -f 显式加载配置卷内的自包含
# 配置（模块按需 LoadModule），Debian 的 apache2.conf/include 体系不参与运行

# 统一生效入口：平台契约要求所有服务镜像都提供 /reload.sh（docker exec <容器> /reload.sh）
COPY reload.sh /reload.sh
# 启动前置脚本：处理配置卷首次挂空 + htpasswd 凭据转换 + 监督循环（apache2 为其子进程）
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# 镜像内置默认配置：内容与 schema.json 默认值的渲染结果保持一致，作为空卷种子
COPY defaults/apache2.conf /usr/share/bmc-webdav/apache2.conf.default
COPY defaults/users.txt /usr/share/bmc-webdav/users.txt.default
RUN chmod +x /reload.sh /usr/local/bin/entrypoint.sh

# WebDAV 服务端口
EXPOSE 8080/tcp

# 判活依据：apache2 主进程必须存活；bookworm-slim 精简掉 procps 没有 pgrep，
# 用 grep 扫描 /proc/*/comm 等效实现（与 reload.sh 同款零依赖方案）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD grep -qs apache2 /proc/[0-9]*/comm || exit 1

# entrypoint 内含监督循环：apache2 以 -DFOREGROUND 作为其子进程运行，
# 日志由配置直通 stdout/stderr 被 docker logs 捕获；reload.sh 终止 apache2 后
# 由循环重建凭据并拉起，docker stop 的 SIGTERM 由循环转发优雅退出
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
