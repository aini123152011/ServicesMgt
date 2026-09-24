"""来源 IP 解析。

**前提**：平台当前直接以 `-p 18080` 暴露，前面没有反向代理，因此 `request.client.host`
就是真实客户端地址，IP 规则必须用它判定。

**为什么默认不信任 `X-Forwarded-For`**：没有代理时这个头完全由客户端控制，信任它等于
「白名单可以用一个请求头绕过」。将来若在平台前加了反向代理，需要同时做两件事：把
`TRUST_FORWARDED_FOR` 打开，并确认代理会重写（而不是追加转发）该头——只做前者不做后者
仍然是可绕过的。

**判不出地址时按拒绝处理**（见 `access_rules.match_ip`）：规则配了却放行「来源不明」的请求，
等于白名单形同虚设。生产环境走 TCP，uvicorn 一定会给出真实 peer，因此这一分支实际不会
影响正常访问；它只在异常部署（如 unix socket）下生效，并会在审计里留下记录。
"""

import ipaddress

TRUSTED_HEADER = "x-forwarded-for"


def resolve_client_ip(
    *,
    peer_host: str | None,
    forwarded_for: str | None = None,
    trust_forwarded_for: bool = False,
) -> str:
    """返回客户端 IP；判不出时返回空串。

    Args:
        peer_host: `request.client.host`；为 None 时（无 peer）退化为空串。
        forwarded_for: `X-Forwarded-For` 头的原值。
        trust_forwarded_for: 是否信任该头（仅在平台前置了可信代理时打开）。

    Returns:
        归一化后的 IP 字符串；非 IP 的 peer 名（如测试客户端的 "testclient"）原样返回，
        交由 `match_ip` 判为「无法解析」，而不是在这里静默当成合法地址。
    """
    if trust_forwarded_for and forwarded_for:
        # 取最左侧地址：它是整条代理链上最接近客户端的一个；右侧是各级代理自己追加的
        candidate = forwarded_for.split(",")[0].strip()
        if candidate:
            return _normalize(candidate)
    if peer_host:
        return _normalize(peer_host)
    return ""


def _normalize(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return value.strip()
