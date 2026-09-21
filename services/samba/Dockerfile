# Samba / CIFS 文件共享服务镜像：debian:bookworm-slim + 官方 apt 包
FROM debian:bookworm-slim

ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        samba \
        procps \
    && rm -rf /var/lib/apt/lists/*

COPY reload.sh /reload.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY defaults/ /etc/samba-bmc.default/
RUN chmod +x /reload.sh /usr/local/bin/entrypoint.sh

EXPOSE 445/tcp 139/tcp

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD pgrep -f smbd || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
