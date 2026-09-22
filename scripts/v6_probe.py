#!/usr/bin/env python3
"""IPv6 探针：在「挂在服务网络里的客户端容器」内运行，对服务名做一次真实 IPv6 请求。

为什么要在容器里跑：宿主没有全局 IPv6 地址，只有启用 IPv6 的容器网络（compose 里
`enable_ipv6: true` + `fd00:30:<n>::/64`）里才有 v6 连通性。客户端用 compose 的服务名解析出
v6 地址，因此判定的是「IPv6 客户端可观测行为」，不是配置比对。

用法（由 verify_bmc_platform_e2e.py 的 IPv6 阶段调用）：
    python3 v6_probe.py <服务名> <端口> <探针类型>

探针类型：
    sntp     NTP 授时（chrony）
    http     HTTP GET（nginx）
    http_put HTTP PUT + 回读（webdav）
    tftp     TFTP RRQ（tftpd-hpa）
    syslog   UDP syslog 发送（rsyslog；落盘由 e2e 在宿主侧核对）
    smtp     SMTP 问候语（postfix）
    ssh      SSH 横幅（sftp）
    ftp      FTP 横幅 + 登录（vsftpd）
    trap     SNMPv2c Trap 发送（snmptrapd；落盘由 e2e 在宿主侧核对）
    tcp      TCP 连接级探针（samba/nfs：容器内没有 smbclient / 挂载能力）

输出一行 `PASS ...` 或 `FAIL ...`，退出码 0/2。
"""

from __future__ import annotations

import socket
import sys

NUL = bytes([0])
CRLF = bytes([13, 10])


def v6_addr(name: str) -> str:
    """把服务名解析成 IPv6 地址（失败时抛异常，由调用方转成 FAIL）。"""
    return socket.getaddrinfo(name, None, socket.AF_INET6)[0][4][0]


def udp_send(addr: str, port: int, payload: bytes, timeout: float = 4) -> bytes:
    """发一个 UDP 报文并等一个回包；超时返回空 bytes（黑洞故障模式下就是这样）。"""
    with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(payload, (addr, port, 0, 0))
        try:
            return sock.recvfrom(2048)[0]
        except socket.timeout:
            return b""


def tcp_exchange(addr: str, port: int, payload: bytes, timeout: float = 8) -> bytes:
    """建 TCP 连接、发 payload、读一次响应。"""
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect((addr, port, 0, 0))
        if payload:
            sock.sendall(payload)
        return sock.recv(4096)


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


def ber_oid(dotted: str) -> bytes:
    parts = [int(x) for x in dotted.split(".")]
    out = bytes([40 * parts[0] + parts[1]])
    for part in parts[2:]:
        chunk = [part & 0x7F]
        part >>= 7
        while part:
            chunk.insert(0, (part & 0x7F) | 0x80)
            part >>= 7
        out += bytes(chunk)
    return ber(0x06, out)


def probe_sntp(addr: str, port: int) -> str:
    """标准 SNTP 请求：回包里 leap/stratum 就是 BMC 会看到的授时状态。"""
    reply = udp_send(addr, port, bytes([0x1B]) + bytes(47))
    if not reply:
        return f"FAIL sntp addr={addr} 无应答（v6 上收不到 NTP）"
    leap = (reply[0] >> 6) & 3
    return f"PASS sntp addr={addr} leap={leap} stratum={reply[1]}"


def probe_http(addr: str, port: int) -> str:
    head = b"GET / HTTP/1.0" + CRLF + b"Host: [" + addr.encode() + b"]" + CRLF + CRLF
    data = tcp_exchange(addr, port, head)
    status = data.split(CRLF)[0].decode("utf-8", "replace") if data else "空响应"
    ok = data.startswith(b"HTTP/")
    return f"{'PASS' if ok else 'FAIL'} http addr={addr} {status}"


def probe_http_put(addr: str, port: int) -> str:
    body = b"v6-probe-payload"
    head = (
        b"PUT /v6-probe.txt HTTP/1.0"
        + CRLF
        + b"Host: ["
        + addr.encode()
        + b"]"
        + CRLF
        + b"Content-Length: "
        + str(len(body)).encode()
        + CRLF
        + CRLF
    )
    put = tcp_exchange(addr, port, head + body)
    get_head = b"GET /v6-probe.txt HTTP/1.0" + CRLF + b"Host: [" + addr.encode() + b"]" + CRLF + CRLF
    got = tcp_exchange(addr, port, get_head)
    ok = put.startswith(b"HTTP/") and body in got
    first = put.split(CRLF)[0].decode("utf-8", "replace") if put else "空响应"
    return f"{'PASS' if ok else 'FAIL'} http_put addr={addr} {first} 回读一致={body in got}"


