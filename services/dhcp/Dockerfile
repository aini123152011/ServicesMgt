# DHCP/DNS 服务镜像：debian:bookworm-slim + 发行版官方 apt 包，架构无关，
# 可直接用 docker buildx build --platform linux/arm64,linux/amd64 多架构构建
FROM debian:bookworm-slim

# 锁定版本：dnsmasq 2.90（bookworm 包版本 2.90-4~deb12u2）。
# 用 2.90* 锁上游版本、放行 bookworm 点版本的安全修订。
# 同时装 isc-dhcp-client 与 dnsutils：本镜像既是夹具，也是验证取址/解析的客户端
#   （e2e 用它 dhclient -4/-6 取址、dig 查 A/AAAA/PTR）。
# APT_MIRROR：默认官方源；境内构建可用 --build-arg APT_MIRROR=mirrors.aliyun.com 加速
ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        dnsmasq=2.90* \
        isc-dhcp-client \
        dnsutils \
        iproute2 \
    && rm -rf /var/lib/apt/lists/*

# 统一生效入口：平台契约要求所有服务镜像都提供 /reload.sh（docker exec <容器> /reload.sh）
COPY reload.sh /reload.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY defaults/dnsmasq.conf /usr/share/bmc-dhcp/dnsmasq.conf.default
RUN chmod +x /reload.sh /usr/local/bin/entrypoint.sh

# DHCPv4 / DHCPv6 / DNS
EXPOSE 67/udp 547/udp 53/udp 53/tcp

# 判活依据：dnsmasq 进程在跑且 53 端口处于监听（无 ps/dig 依赖，直接看 /proc/net）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD grep -q ":0035 " /proc/net/udp /proc/net/udp6 || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
