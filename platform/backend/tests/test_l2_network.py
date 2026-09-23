"""l2_network 单元测试：macvlan 网络重建序列、幂等与地址推导。

用替身模拟 Docker（单测不连真实 daemon）。重点覆盖三条实测约束：
1. 挂着容器的网络删不掉 → 必须「断开 → 删 → 建 → 连」；
2. 网络参数不可改 → 换网口/换网段必然是重建；
3. 重连必须显式给地址 → 同子网沿用、换子网取新子网第 2 个，否则 Docker 拒绝重连。
"""

from __future__ import annotations

from typing import Any

import pytest
from docker.errors import APIError, NotFound

from app import l2_network

L2_NAME = "servicesmgt_dhcp-l2-net"
BRIDGE_NAME = "servicesmgt_dhcp-net"


def _macvlan_attrs(
    parent: str = "enp125s0f1",
    subnet: str = "192.168.90.0/24",
    gateway: str = "192.168.90.1",
    subnet_v6: str = "fd00:90::/64",
    gateway_v6: str = "fd00:90::1",
) -> dict[str, Any]:
    return {
        "Driver": "macvlan",
        "Options": {"parent": parent},
        "EnableIPv6": True,
        "IPAM": {
            "Config": [
                {"Subnet": subnet, "Gateway": gateway},
                {"Subnet": subnet_v6, "Gateway": gateway_v6},
            ]
        },
        "Labels": {
            "com.docker.compose.project": "servicesmgt",
            "com.docker.compose.network": "dhcp-l2-net",
        },
    }


class FakeNetwork:
    """替身网络：记录 connect/disconnect/remove 调用，可注入失败。"""

    def __init__(self, attrs: dict[str, Any], fail: str | None = None) -> None:
        self.attrs = attrs
        self.fail = fail
        self.removed = False
        self.connected: list[dict[str, Any]] = []
        self.disconnected: list[Any] = []

    def remove(self) -> None:
        if self.fail == "remove":
            raise APIError("network has active endpoints")
        self.removed = True

    def connect(self, container: Any, **kwargs: Any) -> None:
        if self.fail == "connect":
            raise APIError("connect failed")
        self.connected.append(kwargs)

    def disconnect(self, container: Any) -> None:
        if self.fail == "disconnect":
            raise APIError("disconnect failed")
        self.disconnected.append(container)


class FakeContainer:
    def __init__(self, attrs: dict[str, Any]) -> None:
        self.attrs = attrs


class FakeContainers:
    def __init__(self, container: FakeContainer) -> None:
        self._container = container

    def get(self, name: str) -> FakeContainer:
        return self._container


class FakeNetworks:
    def __init__(self, networks: dict[str, FakeNetwork]) -> None:
        self.networks = networks

    def get(self, name: str) -> FakeNetwork:
        if name not in self.networks:
            raise NotFound(f"network {name} not found")
        return self.networks[name]


class FakeApi:
    """替身 low-level API：create_network 要真的把网络注册进 FakeNetworks。

    否则后续 connect 会像真实 Docker 一样报 not found——替身失真会让测试假绿。
    """

    def __init__(self, networks: FakeNetworks) -> None:
        self._networks = networks
        self.created: list[dict[str, Any]] = []

    def create_network(self, name: str, **kwargs: Any) -> None:
        self.created.append({"name": name, **kwargs})
        self._networks.networks[name] = FakeNetwork(
            {
                "Driver": kwargs.get("driver"),
                "Options": kwargs.get("options") or {},
                "EnableIPv6": kwargs.get("enable_ipv6"),
                "IPAM": kwargs.get("ipam") or {},
                "Labels": kwargs.get("labels") or {},
            }
        )


class FakeClient:
    """替身客户端：containers/networks/api 三个入口，够 l2_network 用。"""

    def __init__(
        self,
        *,
        container_networks: dict[str, Any] | None = None,
        networks: dict[str, FakeNetwork] | None = None,
        project: str = "servicesmgt",
    ) -> None:
        nets = (
            container_networks
            if container_networks is not None
            else {
                L2_NAME: {
                    "IPAddress": "192.168.90.2",
                    "GlobalIPv6Address": "fd00:90::2",
                },
                BRIDGE_NAME: {"IPAddress": "172.30.12.2", "GlobalIPv6Address": ""},
            }
        )
        self.containers = FakeContainers(
            FakeContainer(
                {
                    "Config": {"Labels": {"com.docker.compose.project": project}},
                    "NetworkSettings": {"Networks": nets},
                }
            )
        )
        self.networks = FakeNetworks(networks or {})
        self.api = FakeApi(self.networks)


