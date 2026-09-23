"""宿主网口采集与二层绑定校验的单测。

覆盖三类容易出错的逻辑（都对应实测踩过的坑）：
1. 从 `/sys` 读网口事实：carrier/speed 的缺省与「-1 归一成 None」；
2. 绑定推导与二层地址：macvlan 绑定取容器 IP，其余 L2 服务取宿主测试口地址，未启用时为 None；
3. 五条校验规则的触发条件与级别（只告警不阻断）。

不测 helper 容器本身（它依赖真实 Docker 与 `--network host`）：那部分用替身返回固定输出，
验证解析与降级分支。
"""

from __future__ import annotations

from typing import Any

import pytest
from docker.errors import DockerException

from app import host_network
from app.core.config import settings


@pytest.fixture(autouse=True)
def _reset_cache() -> Any:
    """每个用例都从干净缓存开始：host_facts 有 30s 进程内缓存，跨用例会串味。"""
    host_network._cache = None
    yield
    host_network._cache = None


def _iface(
    name: str = "enp125s0f1",
    carrier: int | None = 1,
    cidr: str = "192.168.90.0/24",
) -> dict[str, Any]:
    ipv4 = []
    if cidr:
        address = cidr.split("/")[0].rsplit(".", 1)[0] + ".1"
        ipv4 = [{"address": address, "netmask": "255.255.255.0", "cidr": cidr}]
    return {
        "name": name,
        "carrier": carrier,
        "speed_mbps": 1000 if carrier else None,
        "mac": "8c:2a:8e:fd:da:a2",
        "ipv4": ipv4,
        "ipv6": [],
    }


