"""二层夹具参数（部署目录 .env 的 L2 段）的读取与改写。

为什么单独成模块：这几个参数是部署期变量，**权威副本在部署目录的 .env 里**（compose 也从它取值），
既不在平台数据库、也不在平台容器环境变量里——平台要能在页面上改它们，就必须读写那个文件。
而 .env 同时装着 SECRET_KEY / POSTGRES_PASSWORD 等机密，所以本模块的契约是：
只解析白名单键、其余行按原样保留、文件内容不经任何返回值外泄给上层调用方。

写入用 `config_renderer.atomic_write` 的原子替换（同目录临时文件 + os.replace），
但权限固定 0600：那是含机密的文件，不能按配置文件口径放开成 0644。
"""

from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config_renderer import TemplateRenderError, atomic_write

logger = logging.getLogger(__name__)

# 平台允许改写的键（白名单）：.env 里其余内容一律不碰
L2_KEYS: tuple[str, ...] = (
    "DHCP_PARENT_IFACE",
    "L2_SUBNET",
    "L2_GATEWAY",
    "L2_SUBNET_V6",
    "L2_GATEWAY_V6",
    "L2_SERVICES",
)

# .env 含密钥，写入后必须保持 0600
ENV_FILE_MODE = 0o600

# 赋值行：KEY=value（键名规则与 dotenv 一致）
_ASSIGNMENT_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")

# 允许写进 .env 的值字符集：网口名、CIDR、IP、逗号分隔的服务名。
# 收紧成白名单是为了不让换行/引号/井号/空格被写进 .env——那会改掉文件语义，甚至注入新的键。
_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_.,:/\[\]-]*$")

# 网口名：Linux 接口名允许的字符集（含 VLAN 的 `eth0.100` 与别名 `eth0:1`）
_PARENT_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")

# 逗号分隔的服务名列表
_SERVICE_LIST_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*([,][a-z0-9][a-z0-9-]*)*$")


class L2EnvError(Exception):
    """部署目录 .env 读写失败（消息含原始原因，路由层转 502）。"""


class L2ValidationError(Exception):
    """L2 参数不合法（消息直接回给用户，路由层转 400）。"""


@dataclass
class L2EnvValues:
    """从 .env 解析出的白名单键值。

    Attributes:
        values: 只含 `L2_KEYS` 的键值；文件里没出现的键不会出现在这里。
        duplicates: 在文件里出现多次的 L2 键（dotenv 语义是后者生效，这里显式记下来供页面提示）。
    """

    values: dict[str, str] = field(default_factory=dict)
    duplicates: list[str] = field(default_factory=list)


def _unquote(raw: str) -> str:
    """取赋值行右侧的值：去引号，未加引号时按 dotenv 语义截掉 ` #` 之后的注释。"""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    comment_at = value.find(" #")
    if comment_at >= 0:
        value = value[:comment_at]
    return value.strip()


def parse_l2_values(content: str) -> L2EnvValues:
    """从 .env 文本里取出白名单键的值。

    Args:
        content: .env 的完整文本（LF 或 CRLF 均可）。

    Returns:
        白名单键值与重复键列表；重复键以最后一次出现为准。
    """
    values: dict[str, str] = {}
    duplicates: list[str] = []
    for raw_line in content.splitlines():
        match = _ASSIGNMENT_PATTERN.match(raw_line)
        if match is None:
            continue
        key = match.group(1)
        if key not in L2_KEYS:
            continue
        if key in values:
            duplicates.append(key)
        values[key] = _unquote(match.group(2))
    return L2EnvValues(values=values, duplicates=sorted(set(duplicates)))


def render_l2_updates(content: str, updates: dict[str, str]) -> str:
    """把 updates 合并进 .env 文本：已存在的键替换其最后一次出现，缺失的键追加到末尾。

    未改动的行**按原样保留**（含行尾风格、缩进与注释），只重写白名单键所在的那一行。

    Args:
        content: 现有 .env 文本。
        updates: 要写入的键值，键必须在 `L2_KEYS` 内。

    Returns:
        合并后的完整文本。

    Raises:
        L2EnvError: 键不在白名单内，或值含不支持的字符时。
    """
    for key, value in updates.items():
        if key not in L2_KEYS:
            raise L2EnvError(f"Key '{key}' is not an L2 key")
        if not _VALUE_PATTERN.match(value):
            raise L2EnvError(f"Value for '{key}' contains unsupported characters")

    lines = content.splitlines(keepends=True)
    last_index: dict[str, int] = {}
    for index, raw_line in enumerate(lines):
        match = _ASSIGNMENT_PATTERN.match(raw_line.rstrip("\r\n"))
        if match is not None and match.group(1) in updates:
            last_index[match.group(1)] = index

    for key, index in last_index.items():
        body = lines[index].rstrip("\r\n")
        # 行尾按原样保留；最后一行没有行尾时补一个，避免与后续行粘连
        ending = lines[index][len(body) :] or "\n"
        lines[index] = f"{key}={updates[key]}{ending}"

    # 追加顺序按 L2_KEYS 定义，保证同一组输入产出的文件字节稳定
    missing = [key for key in L2_KEYS if key in updates and key not in last_index]
    if missing:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] = f"{lines[-1]}\n"
        lines.extend(f"{key}={updates[key]}\n" for key in missing)
    return "".join(lines)


