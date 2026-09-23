#!/usr/bin/env python3
"""DHCP / RA / DNS 探针：在「挂在 DHCP 服务网络里的客户端容器」内运行，用真实协议报文验证 dnsmasq。

为什么不直接用现成客户端（dhclient / rdisc6 / dig）：

- 判定口径要与「BMC 视角」一致——BMC 实际生效的是 DHCPACK / RA 里那些**具体选项值**（地址、网关、
  租约时长、DNS 列表、PXE 引导参数、RA 前缀），现成客户端只回显它自己挑出来的摘要，看不到原始选项；
- 故障注入要验的是「本该出现的东西没出现」（地址池耗尽时第二个客户端拿不到地址、黑洞模式下完全无应答），
  这需要精确控制客户端标识（chaddr / DUID）与报文时序，现成客户端做不到；
- 客户端容器里不一定装得下这些包，而手写实现只用标准库。

实现方式：
    v4   AF_PACKET 自建以太网帧发 DHCPDISCOVER/REQUEST（源地址 0.0.0.0、广播标志，与真实
         「无地址客户端」一致），AF_PACKET 抓回包解析 OFFER/ACK 的全部选项
    v6   AF_INET6 组播 SOLICIT 到 ff02::1:2，解析 ADVERTISE 里的 IA_NA 地址
    ra   ICMPv6 原始套接字发 Router Solicitation，解析 RA 的前缀信息选项（SLAAC 通告的前缀）
    dns  最小 DNS 查询/解析（A / AAAA / PTR），源地址即「DHCP 下发给 BMC 的 DNS」所在网络

用法（由 verify_bmc_platform_e2e.py 的 DHCP 阶段调用）：
    python3 dhcp_probe.py v4  --iface eth0 [--mac 02:42:ac:1e:0c:aa] [--expect-absent] [--timeout 6]
    python3 dhcp_probe.py v6  --iface eth0 [--expect-absent] [--timeout 6]
    python3 dhcp_probe.py ra  --iface eth0 [--expect-prefix fd00:30:12::/64] [--expect-absent]
    python3 dhcp_probe.py dns --server 172.30.12.2 --name bmc-01.bmc.lab --type A [--expect 172.30.12.10]

输出一行 `PASS ...` 或 `FAIL ...`，退出码 0/2（与 v6_probe.py 一致，便于 e2e 复用判定）。
"""

from __future__ import annotations

import argparse
import os
import random
import socket
import struct
import sys
import time

ETH_P_ALL = 0x0003
ETH_P_IP = 0x0800
SOL_PACKET = 263
PACKET_ADD_MEMBERSHIP = 1
PACKET_MR_PROMISC = 1

BROADCAST_MAC = bytes([0xFF] * 6)
IPV4_BROADCAST = bytes([255, 255, 255, 255])
ZERO_IPV4 = bytes(4)

DHCPDISCOVER = 1
DHCPOFFER = 2
DHCPREQUEST = 3
DHCPACK = 5
DHCP_MAGIC = bytes([99, 130, 83, 99])


# --------------------------------------------------------------------------- #
# 通用小工具
# --------------------------------------------------------------------------- #
def iface_mac(iface: str) -> bytes:
    """读网卡 MAC（sysfs，无需 iproute2）。"""
    with open("/sys/class/net/" + iface + "/address", encoding="ascii") as handle:
        text = handle.read().strip()
    return bytes(int(part, 16) for part in text.split(":"))


def iface_index(iface: str) -> int:
    return socket.if_nametoindex(iface)


