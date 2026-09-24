"""宿主机网口事实与二层绑定的采集、推导与一致性校验。

**为什么需要这个模块**：依赖二层直连的夹具（DHCP 要收广播）必须绑在「接了被测 BMC 的那块宿主网口」上，
而这块口的信息平台默认看不到——平台容器只挂 `./services`、`./volumes`、`docker.sock`。
配错网口的失败表现又极难排查（服务 healthy、BMC 却拿不到地址），所以这里把三件事做成平台可核对的事实：

1. **网口物理事实**：网口名/carrier/速率/MAC 读宿主 `/sys` 的只读挂载（`settings.HOST_SYS_DIR`）。
2. **IP/掩码与默认路由出口**：宿主地址在 netns 里，只读挂载读不到（实测：`/proc/net/fib_trie` 是
   `self/net` 的符号链接，绑进容器读到的还是容器自己的地址）。因此按需起一个 `--network host` 的一次性
   helper 容器：共享宿主 netns 后，`socket`/`fcntl.ioctl` 能读各口 IPv4/掩码，`/proc/net/route` 就是
   宿主路由表。结果做进程内缓存，避免每次打开页面都起容器。
3. **当前绑定**：从运行态推导——容器 → 所在网络 → `docker network inspect` 的 `Options.parent`。
   不新增配置存储：绑定值本身写在 `.env`（`DHCP_PARENT_IFACE`）里，由 compose 交给 macvlan。

校验结论只做展示（`checks`），不阻断任何下发或启停——沿用仓库惯例（reload 失败也先落版本再报
`applied=false`，不阻断）。
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import time
from typing import Any

import docker
from docker.errors import DockerException

from app import l2_config
from app.core.config import settings

logger = logging.getLogger(__name__)

# 虚拟/项目自身接口前缀：这些不是「物理网口」，不进候选列表
VIRTUAL_PREFIXES = ("docker", "br-", "veth", "virbr", "tun", "tap")

# 宿主网络事实的缓存时长：网口变化是低频事件，而 helper 容器调用有成本
CACHE_TTL_SECONDS = 30.0

# helper 脚本：在 --network host 下用标准库枚举网口 IPv4/掩码，并读出默认路由出口。
# 注意 ifreq 结构必须是 40 字节（16 名字 + 24 union），只给 16 字节会 SystemError: buffer overflow。
_HELPER_SCRIPT = """
import fcntl
import json
import socket
import struct

SIOCGIFADDR = 0x8915
SIOCGIFNETMASK = 0x891b


def addr(sock, req, name):
    buf = struct.pack("16s24s", name.encode()[:15], b"")
    try:
        raw = fcntl.ioctl(sock.fileno(), req, buf)
    except OSError:
        return ""
    return socket.inet_ntoa(raw[20:24])


sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
interfaces = {}
for _index, name in socket.if_nameindex():
    interfaces[name] = {"ipv4": addr(sock, SIOCGIFADDR, name), "netmask": addr(sock, SIOCGIFNETMASK, name)}
sock.close()

# 默认路由：/proc/net/route 在 host netns 下就是宿主路由表；目的地址 0.0.0.0 且带网关标志的那条
default_iface = ""
try:
    with open("/proc/net/route") as handle:
        next(handle)
        for line in handle:
            parts = line.split()
            if len(parts) < 4 or parts[1] != "00000000":
                continue
            flags = int(parts[3], 16)
            if flags & 0x2:  # RTF_GATEWAY
                default_iface = parts[0]
                break
except OSError:
    pass

# IPv6：host netns 下 /proc/net/if_inet6 就是宿主各口的 v6 地址（含前缀长度）。
# 跳过 fe80::/10 链路本地与 ::1：它们对「RA 通告前缀是否在绑定口网段内」这条判定没有意义，
# 只会把界面塞满。
ipv6 = {}
try:
    with open("/proc/net/if_inet6") as handle:
        for line in handle:
            parts = line.split()
            if len(parts) < 6:
                continue
            raw_addr = parts[0]
            addr = ":".join(raw_addr[i:i + 4] for i in range(0, 32, 4))
            if addr.startswith("fe80") or addr == "0000:0000:0000:0000:0000:0000:0000:0001":
                continue
            ipv6.setdefault(parts[5], []).append({"address": addr, "prefix": int(parts[2], 16)})
except OSError:
    pass