def read_l2_env(path: Path) -> L2EnvValues:
    """读部署目录 .env 里的 L2 参数。

    Args:
        path: .env 的完整路径（平台容器内为挂载点下的路径）。

    Returns:
        白名单键值与重复键列表。

    Raises:
        L2EnvError: 文件不存在或不可读时（例如部署目录未挂载）。
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        raise L2EnvError(f"Failed to read '{path}': {e}") from e
    parsed = parse_l2_values(content)
    if parsed.duplicates:
        logger.warning(f"Duplicate L2 keys in {path}: {parsed.duplicates}")
    return parsed


def write_l2_env(path: Path, updates: dict[str, str]) -> None:
    """把 L2 参数写回部署目录 .env（原子替换，权限保持 0600）。

    写盘前做一次自检：改后的文本必须能解析回同一组值，否则宁可不写——
    .env 里还有密钥，写坏了代价远高于这次变更本身。

    Args:
        path: .env 的完整路径。
        updates: 要写入的键值，键必须在 `L2_KEYS` 内。

    Raises:
        L2EnvError: 读不到文件、键值非法、自检不通过或写入失败时。
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        raise L2EnvError(f"Failed to read '{path}': {e}") from e

    updated = render_l2_updates(content, updates)
    checked = parse_l2_values(updated)
    for key, value in updates.items():
        if checked.values.get(key) != value:
            raise L2EnvError(f"Self-check failed for '{key}' after update")
    try:
        atomic_write(path, updated, mode=ENV_FILE_MODE)
    except TemplateRenderError as e:
        raise L2EnvError(f"Failed to write '{path}': {e}") from e
    logger.info(f"Updated L2 keys in {path}: {sorted(updates)}")


def validate_params(values: dict[str, str]) -> dict[str, str]:
    """校验并归一化一组 L2 参数（键为 .env 里的键名）。

    每条规则都对应一个真实故障：网段解析不了 → dnsmasq 不服务；网关不在网段内 → BMC 拿到
    不能用的配置；v6 前缀不是 /64 → dnsmasq 的 RA 固定按 /64 处理，收不到；父口名非法 →
    macvlan 建不起来。

    Args:
        values: 至少含 5 个可编辑键（`DHCP_PARENT_IFACE` 不可为空）；`L2_SERVICES` 不在页面可改范围。

    Returns:
        归一化后的参数（网段按标准写法、去空白）。

    Raises:
        L2ValidationError: 任一字段不合法时（消息为英文短句，直接回给用户）。
    """
    parent = (values.get("DHCP_PARENT_IFACE") or "").strip()
    if not _PARENT_PATTERN.match(parent):
        raise L2ValidationError(f"Invalid parent interface name: '{parent}'")

    subnet = (values.get("L2_SUBNET") or "").strip()
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError as e:
        raise L2ValidationError(f"Invalid L2 subnet: '{subnet}'") from e
    if network.version != 4:
        raise L2ValidationError(f"L2 subnet must be IPv4: '{subnet}'")

    gateway = (values.get("L2_GATEWAY") or "").strip()
    try:
        gateway_ip = ipaddress.ip_address(gateway)
    except ValueError as e:
        raise L2ValidationError(f"Invalid L2 gateway: '{gateway}'") from e
    if gateway_ip not in network:
        raise L2ValidationError(f"Gateway {gateway} is not inside L2 subnet {network}")

    subnet_v6 = (values.get("L2_SUBNET_V6") or "").strip()
    try:
        network_v6 = ipaddress.ip_network(subnet_v6, strict=False)
    except ValueError as e:
        raise L2ValidationError(f"Invalid L2 IPv6 subnet: '{subnet_v6}'") from e
    if network_v6.version != 6 or network_v6.prefixlen != 64:
        raise L2ValidationError(f"L2 IPv6 subnet must be an IPv6 /64: '{subnet_v6}'")

    gateway_v6 = (values.get("L2_GATEWAY_V6") or "").strip()
    try:
        gateway_v6_ip = ipaddress.ip_address(gateway_v6)
    except ValueError as e:
        raise L2ValidationError(f"Invalid L2 IPv6 gateway: '{gateway_v6}'") from e
    if gateway_v6_ip not in network_v6:
        raise L2ValidationError(f"IPv6 gateway {gateway_v6} is not inside {network_v6}")

    return {
        "DHCP_PARENT_IFACE": parent,
        "L2_SUBNET": str(network),
        "L2_GATEWAY": gateway,
        "L2_SUBNET_V6": str(network_v6),
        "L2_GATEWAY_V6": gateway_v6,
    }