def checksum(data: bytes) -> int:
    """标准 16 位反码校验和（IP 头 / UDP 伪头都用它）。"""
    if len(data) % 2:
        data += bytes([0])
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) + data[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def ipv4_bytes(text: str) -> bytes:
    return bytes(int(part) for part in text.split("."))


def ipv4_text(raw: bytes) -> str:
    return ".".join(str(b) for b in raw)


def ipv6_text(raw: bytes) -> str:
    """把 16 字节 IPv6 压成最简写法（够读即可，不追求 rfc5952 全规则）。"""
    groups = [raw[i : i + 2].hex() for i in range(0, 16, 2)]
    best_start, best_len = -1, 0
    start = 0
    while start < 8:
        if groups[start] != "0000":
            start += 1
            continue
        end = start
        while end < 8 and groups[end] == "0000":
            end += 1
        if end - start > best_len:
            best_start, best_len = start, end - start
        start = end
    text = ":".join(g.lstrip("0") or "0" for g in groups)
    if best_len >= 2:
        head = ":".join(g.lstrip("0") or "0" for g in groups[:best_start])
        tail = ":".join(g.lstrip("0") or "0" for g in groups[best_start + best_len :])
        text = head + "::" + tail
    return text


# --------------------------------------------------------------------------- #
# DHCPv4：AF_PACKET 自建帧，完全控制源地址与 chaddr
# --------------------------------------------------------------------------- #
def dhcp_v4_packet(mac: bytes, xid: int, msg_type: int, options: list[tuple[int, bytes]]) -> bytes:
    """组一个 DHCPv4 报文（BOOTP 固定头 + 选项），填充到 300 字节最小长度。"""
    header = struct.pack(
        "!BBBBIHH4s4s4s4s16s64s128s",
        1,  # op = BOOTREQUEST
        1,  # htype = Ethernet
        6,  # hlen
        0,  # hops
        xid,
        0,  # secs
        0x8000,  # flags：要求服务端广播应答（客户端还没有地址）
        ZERO_IPV4,  # ciaddr
        ZERO_IPV4,  # yiaddr
        ZERO_IPV4,  # siaddr
        ZERO_IPV4,  # giaddr
        mac + bytes(10),
        bytes(64),
        bytes(128),
    )
    payload = DHCP_MAGIC + bytes([53, 1, msg_type])
    for code, value in options:
        payload += bytes([code, len(value)]) + value
    payload += bytes([255])
    return header + payload + bytes(max(0, 300 - len(header) - len(payload)))


def udp_frame(src_mac: bytes, dst_mac: bytes, src_ip: bytes, dst_ip: bytes,
              src_port: int, dst_port: int, payload: bytes) -> bytes:
    """自建 Ethernet + IPv4 + UDP 帧（IP 头里显式写源地址，避免内核改写）。"""
    udp = struct.pack("!HHHH", src_port, dst_port, 8 + len(payload), 0) + payload
    total = 20 + len(udp)
    ip_header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0, total, random.randint(0, 0xFFFF), 0, 64, 17, 0, src_ip, dst_ip,
    )
    ip_header = ip_header[:10] + struct.pack("!H", checksum(ip_header)) + ip_header[12:]
    return dst_mac + src_mac + struct.pack("!H", ETH_P_IP) + ip_header + udp


def parse_dhcp(payload: bytes) -> dict:
    """解析 BOOTP 固定头与 DHCP 选项。"""
    if len(payload) < 240:
        return {}
    fields = struct.unpack("!BBBBIHH4s4s4s4s16s", payload[:44])
    out = {
        "op": fields[0],
        "xid": fields[4],
        "flags": fields[6],
        "yiaddr": fields[8],
        "siaddr": fields[9],
        "chaddr": fields[11][:6],
        "options": {},
    }
    if payload[236:240] != DHCP_MAGIC:
        return out
    pos = 240
    while pos < len(payload):
        code = payload[pos]
        if code == 255:
            break
        if code == 0:
            pos += 1
            continue
        length = payload[pos + 1]
        out["options"][code] = payload[pos + 2 : pos + 2 + length]
        pos += 2 + length
    return out


def dhcp_v4_exchange(iface: str, mac: bytes, timeout: float, do_request: bool) -> dict | None:
    """发 DISCOVER（可选再发 REQUEST），返回最终应答解析结果；无应答返回 None。"""
    own_mac = iface_mac(iface)
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
    sock.bind((iface, 0))
    try:
        # 尽力开混杂模式：静态绑定用例里服务端可能按 chaddr 单播到「别人的」MAC
        try:
            sock.setsockopt(
                SOL_PACKET,
                PACKET_ADD_MEMBERSHIP,
                struct.pack("IHH8s", iface_index(iface), PACKET_MR_PROMISC, 0, bytes(8)),
            )
        except OSError:
            pass
        sock.settimeout(0.3)

        xid = random.randint(1, 0xFFFFFFFF)
        client_id = bytes([1]) + mac
        hostname = b"bmc-probe"
        want = bytes([1, 3, 6, 51, 54, 66, 67])
        common = [(61, client_id), (12, hostname), (55, want)]

        deadline = time.time() + timeout
        stages = [DHCPDISCOVER, DHCPREQUEST] if do_request else [DHCPDISCOVER]
        reply: dict | None = None

        for index, msg_type in enumerate(stages):
            options = list(common)
            if msg_type == DHCPREQUEST and reply is not None:
                options.append((50, reply["yiaddr"]))
                if 54 in reply["options"]:
                    options.append((54, reply["options"][54]))
            frame = udp_frame(
                own_mac, BROADCAST_MAC, ZERO_IPV4, IPV4_BROADCAST, 68, 67,
                dhcp_v4_packet(mac, xid, msg_type, options),
            )
            sock.send(frame)
            want_type = DHCPOFFER if msg_type == DHCPDISCOVER else DHCPACK
            if index + 1 == len(stages):
                # 最后一次发包：等到 timeout 为止，超时即视为无应答
                deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    frame = sock.recv(4096)
                except socket.timeout:
                    continue
                parsed = parse_dhcp(extract_udp_payload(frame))
                if not parsed or parsed.get("op") != 2 or parsed["xid"] != xid:
                    continue
                if parsed["options"].get(53, bytes([0]))[0] != want_type:
                    continue
                reply = parsed
                break
            if reply is None:
                return None
        return reply
    finally:
        sock.close()