print(json.dumps({"interfaces": interfaces, "default_iface": default_iface, "ipv6": ipv6}))
"""


def _is_physical(name: str) -> bool:
    """判断是否把该网口当作「物理网口」呈现（过滤虚拟/项目自身接口）。

    `lo` 用精确匹配而不是前缀：否则 lom1 这类真实物理口会被静默隐藏（前缀匹配踩过这个坑）。
    """
    if name == "lo":
        return False
    return not name.startswith(VIRTUAL_PREFIXES)


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="ascii") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _read_int(path: str) -> int | None:
    """读一个整数；读不到或非数字返回 None。

    注意**不要把 0 归一成 None**：carrier=0 是有意义的事实（没插线/对端未上电），
    正是本功能最要紧的判据之一——曾经把 0 也归一成 None，结果「无链路」被显示成「未知」。
    """
    text = _read_text(path)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _read_speed(path: str) -> int | None:
    """读速率；链路断开时 /sys 返回 -1，那不是有效速率，归一成 None。"""
    value = _read_int(path)
    return value if value is not None and value > 0 else None


def _read_sys_interfaces() -> dict[str, dict[str, Any]]:
    """从宿主 /sys 读网口的物理事实；挂载缺失时返回空字典（由调用方降级）。"""
    net_dir = os.path.join(settings.HOST_SYS_DIR, "class", "net")
    if not os.path.isdir(net_dir):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(os.listdir(net_dir)):
        if not _is_physical(name):
            continue
        iface_dir = os.path.join(net_dir, name)
        out[name] = {
            "name": name,
            "carrier": _read_int(os.path.join(iface_dir, "carrier")),
            "speed_mbps": _read_speed(os.path.join(iface_dir, "speed")),
            "mac": _read_text(os.path.join(iface_dir, "address")) or None,
            "ipv4": [],
            "ipv6": [],
        }
    return out


def _client() -> Any:
    return docker.from_env()


def _helper_image(client: Any) -> str:
    """helper 容器用的镜像：优先取平台容器自己的镜像，取不到再退回本地 tag。

    不写死镜像名：平台可能以 `${IMAGE_PREFIX}fx-platform:<tag>` 或 `fx-platform:<版本>` 运行，
    写死会让 helper 在换 tag 的部署上起不来（那时 IP 采集整块降级）。
    """
    try:
        container = client.containers.get(settings.PLATFORM_CONTAINER_NAME)
        image = str(container.attrs["Config"]["Image"] or "")
        if image:
            return image
    except Exception as e:  # noqa: BLE001 - 取不到就退回默认 tag，不影响主流程
        logger.debug(f"cannot resolve platform image for helper: {e}")
    return "fx-platform:latest"


def _read_host_addresses() -> tuple[
    dict[str, dict[str, str]], str, str, dict[str, Any]
]:
    """用 `--network host` 的一次性 helper 容器读宿主各口 IPv4/掩码与默认路由出口。

    Returns:
        (地址表, 默认路由出口网口, 状态, 各口 IPv6 表)。状态为 `ok` 或 `unavailable`；后者表示
        helper 起不来或读不到，调用方应降级展示（IP 留空）而不是让整个面板报错。
    """
    try:
        client = _client()
        output = client.containers.run(
            _helper_image(client),
            ["python3", "-c", _HELPER_SCRIPT],
            network_mode="host",
            remove=True,
            detach=False,
        )
    except Exception as e:  # noqa: BLE001 - 任何异常都只降级，不影响面板其余内容
        logger.warning(f"host address helper container failed: {e}")
        return {}, "", "unavailable", {}

    raw = (
        output.decode("utf-8", "replace") if isinstance(output, bytes) else str(output)
    )
    try:
        parsed = json.loads(raw.strip().splitlines()[-1])
        addresses = parsed["interfaces"]
        default_iface = str(parsed.get("default_iface") or "")
        ipv6_map = parsed.get("ipv6") or {}
    except ValueError, IndexError, KeyError, TypeError:
        logger.warning("host address helper returned unparsable output")
        return {}, "", "unavailable", {}
    if not isinstance(addresses, dict):
        return {}, "", "unavailable", {}
    # 逐条校验形状：helper 输出异常时（例如 {"enp125s0f1": "x"}）不能让上层取属性时抛异常，
    # 进而把 /system/host-network 打成 500——降级成「读不到 IP」即可
    clean = {
        name: entry
        for name, entry in addresses.items()
        if isinstance(name, str) and isinstance(entry, dict)
    }
    return clean, default_iface, "ok", ipv6_map if isinstance(ipv6_map, dict) else {}


_cache: tuple[float, dict[str, Any]] | None = None


def host_facts(force: bool = False) -> dict[str, Any]:
    """宿主网络事实（带缓存）：`{interfaces, ip_source, default_iface}`。

    `ip_source` 为 `ok` 时各口带 `ipv4`（含 `cidr`）；为 `unavailable` 时 IP 为空列表，
    前端应提示「IP 不可读」而不是报错。
    """
    global _cache
    now = time.monotonic()
    if not force and _cache is not None and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1]

    interfaces = _read_sys_interfaces()
    addresses, default_iface, ip_source, ipv6_map = _read_host_addresses()
    for name, info in interfaces.items():
        entry = addresses.get(name) or {}
        address = str(entry.get("ipv4") or "")
        netmask = str(entry.get("netmask") or "")
        if address and netmask:
            try:
                cidr = str(ipaddress.ip_network(f"{address}/{netmask}", strict=False))
            except ValueError:
                cidr = ""
            info["ipv4"] = [{"address": address, "netmask": netmask, "cidr": cidr}]
        for item in ipv6_map.get(name) or []:
            if not isinstance(item, dict) or not item.get("address"):
                continue
            try:
                network6 = ipaddress.ip_network(
                    f"{item['address']}/{item['prefix']}", strict=False
                )
                # 归一成压缩写法：/proc/net/if_inet6 出来的是补零的四位一组
                # （fd00:0090:0000:...:0001），直接展示又长又难读
                address6 = str(ipaddress.ip_address(str(item["address"])))
            except ValueError:
                continue
            info["ipv6"].append(
                {
                    "address": address6,
                    "prefix": int(item["prefix"]),
                    "cidr": str(network6),
                }
            )

    result: dict[str, Any] = {
        "interfaces": [interfaces[name] for name in sorted(interfaces)],
        "ip_source": ip_source,
        "default_iface": default_iface,
    }
    _cache = (now, result)
    return result


def host_interfaces(force: bool = False) -> tuple[list[dict[str, Any]], str]:
    """返回宿主物理网口列表与 IP 采集状态（带缓存）。"""
    facts = host_facts(force)
    return facts["interfaces"], facts["ip_source"]


def service_bindings(service_names: list[str]) -> list[dict[str, Any]]:
    """推导每个服务容器当前挂的二层绑定（macvlan 网络及其 parent）。

    容器 → 所在网络 → `docker network inspect` 的 `Options.parent`。没有 macvlan 绑定即
    `parent=None`（未绑定），不影响其他服务的推导。
    """
    bindings: list[dict[str, Any]] = []
    try:
        client = _client()
    except DockerException as e:
        logger.warning(f"Docker unavailable while reading bindings: {e}")
        return bindings

    for name in service_names:
        container_name = "fx-nfs-ganesha" if name == "nfs-ganesha" else f"fx-{name}"
        entry: dict[str, Any] = {
            "service": name,
            "container": container_name,
            "network": None,
            "parent": None,
            "attached": False,
            # 容器在该 macvlan 网络上的地址：既给「使用方式」卡片用，也用于地址冲突校验
            "address": None,
            "address_v6": None,
        }
        try:
            container = client.containers.get(container_name)
            networks = container.attrs["NetworkSettings"]["Networks"] or {}
        except DockerException, KeyError:
            bindings.append(entry)
            continue
        for network_name in networks:
            try:
                network = client.networks.get(network_name)
                network_attrs = network.attrs or {}
            except DockerException:
                continue
            if network_attrs.get("Driver") != "macvlan":
                continue
            options = network_attrs.get("Options") or {}
            entry["network"] = network_name
            entry["parent"] = options.get("parent")
            entry["attached"] = True
            network_info = networks.get(network_name) or {}
            entry["address"] = network_info.get("IPAddress")
            entry["address_v6"] = network_info.get("GlobalIPv6Address")
            break
        bindings.append(entry)
    return bindings


def _ipv4_networks(iface: dict[str, Any]) -> list[ipaddress.IPv4Network]:
    out: list[ipaddress.IPv4Network] = []
    for item in iface.get("ipv4") or []:
        cidr = item.get("cidr")
        if not cidr:
            continue
        try:
            network = ipaddress.ip_network(str(cidr), strict=False)
        except ValueError:
            continue
        # 只接受 IPv4：本模块的「地址池是否在网段内」判定只对 v4 有意义（v6 走前缀比对，另做）
        if isinstance(network, ipaddress.IPv4Network):
            out.append(network)
    return out


def _normalize_ip(value: str) -> str:
    """把地址归一成标准写法（IPv6 压缩形式），解析不了就原样返回。

    两侧来源不同：宿主侧来自 helper 容器的 ioctl 读数，容器侧来自 Docker 的
    `GlobalIPv6Address`。同一地址可能一个压缩一个不压缩（`fd00:90::1` 与
    `fd00:90:0:0:0:0:0:1`），直接比字符串会漏判冲突。
    """
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return value


def l2_intent() -> dict[str, str]:
    """当前的二层夹具意图：**优先读部署目录 `.env`**（页面改的就是它），读不到才退回容器环境变量。

    两个来源必须只认一个：容器环境变量是**启动时的快照**，页面上改过 `.env` 之后它就过期了；
    继续用它会让「宿主网口」面板与「二层夹具绑定」面板显示两套值（实机联调踩到）。
    `.env` 里键存在但值为空是有意义的（= 未启用），所以只有**键不存在**才回落。

    Returns:
        含 parent_iface / l2_subnet / l2_subnet_v6 / l2_services 的意图值。
    """
    try:
        values = l2_config.read_l2_env(settings.env_file_path).values
    except l2_config.L2EnvError as e:
        logger.warning(f"L2 env file unavailable, falling back to container env: {e}")
        values = {}

    def pick(key: str, fallback: str) -> str:
        return values[key].strip() if key in values else fallback.strip()

    return {
        "parent_iface": pick("DHCP_PARENT_IFACE", settings.DHCP_PARENT_IFACE),
        "l2_subnet": pick("L2_SUBNET", settings.L2_SUBNET),
        "l2_subnet_v6": pick("L2_SUBNET_V6", ""),
        "l2_services": pick("L2_SERVICES", settings.L2_SERVICES),
    }


def _reference_v4(
    iface: dict[str, Any], l2_subnet: str
) -> tuple[list[ipaddress.IPv4Network], str]:
    """IPv4 地址池的判定基准：L2_SUBNET 优先，否则绑定口自己的网段。

    Args:
        iface: 绑定网口的采集结果。
        l2_subnet: 期望的测试网段；空串或解析失败时退回绑定口网段。

    Returns:
        (基准网段列表, 基准的人话说明)。
    """
    if l2_subnet:
        try:
            network = ipaddress.ip_network(l2_subnet, strict=False)
        except ValueError:
            network = None
        # 只接受 IPv4：L2_SUBNET 写成 v6 前缀时退回绑定口网段（另有专门提示）
        if isinstance(network, ipaddress.IPv4Network):
            return [network], f"测试网段 {l2_subnet}"
    return _ipv4_networks(iface), f"绑定网口 {iface['name']} 的网段"


def _reference_v6(iface: dict[str, Any], l2_subnet_v6: str) -> tuple[list[str], str]:
    """RA 前缀的判定基准：L2_SUBNET_V6 优先，否则绑定口自己的 v6 网段。"""
    if l2_subnet_v6:
        return [l2_subnet_v6], f"测试网段 {l2_subnet_v6}"
    prefixes = [
        str(item.get("cidr")) for item in (iface.get("ipv6") or []) if item.get("cidr")
    ]
    return prefixes, f"绑定网口 {iface['name']} 的 IPv6 网段"


def evaluate_checks(
    *,
    interfaces: list[dict[str, Any]],
    bindings: list[dict[str, Any]],
    dhcp_values: dict[str, Any] | None,
    parent: str,
    l2_subnet: str,
    l2_services: set[str],
    l2_subnet_v6: str = "",
    default_iface: str = "",
) -> list[dict[str, Any]]:
    """按 PRD R3 产出校验结论（纯函数：不读 settings、不连 Docker）。

    抽成纯函数是为了让「当前状态」与「变更前预检」共用同一套规则——两处各写一份必然漂移。

    Args:
        interfaces: `host_interfaces()` 的结果。
        bindings: `service_bindings()` 的结果。
        dhcp_values: dhcp 服务当前配置值（用 pool_start/pool_end 与 ipv6_prefix）；None 表示取不到。
        parent: 期望的 macvlan 父口名（空串表示未启用二层夹具）。
        l2_subnet: 期望的测试网段（空串表示未配置）。
        l2_services: 需要在测试网段上被 BMC 访问的服务名集合。
        l2_subnet_v6: 期望的测试网段 IPv6 前缀（空串表示未配置，此时退回绑定口的 v6 网段）。
        default_iface: 宿主默认路由出口网口（空串表示未知，相关校验跳过）。

    Returns:
        校验结论列表；全部通过时只有一条 `ok: l2_ok`。
    """
    checks: list[dict[str, Any]] = []
    by_name = {iface["name"]: iface for iface in interfaces}

    def add(level: str, code: str, service: str | None, message: str) -> None:
        checks.append(
            {"level": level, "code": code, "service": service, "message": message}
        )

    if not parent:
        add(
            "info",
            "l2_not_configured",
            None,
            "未启用二层测试网段：DHCP 只在容器网络内服务（BMC 取不到地址）。"
            "启用方式见 docs/network-plan.md「启用/停用」。",
        )
        return checks

    iface = by_name.get(parent)
    if iface is None:
        available = "、".join(sorted(by_name)) or "（读不到宿主网口）"
        add(
            "error",
            "parent_missing",
            "dhcp",
            f"绑定的网口 {parent} 在宿主机上不存在（改名或已拔卡）。当前可用网口：{available}",
        )
        return checks

    if iface.get("carrier") == 0:
        add(
            "warn",
            "parent_no_carrier",
            "dhcp",
            f"绑定网口 {parent} 没有链路（carrier=0）：没插线或对端未上电，BMC 收不到广播、取不到地址。",
        )

    if default_iface and default_iface == parent:
        add(
            "warn",
            "parent_has_default_route",
            "dhcp",
            f"绑定网口 {parent} 承载着宿主默认路由：测试网段一旦波动会影响管理通道与平台访问。"
            "建议把该连接设为 never-default（见 docs/network-plan.md）。",
        )

    # 挂在 macvlan 上的服务：后面几处判断都要用（池子基准、测试口地址提示）
    bound_services = {item["service"] for item in bindings if item.get("attached")}

    # 地址池该落在哪个网段：**优先 L2_SUBNET**（平台/页面维护的显式意图），没配时才退回绑定口自己的网段。
    # 不能只比绑定口——网段切过去、宿主测试口地址还没搬时（那一步是人工的），容器 macvlan 接口
    # 已经在新网段上、dnsmasq 服务正常，拿旧网段当基准会误报「dnsmasq 会拒绝服务」（实机联调踩到）。
    networks, ref_label = _reference_v4(iface, l2_subnet)
    if dhcp_values and networks:
        for field_name, label in (
            ("pool_start", "IPv4 地址池起始"),
            ("pool_end", "IPv4 地址池结束"),
        ):
            raw = str(dhcp_values.get(field_name) or "")
            if not raw:
                continue
            try:
                address = ipaddress.ip_address(raw)
            except ValueError:
                continue
            if not any(address in network for network in networks):
                covered = "、".join(str(network) for network in networks)
                add(
                    "warn",
                    "pool_outside_parent_subnet",
                    "dhcp",
                    f"{label} {raw} 不在{ref_label}（{covered}）内，dnsmasq 只服务接口所在网段。",
                )
    elif dhcp_values and not networks:
        add(
            "info",
            "parent_subnet_unknown",
            "dhcp",
            f"没有可用的网段基准（L2_SUBNET 未配置，且绑定网口 {parent} 没有 IPv4 地址），"
            "无法判定地址池是否落在网段内（若网段配在交换机侧，可忽略此项）。",
        )

    if l2_subnet and networks:
        try:
            l2_network = ipaddress.ip_network(l2_subnet, strict=False)
        except ValueError:
            # 写错的网段被静默吞掉会让「网段比对」整条消失、卡片地址也悄悄回落，必须提示
            add(
                "warn",
                "l2_subnet_invalid",
                None,
                f"L2_SUBNET 无法解析为网段（当前值：{l2_subnet}），"
                "网段一致性与二层地址判定已跳过。",
            )
            l2_network = None
        parent_networks = _ipv4_networks(iface)
        if (
            l2_network is not None
            and parent_networks
            and not any(network.overlaps(l2_network) for network in parent_networks)
        ):
            covered = "、".join(str(network) for network in parent_networks)
            host_served = sorted(l2_services - bound_services - {"dhcp"})
            affected = (
                f"走宿主地址的 {'、'.join(host_served)} 在测试网段上够不到"
                if host_served
                else "走宿主地址的 L2 服务在测试网段上够不到"
            )
            add(
                "warn",
                "parent_subnet_mismatch",
                "dhcp",
                f"宿主测试口 {parent} 的地址（{covered}）不在测试网段 {l2_network} 内："
                f"BMC 取址不受影响（dnsmasq 走容器在 macvlan 上的接口），但{affected}。"
                "按页面给出的 nmcli 命令把测试口地址搬到该网段即可。",
            )

    # 地址冲突：dhcp 容器在 macvlan 上的地址与绑定口自己的地址相同。
    # 成因：macvlan 网络没配 gateway 时，Docker 的 IPAM 会把 .1（v6 是 ::1）分给容器，正好撞上
    # 宿主测试口的地址（同段两个 MAC 抢同一地址，BMC 侧 ARP/邻居表会来回跳）。
    # compose.l2.yaml 已要求 v4/v6 两个子网都显式给 gateway——两个协议族都实测踩过，都要报。
    own_addresses = {
        _normalize_ip(str(item.get("address")))
        for item in iface.get("ipv4") or []
        if item.get("address")
    }
    own_addresses_v6 = {
        _normalize_ip(str(item.get("address")))
        for item in iface.get("ipv6") or []
        if item.get("address")
    }
    for binding in bindings:
        # 变量名避开上面的 address（那里是 ipaddress 对象，复用会被 mypy 判类型冲突）
        binding_address = str(binding.get("address") or "")
        if (
            binding.get("attached")
            and binding_address
            and _normalize_ip(binding_address) in own_addresses
        ):
            add(
                "error",
                "address_conflict",
                str(binding["service"]),
                f"{binding['service']} 在 macvlan 上的地址 {binding_address} 与绑定网口 {parent} 自身的地址相同"
                "（地址冲突：BMC 侧 ARP 会来回跳）。给 macvlan 网络配 gateway（L2_GATEWAY）后重建，"
                "容器会从 .2 起分配。",
            )
        binding_address_v6 = str(binding.get("address_v6") or "")
        if (
            binding.get("attached")
            and binding_address_v6
            and _normalize_ip(binding_address_v6) in own_addresses_v6
        ):
            add(
                "error",
                "address_conflict",
                str(binding["service"]),
                f"{binding['service']} 在 macvlan 上的 IPv6 地址 {binding_address_v6} 与绑定网口 {parent} "
                "自身的地址相同（地址冲突：BMC 侧邻居表会来回跳）。给 macvlan 的 IPv6 子网配 gateway"
                "（L2_GATEWAY_V6）后重建，容器会从 ::2 起分配。",
            )

    # RA 前缀（IPv6）该落在哪个网段：同样**优先 L2_SUBNET_V6**，没配时才退回绑定口的 v6 网段。
    # 只有 ipv6_prefix 写错时 BMC 拿不到 RA，平台此前给不出任何提示（PRD R3③ 的 v6 半边）
    v6_prefixes, v6_ref_label = _reference_v6(iface, l2_subnet_v6)
    if dhcp_values and v6_prefixes:
        raw_prefix = str(dhcp_values.get("ipv6_prefix") or "")
        try:
            ra_network = ipaddress.ip_network(raw_prefix, strict=False)
        except ValueError:
            ra_network = None
        if ra_network is not None:
            overlaps = False
            for cidr6 in v6_prefixes:
                try:
                    if ipaddress.ip_network(cidr6, strict=False).overlaps(ra_network):
                        overlaps = True
                        break
                except ValueError:
                    continue
            if not overlaps:
                add(
                    "warn",
                    "ra_prefix_outside_parent_subnet",
                    "dhcp",
                    f"RA 通告前缀 {ra_network} 不在{v6_ref_label}"
                    f"（{'、'.join(v6_prefixes)}）内，BMC 自动配置出的地址与本服务不在同一网段。",
                )

    # 最可能发生的错配：.env 配了网口，但 dhcp 还挂在 bridge 上（忘了叠加 compose.l2.yaml 重建）。
    # 此时 BMC 收不到任何 DHCP 应答，而其它检查都「看起来正常」——必须显式报出来。
    if "dhcp" not in bound_services:
        add(
            "warn",
            "dhcp_not_attached",
            "dhcp",
            f"已配置二层网口 {parent}，但 dhcp 容器没有挂到 macvlan 网络：BMC 收不到 DHCP 应答。"
            "启用方式：docker compose -f compose.yaml -f compose.l2.yaml up -d dhcp",
        )

    # L2 服务集合里走宿主地址的服务（非 macvlan 绑定）：需要测试口本身有测试网段地址
    host_served = sorted(l2_services - bound_services - {"dhcp"})
    if host_served and not networks:
        add(
            "warn",
            "test_port_without_address",
            None,
            f"{'、'.join(host_served)} 需要 BMC 经宿主测试口 {parent} 访问，"
            "但该口当前没有测试网段地址（BMC 够不到它们）。配置方式见 docs/network-plan.md。",
        )

    if not checks:
        add("ok", "l2_ok", None, f"二层夹具绑定正常：{parent}，地址池与网段一致。")
    return checks


def build_checks(
    *,
    interfaces: list[dict[str, Any]],
    bindings: list[dict[str, Any]],
    dhcp_values: dict[str, Any] | None,
    default_iface: str = "",
) -> list[dict[str, Any]]:
    """用当前 settings 里的 L2 意图调 `evaluate_checks`（保持既有调用方式）。"""
    intent = l2_intent()
    return evaluate_checks(
        interfaces=interfaces,
        bindings=bindings,
        dhcp_values=dhcp_values,
        parent=intent["parent_iface"],
        l2_subnet=intent["l2_subnet"],
        l2_services={
            item.strip() for item in intent["l2_services"].split(",") if item.strip()
        },
        l2_subnet_v6=intent["l2_subnet_v6"],
        default_iface=default_iface,
    )


def snapshot(
    service_names: list[str], dhcp_values: dict[str, Any] | None = None
) -> dict[str, Any]:
    """组装 `/system/host-network` 的响应体。"""
    facts = host_facts()
    interfaces = facts["interfaces"]
    bindings = service_bindings(service_names)
    intent = l2_intent()
    return {
        "ip_source": facts["ip_source"],
        "parent_iface": intent["parent_iface"],
        "l2_subnet": intent["l2_subnet"],
        "l2_services": sorted(
            item.strip() for item in intent["l2_services"].split(",") if item.strip()
        ),
        "default_iface": facts["default_iface"],
        "interfaces": interfaces,
        "bindings": bindings,
        "checks": build_checks(
            interfaces=interfaces,
            bindings=bindings,
            dhcp_values=dhcp_values,
            default_iface=facts["default_iface"],
        ),
    }


def l2_address_for(
    *,
    service_name: str,
    bindings: list[dict[str, Any]],
    interfaces: list[dict[str, Any]],
) -> str | None:
    """该服务在二层网段上应被 BMC 访问的地址（未启用/取不到时返回 None）。

    两类来源（见 design §5）：macvlan 绑定的服务取容器在该网络上的 IP；其余 L2 服务取宿主测试口
    在测试网段上的地址。
    """
    intent = l2_intent()
    parent = intent["parent_iface"]
    l2_services = {
        item.strip() for item in intent["l2_services"].split(",") if item.strip()
    }
    if not parent:
        return None
    if service_name not in l2_services:
        return None

    binding = next((item for item in bindings if item["service"] == service_name), None)
    if binding and binding.get("attached"):
        # 地址已在 service_bindings 里算好（binding["address"]），这里不再起一次 Docker 调用
        address = str(binding.get("address") or "")
        if address:
            return address

    iface = next((item for item in interfaces if item["name"] == parent), None)
    if iface is None:
        return None
    l2_subnet = intent["l2_subnet"]
    for item in iface.get("ipv4") or []:
        address = str(item.get("address") or "")
        cidr = str(item.get("cidr") or "")
        if not address:
            continue
        if l2_subnet and cidr:
            try:
                if ipaddress.ip_network(cidr, strict=False).overlaps(
                    ipaddress.ip_network(l2_subnet, strict=False)
                ):
                    return address
            except ValueError:
                continue
        elif not l2_subnet:
            return address
    return None