def _in_network(
    value: Any, network: ipaddress.IPv4Network | ipaddress.IPv6Network
) -> bool:
    """值是否是该网段内的地址（解析不了当作不在）。"""
    try:
        return ipaddress.ip_address(str(value)) in network
    except ValueError:
        return False


def pool_for(
    network: ipaddress.IPv4Network | ipaddress.IPv6Network,
) -> tuple[str, str]:
    """网段内的默认地址池：IPv4 取第 100–200 个地址，IPv6 取 `::100`–`::200`（十六进制偏移）。

    与 compose 默认布局一致（v6 的 `::100` 是十六进制，256）。放不下时收窄到该网段可用范围，
    避免生成越界地址让 dnsmasq 拒绝启动。

    Args:
        network: 目标网段。

    Returns:
        (池起始, 池结束) 的字符串形式。
    """
    # 用整数做偏移与比较：v4/v6 的地址对象不能互相比较，先落到 int 再按版本构造回来
    base = int(network.network_address)
    step = 0x100 if network.version == 6 else 100
    last_usable = base + network.num_addresses - 2
    start = base + step
    end = base + (2 * step)
    if end > last_usable:
        end = last_usable
    if start > end:
        start = base + 1
    address_cls = (
        ipaddress.IPv4Address if network.version == 4 else ipaddress.IPv6Address
    )
    return str(address_cls(start)), str(address_cls(end))


def derive_dhcp_values(
    current: dict[str, Any], params: dict[str, str]
) -> dict[str, Any]:
    """由 L2 参数推导 dhcp 服务配置里必须同步的字段（只返回需要改的键）。

    池子仍落在新网段内就保留（避免无谓变更让 BMC 的租约抖动）；不在则按 `pool_for` 重新推导。

    Args:
        current: dhcp 服务当前的配置值。
        params: 已归一化的 L2 参数（`validate_params` 的输出）。

    Returns:
        需要改写的 dhcp 配置字段；无需改动时是空字典。
    """
    network = ipaddress.ip_network(params["L2_SUBNET"], strict=False)
    network_v6 = ipaddress.ip_network(params["L2_SUBNET_V6"], strict=False)
    changes: dict[str, Any] = {}

    if str(current.get("gateway") or "") != params["L2_GATEWAY"]:
        changes["gateway"] = params["L2_GATEWAY"]
    if str(current.get("ipv6_prefix") or "") != params["L2_SUBNET_V6"]:
        changes["ipv6_prefix"] = params["L2_SUBNET_V6"]

    pool_start, pool_end = pool_for(network)
    if not _in_network(current.get("pool_start"), network):
        changes["pool_start"] = pool_start
    if not _in_network(current.get("pool_end"), network):
        changes["pool_end"] = pool_end

    pool_start_v6, pool_end_v6 = pool_for(network_v6)
    if not _in_network(current.get("ipv6_pool_start"), network_v6):
        changes["ipv6_pool_start"] = pool_start_v6
    if not _in_network(current.get("ipv6_pool_end"), network_v6):
        changes["ipv6_pool_end"] = pool_end_v6

    return changes


def matches_env(env_values: dict[str, str], params: dict[str, str]) -> bool:
    """`.env` 里的 L2 参数是否已等于目标值（幂等判定的 .env 侧）。

    Args:
        env_values: 从 .env 读出的键值。
        params: 已归一化的目标参数。

    Returns:
        全部相等为 True。
    """
    return all(env_values.get(key) == value for key, value in params.items())


def nmcli_commands(parent: str, gateway: str, subnet: str) -> list[str]:
    """宿主测试口地址的配置命令（平台不代执行，给页面复制）。

    与 docs/network-plan.md 里写的一致；`never-default` 是关键——测试口一旦承载默认路由，
    网段波动会影响管理通道。
    """
    prefix = ipaddress.ip_network(subnet, strict=False).prefixlen
    return [
        f'nmcli con mod {parent} ipv4.addresses {gateway}/{prefix} ipv4.gateway "" ipv4.never-default yes',
        f"nmcli con up {parent}",
    ]