def extract_udp_payload(frame: bytes) -> bytes:
    """从以太网帧里剥出 UDP 载荷（只要目的端口 68 的 DHCP 应答）。"""
    if len(frame) < 42 or frame[12:14] != struct.pack("!H", ETH_P_IP):
        return b""
    ip_start = 14
    ihl = (frame[ip_start] & 0x0F) * 4
    if frame[ip_start + 9] != 17:
        return b""
    udp_start = ip_start + ihl
    dst_port = struct.unpack("!H", frame[udp_start + 2 : udp_start + 4])[0]
    if dst_port != 68:
        return b""
    return frame[udp_start + 8 :]


def option_ipv4_text(raw: bytes) -> str:
    return ipv4_text(raw[:4]) if raw else ""


def option_ipv4_list(raw: bytes) -> str:
    if not raw:
        return ""
    return ",".join(ipv4_text(raw[i : i + 4]) for i in range(0, len(raw) - 3, 4))


def probe_v4(args: argparse.Namespace) -> str:
    mac = bytes(int(part, 16) for part in args.mac.split(":")) if args.mac else iface_mac(args.iface)
    reply = dhcp_v4_exchange(args.iface, mac, args.timeout, do_request=not args.no_request)
    if reply is None:
        if args.expect_absent:
            return "PASS v4 无 DHCP 应答（故障注入生效，BMC 取不到地址）"
        return "FAIL v4 " + str(args.timeout) + "s 内未收到 DHCPOFFER/ACK（BMC 取不到地址）"
    if args.expect_absent:
        return "FAIL v4 本应无应答，却拿到了地址 " + ipv4_text(reply["yiaddr"])

    options = reply["options"]
    lease = struct.unpack("!I", options[51])[0] if 51 in options and len(options[51]) == 4 else 0
    # 选项号按 RFC 2132：1=子网掩码、3=默认网关、6=DNS 服务器（别把 1 当网关，实测踩过）
    netmask = option_ipv4_text(options.get(1, b""))
    router = option_ipv4_text(options.get(3, b""))
    dns = option_ipv4_list(options.get(6, b""))
    # 选项 67 可能带结尾 NUL（实测），不剥掉会让 e2e 的 "boot=xxx@yyy" 子串断言失配
    boot_file = options.get(67, b"").rstrip(bytes([0])).decode("utf-8", "replace").strip()
    siaddr = ipv4_text(reply["siaddr"])
    detail = (
        "v4 addr=" + ipv4_text(reply["yiaddr"])
        + " router=" + (router or "未下发")
        + " mask=" + (netmask or "未下发")
        + " lease=" + str(lease)
        + " dns=" + (dns or "未下发")
    )
    if boot_file:
        detail += " boot=" + boot_file + "@" + siaddr
    if args.expect_addr and ipv4_text(reply["yiaddr"]) != args.expect_addr:
        return "FAIL " + detail + "（期望地址 " + args.expect_addr + "）"
    if args.expect_addr_prefix and not ipv4_text(reply["yiaddr"]).startswith(args.expect_addr_prefix):
        return "FAIL " + detail + "（期望地址落在 " + args.expect_addr_prefix + "* 内）"
    if args.expect_router and router != args.expect_router:
        return "FAIL " + detail + "（期望网关 " + args.expect_router + "）"
    if args.expect_lease and lease != args.expect_lease:
        return "FAIL " + detail + "（期望租约 " + str(args.expect_lease) + "）"
    return "PASS " + detail