def _client(
    networks: dict[str, FakeNetwork] | None = None,
    container_networks: dict[str, Any] | None = None,
) -> FakeClient:
    """默认场景：容器已挂在 macvlan 网络上，网络对象也存在。"""
    return FakeClient(
        networks=networks
        if networks is not None
        else {L2_NAME: FakeNetwork(_macvlan_attrs())},
        container_networks=container_networks,
    )


def test_read_state_reads_parent_ipam_and_addresses() -> None:
    """从容器与网络 inspect 结果里读出父口、双栈 IPAM、容器地址与标签。"""
    state = l2_network.read_state(_client())

    assert state.network == L2_NAME
    assert state.attached is True
    assert state.exists is True
    assert state.parent == "enp125s0f1"
    assert (state.subnet, state.gateway) == ("192.168.90.0/24", "192.168.90.1")
    assert (state.subnet_v6, state.gateway_v6) == ("fd00:90::/64", "fd00:90::1")
    assert state.address == "192.168.90.2"
    assert state.address_v6 == "fd00:90::2"
    assert state.labels["com.docker.compose.network"] == "dhcp-l2-net"


def test_read_state_without_macvlan_derives_network_name() -> None:
    """没挂 macvlan 时 attached/exists 为假，但网络名仍按 compose 项目名推导出来。"""
    client = FakeClient(
        container_networks={BRIDGE_NAME: {"IPAddress": "172.30.12.2"}},
        networks={},
    )

    state = l2_network.read_state(client)

    assert state.attached is False
    assert state.exists is False
    assert state.network == L2_NAME
    assert state.parent is None


def test_apply_network_is_noop_when_already_matching() -> None:
    """参数与连接都已一致时是空操作：不碰网络、不重连（幂等）。"""
    network = FakeNetwork(_macvlan_attrs())
    client = _client(networks={L2_NAME: network})

    state, steps = l2_network.apply_network(
        client,
        parent="enp125s0f1",
        subnet="192.168.90.0/24",
        gateway="192.168.90.1",
        subnet_v6="fd00:90::/64",
        gateway_v6="fd00:90::1",
    )

    assert steps == ["无需变更：网络参数与容器连接都已与目标一致"]
    assert state.parent == "enp125s0f1"
    assert network.removed is False
    assert network.connected == []
    assert client.api.created == []


def test_apply_network_connects_only_when_params_match() -> None:
    """网络已存在但容器没挂上时只补一次重连，不重建网络。"""
    network = FakeNetwork(_macvlan_attrs())
    client = _client(
        networks={L2_NAME: network},
        container_networks={BRIDGE_NAME: {"IPAddress": "172.30.12.2"}},
    )

    _, steps = l2_network.apply_network(
        client,
        parent="enp125s0f1",
        subnet="192.168.90.0/24",
        gateway="192.168.90.1",
        subnet_v6="fd00:90::/64",
        gateway_v6="fd00:90::1",
    )

    assert steps == [f"网络已存在，仅把容器重连到 {L2_NAME}"]
    assert network.removed is False
    assert client.api.created == []


def test_apply_network_rebuilds_in_verified_order() -> None:
    """换网口时的完整序列：断开 → 删 → 建（新 parent + 双栈 IPAM）→ 连。"""
    network = FakeNetwork(_macvlan_attrs())
    client = _client(networks={L2_NAME: network})

    state, steps = l2_network.apply_network(
        client,
        parent="enp125s0f3",
        subnet="192.168.90.0/24",
        gateway="192.168.90.1",
        subnet_v6="fd00:90::/64",
        gateway_v6="fd00:90::1",
    )

    assert network.disconnected, "必须先断开容器，否则网络删不掉"
    assert network.removed is True
    assert len(client.api.created) == 1
    created = client.api.created[0]
    assert created["name"] == L2_NAME
    assert created["driver"] == "macvlan"
    assert created["options"] == {"parent": "enp125s0f3"}
    assert created["enable_ipv6"] is True
    assert created["ipam"]["Config"] == [
        {"Subnet": "192.168.90.0/24", "Gateway": "192.168.90.1"},
        {"Subnet": "fd00:90::/64", "Gateway": "fd00:90::1"},
    ]
    # 旧网络的 compose 标签要继承，否则 compose 认不出这张网络
    assert created["labels"]["com.docker.compose.project"] == "servicesmgt"
    # 4 步：断开 → 删 → 建 → 连
    assert len(steps) == 4
    assert state.network == L2_NAME
    assert state.parent == "enp125s0f3"