# --------------------------------------------------------------------------- #
# 1. /sys 读取
# --------------------------------------------------------------------------- #
def test_read_sys_interfaces_normalizes_and_filters(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """只收物理口；carrier/speed 缺失或为 -1 时归一成 None（不把 -1 当有效速率）。"""
    net = tmp_path / "class" / "net"
    for name, carrier, speed in [
        ("enp125s0f0", "1", "1000"),
        ("enp125s0f1", "0", "-1"),
        ("docker0", "1", "10000"),
        ("veth1234", "1", "10000"),
        ("br-abc", "1", "10000"),
        ("virbr0", "1", "1000"),
        ("lo", "1", "1000"),
    ]:
        iface_dir = net / name
        iface_dir.mkdir(parents=True)
        (iface_dir / "carrier").write_text(carrier, encoding="ascii")
        (iface_dir / "speed").write_text(speed, encoding="ascii")
        (iface_dir / "address").write_text("8c:2a:8e:fd:da:a1", encoding="ascii")
    monkeypatch.setattr(settings, "HOST_SYS_DIR", str(tmp_path))

    interfaces = host_network._read_sys_interfaces()

    assert sorted(interfaces) == ["enp125s0f0", "enp125s0f1"]
    assert interfaces["enp125s0f0"]["carrier"] == 1
    assert interfaces["enp125s0f0"]["speed_mbps"] == 1000
    # 链路断开时 /sys 的 speed 是 -1：不是有效速率
    # carrier=0 必须保留（「无链路」是要紧的事实，不能归一成 None）；speed 的 -1 才是无效值
    assert interfaces["enp125s0f1"]["carrier"] == 0
    assert interfaces["enp125s0f1"]["speed_mbps"] is None


def test_read_sys_interfaces_returns_empty_without_mount(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """只读挂载缺失时返回空字典（调用方降级展示），不抛异常。"""
    monkeypatch.setattr(settings, "HOST_SYS_DIR", str(tmp_path / "nope"))
    assert host_network._read_sys_interfaces() == {}


def test_helper_output_parsing_and_degradation(monkeypatch: Any) -> None:
    """helper 容器输出正常时解析出地址与默认路由；异常时降级为 unavailable。"""

    class _Containers:
        def __init__(self, payload: Any) -> None:
            self.payload = payload

        def run(self, *_args: Any, **_kwargs: Any) -> bytes:
            if isinstance(self.payload, Exception):
                raise self.payload
            return bytes(self.payload)

    class _Client:
        def __init__(self, payload: Any) -> None:
            self.containers = _Containers(payload)

    monkeypatch.setattr(
        host_network,
        "_client",
        lambda: _Client(b'{"interfaces": {}, "default_iface": "enp125s0f0"}'),
    )
    addresses, default_iface, status, _ipv6 = host_network._read_host_addresses()
    assert (addresses, default_iface, status) == ({}, "enp125s0f0", "ok")

    monkeypatch.setattr(
        host_network, "_client", lambda: _Client(DockerException("daemon down"))
    )
    addresses, default_iface, status, _ipv6 = host_network._read_host_addresses()
    assert status == "unavailable"
    assert addresses == {} and default_iface == ""

    monkeypatch.setattr(host_network, "_client", lambda: _Client(b"not json"))
    assert host_network._read_host_addresses()[2] == "unavailable"


def test_host_facts_merges_ipv4_cidr(tmp_path: Any, monkeypatch: Any) -> None:
    """IP 与掩码合并成 cidr 供校验用；helper 不可用时 ip_source 反映出来。"""
    net = tmp_path / "class" / "net" / "enp125s0f1"
    net.mkdir(parents=True)
    (net / "carrier").write_text("1", encoding="ascii")
    (net / "address").write_text("8c:2a:8e:fd:da:a2", encoding="ascii")
    monkeypatch.setattr(settings, "HOST_SYS_DIR", str(tmp_path))
    monkeypatch.setattr(
        host_network,
        "_read_host_addresses",
        lambda: (
            {"enp125s0f1": {"ipv4": "192.168.90.1", "netmask": "255.255.255.0"}},
            "enp125s0f0",
            "ok",
            {},
        ),
    )

    facts = host_network.host_facts()
    assert facts["ip_source"] == "ok"
    assert facts["default_iface"] == "enp125s0f0"
    assert facts["interfaces"][0]["ipv4"][0]["cidr"] == "192.168.90.0/24"


# --------------------------------------------------------------------------- #
# 2. 绑定推导与二层地址
# --------------------------------------------------------------------------- #
class _FakeNetwork:
    def __init__(self, driver: str, parent: str | None) -> None:
        self.attrs = {"Driver": driver, "Options": {"parent": parent} if parent else {}}


class _FakeContainerObj:
    def __init__(self, networks: dict[str, Any]) -> None:
        self.attrs = {"NetworkSettings": {"Networks": networks}}


class _FakeDocker:
    """同时支撑 containers.get / networks.get 的最小替身。"""

    def __init__(self, containers: dict[str, Any], networks: dict[str, Any]) -> None:
        self._containers = containers
        self._networks = networks

        outer = self

        class _Containers:
            def get(self, name: str) -> Any:
                if name in outer._containers:
                    return outer._containers[name]
                raise DockerException(f"no such container {name}")

        class _Networks:
            def get(self, name: str) -> Any:
                if name in outer._networks:
                    return outer._networks[name]
                raise DockerException(f"no such network {name}")

        self.containers = _Containers()
        self.networks = _Networks()


def _fake_client(
    monkeypatch: Any, containers: dict[str, Any], networks: dict[str, Any]
) -> None:
    monkeypatch.setattr(
        host_network, "_client", lambda: _FakeDocker(containers, networks)
    )


def test_service_bindings_reads_macvlan_parent(monkeypatch: Any) -> None:
    """从容器 → 网络 → Options.parent 推导绑定；非 macvlan 网络不算绑定。"""
    _fake_client(
        monkeypatch,
        {
            "bmc-dhcp": _FakeContainerObj(
                {
                    "servicesmgt_dhcp-net": {"IPAddress": "172.30.12.2"},
                    "servicesmgt_dhcp-l2-net": {"IPAddress": "192.168.90.2"},
                }
            ),
            "bmc-nginx": _FakeContainerObj(
                {"servicesmgt_nginx-net": {"IPAddress": "172.30.2.2"}}
            ),
        },
        {
            "servicesmgt_dhcp-net": _FakeNetwork("bridge", None),
            "servicesmgt_dhcp-l2-net": _FakeNetwork("macvlan", "enp125s0f1"),
            "servicesmgt_nginx-net": _FakeNetwork("bridge", None),
        },
    )

    bindings = {
        item["service"]: item
        for item in host_network.service_bindings(["dhcp", "nginx"])
    }

    assert bindings["dhcp"]["attached"] is True
    assert bindings["dhcp"]["parent"] == "enp125s0f1"
    assert bindings["nginx"]["attached"] is False
    assert bindings["nginx"]["parent"] is None


def test_l2_address_two_sources(monkeypatch: Any) -> None:
    """macvlan 绑定取容器在该网络上的 IP；其余 L2 服务取宿主测试口在测试网段上的地址。"""
    monkeypatch.setattr(settings, "DHCP_PARENT_IFACE", "enp125s0f1")
    monkeypatch.setattr(settings, "L2_SUBNET", "192.168.90.0/24")
    monkeypatch.setattr(settings, "L2_SERVICES", "dhcp,tftpd-hpa,rsyslog,chrony")
    _fake_client(
        monkeypatch,
        {
            "bmc-dhcp": _FakeContainerObj(
                {"servicesmgt_dhcp-l2-net": {"IPAddress": "192.168.90.2"}}
            )
        },
        {"servicesmgt_dhcp-l2-net": _FakeNetwork("macvlan", "enp125s0f1")},
    )
    interfaces = [_iface("enp125s0f1", cidr="192.168.90.0/24")]
    bindings = host_network.service_bindings(["dhcp"])

    assert (
        host_network.l2_address_for(
            service_name="dhcp", bindings=bindings, interfaces=interfaces
        )
        == "192.168.90.2"
    )
    # 非 macvlan 的 L2 服务走宿主测试口地址
    assert (
        host_network.l2_address_for(
            service_name="rsyslog", bindings=bindings, interfaces=interfaces
        )
        == "192.168.90.1"
    )
    # 不在 L2 集合里的服务不给二层地址
    assert (
        host_network.l2_address_for(
            service_name="nginx", bindings=bindings, interfaces=interfaces
        )
        is None
    )


def test_l2_address_none_when_disabled(monkeypatch: Any) -> None:
    """未启用二层（DHCP_PARENT_IFACE 为空）时不返回二层地址，卡片回落显示访问地址。"""
    monkeypatch.setattr(settings, "DHCP_PARENT_IFACE", "")
    assert (
        host_network.l2_address_for(
            service_name="dhcp", bindings=[], interfaces=[_iface()]
        )
        is None
    )


# --------------------------------------------------------------------------- #
# 3. 校验规则
# --------------------------------------------------------------------------- #
def test_checks_not_configured(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "DHCP_PARENT_IFACE", "")
    checks = host_network.build_checks(interfaces=[], bindings=[], dhcp_values=None)
    assert [c["code"] for c in checks] == ["l2_not_configured"]
    assert checks[0]["level"] == "info"


def test_checks_parent_missing_lists_candidates(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "DHCP_PARENT_IFACE", "enp125s0f9")
    checks = host_network.build_checks(
        interfaces=[_iface("enp125s0f1")], bindings=[], dhcp_values=None
    )
    assert checks[0]["code"] == "parent_missing"
    assert checks[0]["level"] == "error"
    assert "enp125s0f1" in checks[0]["message"]


def test_checks_no_carrier_and_default_route(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "DHCP_PARENT_IFACE", "enp125s0f1")
    checks = host_network.build_checks(
        interfaces=[_iface("enp125s0f1", carrier=0)],
        bindings=[],
        dhcp_values=None,
        default_iface="enp125s0f1",
    )
    codes = {c["code"] for c in checks}
    assert "parent_no_carrier" in codes
    assert "parent_has_default_route" in codes
    assert all(c["level"] == "warn" for c in checks if c["code"] != "l2_ok")


def test_checks_pool_outside_subnet(monkeypatch: Any) -> None:
    """地址池不在绑定口网段内必须报出来——这是实测踩过的「dnsmasq 拒绝服务」成因。"""
    monkeypatch.setattr(settings, "DHCP_PARENT_IFACE", "enp125s0f1")
    monkeypatch.setattr(settings, "L2_SUBNET", "")
    checks = host_network.build_checks(
        interfaces=[_iface("enp125s0f1", cidr="192.168.90.0/24")],
        bindings=[],
        dhcp_values={"pool_start": "10.9.9.10", "pool_end": "10.9.9.20"},
    )
    outside = [c for c in checks if c["code"] == "pool_outside_parent_subnet"]
    assert len(outside) == 2
    assert all(c["level"] == "warn" for c in outside)

    # 池在网段内时不再报这条；此时若 dhcp 没挂 macvlan，只应剩「未挂」这一条（不再有池告警）
    checks_ok = host_network.build_checks(
        interfaces=[_iface("enp125s0f1", cidr="192.168.90.0/24")],
        bindings=[],
        dhcp_values={"pool_start": "192.168.90.100", "pool_end": "192.168.90.200"},
    )
    assert all(c["code"] != "pool_outside_parent_subnet" for c in checks_ok)
    assert [c["code"] for c in checks_ok] == ["dhcp_not_attached"]

    # 池在网段内 + dhcp 已挂 macvlan → 才是真正的「一切正常」
    attached = [
        {
            "service": "dhcp",
            "container": "bmc-dhcp",
            "network": "servicesmgt_dhcp-l2-net",
            "parent": "enp125s0f1",
            "attached": True,
            "address": "192.168.90.2",
            "address_v6": "fd00:90::2",
        }
    ]
    checks_all_good = host_network.build_checks(
        interfaces=[_iface("enp125s0f1", cidr="192.168.90.0/24")],
        bindings=attached,
        dhcp_values={"pool_start": "192.168.90.100", "pool_end": "192.168.90.200"},
    )
    assert [c["code"] for c in checks_all_good] == ["l2_ok"]


def test_checks_subnet_mismatch_and_host_served_without_address(
    monkeypatch: Any,
) -> None:
    """L2_SUBNET 与绑定口网段不一致、以及走宿主地址的服务缺测试口地址，都要报。"""
    monkeypatch.setattr(settings, "DHCP_PARENT_IFACE", "enp125s0f1")
    monkeypatch.setattr(settings, "L2_SUBNET", "192.168.91.0/24")
    monkeypatch.setattr(settings, "L2_SERVICES", "dhcp,tftpd-hpa")
    checks = host_network.build_checks(
        interfaces=[_iface("enp125s0f1", cidr="192.168.90.0/24")],
        bindings=[
            {
                "service": "dhcp",
                "container": "bmc-dhcp",
                "network": "n",
                "parent": "enp125s0f1",
                "attached": True,
            }
        ],
        dhcp_values=None,
    )
    codes = {c["code"] for c in checks}
    assert "parent_subnet_mismatch" in codes
    # 测试口本身有地址（只是与 L2_SUBNET 不符），所以不该报「测试口没有地址」那条
    assert "test_port_without_address" not in codes


def test_checks_address_conflict(monkeypatch: Any) -> None:
    """容器在 macvlan 上的地址与绑定口自身地址相同时必须报 error。

    实测踩到：macvlan 网络没配 gateway 时 Docker 把 .1 分给容器，正好撞上宿主测试口地址，
    同段两个 MAC 抢同一地址，BMC 侧 ARP 会来回跳。
    """
    monkeypatch.setattr(settings, "DHCP_PARENT_IFACE", "enp125s0f1")
    monkeypatch.setattr(settings, "L2_SUBNET", "")
    checks = host_network.build_checks(
        interfaces=[
            _iface("enp125s0f1", cidr="192.168.90.0/24")
        ],  # 宿主侧是 192.168.90.1
        bindings=[
            {
                "service": "dhcp",
                "container": "bmc-dhcp",
                "network": "servicesmgt_dhcp-l2-net",
                "parent": "enp125s0f1",
                "attached": True,
                "address": "192.168.90.1",
            }
        ],
        dhcp_values=None,
    )
    conflict = [c for c in checks if c["code"] == "address_conflict"]
    assert len(conflict) == 1
    assert conflict[0]["level"] == "error"
    assert "192.168.90.1" in conflict[0]["message"]

    # 容器拿到 .2（配了 gateway 后的正常情况）时不该报
    checks_ok = host_network.build_checks(
        interfaces=[_iface("enp125s0f1", cidr="192.168.90.0/24")],
        bindings=[
            {
                "service": "dhcp",
                "container": "bmc-dhcp",
                "network": "servicesmgt_dhcp-l2-net",
                "parent": "enp125s0f1",
                "attached": True,
                "address": "192.168.90.2",
                "address_v6": "fd00:90::2",
            }
        ],
        dhcp_values=None,
    )
    assert all(c["code"] != "address_conflict" for c in checks_ok)


def test_checks_dhcp_not_attached(monkeypatch: Any) -> None:
    """配了二层网口但 dhcp 没挂 macvlan 时必须报出来（忘了叠加 override 的典型错配）。

    这是最可能发生、也最该报的一种：服务全健康、地址池也在网段内，但 BMC 收不到任何 DHCP 应答。
    """
    monkeypatch.setattr(settings, "DHCP_PARENT_IFACE", "enp125s0f1")
    monkeypatch.setattr(settings, "L2_SUBNET", "192.168.90.0/24")
    monkeypatch.setattr(settings, "L2_SERVICES", "dhcp")
    checks = host_network.build_checks(
        interfaces=[_iface("enp125s0f1", cidr="192.168.90.0/24")],
        bindings=[
            {
                "service": "dhcp",
                "container": "bmc-dhcp",
                "network": None,
                "parent": None,
                "attached": False,
                "address": None,
                "address_v6": None,
            }
        ],
        dhcp_values={"pool_start": "192.168.90.100", "pool_end": "192.168.90.200"},
    )
    codes = {c["code"] for c in checks}
    assert "dhcp_not_attached" in codes
    # 不能报成「一切正常」
    assert "l2_ok" not in codes


def test_checks_ra_prefix_outside_parent_subnet(monkeypatch: Any) -> None:
    """RA 前缀不在绑定口的 IPv6 网段内要报出来（PRD R3③ 的 v6 半边）。"""
    monkeypatch.setattr(settings, "DHCP_PARENT_IFACE", "enp125s0f1")
    monkeypatch.setattr(settings, "L2_SUBNET", "")
    iface = _iface("enp125s0f1", cidr="192.168.90.0/24")
    iface["ipv6"] = [{"address": "fd00:90::1", "prefix": 64, "cidr": "fd00:90::/64"}]
    checks = host_network.build_checks(
        interfaces=[iface],
        bindings=[
            {
                "service": "dhcp",
                "container": "bmc-dhcp",
                "network": "servicesmgt_dhcp-l2-net",
                "parent": "enp125s0f1",
                "attached": True,
                "address": "192.168.90.2",
                "address_v6": "fd00:90::2",
            }
        ],
        dhcp_values={"ipv6_prefix": "fd00:30:12::/64"},
    )
    assert any(c["code"] == "ra_prefix_outside_parent_subnet" for c in checks)

    # 前缀落在同一 /64 内时不该报
    checks_ok = host_network.build_checks(
        interfaces=[iface],
        bindings=[
            {
                "service": "dhcp",
                "container": "bmc-dhcp",
                "network": "servicesmgt_dhcp-l2-net",
                "parent": "enp125s0f1",
                "attached": True,
                "address": "192.168.90.2",
                "address_v6": "fd00:90::2",
            }
        ],
        dhcp_values={"ipv6_prefix": "fd00:90::/64"},
    )
    assert all(c["code"] != "ra_prefix_outside_parent_subnet" for c in checks_ok)


def test_checks_l2_subnet_invalid_is_reported(monkeypatch: Any) -> None:
    """L2_SUBNET 写错时不能静默跳过比对，要给出可读提示。"""
    monkeypatch.setattr(settings, "DHCP_PARENT_IFACE", "enp125s0f1")
    monkeypatch.setattr(settings, "L2_SUBNET", "not-a-cidr")
    checks = host_network.build_checks(
        interfaces=[_iface("enp125s0f1", cidr="192.168.90.0/24")],
        bindings=[],
        dhcp_values=None,
    )
    assert any(c["code"] == "l2_subnet_invalid" for c in checks)


def test_is_physical_keeps_lom_like_names() -> None:
    """过滤要精确匹配 lo：lom1 这类真实物理口不能被前缀匹配误藏。"""
    assert host_network._is_physical("enp125s0f1") is True
    assert host_network._is_physical("lom1") is True
    assert host_network._is_physical("lo") is False
    assert host_network._is_physical("docker0") is False
    assert host_network._is_physical("veth1234") is False


def test_checks_address_conflict_ipv6(monkeypatch: Any) -> None:
    """v6 侧同样要报地址冲突：macvlan 的 v6 子网不给 gateway 时 Docker 会把 ::1 分给容器。

    实测踩到两次（先 v4 的 .1，再 v6 的 ::1），所以冲突校验必须同时比两个协议族。
    """
    monkeypatch.setattr(settings, "DHCP_PARENT_IFACE", "enp125s0f1")
    monkeypatch.setattr(settings, "L2_SUBNET", "")
    iface = _iface("enp125s0f1", cidr="192.168.90.0/24")
    iface["ipv6"] = [{"address": "fd00:90::1", "prefix": 64, "cidr": "fd00:90::/64"}]
    checks = host_network.build_checks(
        interfaces=[iface],
        bindings=[
            {
                "service": "dhcp",
                "container": "bmc-dhcp",
                "network": "servicesmgt_dhcp-l2-net",
                "parent": "enp125s0f1",
                "attached": True,
                "address": "192.168.90.2",
                "address_v6": "fd00:90::1",
            }
        ],
        dhcp_values=None,
    )
    conflict = [c for c in checks if c["code"] == "address_conflict"]
    assert len(conflict) == 1 and conflict[0]["level"] == "error"
    assert "fd00:90::1" in conflict[0]["message"]