# --------------------------------------------------------------------------- #
# DHCPv6：组播 SOLICIT → ADVERTISE（有状态下发地址）
# --------------------------------------------------------------------------- #
def dhcp_v6_message(msg_type: int, xid: int, mac: bytes, iaid: int) -> bytes:
    duid = bytes([0, 3, 0, 1]) + mac  # DUID-LL
    ia_na = struct.pack("!III", iaid, 0, 0)
    options = (
        bytes([0, 1]) + struct.pack("!H", len(duid)) + duid
        + bytes([0, 3]) + struct.pack("!H", len(ia_na)) + ia_na
        + bytes([0, 8]) + struct.pack("!H", 2) + struct.pack("!H", 0)  # elapsed-time
        + bytes([0, 6]) + struct.pack("!H", 2) + struct.pack("!H", 23)  # 请求 DNS 选项
    )
    return bytes([msg_type]) + xid.to_bytes(3, "big") + options


def parse_v6_options(payload: bytes) -> list[tuple[int, bytes]]:
    out = []
    pos = 4
    while pos + 4 <= len(payload):
        code, length = struct.unpack("!HH", payload[pos : pos + 4])
        out.append((code, payload[pos + 4 : pos + 4 + length]))
        pos += 4 + length
    return out


def v6_ia_addresses(options: list[tuple[int, bytes]]) -> list[str]:
    """从 IA_NA（选项 3）里取出 IAADDR（子选项 5）的地址。"""
    found = []
    for code, value in options:
        if code != 3 or len(value) < 12:
            continue
        pos = 12
        while pos + 4 <= len(value):
            sub_code, sub_len = struct.unpack("!HH", value[pos : pos + 4])
            sub = value[pos + 4 : pos + 4 + sub_len]
            if sub_code == 5 and len(sub) >= 16:
                found.append(ipv6_text(sub[:16]))
            pos += 4 + sub_len
    return found


def probe_v6(args: argparse.Namespace) -> str:
    index = iface_index(args.iface)
    mac = iface_mac(args.iface)
    sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, index)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.bind(("", 546))
        sock.settimeout(args.timeout)
        xid = random.randint(0, 0xFFFFFF)
        sock.sendto(
            dhcp_v6_message(1, xid, mac, random.randint(1, 0xFFFFFF)),
            ("ff02::1:2", 547, 0, index),
        )
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                break
            if len(data) < 4 or data[1:4] != xid.to_bytes(3, "big"):
                continue
            msg_type = data[0]
            if msg_type not in (2, 7):  # ADVERTISE / REPLY
                continue
            addrs = v6_ia_addresses(parse_v6_options(data))
            if args.expect_absent:
                # 有应答就必须判失败：这里曾经漏掉 expect_absent，导致「第二个客户端也拿到了地址」
                # 被当成 PASS（假通过，实测在 dhcpv6 池耗尽用例上踩到）
                return ("FAIL v6 本应无应答，却收到了 ADVERTISE（地址 "
                        + (addrs[0] if addrs else "未下发") + "）")
            if not addrs:
                return "FAIL v6 收到 ADVERTISE 但没有下发地址（IA_NA 为空）"
            if args.expect_addr_prefix and not addrs[0].startswith(args.expect_addr_prefix):
                return "FAIL v6 addr=" + addrs[0] + "（期望落在 " + args.expect_addr_prefix + "* 内）"
            return "PASS v6 addr=" + addrs[0]
    finally:
        sock.close()
    if args.expect_absent:
        return "PASS v6 无 DHCPv6 应答（故障注入生效）"
    return "FAIL v6 " + str(args.timeout) + "s 内未收到 DHCPv6 ADVERTISE"


