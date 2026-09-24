# snmptrapd SNMP Trap 接收服务镜像：debian:bookworm-slim + 发行版官方 apt 包，架构无关，
# 可直接用 docker buildx build --platform linux/arm64,linux/amd64 多架构构建
FROM debian:bookworm-slim

# 锁定版本：net-snmp 5.9（编写时 bookworm 包版本为 5.9.3+dfsg-2+deb12u1）。
# 用 5.9* 锁上游版本、放行 bookworm 点版本的安全修订——精确锁定整段包版本
# 会在下次点发布后失败（旧修订会被移出存档）。
# 升级方法：把 5.9* 改为新上游版本模式（如 5.9.4*），确认 bookworm 存档已有该版本后重建镜像。
# APT_MIRROR：默认官方源；境内构建可用 --build-arg APT_MIRROR=mirrors.aliyun.com 加速
ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        snmptrapd=5.9* \
    && rm -rf /var/lib/apt/lists/*

# MIB 目录处理：Debian 官方仓库不附带 IETF MIB 文本（在 non-free 的 snmp-mibs-downloader 包里），
# MIBDIRS 指向包自带的 MIB 目录；MIBS=NONE 禁用 MIB 加载，trap 按数字 OID 记录，
# 避免缺 MIB 告警并加快启动；需要符号名解析时，把 MIB 文件挂载到 MIBDIRS 目录并把 MIBS 改为 ALL
ENV MIBDIRS=/usr/share/snmp/mibs \
    MIBS=NONE

# 清空包自带的示例配置：首次挂命名卷时 Docker 会把镜像内该路径内容拷进卷，
# 必须从空白起步，由 entrypoint 播种下方内置的默认配置
RUN rm -f /etc/snmp/snmptrapd.conf

# 统一生效入口：平台契约要求所有服务镜像都提供 /reload.sh（docker exec <容器> /reload.sh）
COPY reload.sh /reload.sh
# 启动前置脚本：处理配置卷首次挂空 + 监督循环（snmptrapd 为其子进程）
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# 镜像内置默认配置：内容与 schema.json 默认值的渲染结果保持一致，作为空卷种子
COPY defaults/snmptrapd.conf /usr/share/bmc-snmptrapd/snmptrapd.conf.default
RUN chmod +x /reload.sh /usr/local/bin/entrypoint.sh

# SNMP Trap 标准端口（UDP）
EXPOSE 162/udp

# 判活依据：snmptrapd 进程必须存活；bookworm-slim 精简掉 procps 没有 pgrep，
# 用 grep 扫描 /proc/*/comm 等效实现（与 reload.sh 同款零依赖方案）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD grep -qs snmptrapd /proc/[0-9]*/comm || exit 1

# entrypoint 内含监督循环：snmptrapd 以 -f（前台不 fork）作为其子进程运行，
# 日志走 stderr 由 docker logs 捕获；reload.sh 终止 snmptrapd 后由循环带新配置拉起，
# docker stop 的 SIGTERM 由循环转发给 snmptrapd 优雅退出
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
