# nginx HTTP/HTTPS 文件服务镜像：debian:bookworm-slim + 发行版官方 apt 包
# 支持 docker buildx build --platform linux/arm64,linux/amd64 多架构构建
FROM debian:bookworm-slim

# 锁定版本：nginx 1.22（Debian 12 bookworm 稳定版本为 1.22.1）
ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        nginx-extras=1.22* \
        openssl \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 统一生效入口与启动脚本
COPY reload.sh /reload.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY defaults/ /etc/nginx-bmc.default/
RUN chmod +x /reload.sh /usr/local/bin/entrypoint.sh

# 暴露端口
EXPOSE 80/tcp 443/tcp

# 判活检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD grep -qs nginx /proc/[0-9]*/comm || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