# --------------------------------------------------------------------------- #
# RA：ICMPv6 Router Solicitation → Router Advertisement（SLAAC 前缀）
# --------------------------------------------------------------------------- #
def probe_ra(args: argparse.Namespace) -> str:
    index = iface_index(args.iface)
    mac = iface_mac(args.iface)
    sock = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_ICMPV6)
    try:
        # RS 的跳限必须是 255，否则接收端直接丢弃
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, 255)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, index)
        sock.settimeout(args.timeout)

        # 选项：源链路层地址（类型 1，长度 1，MAC）。校验和留 0 由内核填（ICMPv6 原始套接字
        # 的校验和由内核计算——实测手工用 IPV6_PKTINFO 指定链路本地源地址会被内核判 EINVAL）。
        rs = bytes([133, 0, 0, 0]) + bytes(4) + bytes([1, 1]) + mac
        sock.sendto(rs, ("ff02::2", 0, 0, index))

        deadline = time.time() + args.timeout
        prefixes: list[str] = []
        while time.time() < deadline:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                break
            if not data or data[0] != 134:  # 只认 Router Advertisement（会有邻居请求等杂包）
                continue
            pos = 16  # type/code/checksum/cur hop limit/flags/router lifetime/reachable/retrans
            while pos + 2 <= len(data):
                opt_type, opt_len = data[pos], data[pos + 1]
                if opt_len == 0:
                    break
                body = data[pos + 2 : pos + 2 + opt_len * 8 - 2]
                if opt_type == 3 and len(body) >= 30:  # Prefix Information
                    prefix_len = body[0]
                    prefix = ipv6_text(body[14:30])
                    prefixes.append(prefix + "/" + str(prefix_len))
                pos += opt_len * 8
            break
    finally:
        sock.close()

    if not prefixes:
        if args.expect_absent:
            return "PASS ra 未收到 RA（故障注入生效）"
        return "FAIL ra " + str(args.timeout) + "s 内未收到 Router Advertisement"
    detail = "ra prefixes=" + ",".join(prefixes)
    if args.expect_prefix and args.expect_prefix not in prefixes:
        return "FAIL " + detail + "（期望前缀 " + args.expect_prefix + "）"
    return "PASS " + detail


# --------------------------------------------------------------------------- #
# DNS：最小查询 / 解析（A / AAAA / PTR）
# --------------------------------------------------------------------------- #
QTYPES = {"A": 1, "AAAA": 28, "PTR": 12}


def dns_encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        out += bytes([len(label)]) + label.encode("ascii")
    return out + bytes([0])


def dns_decode_name(payload: bytes, pos: int) -> tuple[str, int]:
    """解域名，支持压缩指针。"""
    labels = []
    jumped = False
    end = pos
    while True:
        length = payload[pos]
        if length == 0:
            pos += 1
            break
        if length & 0xC0 == 0xC0:
            if not jumped:
                end = pos + 2
                jumped = True
            pos = ((length & 0x3F) << 8) | payload[pos + 1]
            continue
        labels.append(payload[pos + 1 : pos + 1 + length].decode("ascii", "replace"))
        pos += 1 + length
    return ".".join(labels), (end if jumped else pos)


def ptr_name(address: str) -> str:
    if ":" in address:
        expanded = socket.inet_pton(socket.AF_INET6, address).hex()
        return ".".join(reversed(list(expanded))) + ".ip6.arpa"
    return ".".join(reversed(address.split("."))) + ".in-addr.arpa"


def dns_server_address(server: str, family: str) -> str:
    """把 --server 归一成地址：字面量直接用，服务名（容器名）按指定协议族解析。"""
    literal_family = socket.AF_INET6 if ":" in server else socket.AF_INET
    try:
        socket.inet_pton(literal_family, server)
        return server
    except OSError:
        pass
    want = socket.AF_INET6 if family == "v6" else socket.AF_INET
    return socket.getaddrinfo(server, 53, want, socket.SOCK_DGRAM)[0][4][0]


def dns_query(server: str, name: str, qtype: str, timeout: float) -> list[str]:
    query_id = random.randint(0, 0xFFFF)
    packet = (
        struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
        + dns_encode_name(name)
        + struct.pack("!HH", QTYPES[qtype], 1)
    )
    family = socket.AF_INET6 if ":" in server else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        target = (server, 53, 0, 0) if family == socket.AF_INET6 else (server, 53)
        sock.sendto(packet, target)
        data, _ = sock.recvfrom(4096)
    finally:
        sock.close()

    header = struct.unpack("!HHHHHH", data[:12])
    # header: id, flags, qdcount, ancount, nscount, arcount（rcode 在 flags 的低 4 位，
    # 不是独立字段——曾经把 ancount 当 rcode 判断，NOERROR+0 答案被误报成 NXDOMAIN）
    rcode = header[1] & 0x000F
    if header[0] != query_id:
        raise ValueError("响应 ID 不匹配")
    if rcode == 3:
        raise ValueError("DNS 返回 NXDOMAIN：名字不存在")
    if rcode != 0:
        raise ValueError("DNS 返回错误 rcode=" + str(rcode))
    if header[3] == 0:
        raise ValueError("DNS 返回 NOERROR 但答案数为 0（NODATA：名字在但没有该类型记录）")
    pos = 12
    for _ in range(header[2]):  # 跳过 question
        _, pos = dns_decode_name(data, pos)
        pos += 4
    answers = []
    for _ in range(header[3]):
        _, pos = dns_decode_name(data, pos)
        rtype, _rclass, _ttl, rdlength = struct.unpack("!HHIH", data[pos : pos + 10])
        pos += 10
        rdata = data[pos : pos + rdlength]
        pos += rdlength
        if rtype == 1 and rdlength == 4:
            answers.append(ipv4_text(rdata))
        elif rtype == 28 and rdlength == 16:
            answers.append(ipv6_text(rdata))
        elif rtype == 12:
            name_out, _ = dns_decode_name(data, pos - rdlength)
            answers.append(name_out)
    return answers


