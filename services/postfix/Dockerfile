# postfix SMTP 邮件中继服务镜像：debian:bookworm-slim + 发行版官方 apt 包，架构无关，
# 可直接用 docker buildx build --platform linux/arm64,linux/amd64 多架构构建
FROM debian:bookworm-slim

# 锁定版本：postfix 3.7（编写时 bookworm 包版本为 3.7.11-0+deb12u1）。
# 用 3.7* 锁上游版本、放行 bookworm 点版本的安全修订——精确锁定整段包版本
# 会在下次点发布后失败（旧修订会被移出存档）。
# 升级方法：把 3.7* 改为新上游版本模式（如 3.8*），确认 bookworm 存档已有该版本后重建镜像。
# APT_MIRROR：默认官方源；境内构建可用 --build-arg APT_MIRROR=mirrors.aliyun.com 加速。
# DEBIAN_FRONTEND=noninteractive 跳过 debconf 的 Debian 主配置向导，main.cf 由平台渲染/播种覆盖
ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        postfix=3.7* \
    && rm -rf /var/lib/apt/lists/*

# 支撑配置快照：master.cf / dynamicmaps.cf 及 postfix 脚本工具集（post-install, postfix-files等）
# 是包自带且平台不渲染的必需配置。配置卷挂到 /etc/postfix 后，若平台先渲染 main.cf 再启动容器，
# Docker 不会把镜像内该路径内容拷进卷，故完整备份到种子目录由 entrypoint 兜底回填
RUN mkdir -p /usr/share/fx-postfix/seed_etc \
    && cp -a /etc/postfix/. /usr/share/fx-postfix/seed_etc/ \
    && cp /etc/postfix/master.cf /usr/share/fx-postfix/master.cf.default \
    && cp /etc/postfix/dynamicmaps.cf /usr/share/fx-postfix/dynamicmaps.cf.default \
    && rm -f /etc/postfix/main.cf /usr/share/fx-postfix/seed_etc/main.cf

# 统一生效入口：平台契约要求所有服务镜像都提供 /reload.sh（docker exec <容器> /reload.sh）
COPY reload.sh /reload.sh
# 启动前置脚本：处理配置卷首次挂空 + chroot 目录修复 + 监督循环
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# 镜像内置默认配置：内容与 schema.json 默认值的渲染结果保持一致，作为空卷种子
COPY defaults/main.cf /usr/share/fx-postfix/main.cf.default
RUN chmod +x /reload.sh /usr/local/bin/entrypoint.sh

# SMTP 标准端口
EXPOSE 25

# 判活依据：postfix status 检查 master 守护进程是否在运行，未运行时非 0 退出
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD postfix status || exit 1

# entrypoint 内含监督循环：postfix start-fg 让 master 前台运行作为其子进程，
# 日志走 stderr 由 docker logs 捕获；reload.sh 停止 postfix 后由循环带新配置拉起，
# docker stop 的 SIGTERM 由循环转发给 master 优雅退出
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
