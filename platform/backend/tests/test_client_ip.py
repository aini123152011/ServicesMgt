"""来源 IP 解析测试。

核心断言是**默认不信任 `X-Forwarded-For`**：平台前面没有反向代理时，这个头完全由客户端
控制，信任它等于「白名单可以用一个请求头绕过」。
"""

from app.client_ip import resolve_client_ip


def test_uses_peer_by_default() -> None:
    assert (
        resolve_client_ip(peer_host="10.0.0.5", forwarded_for="1.2.3.4") == "10.0.0.5"
    )


def test_trusts_header_only_when_explicitly_enabled() -> None:
    assert (
        resolve_client_ip(
            peer_host="10.0.0.5", forwarded_for="1.2.3.4", trust_forwarded_for=True
        )
        == "1.2.3.4"
    )


def test_takes_leftmost_address_of_chain() -> None:
    # 最左侧是最接近客户端的一跳，右侧是各级代理自己追加的
    assert (
        resolve_client_ip(
            peer_host="10.0.0.5",
            forwarded_for="1.2.3.4, 10.0.0.1, 10.0.0.2",
            trust_forwarded_for=True,
        )
        == "1.2.3.4"
    )


def test_falls_back_to_peer_when_header_is_blank() -> None:
    assert (
        resolve_client_ip(
            peer_host="10.0.0.5", forwarded_for="   ", trust_forwarded_for=True
        )
        == "10.0.0.5"
    )


def test_normalizes_ipv6() -> None:
    assert resolve_client_ip(peer_host="fd00:0090::0001") == "fd00:90::1"


def test_empty_when_no_peer() -> None:
    assert resolve_client_ip(peer_host=None) == ""


def test_non_ip_peer_is_returned_as_is() -> None:
    # 测试客户端的 peer 是 "testclient"：原样返回，交由 match_ip 判为「无法解析」
    assert resolve_client_ip(peer_host="testclient") == "testclient"
