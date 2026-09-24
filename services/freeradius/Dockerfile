# RADIUS 服务镜像：debian:bookworm-slim + 发行版官方 apt 包，架构无关，
# 可直接用 docker buildx build --platform linux/arm64,linux/amd64 多架构构建
FROM debian:bookworm-slim

# 锁定版本：freeradius 3.2.1（bookworm 包版本 3.2.1+dfsg-4+deb12u1）。
# 用 3.2* 锁上游版本、放行 bookworm 点版本的安全修订。
# 同时装 iproute2：排障用（ss/ip），运行时不需要。
# APT_MIRROR：默认官方源；境内构建可用 --build-arg APT_MIRROR=mirrors.aliyun.com 加速
ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        freeradius=3.2* \
        iproute2 \
    && rm -rf /var/lib/apt/lists/*

# 把平台渲染的两个文件接进发行版自带的配置树（配置卷只挂 bmc/ 子目录，不覆盖整棵树）：
#   1) clients.conf 末尾追加 $INCLUDE bmc/clients.conf（$INCLUDE 相对 ${confdir}）
#   2) 把 files 模块读的 mods-config/files/authorize 换成指向渲染产物的符号链接
#      （比改 mods-available/files 里的 filename 稳：那个文件里写的是 ${moddir}/authorize，
#       要 sed 掉 ${...} 得先跟 Dockerfile 的 $ 转义打架，实测直接把目标文件做成软链更省事）
# 注意：Dockerfile 里 $ 要写成 $$，否则 Docker 会当成构建变量把 ${confdir} 展开成空串
RUN printf '\n$INCLUDE bmc/clients.conf\n' >> /etc/freeradius/3.0/clients.conf \
    && rm -f /etc/freeradius/3.0/mods-config/files/authorize \
    && ln -sf /etc/freeradius/3.0/bmc/authorize /etc/freeradius/3.0/mods-config/files/authorize \
    && test -L /etc/freeradius/3.0/mods-config/files/authorize

# 双栈监听：**不需要额外配置**。自带 listen 块的 ipaddr = * 在 3.2 里已经是双栈
# （实测：再追加一个 ipv6addr = :: 的 listen 块会报 "Address already in use"，说明 :: 已被占用），
# 容器里 /proc/net/udp6 也能看到 :0714（1812）与 :0715（1813）。这条与 rsyslog/sftp 等
# 「默认双栈」的服务同类，记在 container-runtime-guidelines 第 12 条。

# 统一生效入口：平台契约要求所有服务镜像都提供 /reload.sh（docker exec <容器> /reload.sh）
COPY reload.sh /reload.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# 镜像内置默认配置：内容与 schema.json 默认值的渲染结果一致，作为空卷种子
COPY defaults/clients.conf /usr/share/bmc-freeradius/clients.conf.default
COPY defaults/authorize /usr/share/bmc-freeradius/authorize.default
RUN chmod +x /reload.sh /usr/local/bin/entrypoint.sh

# RADIUS 认证 / 计费
EXPOSE 1812/udp 1813/udp

# 判活依据：radiusd 进程在跑且认证端口在监听（1812 = 0x714；UDP 套接字状态是 07 不是 0A）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD grep -q ":0714 " /proc/net/udp /proc/net/udp6 || exit 1

# entrypoint 内含监督循环：radiusd 以 -f（前台）作为其子进程运行，
# reload.sh 终止 radiusd 后由循环带新配置拉起，docker stop 的 SIGTERM 由循环转发
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
