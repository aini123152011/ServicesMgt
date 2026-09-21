# rsyslog 日志归集服务镜像：debian:bookworm-slim + 发行版官方 apt 包，架构无关，
# 可直接用 docker buildx build --platform linux/arm64,linux/amd64 多架构构建
FROM debian:bookworm-slim

# 锁定版本：rsyslog 8.2302（编写时 bookworm 包版本为 8.2302.0-1+deb12u1）。
# 用 8.2302* 锁上游版本、放行 bookworm 点版本的安全修订——精确锁定整段包版本
# 会在下次点发布后失败（旧修订会被移出存档）。
# 升级方法：把 8.2302* 改为新上游版本模式（如 8.2412*），确认 bookworm 存档已有该版本后重建镜像。
# APT_MIRROR：默认官方源；境内构建可用 --build-arg APT_MIRROR=mirrors.aliyun.com 加速
ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        rsyslog=8.2302* \
    && rm -rf /var/lib/apt/lists/*

# 清空包自带配置：本容器专职接收 BMC 日志，entrypoint 用 -f 显式指定配置卷内的
# rsyslog.conf 启动，Debian 示例配置（imuxsock/imklog 模块与本地规则）全部弃用，
# 避免 BMC 日志被重复写进容器内的 /var/log/syslog
RUN rm -f /etc/rsyslog.conf && rm -rf /etc/rsyslog.d/*

# 统一生效入口：平台契约要求所有服务镜像都提供 /reload.sh（docker exec <容器> /reload.sh）
COPY reload.sh /reload.sh
# 启动前置脚本：处理配置卷首次挂空 + 监督循环（rsyslogd 为其子进程）
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# 镜像内置默认配置：内容与 schema.json 默认值的渲染结果保持一致，作为空卷种子
COPY defaults/rsyslog.conf /usr/share/bmc-rsyslog/rsyslog.conf.default
RUN chmod +x /reload.sh /usr/local/bin/entrypoint.sh

# Syslog 标准端口：UDP 为主，TCP 可选
EXPOSE 514/udp 514/tcp

# 判活依据：rsyslogd 进程必须存活；bookworm-slim 精简掉 procps 没有 pgrep，
# 用 grep 扫描 /proc/*/comm 等效实现（与 reload.sh 同款零依赖方案）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD grep -qs rsyslogd /proc/[0-9]*/comm || exit 1

# entrypoint 内含监督循环：rsyslogd 以 -n（前台不 fork）作为其子进程运行，
# 日志走 stderr 由 docker logs 捕获；reload.sh 终止 rsyslogd 后由循环带新配置拉起，
# docker stop 的 SIGTERM 由循环转发给 rsyslogd 优雅退出
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
