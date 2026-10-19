"""pki 夹具的签发必须是无状态的：不许再出现持久序列号文件。

背景（实测两次复发）：openssl 的 `-CAcreateserial` 会维护一个 .srl 文件，
「先截断再写」。健康检查是**独立进程**（进程内的锁拦不住跨进程并发），
容器重启/监督循环换配置时的 SIGTERM 一旦落在写入瞬间，.srl 就变成 0 字节，
之后所有签发永久失败，表现为 `/healthz` 恒 503（2026-10-01、2026-10-19 各一次）。

所以：签发用 `-set_serial <随机值>`，源码里不得再出现 `-CAcreateserial` / `-CAserial`。
"""

from pathlib import Path

from app.core.config import settings

# 服务目录统一取 settings.SERVICES_DIR：本地默认锚定到仓库 services/，
# 容器内由环境变量指向挂载目录——按测试文件层级回溯在容器里会失效（实测 3 条全挂）
API_SOURCE = Path(settings.SERVICES_DIR).resolve() / "pki" / "api.py"


def test_signing_does_not_use_persistent_serial_file() -> None:
    source = API_SOURCE.read_text(encoding="utf-8")
    assert '"-CAcreateserial"' not in source, (
        "签发又用回了持久序列号文件：会被写坏导致 503"
    )
    assert '"-CAserial"' not in source, "签发又用回了持久序列号文件：会被写坏导致 503"


def test_signing_passes_random_serial() -> None:
    source = API_SOURCE.read_text(encoding="utf-8")
    # 两处签发：中间证书与叶证书
    assert source.count('"-set_serial"') == 2, "两处签发都应显式给随机序列号"
    assert "_random_serial()" in source


def test_healthcheck_and_sign_share_the_same_issuer() -> None:
    """健康检查真签一次自检——它也走同一签发路径，所以修一处即可覆盖两条路径。"""
    source = API_SOURCE.read_text(encoding="utf-8")
    assert "healthcheck" in source
    assert source.count('"-set_serial"') == source.count('"-CAkey"'), (
        "每处用 CA 私钥签发的地方都必须给随机序列号"
    )
