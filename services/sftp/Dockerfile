# SFTP 文件传输服务镜像：debian:bookworm-slim + 发行版官方 apt 包，架构无关，
# 可直接用 docker buildx build --platform linux/arm64,linux/amd64 多架构构建
FROM debian:bookworm-slim

# 锁定版本：OpenSSH 9.2p1（编写时 bookworm 包版本为 1:9.2p1-2+deb12u10）。
# 用 9.2p1* 锁上游版本、放行 bookworm 点版本的安全修订——精确锁定整段包版本
# 会在下次点发布后失败（旧修订会被移出存档）。
# 升级方法：把 1:9.2p1* 改为新上游版本模式（如 1:10.0p1*），确认 bookworm 存档已有该版本后重建镜像。
# APT_MIRROR：默认官方源；境内构建可用 --build-arg APT_MIRROR=mirrors.aliyun.com 加速
ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        openssh-server=1:9.2p1* \
    && rm -rf /var/lib/apt/lists/*

# 清空包自带配置与主机密钥：配置卷直接挂在 /etc/ssh，
# 包 postinst 生成的示例配置和密钥会随首次挂载被拷进卷，必须从空白起步；
# 主机密钥由 entrypoint 用 ssh-keygen -A 按需生成（写入卷后重建容器不丢失）
RUN rm -rf /etc/ssh/*

# 统一生效入口：平台契约要求所有服务镜像都提供 /reload.sh（docker exec <容器> /reload.sh）
COPY reload.sh /reload.sh
# 启动前置脚本：处理配置卷首次挂空 + 主机密钥/账号准备 + 监督循环（sshd 为其子进程）
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# 镜像内置默认配置：内容与 schema.json 默认值的渲染结果保持一致，作为空卷种子
COPY defaults/sshd_config /usr/share/fx-sftp/sshd_config.default
COPY defaults/users.txt /usr/share/fx-sftp/users.txt.default
RUN chmod +x /reload.sh /usr/local/bin/entrypoint.sh

# SFTP 标准端口
EXPOSE 22/tcp

# 判活依据：sshd 监听进程必须存活；bookworm-slim 精简掉 procps 没有 pgrep，
# 用 grep 扫描 /proc/*/comm 等效实现（与 reload.sh 同款零依赖方案）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD grep -qs sshd /proc/[0-9]*/comm || exit 1

# entrypoint 内含监督循环：sshd 以 -D（前台不 fork）作为其子进程运行，
# 日志走 stderr（-e）由 docker logs 捕获；reload.sh 终止 sshd 后由循环
# 重建账号并拉起，docker stop 的 SIGTERM 由循环转发给 sshd 优雅退出
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
