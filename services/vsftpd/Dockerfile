# FTP 文件传输服务镜像：debian:bookworm-slim + 发行版官方 apt 包，架构无关，
# 可直接用 docker buildx build --platform linux/arm64,linux/amd64 多架构构建
FROM debian:bookworm-slim

# 锁定版本：vsftpd 3.0.3（编写时 bookworm 包版本为 3.0.3-13）、PAM 1.5.2
# （编写时 bookworm 包版本为 1.5.2-6+deb12u2，显式安装确保容器内密码认证可用）。
# 用上游版本通配锁定、放行 bookworm 点版本的安全修订——精确锁定整段包版本
# 会在下次点发布后失败（旧修订会被移出存档）。
# 升级方法：把 3.0.3*/1.5.2* 改为新上游版本模式，确认 bookworm 存档已有该版本后重建镜像。
# APT_MIRROR：默认官方源；境内构建可用 --build-arg APT_MIRROR=mirrors.aliyun.com 加速
ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        vsftpd=3.0.3* \
        libpam-modules=1.5.2* \
    && rm -rf /var/lib/apt/lists/*

# 覆写包自带 PAM 配置：默认配置含 pam_shells（要求登录 shell 在 /etc/shells 内，
# 而 FTP 用户 shell 为 nologin，会被拒登）并 include common-session
# （其 pam_systemd 依赖 systemd），容器内均不可用；
# 只保留 pam_unix 直接校验 /etc/shadow，确保容器内无 systemd 时密码登录可用
RUN printf '%s\n' \
        'auth    required    pam_unix.so' \
        'account required    pam_unix.so' \
        'session required    pam_unix.so' \
        > /etc/pam.d/vsftpd

# 清空包自带配置目录：配置卷直接挂在 /etc/vsftpd（entrypoint 以显式路径
# 加载其中的 vsftpd.conf），从空白起步，避免 Debian 示例配置混入渲染产物
RUN rm -rf /etc/vsftpd && mkdir -p /etc/vsftpd

# 统一生效入口：平台契约要求所有服务镜像都提供 /reload.sh（docker exec <容器> /reload.sh）
COPY reload.sh /reload.sh
# 启动前置脚本：处理配置卷首次挂空 + 本地账号准备 + 监督循环（vsftpd 为其子进程）
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# 镜像内置默认配置：内容与 schema.json 默认值的渲染结果保持一致，作为空卷种子
COPY defaults/vsftpd.conf /usr/share/fx-vsftpd/vsftpd.conf.default
COPY defaults/local_users.txt /usr/share/fx-vsftpd/local_users.txt.default
RUN chmod +x /reload.sh /usr/local/bin/entrypoint.sh

# FTP 控制端口与被动模式数据端口（schema 默认范围的两端）
EXPOSE 21/tcp 40000/tcp 40100/tcp

# 判活依据：vsftpd 监听进程必须存活；bookworm-slim 精简掉 procps 没有 pgrep，
# 用 grep 扫描 /proc/*/comm 等效实现（与 reload.sh 同款零依赖方案）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD grep -qs vsftpd /proc/[0-9]*/comm || exit 1

# entrypoint 内含监督循环：vsftpd standalone 模式（background 默认 NO）作为其
# 子进程前台运行；reload.sh 终止 vsftpd 后由循环重建账号并拉起，
# docker stop 的 SIGTERM 由循环转发给 vsftpd 优雅退出
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
