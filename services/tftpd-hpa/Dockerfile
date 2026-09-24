# TFTP 文件服务器服务镜像：debian:bookworm-slim + 发行版官方 apt 包，架构无关，
# 可直接用 docker buildx build --platform linux/arm64,linux/amd64 多架构构建
FROM debian:bookworm-slim

# 锁定版本：tftp-hpa 5.2（编写时 bookworm 包版本为 5.2+20150808-1.4）。
# 用 5.2* 锁上游版本、放行 bookworm 点版本的安全修订——精确锁定整段包版本
# 会在下次点发布后失败（旧修订会被移出存档）。
# 升级方法：把 5.2* 改为新上游版本模式，确认 bookworm 存档已有该版本后重建镜像。
# APT_MIRROR：默认官方源；境内构建可用 --build-arg APT_MIRROR=mirrors.aliyun.com 加速
ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        tftpd-hpa=5.2* \
    && rm -rf /var/lib/apt/lists/*

# NOTE: 不清空任何包配置——entrypoint 以显式参数启动 in.tftpd，配置卷挂在
# 自定义目录 /etc/tftpd-hpa，包自带的 /etc/default/tftpd-hpa 与 init 脚本不参与运行

# 统一生效入口：平台契约要求所有服务镜像都提供 /reload.sh（docker exec <容器> /reload.sh）
COPY reload.sh /reload.sh
# 启动前置脚本：处理配置卷首次挂空 + 服务目录准备 + 监督循环（in.tftpd 为其子进程）
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# 镜像内置默认配置：内容与 schema.json 默认值的渲染结果保持一致，作为空卷种子
COPY defaults/tftpd-hpa.conf /usr/share/bmc-tftpd-hpa/tftpd-hpa.conf.default
RUN chmod +x /reload.sh /usr/local/bin/entrypoint.sh

# TFTP 标准端口
EXPOSE 69/udp

# 判活依据：in.tftpd 进程必须存活；bookworm-slim 精简掉 procps 没有 pgrep，
# 用 grep 扫描 /proc/*/comm 等效实现（与 reload.sh 同款零依赖方案）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD grep -qs in.tftpd /proc/[0-9]*/comm || exit 1

# entrypoint 内含监督循环：in.tftpd 以 --foreground（前台，隐含 standalone 监听）
# 作为其子进程运行，日志走 stderr 由 docker logs 捕获；reload.sh 终止 in.tftpd 后
# 由循环带新配置拉起，docker stop 的 SIGTERM 由循环转发优雅退出
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
