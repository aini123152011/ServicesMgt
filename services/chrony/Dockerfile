# chrony NTP 服务镜像：debian:bookworm-slim + 发行版官方 apt 包，架构无关，
# 可直接用 docker buildx build --platform linux/arm64,linux/amd64 多架构构建
FROM debian:bookworm-slim

# 锁定版本：chrony 4.3（编写时 bookworm 包版本为 4.3-2+deb12u3）。
# 用 4.3* 锁上游版本、放行 bookworm 点版本的安全修订——精确锁定整段包版本
# 会在下次点发布后失败（旧修订会被移出存档）。
# 升级方法：把 4.3* 改为新上游版本模式（如 4.5*），确认 bookworm 存档已有该版本后重建镜像。
# APT_MIRROR：默认官方源；境内构建可用 --build-arg APT_MIRROR=mirrors.aliyun.com 加速
ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        chrony=4.3* \
        faketime \
    && rm -rf /var/lib/apt/lists/*

# 清空包自带的示例配置：首次挂命名卷时 Docker 会把镜像内该路径内容拷进卷，
# 必须从空白起步，由 entrypoint 播种下方内置的默认配置，避免 Debian 示例配置混入
RUN rm -rf /etc/chrony/*

# 统一生效入口：平台契约要求所有服务镜像都提供 /reload.sh（docker exec <容器> /reload.sh）
COPY reload.sh /reload.sh
# 启动前置脚本：处理配置卷首次挂空
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# 镜像内置默认配置：内容与 schema.json 默认值的渲染结果保持一致，作为空卷种子
COPY defaults/chrony.conf /usr/share/bmc-chrony/chrony.conf.default
RUN chmod +x /reload.sh /usr/local/bin/entrypoint.sh

# NTP 标准端口
EXPOSE 123/udp

# 判活依据：chronyc 通过控制套接字向 chronyd 查询同步状态，
# 进程未运行或无响应时非 0 退出
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD chronyc tracking || exit 1

# 前台运行（-n 不后台化），日志走 stderr 由 docker logs 捕获；
# entrypoint 是 1 号进程并在监督循环里拉起 chronyd：故障注入的时间偏移只能随进程启动
# 注入（LD_PRELOAD），偏移变更时 /reload.sh 结束 chronyd，循环随即带新偏移重启它，
# 容器本身不重启（避免 Docker 重启退避，以及重启期间 docker exec 被 409 拒绝）。
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
