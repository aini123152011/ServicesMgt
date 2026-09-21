# NFS-Ganesha 网络文件系统服务镜像：debian:bookworm-slim + 官方 apt 包
FROM debian:bookworm-slim

ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        nfs-ganesha \
        nfs-ganesha-vfs \
        rpcbind \
        procps \
    && rm -rf /var/lib/apt/lists/*

COPY reload.sh /reload.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY defaults/ /etc/ganesha-bmc.default/
RUN chmod +x /reload.sh /usr/local/bin/entrypoint.sh

EXPOSE 2049/tcp 111/tcp 111/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD pgrep -f ganesha.nfsd || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
