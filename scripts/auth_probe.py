#!/usr/bin/env python3
"""认证探针：RADIUS 与 LDAP 的真实协议客户端，在「挂在服务网络里的客户端容器」内运行。

为什么手写而不装现成客户端（radtest / ldapsearch）：
- 判定口径要与「BMC 视角」一致——BMC 拿到的是 Access-Accept/Reject 与其中的**属性值**（典型是
  Tunnel-Private-Group-Id 这类 VLAN 下发），现成客户端只回显它自己挑出来的摘要；
- 故障注入要验「本该没有应答却超时」「属性值被篡改」这类差异，需要精确控制请求内容与超时口径；
- 探针容器（平台镜像）里没有 freeradius-utils / ldap-utils，装它们只为了跑测试不值当。

实现：
    radius  Access-Request（User-Password 按 RFC 2865 用 MD5 加密），校验响应 Authenticator
            （校验通过才证明共享密钥一致），解析 Access-Accept 的回复属性
    ldap    BindRequest（simple）+ SearchRequest，解析 BindResponse 的 resultCode 与
            SearchResultEntry 的属性值；--tls 走 LDAPS

用法（由 verify_bmc_platform_e2e.py 的认证阶段调用）：
    python3 auth_probe.py radius --host freeradius --secret bmc-radius-secret \\
        --user bmcuser --password ChangeMe123 [--expect accept|reject|timeout] \\
        [--expect-attr Tunnel-Private-Group-Id=100] [--min-delay 3]
    python3 auth_probe.py ldap --host slapd --base dc=bmc,dc=lab --bind-dn cn=admin,dc=bmc,dc=lab \\
        --bind-password ChangeMe123 [--expect bind-ok|bind-fail] [--search '(uid=bmcuser)'] \\
        [--expect-attr cn=bmcuser] [--tls]

输出一行 `PASS ...` 或 `FAIL ...`，退出码 0/2（与 v6_probe.py / dhcp_probe.py 一致）。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import socket
import ssl
import struct
import sys
import time

RADIUS_ACCESS_REQUEST = 1
RADIUS_ACCESS_ACCEPT = 2
RADIUS_ACCESS_REJECT = 3

ATTR_USER_NAME = 1
ATTR_USER_PASSWORD = 2
ATTR_NAS_IP_ADDRESS = 4
ATTR_TUNNEL_TYPE = 64
ATTR_TUNNEL_MEDIUM_TYPE = 65
ATTR_TUNNEL_PRIVATE_GROUP_ID = 81

REPLY_ATTR_NAMES = {
    ATTR_TUNNEL_TYPE: "Tunnel-Type",
    ATTR_TUNNEL_MEDIUM_TYPE: "Tunnel-Medium-Type",
    ATTR_TUNNEL_PRIVATE_GROUP_ID: "Tunnel-Private-Group-Id",
}

TUNNEL_TYPE_NAMES = {1: "PPTP", 2: "L2F", 3: "L2TP", 13: "VLAN"}
TUNNEL_MEDIUM_NAMES = {6: "IEEE-802"}


# --------------------------------------------------------------------------- #
# RADIUS
# --------------------------------------------------------------------------- #
def radius_password(secret: bytes, request_auth: bytes, password: str) -> bytes:
    """RFC 2865 User-Password：按 16 字节块做 MD5(secret + 上一块密文) 异或。"""
    raw = password.encode()
    if len(raw) % 16:
        raw += bytes(16 - len(raw) % 16)
    out = b""
    previous = request_auth
    for offset in range(0, len(raw), 16):
        digest = hashlib.md5(secret + previous).digest()
        block = bytes(a ^ b for a, b in zip(raw[offset : offset + 16], digest))
        out += block
        previous = block
    return out


def radius_attribute(attr_type: int, value: bytes) -> bytes:
    return bytes([attr_type, len(value) + 2]) + value


def radius_request(secret: bytes, user: str, password: str) -> tuple[bytes, bytes]:
    """组 Access-Request，返回 (报文, RequestAuthenticator)。

    RADIUS 头是 Code(0) Id(1) Length(2-3) Authenticator(4-19)：**Id 在第 1 字节**。
    曾经把随机 Id 写到第 2 字节上，等于把 Length 的高位字节改成了随机值——报文长度非法，
    freeradius 连日志都不打就直接丢弃（表现为「认证一直超时」），排查了很久。
    """
    request_auth = os.urandom(16)
    identifier = int.from_bytes(os.urandom(1), "big")
    attributes = (
        radius_attribute(ATTR_USER_NAME, user.encode())
        + radius_attribute(ATTR_USER_PASSWORD, radius_password(secret, request_auth, password))
        + radius_attribute(ATTR_NAS_IP_ADDRESS, socket.inet_aton("0.0.0.0"))
    )
    length = 20 + len(attributes)
    return (
        bytes([RADIUS_ACCESS_REQUEST, identifier]) + struct.pack("!H", length)
        + request_auth + attributes,
        request_auth,
    )


def radius_parse(data: bytes) -> dict:
    """解析 RADIUS 响应：code、属性表（类型 -> 值）。"""
    if len(data) < 20:
        return {}
    code, _ident, length = data[0], data[1], struct.unpack("!H", data[2:4])[0]
    authenticator = data[4:20]
    attributes: dict[int, bytes] = {}
    pos = 20
    while pos + 2 <= min(length, len(data)):
        attr_type, attr_len = data[pos], data[pos + 1]
        if attr_len < 2:
            break
        attributes[attr_type] = data[pos + 2 : pos + attr_len]
        pos += attr_len
    return {"code": code, "authenticator": authenticator, "attributes": attributes}


def radius_verify_response(secret: bytes, request_auth: bytes, data: bytes) -> bool:
    """校验响应 Authenticator：只有共享密钥一致才会相等（顺带证明服务端用了同一密钥）。"""
    if len(data) < 20:
        return False
    head = data[:4] + request_auth + data[20:]
    expected = hashlib.md5(head + secret).digest()
    return expected == data[4:20]


def radius_attr_text(attr_type: int, raw: bytes) -> str:
    if attr_type in (ATTR_TUNNEL_TYPE, ATTR_TUNNEL_MEDIUM_TYPE) and len(raw) == 4:
        number = struct.unpack("!I", raw)[0]
        table = TUNNEL_TYPE_NAMES if attr_type == ATTR_TUNNEL_TYPE else TUNNEL_MEDIUM_NAMES
        return table.get(number, str(number))
    return raw.decode("utf-8", "replace").strip(chr(0))


def probe_radius(args: argparse.Namespace) -> str:
    secret = args.secret.encode()
    packet, request_auth = radius_request(secret, args.user, args.password)
    family = socket.AF_INET6 if ":" in args.host else socket.AF_INET
    target = (args.host, args.port, 0, 0) if family == socket.AF_INET6 else (args.host, args.port)
    sock = socket.socket(family, socket.SOCK_DGRAM)
    started = time.time()
    try:
        sock.settimeout(args.timeout)
        sock.sendto(packet, target)
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            data = b""
    finally:
        sock.close()
    elapsed = time.time() - started

    if not data:
        if args.expect == "timeout":
            return "PASS radius 无应答（{:.1f}s 超时，故障注入生效）".format(elapsed)
        return "FAIL radius {:.1f}s 内无应答（共享密钥不匹配或服务不可用）".format(elapsed)

    parsed = radius_parse(data)
    code = parsed.get("code")
    names = {2: "Access-Accept", 3: "Access-Reject", 11: "Access-Challenge"}
    detail = "radius code={} ({}) {:.2f}s".format(code, names.get(code, "未知"), elapsed)
    if parsed.get("attributes"):
        shown = []
        for attr_type, raw in parsed["attributes"].items():
            name = REPLY_ATTR_NAMES.get(attr_type)
            if name:
                shown.append("{}={}".format(name, radius_attr_text(attr_type, raw)))
        if shown:
            detail += " " + " ".join(shown)

    if not radius_verify_response(secret, request_auth, data):
        return "FAIL " + detail + "（响应 Authenticator 校验失败：共享密钥与服务端不一致）"

    if args.expect == "timeout":
        return "FAIL 本应无应答，却收到了 " + detail
    if args.expect == "accept" and code != RADIUS_ACCESS_ACCEPT:
        return "FAIL " + detail + "（期望 Access-Accept）"
    if args.expect == "reject" and code != RADIUS_ACCESS_REJECT:
        return "FAIL " + detail + "（期望 Access-Reject）"
    if args.expect_attr:
        want_name, want_value = args.expect_attr.split("=", 1)
        got = None
        for attr_type, raw in parsed["attributes"].items():
            if REPLY_ATTR_NAMES.get(attr_type) == want_name:
                got = radius_attr_text(attr_type, raw)
        if got != want_value:
            return "FAIL " + detail + "（期望 {}={}，实际 {}）".format(want_name, want_value, got)
    if args.min_delay and elapsed < args.min_delay:
        return "FAIL " + detail + "（期望至少延迟 {}s）".format(args.min_delay)
    return "PASS " + detail


# --------------------------------------------------------------------------- #
# LDAP（最小 BER 实现：够 bind + search 用）
# --------------------------------------------------------------------------- #
def ber_len(n: int) -> bytes:
    if n < 128:
        return bytes([n])
    out = b""
    while n:
        out = bytes([n & 0xFF]) + out
        n >>= 8
    return bytes([0x80 | len(out)]) + out


def ber(tag: int, payload: bytes) -> bytes:
    return bytes([tag]) + ber_len(len(payload)) + payload


def ber_int(value: int) -> bytes:
    if value == 0:
        return ber(0x02, bytes([0]))
    out = b""
    while value:
        out = bytes([value & 0xFF]) + out
        value >>= 8
    if out[0] & 0x80:
        out = bytes([0]) + out
    return ber(0x02, out)


def ber_enum(value: int) -> bytes:
    return ber(0x0A, bytes([value]))


def ber_str(value: str) -> bytes:
    return ber(0x04, value.encode())


def ber_bool(value: bool) -> bytes:
    return ber(0x01, bytes([0xFF if value else 0x00]))


def ldap_message(message_id: int, protocol_op: bytes) -> bytes:
    return ber(0x30, ber_int(message_id) + protocol_op)


def ldap_read_tlv(data: bytes, pos: int) -> tuple[int, bytes, int]:
    """读一个 BER TLV，返回 (tag, 内容, 下一个位置)；支持长长度。"""
    tag = data[pos]
    length = data[pos + 1]
    pos += 2
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(data[pos : pos + count], "big")
        pos += count
    return tag, data[pos : pos + length], pos + length


def ldap_result_code(data: bytes) -> int:
    """取 LDAPMessage 里第一个 protocolOp 的 resultCode（ENUMERATED）。"""
    _tag, body, _ = ldap_read_tlv(data, 0)
    pos = 0
    _id_tag, _id_body, pos = ldap_read_tlv(body, pos)
    _op_tag, op_body, _ = ldap_read_tlv(body, pos)
    code_tag, code_body, _ = ldap_read_tlv(op_body, 0)
    if code_tag != 0x0A:
        return -1
    return code_body[0]


def ldap_entries(data: bytes) -> list[tuple[str, dict[str, list[str]]]]:
    """从（可能拼接了多条 LDAPMessage 的）响应里抽出 SearchResultEntry 的 DN 与属性。"""
    entries: list[tuple[str, dict[str, list[str]]]] = []
    pos = 0
    while pos < len(data):
        _tag, body, pos = ldap_read_tlv(data, pos)
        inner = 0
        _id_tag, _id_body, inner = ldap_read_tlv(body, inner)
        op_tag, op_body, _ = ldap_read_tlv(body, inner)
        if op_tag != 0x64:  # SearchResultEntry
            continue
        cursor = 0
        _dn_tag, dn_body, cursor = ldap_read_tlv(op_body, cursor)
        attrs: dict[str, list[str]] = {}
        _attrs_tag, attrs_body, _ = ldap_read_tlv(op_body, cursor)
        attr_pos = 0
        while attr_pos < len(attrs_body):
            _seq_tag, seq_body, attr_pos = ldap_read_tlv(attrs_body, attr_pos)
            item = 0
            _name_tag, name_body, item = ldap_read_tlv(seq_body, item)
            _vals_tag, vals_body, _ = ldap_read_tlv(seq_body, item)
            values = []
            val_pos = 0
            while val_pos < len(vals_body):
                _v_tag, v_body, val_pos = ldap_read_tlv(vals_body, val_pos)
                values.append(v_body.decode("utf-8", "replace"))
            attrs[name_body.decode("utf-8", "replace")] = values
        entries.append((dn_body.decode("utf-8", "replace"), attrs))
    return entries


def ldap_exchange(host: str, port: int, payloads: list[bytes], timeout: float,
                  use_tls: bool) -> bytes:
    """建立连接（可选 TLS）后顺序发送多个请求，返回拼接的响应字节。"""
    sock = socket.create_connection((host, port), timeout=timeout)
    if use_tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        sock = context.wrap_socket(sock, server_hostname=host)
    try:
        collected = b""
        for payload in payloads:
            sock.sendall(payload)
            collected += sock.recv(8192)
        return collected
    finally:
        sock.close()


def probe_ldap(args: argparse.Namespace) -> str:
    if args.expect == "timeout":
        try:
            data = ldap_exchange(args.host, args.port, [ldap_bind(args, args.bind_password)],
                                 args.timeout, args.tls)
        except (socket.timeout, OSError, ssl.SSLError) as exc:
            return "PASS ldap 连接/应答失败（{}，故障注入生效）".format(type(exc).__name__)
        code = ldap_result_code(data) if data else -1
        return "FAIL 本应无应答，却拿到了 bind 结果 resultCode={}".format(code)

    payloads = [ldap_bind(args, args.bind_password)]
    if args.search and args.expect == "bind-ok":
        payloads.append(ldap_search(args))
    try:
        data = ldap_exchange(args.host, args.port, payloads, args.timeout, args.tls)
    except (socket.timeout, OSError, ssl.SSLError) as exc:
        return "FAIL ldap 连接失败：{}: {}".format(type(exc).__name__, exc)

    if not data:
        return "FAIL ldap 无响应"
    code = ldap_result_code(data)
    names = {0: "success", 32: "noSuchObject", 49: "invalidCredentials", 50: "insufficientAccess"}
    detail = "ldap bind {} resultCode={}".format(
        "LDAPS" if args.tls else "LDAP", code) + " ({})".format(names.get(code, "未知"))
    if args.expect == "bind-ok" and code != 0:
        return "FAIL " + detail + "（期望 success）"
    if args.expect == "bind-fail" and code == 0:
        return "FAIL " + detail + "（期望认证失败）"

    if args.search and args.expect == "bind-ok":
        entries = ldap_entries(data)
        if not entries:
            return "FAIL " + detail + "（搜索未返回任何条目）"
        dn, attrs = entries[0]
        detail += " 命中={} ".format(dn) + ",".join(sorted(attrs)[:6])
        if args.expect_attr:
            want_name, want_value = args.expect_attr.split("=", 1)
            got = attrs.get(want_name, [])
            if want_value not in got:
                return "FAIL " + detail + "（期望 {}={}，实际 {}）".format(
                    want_name, want_value, got)
    return "PASS " + detail


def ldap_bind(args: argparse.Namespace, password: str) -> bytes:
    auth = ber(0x80, password.encode())  # [0] simple
    bind_request = ber(0x60, ber_int(3) + ber_str(args.bind_dn) + auth)  # [APPLICATION 0]
    return ldap_message(1, bind_request)


def ldap_search(args: argparse.Namespace) -> bytes:
    filter_bytes = ber(0xA3, ber_str(args.filter.strip("()").split("=", 1)[0])
                       + ber_str(args.filter.strip("()").split("=", 1)[1]))
    search_request = ber(
        0x63,  # [APPLICATION 3]
        ber_str(args.base)
        + ber_enum(2)  # wholeSubtree
        + ber_enum(0)  # neverDerefAliases
        + ber_int(0)
        + ber_int(0)
        + ber_bool(False)
        + filter_bytes
        + ber(0x30, ber_str("cn") + ber_str("uid") + ber_str("mail")),
    )
    return ldap_message(2, search_request)


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="RADIUS / LDAP 真实协议探针")
    sub = parser.add_subparsers(dest="command", required=True)

    radius = sub.add_parser("radius", help="RADIUS 认证")
    radius.add_argument("--host", required=True)
    radius.add_argument("--port", type=int, default=1812)
    radius.add_argument("--secret", required=True)
    radius.add_argument("--user", default="bmcuser")
    radius.add_argument("--password", default="ChangeMe123")
    radius.add_argument("--expect", default="accept", choices=["accept", "reject", "timeout"])
    radius.add_argument("--expect-attr", default="", help="形如 Tunnel-Private-Group-Id=100")
    radius.add_argument("--min-delay", type=float, default=0.0)
    radius.add_argument("--timeout", type=float, default=6.0)

    ldap = sub.add_parser("ldap", help="LDAP bind / search")
    ldap.add_argument("--host", required=True)
    ldap.add_argument("--port", type=int, default=389)
    ldap.add_argument("--bind-dn", required=True)
    ldap.add_argument("--bind-password", default="")
    ldap.add_argument("--base", default="")
    ldap.add_argument("--search", default="", help="形如 (uid=bmcuser)")
    ldap.add_argument("--expect", default="bind-ok", choices=["bind-ok", "bind-fail", "timeout"])
    ldap.add_argument("--expect-attr", default="", help="形如 cn=bmcuser")
    ldap.add_argument("--tls", action="store_true")
    ldap.add_argument("--timeout", type=float, default=6.0)

    args = parser.parse_args()
    try:
        result = probe_radius(args) if args.command == "radius" else probe_ldap(args)
    except Exception as exc:  # noqa: BLE001 - 探针失败就是 FAIL，细节写进输出
        print("FAIL " + args.command + ": " + type(exc).__name__ + ": " + str(exc))
        return 2
    print(result)
    return 0 if result.startswith("PASS") else 2


if __name__ == "__main__":
    raise SystemExit(main())
