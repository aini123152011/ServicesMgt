"""published_ports 的单测：从容器 inspect 结果解析「宿主机发布端口」。

背景（实机踩到）：「使用方式」卡片按管理网地址给命令，但命令里的端口是容器的端口，
而容器端口只在容器网络里可用——nginx 容器 80 发布在宿主 18102，卡片却写着
`http://<宿主>:80/...`，恰好命中宿主机上另一个 web 服务，看起来"能用"其实连错了东西。
端口发布由各服务自己的 compose 决定、平台不参与，只能在运行期从容器读回来，
所以这里的解析规则必须锁住。形状取自实机 docker inspect。
"""

from typing import Any

import pytest

from app import lifecycle


class _FakeContainer:
    def __init__(self, ports: dict[str, Any] | None) -> None:
        self.attrs = {"NetworkSettings": {"Ports": ports}}


def _patch_container(
    monkeypatch: pytest.MonkeyPatch, ports: dict[str, Any] | None
) -> None:
    monkeypatch.setattr(
        lifecycle, "_get_container", lambda _name, _action: _FakeContainer(ports)
    )


def test_prefers_binding_with_same_port_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """同号端口与高位端口都发布时取同号：BMC 的 SMTP/Syslog 配置只让填端口。"""
    # 实机 fx-postfix：25 与 18112 都发布到 25/tcp
    _patch_container(
        monkeypatch,
        {
            "25/tcp": [
                {"HostIp": "0.0.0.0", "HostPort": "25"},
                {"HostIp": "::", "HostPort": "25"},
                {"HostIp": "0.0.0.0", "HostPort": "18112"},
                {"HostIp": "::", "HostPort": "18112"},
            ]
        },
    )

    assert lifecycle.published_ports("fx-postfix") == {(25, "tcp"): 25}


def test_uses_high_port_when_container_port_is_not_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """容器端口没被同号发布时只能用高位端口（宿主的 80 被别的应用占着）。"""
    # 实机 fx-nginx：只有 80→18102、443→18103，宿主的 80 是另一个 web 服务
    _patch_container(
        monkeypatch,
        {
            "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18102"}],
            "443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18103"}],
        },
    )

    assert lifecycle.published_ports("fx-nginx") == {
        (80, "tcp"): 18102,
        (443, "tcp"): 18103,
    }


def test_keeps_protocols_apart_and_skips_unpublished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同号不同协议各自成键；未被发布的端口（值为 null）不进结果。"""
    _patch_container(
        monkeypatch,
        {
            "514/udp": [{"HostIp": "0.0.0.0", "HostPort": "514"}],
            "514/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18104"}],
            "111/tcp": None,
        },
    )

    assert lifecycle.published_ports("fx-rsyslog") == {
        (514, "udp"): 514,
        (514, "tcp"): 18104,
    }


def test_returns_empty_when_container_is_not_deployed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未部署的服务不该让详情页报错：读不到绑定就返回空 dict。"""

    def _boom(_name: str, action: str) -> Any:
        raise lifecycle.LifecycleError(f"Container 'fx-none' not found ({action})")

    monkeypatch.setattr(lifecycle, "_get_container", _boom)

    assert lifecycle.published_ports("fx-none") == {}


def test_returns_empty_for_l2_attached_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """二层网段上的服务（macvlan）没有端口发布，返回空 dict。"""
    _patch_container(monkeypatch, {})  # 实机 fx-dhcp 的 NetworkSettings.Ports

    assert lifecycle.published_ports("fx-dhcp") == {}