def probe_tftp(addr: str, port: int) -> str:
    # 用 IPv4 阶段已经放进 TFTP 根目录的文件（tftpd 阶段会写入 e2e_tftp.bin）
    req = bytes([0, 1]) + b"e2e_tftp.bin" + NUL + b"octet" + NUL
    data = udp_send(addr, port, req, timeout=8)
    if not data:
        return f"FAIL tftp addr={addr} 无应答（v6 上取不到文件）"
    opcode = data[1]
    ok = opcode == 3  # DATA
    return f"{'PASS' if ok else 'FAIL'} tftp addr={addr} opcode={opcode} 字节={len(data)}"


def probe_syslog(addr: str, port: int) -> str:
    # 落盘由 e2e 在宿主侧核对归档文件，这里只证明 v6 报文能发到服务
    udp_send(addr, port, b"<134>v6probe bmc-v6: ipv6 syslog probe")
    return f"PASS syslog addr={addr} 已发送（落盘由宿主侧核对）"


def probe_smtp(addr: str, port: int) -> str:
    banner = tcp_exchange(addr, port, b"")
    ok = banner.startswith(b"220")
    return f"{'PASS' if ok else 'FAIL'} smtp addr={addr} {banner.strip().decode('utf-8', 'replace')[:60]}"


def probe_ssh(addr: str, port: int) -> str:
    banner = tcp_exchange(addr, port, b"")
    ok = banner.startswith(b"SSH-")
    return f"{'PASS' if ok else 'FAIL'} ssh addr={addr} {banner.strip().decode('utf-8', 'replace')[:60]}"


def probe_ftp(addr: str, port: int) -> str:
    banner = tcp_exchange(addr, port, b"")
    if not banner.startswith(b"220"):
        return f"FAIL ftp addr={addr} 无横幅"
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
        sock.settimeout(8)
        sock.connect((addr, port, 0, 0))
        sock.recv(256)
        # 账号与 e2e 的 vsftpd 阶段一致（CFG_VSFTPD.local_users）
        sock.sendall(b"USER bmce2e" + CRLF)
        sock.recv(256)
        sock.sendall(b"PASS ChangeMe123" + CRLF)
        reply = sock.recv(256).decode("utf-8", "replace")
    ok = reply.startswith("230")
    return f"{'PASS' if ok else 'FAIL'} ftp addr={addr} 登录={reply.strip()[:40]}"


def probe_trap(addr: str, port: int) -> str:
    """SNMPv2c Trap（BER 最小实现，与 e2e 主脚本同一套编码）。"""
    varbind = ber(0x30, ber_oid("1.3.6.1.4.1.99999.1") + ber(0x04, b"v6probe"))
    pdu = ber(
        0xA7,
        ber(0x02, bytes([1])) + ber(0x02, NUL) + ber(0x02, NUL) + ber(0x30, varbind),
    )
    msg = ber(0x30, ber(0x02, bytes([1])) + ber(0x04, b"public") + pdu)
    udp_send(addr, port, msg)
    return f"PASS trap addr={addr} 已发送（落盘由宿主侧核对）"


def probe_tcp(addr: str, port: int) -> str:
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
        sock.settimeout(6)
        sock.connect((addr, port, 0, 0))
    return f"PASS tcp addr={addr} 端口 {port} 可连接（连接级探针）"


PROBES = {
    "sntp": probe_sntp,
    "http": probe_http,
    "http_put": probe_http_put,
    "tftp": probe_tftp,
    "syslog": probe_syslog,
    "smtp": probe_smtp,
    "ssh": probe_ssh,
    "ftp": probe_ftp,
    "trap": probe_trap,
    "tcp": probe_tcp,
}


def main() -> int:
    if len(sys.argv) != 4:
        print("FAIL 用法: v6_probe.py <服务名> <端口> <探针类型>")
        return 2
    service, port_text, kind = sys.argv[1], sys.argv[2], sys.argv[3]
    probe = PROBES.get(kind)
    if probe is None:
        print(f"FAIL 未知探针类型 {kind}")
        return 2
    try:
        addr = v6_addr(service)
        result = probe(addr, int(port_text))
    except Exception as exc:  # noqa: BLE001 - 探针失败就是 FAIL，细节写进输出
        print(f"FAIL {kind} {service}: {type(exc).__name__}: {exc}")
        return 2
    print(result)
    return 0 if result.startswith("PASS") else 2


if __name__ == "__main__":
    raise SystemExit(main())