def test_apply_network_keeps_address_when_subnet_unchanged() -> None:
    """只换父口（网段不变）时沿用原地址，BMC 侧已配的 DNS 指向不会失效。"""
    network = FakeNetwork(_macvlan_attrs())
    client = _client(networks={L2_NAME: network})

    l2_network.apply_network(
        client,
        parent="enp125s0f3",
        subnet="192.168.90.0/24",
        gateway="192.168.90.1",
        subnet_v6="fd00:90::/64",
        gateway_v6="fd00:90::1",
    )

    # 网络被重建过，connect 落在新注册的对象上
    assert client.networks.networks[L2_NAME].connected == [
        {"ipv4_address": "192.168.90.2", "ipv6_address": "fd00:90::2"}
    ]


def test_apply_network_moves_address_when_subnet_changes() -> None:
    """换网段时旧地址不在新子网内，必须换成新子网的第 2 个地址（否则 Docker 拒绝重连）。"""
    network = FakeNetwork(_macvlan_attrs())
    client = _client(networks={L2_NAME: network})

    l2_network.apply_network(
        client,
        parent="enp125s0f1",
        subnet="192.168.95.0/24",
        gateway="192.168.95.1",
        subnet_v6="fd00:95::/64",
        gateway_v6="fd00:95::1",
    )

    assert client.networks.networks[L2_NAME].connected == [
        {"ipv4_address": "192.168.95.2", "ipv6_address": "fd00:95::2"}
    ]


def test_apply_network_uses_explicit_addresses_when_given() -> None:
    """调用方显式给地址时以入参为准（启用场景：容器本来没有地址）。"""
    client = FakeClient(
        container_networks={BRIDGE_NAME: {"IPAddress": "172.30.12.2"}},
        networks={},
    )

    l2_network.apply_network(
        client,
        parent="enp125s0f1",
        subnet="192.168.90.0/24",
        gateway="192.168.90.1",
        subnet_v6="fd00:90::/64",
        gateway_v6="fd00:90::1",
        address="192.168.90.2",
        address_v6="fd00:90::2",
    )

    assert len(client.api.created) == 1
    created = client.api.created[0]
    assert created["labels"] == {
        "com.docker.compose.project": "servicesmgt",
        "com.docker.compose.network": "dhcp-l2-net",
    }
    assert created["options"] == {"parent": "enp125s0f1"}


def test_apply_network_raises_on_docker_failure() -> None:
    """Docker 调用失败转成 L2NetworkError（路由层据此转 502 并触发回滚）。"""
    network = FakeNetwork(_macvlan_attrs(), fail="disconnect")
    client = _client(networks={L2_NAME: network})

    with pytest.raises(l2_network.L2NetworkError, match="Failed to disconnect"):
        l2_network.apply_network(
            client,
            parent="enp125s0f3",
            subnet="192.168.90.0/24",
            gateway="192.168.90.1",
            subnet_v6="fd00:90::/64",
            gateway_v6="fd00:90::1",
        )


def test_detach_l2_disconnects_and_keeps_network() -> None:
    """停用只断开容器，网络对象保留（删了它容器就重启不了，实机踩过）。"""
    network = FakeNetwork(_macvlan_attrs())
    client = _client(networks={L2_NAME: network})

    steps = l2_network.detach_l2(client)

    assert network.disconnected
    assert network.removed is False
    assert steps == [f"已断开容器与 {L2_NAME} 的连接（网络对象保留，容器仍可重启）"]


def test_detach_l2_is_noop_when_absent() -> None:
    """已停用时再停用是安全空操作。"""
    client = FakeClient(
        container_networks={BRIDGE_NAME: {"IPAddress": "172.30.12.2"}},
        networks={},
    )

    steps = l2_network.detach_l2(client)

    assert steps == ["无需变更：容器未挂 macvlan 网络"]
