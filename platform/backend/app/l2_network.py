"""二层 macvlan 网络与 dhcp 容器连接的操作层。

为什么单独成模块：这些操作会**删除并重建宿主上的网络**，是会短暂中断数据面的动作。
集中在一处便于加锁、便于失败时逆序回滚，也便于单测用替身（单测不连真实 Docker）。

两条实测约束（2026-09-23 演练，18/18 通过）：
1. 挂着容器的网络删不掉（Docker 报 APIError）——必须先 disconnect，再删、再建、再连；
2. 网络的 `parent` 与 IPAM 在创建后不可修改，所以「换网口/换网段」必然是「重建」，
   而不是原地改属性。
重连时**必须显式给地址**：不给的话 Docker 会从 IPAM 池里重新分配，页面上的地址与 BMC 侧
DNS 指向都会失效。
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field
from typing import Any

import docker
from docker.errors import APIError, DockerException, NotFound
from docker.models.containers import Container
from docker.models.networks import Network

logger = logging.getLogger(__name__)

# 形态甲里唯一挂 macvlan 的服务容器（与 host_network.py 的容器名推导保持一致）
DHCP_CONTAINER = "bmc-dhcp"

# 网络名后缀：compose 的网络名是 `<project>_dhcp-l2-net`，project 名从容器标签读
L2_NETWORK_SUFFIX = "_dhcp-l2-net"

# 创建新网络时若没有旧网络可继承标签，至少要带上这几项，compose 才认得它是自己的网络
COMPOSE_VERSION_LABEL = "com.docker.compose.version"


class L2NetworkError(Exception):
    """二层网络操作失败（消息含原始原因，路由层转 502）。"""


@dataclass
class L2NetworkState:
    """容器当前所在的 macvlan 网络状态（未挂载时字段为 None/False）。

    Attributes:
        network: 容器挂着的 macvlan 网络名；未挂载时为 None。
        parent: 该网络的 `Options.parent`（宿主网口名）。
        attached: 容器是否挂在该网络上。
        exists: 网络对象是否存在（未挂载但网络可能仍然存在，停用失败时会留下这种状态）。
        subnet: IPv4 子网；gateway: IPv4 网关。
        subnet_v6: IPv6 子网；gateway_v6: IPv6 网关。
        address: 容器在该网络上的 IPv4 地址；address_v6: IPv6 地址。
    """

    network: str | None = None
    parent: str | None = None
    attached: bool = False
    exists: bool = False
    subnet: str | None = None
    gateway: str | None = None
    subnet_v6: str | None = None
    gateway_v6: str | None = None
    address: str | None = None
    address_v6: str | None = None
    # 复制自旧网络的标签，重建时带上，尽量让 compose 仍认得这张网络
    labels: dict[str, str] = field(default_factory=dict)


def _fallback_labels(project: str) -> dict[str, str]:
    """没有旧网络可继承时用的最小 compose 标签集，让 compose 仍认得出这张网络。"""
    return {
        "com.docker.compose.project": project,
        "com.docker.compose.network": "dhcp-l2-net",
    }


def _params_match(
    state: L2NetworkState,
    *,
    parent: str,
    subnet: str,
    gateway: str,
    subnet_v6: str,
    gateway_v6: str,
) -> bool:
    """网络参数是否已与目标一致（用于幂等判定）。"""
    return (
        state.exists
        and state.parent == parent
        and state.subnet == subnet
        and state.gateway == gateway
        and state.subnet_v6 == subnet_v6
        and state.gateway_v6 == gateway_v6
    )


def _in_subnet(address: str, subnet: str) -> bool:
    """地址是否落在该子网内（解析不了就当作不在，交给下面重新推导）。"""
    try:
        return ipaddress.ip_address(address) in ipaddress.ip_network(
            subnet, strict=False
        )
    except ValueError:
        return False


def _default_host(subnet: str, offset: int = 2) -> str:
    """子网里给容器用的默认地址（默认第 2 个）。

    Docker 的 IPAM 把网关（宿主测试口地址）保留在第一个地址，容器从 .2 / ::2 起分配；
    换网段后旧地址已不在新子网内，重连必须换成新子网里的地址，否则 Docker 直接拒绝。
    """
    network = ipaddress.ip_network(subnet, strict=False)
    return str(network.network_address + offset)


def _pick_address(
    previous: str | None, explicit: str | None, subnet: str
) -> str | None:
    """决定容器重连时用哪个地址：显式给 > 同子网沿用 > 新子网第 2 个。"""
    if explicit is not None:
        return explicit
    if previous and _in_subnet(previous, subnet):
        return previous
    return _default_host(subnet)


def apply_network(
    client: docker.DockerClient,
    *,
    parent: str,
    subnet: str,
    gateway: str,
    subnet_v6: str,
    gateway_v6: str,
    container_name: str = DHCP_CONTAINER,
    address: str | None = None,
    address_v6: str | None = None,
) -> tuple[L2NetworkState, list[str]]:
    """把容器的 macvlan 网络重建为目标参数，返回重建后的状态与步骤日志。

    网络的 `parent` 与 IPAM 创建后不可改，所以「换网口/换网段」只能重建：
    断开 → 删 → 建 → 连（顺序由实测确定，挂着容器的网络删不掉）。
    已与目标一致时是空操作；地址默认沿用重建前的值，避免容器换到新池里的随机地址。

    Args:
        client: Docker 客户端。
        parent: 目标宿主网口名。
        subnet: 目标 IPv4 子网；gateway: 目标 IPv4 网关。
        subnet_v6: 目标 IPv6 子网；gateway_v6: 目标 IPv6 网关。
        container_name: 服务容器名。
        address: 重连时容器要用的 IPv4 地址；None 表示沿用重建前的地址。
        address_v6: 同上，IPv6。

    Returns:
        重建后的网络状态与可读的步骤日志。

    Raises:
        L2NetworkError: 任一 Docker 调用失败时（此时状态可能已被改了一半，由调用方回滚）。
    """
    before = read_state(client, container_name)
    project = compose_project(get_container(client, container_name))
    name = before.network or f"{project}{L2_NETWORK_SUFFIX}"
    target_address = _pick_address(before.address, address, subnet)
    target_address_v6 = _pick_address(before.address_v6, address_v6, subnet_v6)

    if _params_match(
        before,
        parent=parent,
        subnet=subnet,
        gateway=gateway,
        subnet_v6=subnet_v6,
        gateway_v6=gateway_v6,
    ):
        if before.attached:
            return before, ["无需变更：网络参数与容器连接都已与目标一致"]
        connect(
            client,
            container_name,
            name,
            address=target_address,
            address_v6=target_address_v6,
        )
        return read_state(client, container_name), [
            f"网络已存在，仅把容器重连到 {name}"
        ]

    steps: list[str] = []
    if before.attached:
        disconnect(client, container_name, name)
        steps.append(f"已断开容器与 {name} 的连接")
    if before.exists:
        remove_network(client, name)
        steps.append(f"已删除旧网络 {name}（parent={before.parent}）")

    create_network(
        client,
        name=name,
        parent=parent,
        subnet=subnet,
        gateway=gateway,
        subnet_v6=subnet_v6,
        gateway_v6=gateway_v6,
        labels=before.labels or _fallback_labels(project),
    )
    steps.append(f"已创建网络 {name}（parent={parent}, {subnet}, {subnet_v6}）")
    connect(
        client,
        container_name,
        name,
        address=target_address,
        address_v6=target_address_v6,
    )
    steps.append(
        f"已把容器重连到 {name}（v4={target_address}, v6={target_address_v6}）"
    )
    return read_state(client, container_name), steps


def detach_l2(
    client: docker.DockerClient, container_name: str = DHCP_CONTAINER
) -> list[str]:
    """停用二层夹具：把容器从 macvlan 网络断开。

    **只断开、不删除网络**——实测（2026-09-23 实机）：dhcp 容器是 compose 按「双网络」创建的，
    容器配置里仍引用那个网络名；网络一删，`docker restart` 直接失败
    （`could not find a network matching network mode ...: network not found`），
    于是停用后连容器都重启不了。保留网络对象既让容器保持可重启，也让下次启用只需重连。
    要彻底删掉网络，得重建容器（走 compose 不带 override），不在本功能范围内。

    容器未连接时是安全空操作（幂等）。

    Args:
        client: Docker 客户端。
        container_name: 服务容器名。

    Returns:
        可读的步骤日志。

    Raises:
        L2NetworkError: 任一 Docker 调用失败时。
    """
    before = read_state(client, container_name)
    name = (
        before.network
        or f"{compose_project(get_container(client, container_name))}{L2_NETWORK_SUFFIX}"
    )
    steps: list[str] = []
    if before.attached:
        disconnect(client, container_name, name)
        steps.append(f"已断开容器与 {name} 的连接（网络对象保留，容器仍可重启）")
    else:
        steps.append("无需变更：容器未挂 macvlan 网络")
    return steps


def get_client() -> docker.DockerClient:
    """惰性连接 Docker daemon，失败转 L2NetworkError。"""
    try:
        return docker.from_env()
    except DockerException as e:  # pragma: no cover - 环境相关
        raise L2NetworkError(f"Failed to connect to Docker daemon: {e}") from e


def get_container(client: docker.DockerClient, name: str = DHCP_CONTAINER) -> Container:
    """按名取容器，不存在转 L2NetworkError。"""
    try:
        return client.containers.get(name)
    except NotFound as e:
        raise L2NetworkError(f"Container '{name}' not found") from e
    except (APIError, OSError) as e:
        raise L2NetworkError(f"Failed to inspect container '{name}': {e}") from e


def compose_project(container: Container) -> str:
    """从容器标签读 compose 项目名（网络名由它拼出），读不到时按部署目录名兜底。"""
    labels = ((container.attrs or {}).get("Config") or {}).get("Labels") or {}
    project = str(labels.get("com.docker.compose.project") or "").strip()
    return project or "servicesmgt"


def l2_network_name(client: docker.DockerClient, container: Container) -> str:
    """二层 macvlan 网络名：优先用容器当前挂着的那个，其次按 compose 项目名拼。"""
    for name in ((container.attrs or {}).get("NetworkSettings") or {}).get(
        "Networks"
    ) or {}:
        try:
            network = client.networks.get(name)
        except NotFound, APIError, OSError:
            continue
        if (network.attrs or {}).get("Driver") == "macvlan":
            return str(name)
    return f"{compose_project(container)}{L2_NETWORK_SUFFIX}"


def _ipam_entry(attrs: dict[str, Any], version: int) -> tuple[str | None, str | None]:
    """从网络 inspect 结果里取指定协议族的 (子网, 网关)。"""
    for entry in (attrs.get("IPAM") or {}).get("Config") or []:
        subnet = str(entry.get("Subnet") or "")
        if not subnet:
            continue
        try:
            network = ipaddress.ip_network(subnet, strict=False)
        except ValueError:
            continue
        if network.version == version:
            return subnet, (str(entry.get("Gateway")) if entry.get("Gateway") else None)
    return None, None


def read_state(
    client: docker.DockerClient, container_name: str = DHCP_CONTAINER
) -> L2NetworkState:
    """读容器当前的 macvlan 绑定与网络参数。

    Args:
        client: Docker 客户端。
        container_name: 服务容器名。

    Returns:
        当前状态；未挂 macvlan 时 `attached=False`，但 `network`/`exists` 仍会反映网络对象。

    Raises:
        L2NetworkError: 容器不存在或 Docker 调用失败时。
    """
    container = get_container(client, container_name)
    state = L2NetworkState()
    network_name = l2_network_name(client, container)
    state.network = network_name

    networks = ((container.attrs or {}).get("NetworkSettings") or {}).get(
        "Networks"
    ) or {}
    attached_info = networks.get(network_name) or {}
    if attached_info:
        state.attached = True
        state.address = attached_info.get("IPAddress") or None
        state.address_v6 = attached_info.get("GlobalIPv6Address") or None

    try:
        network: Network = client.networks.get(network_name)
    except NotFound:
        return state
    except (APIError, OSError) as e:
        raise L2NetworkError(f"Failed to inspect network '{network_name}': {e}") from e

    attrs = network.attrs or {}
    state.exists = True
    state.parent = (attrs.get("Options") or {}).get("parent")
    state.subnet, state.gateway = _ipam_entry(attrs, 4)
    state.subnet_v6, state.gateway_v6 = _ipam_entry(attrs, 6)
    state.labels = dict(attrs.get("Labels") or {})
    return state


def _build_ipam(
    subnet: str, gateway: str, subnet_v6: str, gateway_v6: str
) -> dict[str, Any]:
    """拼出双栈 IPAM 配置（与 compose.l2.yaml 的写法一致：v4/v6 都要显式给网关）。"""
    return {
        "Driver": "default",
        "Config": [
            {"Subnet": subnet, "Gateway": gateway},
            {"Subnet": subnet_v6, "Gateway": gateway_v6},
        ],
    }


def create_network(
    client: docker.DockerClient,
    *,
    name: str,
    parent: str,
    subnet: str,
    gateway: str,
    subnet_v6: str,
    gateway_v6: str,
    labels: dict[str, str] | None = None,
) -> None:
    """创建 macvlan 网络（双栈 + 显式网关）。

    Args:
        client: Docker 客户端。
        name: 网络名（由调用方按 compose 项目名拼出）。
        parent: 宿主网口名，写进 `Options.parent`。
        subnet: IPv4 子网；gateway: IPv4 网关（宿主测试口地址）。
        subnet_v6: IPv6 子网；gateway_v6: IPv6 网关。
        labels: 要带上的标签；传旧网络的标签可以让 compose 继续认得它。

    Raises:
        L2NetworkError: Docker 调用失败时。
    """
    try:
        client.api.create_network(
            name,
            driver="macvlan",
            options={"parent": parent},
            enable_ipv6=True,
            labels=labels or {},
            ipam=_build_ipam(subnet, gateway, subnet_v6, gateway_v6),
        )
    except (APIError, OSError) as e:
        raise L2NetworkError(f"Failed to create network '{name}': {e}") from e
    logger.info(
        f"Created macvlan network {name} (parent={parent}, {subnet}/{subnet_v6})"
    )


def disconnect(
    client: docker.DockerClient, container_name: str, network_name: str
) -> None:
    """把容器从网络断开；未连接时静默跳过（幂等）。

    Raises:
        L2NetworkError: Docker 调用失败时。
    """
    container = get_container(client, container_name)
    networks = ((container.attrs or {}).get("NetworkSettings") or {}).get(
        "Networks"
    ) or {}
    if network_name not in networks:
        return
    try:
        client.networks.get(network_name).disconnect(container)
    except NotFound:
        return
    except (APIError, OSError) as e:
        raise L2NetworkError(
            f"Failed to disconnect '{container_name}' from '{network_name}': {e}"
        ) from e
    logger.info(f"Disconnected {container_name} from {network_name}")


def remove_network(client: docker.DockerClient, network_name: str) -> None:
    """删除网络；不存在时静默跳过（幂等）。

    注意：调用方必须先 `disconnect`——挂着容器的网络 Docker 会拒绝删除。

    Raises:
        L2NetworkError: Docker 调用失败时。
    """
    try:
        client.networks.get(network_name).remove()
    except NotFound:
        return
    except (APIError, OSError) as e:
        raise L2NetworkError(f"Failed to remove network '{network_name}': {e}") from e
    logger.info(f"Removed network {network_name}")


def connect(
    client: docker.DockerClient,
    container_name: str,
    network_name: str,
    *,
    address: str | None,
    address_v6: str | None,
) -> None:
    """把容器连到网络，并显式指定地址（不给地址会变成新池里的随机地址）。

    Args:
        client: Docker 客户端。
        container_name: 服务容器名。
        network_name: 目标网络名。
        address: 容器在该网络上的 IPv4 地址；None 表示交给 Docker 分配。
        address_v6: 同上，IPv6。

    Raises:
        L2NetworkError: Docker 调用失败时。
    """
    container = get_container(client, container_name)
    try:
        client.networks.get(network_name).connect(
            container, ipv4_address=address, ipv6_address=address_v6
        )
    except (APIError, OSError) as e:
        raise L2NetworkError(
            f"Failed to connect '{container_name}' to '{network_name}': {e}"
        ) from e
    logger.info(
        f"Connected {container_name} to {network_name} (v4={address}, v6={address_v6})"
    )