def probe_dns(args: argparse.Namespace) -> str:
    name = ptr_name(args.addr) if args.addr else args.name
    qtype = "PTR" if args.addr else args.type
    if not name:
        return "FAIL dns 需要 --name 或 --addr"
    server = dns_server_address(args.server, args.family)
    answers = dns_query(server, name, qtype, args.timeout)
    detail = "dns " + qtype + " " + name + " @" + server + " -> " + (",".join(answers) or "空")
    if args.expect:
        wanted = [item.strip() for item in args.expect.split(",") if item.strip()]
        missing = [item for item in wanted if item not in answers]
        if missing:
            return "FAIL " + detail + "（缺少 " + ",".join(missing) + "）"
    return "PASS " + detail


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="DHCP / RA / DNS 真实协议探针")
    sub = parser.add_subparsers(dest="command", required=True)

    common = {"help": "网卡名（默认 eth0）"}
    v4 = sub.add_parser("v4", help="DHCPv4 取址（DISCOVER/REQUEST）")
    v4.add_argument("--iface", default="eth0", **common)
    v4.add_argument("--mac", default="", help="自定义 chaddr（静态绑定用例）")
    v4.add_argument("--timeout", type=float, default=6.0)
    v4.add_argument("--expect-absent", action="store_true", help="无应答才算通过")
    v4.add_argument("--no-request", action="store_true", help="只发 DISCOVER，不发 REQUEST")
    v4.add_argument("--expect-addr", default="")
    v4.add_argument("--expect-addr-prefix", default="", help="地址前缀匹配（池里取到的具体地址不固定）")
    v4.add_argument("--expect-router", default="")
    v4.add_argument("--expect-lease", type=int, default=0)

    v6 = sub.add_parser("v6", help="DHCPv6 有状态取址（SOLICIT/ADVERTISE）")
    v6.add_argument("--iface", default="eth0", **common)
    v6.add_argument("--timeout", type=float, default=6.0)
    v6.add_argument("--expect-absent", action="store_true")
    v6.add_argument("--expect-addr-prefix", default="")

    ra = sub.add_parser("ra", help="RA/SLAAC 前缀通告")
    ra.add_argument("--iface", default="eth0", **common)
    ra.add_argument("--timeout", type=float, default=6.0)
    ra.add_argument("--expect-absent", action="store_true")
    ra.add_argument("--expect-prefix", default="")

    dns = sub.add_parser("dns", help="DNS 查询（A / AAAA / PTR）")
    dns.add_argument("--server", required=True, help="DNS 服务器地址或容器名")
    dns.add_argument("--family", default="v4", choices=["v4", "v6"],
                     help="--server 是容器名时按哪个协议族解析")
    dns.add_argument("--name", default="")
    dns.add_argument("--addr", default="", help="反向解析的地址（给了就查 PTR）")
    dns.add_argument("--type", default="A", choices=sorted(QTYPES))
    dns.add_argument("--expect", default="")
    dns.add_argument("--timeout", type=float, default=5.0)

    args = parser.parse_args()
    handlers = {"v4": probe_v4, "v6": probe_v6, "ra": probe_ra, "dns": probe_dns}
    try:
        result = handlers[args.command](args)
    except Exception as exc:  # noqa: BLE001 - 探针失败就是 FAIL，细节写进输出
        print("FAIL " + args.command + ": " + type(exc).__name__ + ": " + str(exc))
        return 2
    print(result)
    return 0 if result.startswith("PASS") else 2


if __name__ == "__main__":
    raise SystemExit(main())
