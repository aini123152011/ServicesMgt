# 证书服务（CA）镜像：debian:bookworm-slim + openssl CLI + python3 标准库。
# 刻意不引入任何 Python 框架（FastAPI/uvicorn 等）：夹具镜像保持最小依赖，签发动作全部
# 通过 openssl CLI 完成（与 nginx 夹具已在用 openssl 一致），避免为测试桩引入运行时依赖链。
# 架构无关，可直接 docker buildx build --platform linux/arm64,linux/amd64 多架构构建。
FROM debian:bookworm-slim

# 锁定上游版本：openssl 3.0（bookworm 稳定版）、python3 3.11。
# 用 3.0* / 3.11* 锁上游版本、放行点发布的安全修订——精确锁定到修订号会在旧修订
# 被移出存档后失败。升级方法：改为新的上游版本模式并确认 bookworm 存档已有该版本。
# APT_MIRROR：默认官方源；境内构建可用 --build-arg APT_MIRROR=mirrors.aliyun.com 加速
ARG APT_MIRROR=deb.debian.org
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        openssl=3.0* \
        python3=3.11* \
    && rm -rf /var/lib/apt/lists/*

# 统一生效入口：平台契约要求所有服务镜像都提供 /reload.sh（docker exec <容器> /reload.sh）
COPY reload.sh /reload.sh
# 启动前置脚本：空卷播种 + 两个卷准备 + 根生成/校验 + 监督循环
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# 标准库 HTTP API（仅 http.server + subprocess 调 openssl）
COPY api.py /usr/local/bin/api.py
# 镜像内置默认配置：内容与 schema.json 默认值的渲染结果一致，作为空卷种子
COPY defaults/pki.conf /usr/share/bmc-pki/pki.conf.default
RUN chmod +x /reload.sh /usr/local/bin/entrypoint.sh /usr/local/bin/api.py

EXPOSE 8090/tcp

# 判活依据：真的签一次一次性 CSR 自检（见 api.py /healthz）——不是"进程活着"。
# --healthcheck 模式读 pki.conf 取实际监听端口（listen_port 可被平台改），再探本机 /healthz。
HEALTHCHECK --interval=30s --timeout=15s --start-period=15s --retries=3 \
    CMD python3 /usr/local/bin/api.py --config /etc/pki-bmc/pki.conf --healthcheck

# 前台运行：entrypoint 是 1 号进程并在监督循环里拉起 api.py，日志全部进 stdout 由 docker logs 捕获
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
