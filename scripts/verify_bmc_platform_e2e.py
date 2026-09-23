"""BMC 服务管理平台实机验收套件（在部署目标机上运行，脚本内通过 127.0.0.1 访问平台与服务）。

在目标机上运行（脚本内部通过 127.0.0.1 访问平台与服务容器）：

    python3 verify_bmc_platform_e2e.py              # 全部阶段
    python3 verify_bmc_platform_e2e.py --phase nginx # 只跑某个阶段

覆盖范围：
  - 平台基础：健康检查、JWT 登录、12 服务注册表、容器运行状态
  - AC11 各服务特性配置真实生效：chrony(NTP 应答)/nginx(上传下载+Basic认证)/
    rsyslog(按IP日期归档)/webdav(PUT)/sftp(密码登录)/vsftpd(本地用户上传)/
    tftpd-hpa(文件下载)/samba(共享读写)/nfs-ganesha(NFSv4 挂载)/
    snmptrapd(Trap 接收落盘)/postfix(SMTP 投递)/dhcp(DHCPv4 取址 + RA/SLAAC + DNS A/AAAA/PTR)
  - 故障注入真实生效：chrony(stratum_16/fake_offset)、nginx(500/503/限速)、
    rsyslog(黑洞丢弃)、webdav(423/507)、dhcp(池耗尽/黑洞/错误网关/RA错误前缀/短租约)
  - AC12 rsyslog 日志浏览接口（/data/tree、/data/content 关键字过滤）
  - AC13 secret 字段脱敏与掩码回填、审计不含明文
  - AC14 nginx HTTPS（自签证书）可访问
  - 前端 SPA 静态分发
  - AC1–AC4 配置版本历史与一键回滚（列表/详情脱敏/回滚真实生效/超限裁剪）
  - IPv6：容器网络内真实 v6 客户端（scripts/v6_probe.py）
  - DHCP：同网络客户端容器内的真实 DHCP/DHCPv6/RA/DNS 报文（scripts/dhcp_probe.py）

凭据不写在脚本里：优先取环境变量 BMC_ADMIN_EMAIL/BMC_ADMIN_PASSWORD，
其次从平台容器环境变量 FIRST_SUPERUSER/FIRST_SUPERUSER_PASSWORD 读取。

兼容目标机自带的 Python 3.9（运行时联合类型注解需靠 future import 延迟求值）。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import socket
import ssl
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "http://127.0.0.1:18080"
PLATFORM_CONTAINER = "bmc-platform-backend"
NTP_EPOCH_DELTA = 2208988800

# 各服务发布到宿主机的端口：统一范围 18101–18112（compose.yaml 同时为 BMC 侧保留了标准端口
# 123/69/162/514/25/445/2049/21；套件按统一范围探测，避免与宿主机自带服务冲突）
PORT_NGINX_HTTP = 18102
PORT_NGINX_HTTPS = 18103
PORT_WEBDAV = 18105
PORT_SFTP = 18106
PORT_FTP = 18107
PORT_TFTP = 18108
PORT_SMB = 18109
PORT_NFS = 18110
PORT_SNMPTRAP = 18111
PORT_SMTP = 18112
PORT_SYSLOG = 18104

# 每服务保留的配置版本上限，与后端 settings.CONFIG_VERSION_LIMIT 默认值一致
VERSION_LIMIT = 20


def host_address() -> str:
    """本机对外 IPv4（BMC 设备从局域网访问服务时用的就是这个地址）。

    UDP 探测统一走真实网卡地址而非 127.0.0.1：经 Docker 端口映射的回环路径下，
    "服务端另起临时端口应答"的协议（TFTP 最典型）会因 NAT 会话匹配不上而收不到
    回包——实测回环地址无应答、本机网卡地址正常。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 不发包，只让内核按路由表选出源地址
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:  # noqa: BLE001
        return "127.0.0.1"
    finally:
        sock.close()


HOST_ADDR = host_address()

EMAIL = ""
PASSWORD = ""

results: list[tuple[str, str, str]] = []
failures: list[str] = []


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #
def record(name: str, ok: bool, detail: str = "") -> bool:
    """记录一条用例结果；返回 ok 便于按需提前返回。"""
    status = "PASS" if ok else "FAIL"
    results.append((name, status, detail))
    if not ok:
        failures.append(name)
    print(f"[{status}] {name}" + (f" -> {detail}" if detail else ""), flush=True)
    return ok


def sh(cmd: str, timeout: int = 60) -> str:
    """在目标机执行 shell 命令并合并输出（测试脚本自身使用）。"""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return (r.stdout + r.stderr).strip()


def api(method: str, path: str, token: str | None = None, form: dict | None = None,
        body: dict | None = None, timeout: int = 25, raw: bool = False):
    """调用平台 REST 接口，返回 (status, 文本或字节)。"""
    url = BASE_URL + path
    data = None
    headers = {}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            return resp.status, payload if raw else payload.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        payload = e.read()
        return e.code, payload if raw else payload.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def put_config(token: str, service: str, values: dict,
               retries: int = 6, interval: float = 3.0) -> tuple[int, bool, str]:
    """提交服务配置，返回 (status, applied, 原始响应)。

    reload_mode=restart 的服务（rsyslog/webdav/sftp 等）在 reload 时进程会短暂退出，
    紧接着的 PUT 可能撞上重启窗口拿到 502；这类失败是时序问题而非配置问题，自动重试。
    """
    result = (0, None, "")
    for _ in range(retries):
        st, resp = api("PUT", f"/api/v1/services/{service}/config", token=token,
                       body={"values": values})
        try:
            applied = json.loads(resp).get("applied")
        except Exception:  # noqa: BLE001
            applied = None
        result = (st, applied, resp)
        if st != 502:
            return result
        time.sleep(interval)
    return result


def wait_for(predicate, timeout: float = 40, interval: float = 2, label: str = ""):
    """轮询直到 predicate 返回真值或超时；返回最后一次取值（真值或 None）。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(interval)
    if label:
        print(f"    (等待 {label} 超时，最后取值: {last})", flush=True)
    return None


def load_credentials() -> None:
    """从环境变量或平台容器环境变量取超管凭据，避免脚本内硬编码口令。"""
    global EMAIL, PASSWORD
    import os

    EMAIL = os.environ.get("BMC_ADMIN_EMAIL", "")
    PASSWORD = os.environ.get("BMC_ADMIN_PASSWORD", "")
    if EMAIL and PASSWORD:
        return
    env_dump = sh(
        f"docker inspect {PLATFORM_CONTAINER} "
        "--format '{{range .Config.Env}}{{println .}}{{end}}'"
    )
    env_map = dict(
        line.split("=", 1) for line in env_dump.splitlines() if "=" in line
    )
    EMAIL = EMAIL or env_map.get("FIRST_SUPERUSER", "")
    PASSWORD = PASSWORD or env_map.get("FIRST_SUPERUSER_PASSWORD", "")
    if not EMAIL or not PASSWORD:
        raise SystemExit(
            "无法获取平台超管凭据：请设置 BMC_ADMIN_EMAIL / BMC_ADMIN_PASSWORD，"
            f"或确认容器 {PLATFORM_CONTAINER} 环境变量 FIRST_SUPERUSER 存在"
        )


def login() -> str:
    """登录并返回 JWT。"""
    st, resp = api("POST", "/api/v1/login/access-token", form={"username": EMAIL, "password": PASSWORD})
    if st != 200:
        raise SystemExit(f"登录失败: status={st} {resp[:200]}")
    return json.loads(resp)["access_token"]


def container_running(name: str) -> bool:
    return sh(f"docker inspect -f '{{{{.State.Running}}}}' {name}") == "true"


def exec_in(container: str, cmd: str, timeout: int = 60) -> str:
    return sh(f"docker exec {container} sh -c {json.dumps(cmd)}", timeout=timeout)


# --------------------------------------------------------------------------- #
# 协议客户端
# --------------------------------------------------------------------------- #
def ntp_query(host: str = HOST_ADDR, port: int = 18101, timeout: float = 5):
    """发标准 SNTP 请求，返回 (leap, stratum, 应答时间与本地时钟差秒数)。"""
    packet = b"\x23" + b"\x00" * 47
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (host, port))
        data, _ = sock.recvfrom(1024)
    finally:
        sock.close()
    leap = (data[0] >> 6) & 0x03
    stratum = data[1]
    seconds = struct.unpack("!I", data[40:44])[0]
    frac = struct.unpack("!I", data[44:48])[0]
    served = (seconds - NTP_EPOCH_DELTA) + frac / 2**32
    return leap, stratum, served - time.time()


def tftp_get(host: str, port: int, filename: str, timeout: float = 8) -> bytes:
    """TFTP RRQ 读文件（单块足够的小文件）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(b"\x00\x01" + filename.encode() + b"\x00" + b"octet" + b"\x00", (host, port))
        data, addr = sock.recvfrom(4096)
        if data[:2] == b"\x00\x05":
            raise RuntimeError(f"TFTP ERROR code={struct.unpack('!H', data[2:4])[0]} {data[4:].rstrip(bytes(1))!r}")
        sock.sendto(b"\x00\x04" + data[2:4], addr)
        return data[4:]
    finally:
        sock.close()


def tftp_wrq_probe(host: str, port: int, filename: str, timeout: float = 8):
    """发 TFTP 写请求（WRQ），返回 (opcode, errcode)。

    opcode=5 表示被拒（errcode 见 TFTP 规范：2=access violation）；opcode=4 表示服务端
    接受了写会话——此时主动发 ERROR 中止，避免在服务端留下半截文件。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(b"\x00\x02" + filename.encode() + b"\x00" + b"octet" + b"\x00", (host, port))
        data, addr = sock.recvfrom(1024)
        opcode = struct.unpack("!H", data[:2])[0]
        if opcode == 4:
            # 已建立写会话：主动中止（code 0 = 未定义错误，仅用于收尾）
            sock.sendto(b"\x00\x05\x00\x00" + b"abort" + b"\x00", addr)
            return opcode, 0
        errcode = struct.unpack("!H", data[2:4])[0] if opcode == 5 else 0
        return opcode, errcode
    finally:
        sock.close()


def _ber_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = b""
    while n:
        body = bytes([n & 0xFF]) + body
        n >>= 8
    return bytes([0x80 | len(body)]) + body


def _ber(tag: int, payload: bytes) -> bytes:
    return bytes([tag]) + _ber_len(len(payload)) + payload


def _ber_int(value: int) -> bytes:
    body = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    return _ber(0x02, body)


def _ber_oid(dotted: str) -> bytes:
    parts = [int(p) for p in dotted.split(".")]
    body = bytes([parts[0] * 40 + parts[1]])
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.append((p & 0x7F) | 0x80)
            p >>= 7
        body += bytes(reversed(chunk))
    return _ber(0x06, body)


def _ber_str(value: str) -> bytes:
    return _ber(0x04, value.encode())


def snmp_v2c_trap(host: str, port: int, community: str, marker: str) -> None:
    """发送一条 SNMPv2c Trap（coldStart + 一个字符串 varbind 作为识别标记）。"""
    varbind_trap_oid = _ber(
        0x30, _ber_oid("1.3.6.1.6.3.1.1.4.1.0") + _ber_oid("1.3.6.1.6.3.1.1.5.1")
    )
    varbind_marker = _ber(
        0x30, _ber_oid("1.3.6.1.4.1.99999.1.1.0") + _ber_str(marker)
    )
    varbinds = _ber(0x30, varbind_trap_oid + varbind_marker)
    pdu = _ber(0xA7, _ber_int(20260921) + _ber_int(0) + _ber_int(0) + varbinds)
    message = _ber(0x30, _ber_int(1) + _ber_str(community) + pdu)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(message, (host, port))
    finally:
        sock.close()


def smtp_banner(host: str, port: int, timeout: float = 8) -> str:
    """读取 SMTP 服务的 220 问候语（直连读一行，不依赖 curl 的 SMTP 支持）。"""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        return sock.recv(512).decode("utf-8", "replace").strip()


def smtp_send(host: str, port: int, sender: str, rcpt: str, marker: str, timeout: float = 15):
    """SMTP 投递一封测试邮件，返回 (是否 250 接受, 会话记录)。"""
    import smtplib

    body = f"Subject: BMC E2E {marker}\r\nFrom: {sender}\r\nTo: {rcpt}\r\n\r\n{marker}\r\n"
    try:
        with smtplib.SMTP(host, port, timeout=timeout) as s:
            code, _ = s.ehlo("bmc-e2e")
            code, resp = s.mail(sender)
            if code != 250:
                return False, f"MAIL 被拒: {code} {resp}"
            code, resp = s.rcpt(rcpt)
            if code != 250:
                return False, f"RCPT 被拒: {code} {resp}"
            code, resp = s.data(body)
            return code == 250, f"DATA: {code} {resp[:120]}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def smtp_send_starttls(host: str, port: int, sender: str, rcpt: str, marker: str,
                       timeout: float = 20):
    """经 STARTTLS 投递一封测试邮件，返回 (是否 250 接受, 会话记录)。

    用于验证 force_tls 故障模式：该模式下明文必须被拒、TLS 客户端必须仍能投递。
    """
    import smtplib
    import ssl as _ssl

    body = f"Subject: BMC E2E TLS {marker}\r\nFrom: {sender}\r\nTo: {rcpt}\r\n\r\n{marker}\r\n"
    try:
        with smtplib.SMTP(host, port, timeout=timeout) as s:
            s.ehlo("bmc-e2e")
            code, resp = s.starttls(context=_ssl._create_unverified_context())
            if code != 220:
                return False, f"STARTTLS 被拒: {code} {resp}"
            s.ehlo("bmc-e2e")
            code, resp = s.mail(sender)
            if code != 250:
                return False, f"MAIL 被拒: {code} {resp}"
            code, resp = s.rcpt(rcpt)
            if code != 250:
                return False, f"RCPT 被拒: {code} {resp}"
            code, resp = s.data(body)
            return code == 250, f"DATA: {code} {resp[:120]}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def http_get(url: str, timeout: int = 15, insecure: bool = False, auth: tuple | None = None):
    """GET 请求，返回 (status, body 文本)。"""
    req = urllib.request.Request(url, method="GET")
    if auth:
        import base64

        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    ctx = ssl._create_unverified_context() if insecure else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def http_put(url: str, content: bytes, timeout: int = 15):
    """PUT 请求（WebDAV/nginx 上传），返回 (status, body)。"""
    req = urllib.request.Request(url, data=content, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------- #
# 各服务正向配置（与 schema 默认值一致的显式配置，保证用例可重复）
# --------------------------------------------------------------------------- #
CFG_CHRONY = {
    "servers": ["ntp.aliyun.com", "time.windows.com"],
    "allow_networks": ["0.0.0.0/0"],
    "makestep": "1.0 3",
    "rtcsync": True,
    "driftfile": "/var/lib/chrony/drift",
    "maxdistance": 6,
    "fault_mode": "none",
    "fake_time_offset": "+1y",
}
CFG_NGINX = {
    "listen_port": 80, "ssl_enabled": False, "ssl_port": 443,
    "ssl_cert_pem": "", "ssl_key_pem": "",
    "autoindex": True, "enable_upload": True, "max_upload_size_mb": 512,
    "auth_basic_enabled": False, "auth_basic_user": "bmc_admin",
    "auth_basic_password": "bmc-fixture-pass",
    "fault_mode": "none", "mock_status_code": 500, "throttle_rate_kbs": 50,
}
CFG_RSYSLOG = {
    "listen_port": 514, "protocols": "both", "log_root": "/var/log/bmc",
    "date_dir": "daily", "filename_by_ip": True,
    "file_format": "RSYSLOG_TraditionalFileFormat",
    "allowed_senders": ["0.0.0.0/0"], "fault_mode": "none",
}
CFG_WEBDAV = {
    "port": 8080, "auth_enabled": False, "users": ["bmc:ChangeMe123"],
    "read_only": False, "root_dir": "/var/lib/dav", "max_upload_mb": 1024,
    "timeout_s": 300, "fault_mode": "none",
}
CFG_SFTP = {
    "port": 22, "password_auth": True, "users": ["bmce2e:ChangeMe123"],
    "root_dir": "/data", "allow_tcp_forwarding": False, "fault_mode": "none",
}
CFG_VSFTPD = {
    "port": 21, "anonymous_enabled": False, "local_users": ["bmce2e:ChangeMe123"],
    "write_enabled": True, "pasv_min_port": 40000, "pasv_max_port": 40100,
    "chroot_local": True, "max_upload_mbps": 0, "fault_mode": "none",
}
CFG_TFTPD = {
    "port": 69, "create_enabled": True, "root_dir": "/srv/tftp",
    "blksize": 512, "timeout": 5, "fault_mode": "none",
}
CFG_SAMBA = {
    "workgroup": "WORKGROUP", "share_name": "bmc_share", "read_only": False,
    "guest_ok": False, "min_protocol": "SMB2_02", "username": "smbe2e",
    "password": "ChangeMe123", "fault_mode": "none",
}
CFG_NFS = {
    "export_path": "/data/nfs", "export_id": 1, "allowed_clients": "*",
    "access_type": "RW", "squash": "no_root_squash",
    "enable_v3": True, "enable_v4": True, "fault_mode": "none",
}
CFG_SNMPTRAPD = {
    "communities": ["public", "bmctrap"], "output_file": "/var/log/snmp/traps.log",
    "output_format": "full", "listen_address": "0.0.0.0", "fault_mode": "none",
}
CFG_POSTFIX = {
    "myhostname": "bmc-mail.local", "relayhost": "",
    "mynetworks": ["127.0.0.0/8", "192.168.0.0/16"],
    "message_size_limit_mb": 10, "mailbox_size_limit_mb": 512,
    "fault_mode": "none", "tarpit_delay_seconds": 20,
}
# DHCP 正向配置：地址池/网关/前缀必须落在 dhcp-net 的网段里（compose.yaml 的 172.30.12.0/24 +
# fd00:30:12::/64），否则 dnsmasq 会因为池不在接口子网内而拒绝服务
CFG_DHCP = {
    "domain": "bmc.lab",
    "pool_start": "172.30.12.100", "pool_end": "172.30.12.200",
    "lease_time": "12h", "gateway": "172.30.12.1",
    "dns_servers": [], "dns_records": ["bmc-01,172.30.12.10,fd00:30:12::10"],
    "static_hosts": [],
    "ra_mode": "slaac", "ipv6_prefix": "fd00:30:12::/64",
    "ipv6_pool_start": "fd00:30:12::100", "ipv6_pool_end": "fd00:30:12::200",
    "next_server": "", "boot_file": "",
    "fault_mode": "none",
}
# 宿主机经 docker 网桥访问容器，postfix 看到的源地址是网桥网关。默认 bridge 是 172.17.0.1，
# 而 compose 编排（compose.yaml）给每张网络显式分配 172.30.x/24，网关是 172.30.x.1——
# 两个网段都放进白名单，两种部署方式都能跑。
# 把它加入 mynetworks 才能中继——这一步同时验证白名单配置真实生效。
CFG_POSTFIX_RELAY = {
    **CFG_POSTFIX,
    "mynetworks": ["127.0.0.0/8", "192.168.0.0/16", "172.17.0.0/16", "172.30.0.0/16"],
}

SERVICES_ALL = {
    "chrony", "nginx", "rsyslog", "webdav", "postfix", "snmptrapd",
    "sftp", "vsftpd", "tftpd-hpa", "samba", "nfs-ganesha", "dhcp",
}

# DHCP 阶段用的固定值（与 CFG_DHCP / compose.yaml 的 dhcp-net 网段一致）
DHCP_CONTAINER = "bmc-dhcp"
DHCP_NETWORK = "servicesmgt_dhcp-net"
DHCP_POOL_PREFIX = "172.30.12."
DHCP_PREFIX_V6 = "fd00:30:12::/64"
DHCP_RESERVED_MAC = "02:42:ac:1e:0c:aa"
DHCP_RESERVED_ADDR = "172.30.12.50"
DHCP_OTHER_MAC = "02:42:ac:1e:0c:99"
DHCP_LEASE_SECONDS = 12 * 3600


# --------------------------------------------------------------------------- #
# 阶段实现
# --------------------------------------------------------------------------- #
def phase_platform(token: str) -> None:
    """平台 API、认证、注册表与容器状态。"""
    print("\n>>> 阶段 1: 平台 API、JWT 认证与注册表")
    st, resp = api("GET", "/api/v1/utils/health-check/")
    record("1.1 平台健康检查", st == 200 and "true" in resp.lower(), f"status={st}")

    st, resp = api("GET", "/api/v1/services/", token=token)
    names = {s["name"] for s in json.loads(resp).get("data", [])} if st == 200 else set()
    record("1.2 12 种 BMC 支撑服务全部注册", st == 200 and SERVICES_ALL.issubset(names),
           f"found {len(names)}: {sorted(names)}")

    not_running = [n for n in sorted(SERVICES_ALL) if not container_running(f"bmc-{n}") and not (
        n == "nfs-ganesha" and container_running("bmc-nfs"))]
    record("1.3 12 个服务容器均处于运行状态", not not_running, f"未运行: {not_running}")

    st, resp = api("GET", "/api/v1/services/chrony", token=token)
    doc = json.loads(resp) if st == 200 else {}
    record("1.4 服务详情返回 schema 与 manifest",
           st == 200 and "schema" in doc and "manifest" in doc, f"status={st}")


def phase_chrony(token: str) -> None:
    """NTP 正向授时 + 两种故障注入（以真实 NTP 应答判定，模拟 BMC 视角）。"""
    print("\n>>> 阶段 2: NTP (chrony) 正向授时与故障注入")
    st, applied, resp = put_config(token, "chrony", CFG_CHRONY)
    record("2.1 正向配置提交并生效", st == 200 and applied is True, f"status={st} {resp[:140]}")

    got = wait_for(lambda: (lambda r: r if r[0] == 0 and abs(r[2]) < 300 else None)(ntp_query()),
                   timeout=30, label="正向授时")
    record("2.2 NTP 应答已同步且时间与基准一致（leap=0）",
           bool(got), f"stratum={got[1]} offset={got[2]:.1f}s" if got else "无有效应答")

    st, applied, resp = put_config(token, "chrony", {**CFG_CHRONY, "fault_mode": "stratum_16"})
    record("2.3 故障注入 stratum_16 配置生效（容器不崩溃）",
           st == 200 and applied is True and container_running("bmc-chrony"),
           f"status={st} {resp[:140]}")
    got = wait_for(lambda: (lambda r: r if r[0] == 3 else None)(ntp_query()),
                   timeout=30, label="未同步宣告")
    record("2.4 NTP 应答宣告未同步（leap=3 / stratum 0）",
           bool(got) and got[1] == 0, f"stratum={got[1]} leap={got[0]}" if got else "仍为已同步")

    st, applied, resp = put_config(token, "chrony",
                                   {**CFG_CHRONY, "fault_mode": "fake_offset", "fake_time_offset": "+3h"})
    record("2.5 故障注入 fake_offset 配置生效", st == 200 and applied is True, f"status={st} {resp[:140]}")

    def offset_visible():
        leap, stratum, delta = ntp_query()
        return (leap, stratum, delta) if 2.5 * 3600 < delta < 3.5 * 3600 else None

    got = wait_for(offset_visible, timeout=60, label="时钟偏移 +3h")
    record("2.6 NTP 授出时间 = 基准时间 + 3 小时", bool(got),
           f"offset={got[2] / 3600:.2f}h" if got else f"实测 offset={ntp_query()[2] / 3600:.2f}h")

    st, applied, resp = put_config(token, "chrony", CFG_CHRONY)
    record("2.7 复位正向配置生效", st == 200 and applied is True, f"status={st} {resp[:140]}")
    got = wait_for(lambda: (lambda r: r if r[0] == 0 and abs(r[2]) < 300 else None)(ntp_query()),
                   timeout=60, label="偏移清除")
    record("2.8 偏移已清除，恢复正向授时", bool(got),
           f"offset={got[2]:.1f}s" if got else f"实测 offset={ntp_query()[2] / 3600:.2f}h")


def phase_nginx(token: str) -> None:
    """HTTP 正向文件交互、Basic 认证、故障注入与 HTTPS。"""
    print("\n>>> 阶段 3: HTTP/HTTPS (nginx) 正向交互、Basic 认证与故障注入")
    st, applied, resp = put_config(token, "nginx", CFG_NGINX)
    record("3.1 正向配置提交并生效", st == 200 and applied is True, f"status={st} {resp[:140]}")
    time.sleep(3)

    fw = "BMC_FIRMWARE_E2E_" + "A" * 1024
    st, _ = http_put(f"http://127.0.0.1:{PORT_NGINX_HTTP}/bmc_fw_e2e.bin", fw.encode())
    record("3.2 上传固件文件（HTTP PUT）", st in (200, 201, 204), f"HTTP {st}")

    got = wait_for(lambda: (lambda r: r if r[0] == 200 and r[1] == fw else None)(
        http_get(f"http://127.0.0.1:{PORT_NGINX_HTTP}/bmc_fw_e2e.bin")), timeout=20, label="下载一致")
    record("3.3 下载固件文件且内容一致", bool(got), f"HTTP {got[0]}" if got else "内容不匹配")

    # Basic 认证（AC11 特性之一）
    st, applied, _ = put_config(token, "nginx",
                                {**CFG_NGINX, "auth_basic_enabled": True, "auth_basic_user": "bmc_admin",
                                 "auth_basic_password": "bmc-fixture-pass"})
    record("3.4 Basic 认证配置提交并生效", st == 200 and applied is True, f"status={st}")
    time.sleep(3)
    st_noauth, _ = http_get(f"http://127.0.0.1:{PORT_NGINX_HTTP}/bmc_fw_e2e.bin")
    st_auth, _ = http_get(f"http://127.0.0.1:{PORT_NGINX_HTTP}/bmc_fw_e2e.bin",
                          auth=("bmc_admin", "bmc-fixture-pass"))
    record("3.5 未认证被拒(401)、认证后放行", st_noauth == 401 and st_auth == 200,
           f"noauth={st_noauth} auth={st_auth}")

    # HTTPS（AC14，容器内自签证书兜底）
    st, applied, _ = put_config(token, "nginx", {**CFG_NGINX, "ssl_enabled": True, "ssl_port": 443})
    record("3.6 HTTPS 配置提交并生效", st == 200 and applied is True, f"status={st}")
    got = wait_for(lambda: (lambda r: r if r[0] in (200, 404) else None)(
        http_get(f"https://127.0.0.1:{PORT_NGINX_HTTPS}/", insecure=True)), timeout=25, label="HTTPS 可访问")
    record("3.7 HTTPS 经自签证书可访问（AC14）", bool(got), f"HTTP {got[0]}" if got else "不可访问")

    # 故障注入
    st, applied, _ = put_config(token, "nginx", {**CFG_NGINX, "fault_mode": "http_status",
                                                 "mock_status_code": 500})
    got = wait_for(lambda: (lambda r: r if r[0] == 500 else None)(
        http_get(f"http://127.0.0.1:{PORT_NGINX_HTTP}/bmc_fw_e2e.bin")), timeout=25, label="500 注入")
    record("3.8 故障注入：返回 500", bool(got), f"HTTP {got[0]}" if got else "未返回 500")

    put_config(token, "nginx", {**CFG_NGINX, "fault_mode": "http_status", "mock_status_code": 503})
    got = wait_for(lambda: (lambda r: r if r[0] == 503 else None)(
        http_get(f"http://127.0.0.1:{PORT_NGINX_HTTP}/bmc_fw_e2e.bin")), timeout=25, label="503 注入")
    record("3.9 故障注入：返回 503", bool(got), f"HTTP {got[0]}" if got else "未返回 503")

    put_config(token, "nginx", {**CFG_NGINX, "fault_mode": "extreme_slow", "throttle_rate_kbs": 5})
    conf = wait_for(lambda: (lambda c: c if "limit_rate 5k" in c else None)(
        exec_in("bmc-nginx", "cat /etc/nginx-bmc/nginx.conf")), timeout=25, label="限速规则")
    record("3.10 故障注入：限速规则 limit_rate 5k 已生效", bool(conf))

    # 3.11/3.12 故障注入 corrupt_content_length：声明一个远大于实际文件的 Content-Length。
    # 客户端可观测：响应里出现**两个** Content-Length（nginx 自己的 + 注入的），
    # 且请求会一直等那个永远不来的大 body（用 --max-time 兜住，避免拖死套件）。
    def content_length_headers() -> str:
        return sh(
            f"curl -s -D - -o /dev/null --max-time 8 "
            f"http://127.0.0.1:{PORT_NGINX_HTTP}/bmc_fw_e2e.bin 2>&1 | grep -ci content-length"
        ).strip()

    put_config(token, "nginx", {**CFG_NGINX, "fault_mode": "corrupt_content_length"})
    got = wait_for(lambda: (lambda n: n if n == "2" else None)(content_length_headers()),
                   timeout=40, interval=5, label="长度不符注入")
    record("3.11 故障注入：响应出现两个 Content-Length（长度不符）",
           bool(got), f"Content-Length 头数={got or content_length_headers()}")

    st, applied, _ = put_config(token, "nginx", CFG_NGINX)
    record("3.12 复位正向配置生效", st == 200 and applied is True, f"status={st}")
    got = wait_for(lambda: (lambda n: n if n == "1" else None)(content_length_headers()),
                   timeout=40, interval=5, label="复位后长度恢复")
    record("3.13 复位后恢复单个 Content-Length 且可正常下载",
           bool(got) and http_get(f"http://127.0.0.1:{PORT_NGINX_HTTP}/bmc_fw_e2e.bin")[0] == 200,
           f"Content-Length 头数={got or content_length_headers()}")
    time.sleep(3)


def phase_rsyslog(token: str) -> None:
    """Syslog 按 IP/日期归档 + 日志浏览接口 + 黑洞故障。"""
    print("\n>>> 阶段 4: Syslog (rsyslog) 归档、日志浏览接口与故障注入")
    st, applied, resp = put_config(token, "rsyslog", CFG_RSYSLOG)
    record("4.1 正向配置提交并生效", st == 200 and applied is True, f"status={st} {resp[:140]}")
    time.sleep(3)

    marker = f"SENSOR_ALERT_{int(time.time())}"
    message = f"<134>1 - bmc-node-e2e bmc_sensor - - - [{marker}] CPU Temp exceeds threshold\n"

    def send_and_locate():
        """发送一条 BMC 风格的 syslog 并定位落盘文件。

        reload_mode=restart 的服务在 reload 时会短暂停掉 rsyslogd，重启窗口内发出的
        UDP 报文会直接丢失（UDP 无重传），所以这里每轮都重发一次再检查落盘。
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(message.encode(), (HOST_ADDR, PORT_SYSLOG))
        finally:
            sock.close()
        time.sleep(2)
        hit = exec_in("bmc-rsyslog", f"grep -rl {marker} /var/log/bmc 2>/dev/null | head -n 1")
        return hit or None

    hit_file = wait_for(send_and_locate, timeout=45, interval=3, label="日志落盘")
    record("4.2 按 IP/日期归档为文件", bool(hit_file), (hit_file or "未落盘").replace("\n", " ")[:160])

    got = wait_for(lambda: (lambda t: t if t.get("entries") else None)(
        json.loads(api("GET", "/api/v1/services/rsyslog/data/tree", token=token)[1] or "{}")),
        timeout=25, label="data/tree")
    record("4.3 日志浏览接口 /data/tree 返回目录树（AC12）", bool(got),
           f"entries={len(got['entries'])}" if got else "空")

    if hit_file:
        rel = hit_file.replace("/var/log/bmc/", "").lstrip("/")
        st, resp = api("GET", f"/api/v1/services/rsyslog/data/content?subpath={urllib.parse.quote(rel)}"
                              f"&keyword={marker}", token=token)
        body = json.loads(resp) if st == 200 else {}
        lines = body.get("lines") or []
        record("4.4 日志浏览接口 /data/content 关键字过滤命中（AC12）",
               st == 200 and any(marker in line for line in lines),
               f"status={st} subpath={rel} size={body.get('size')} 命中行={len(lines)}")
    else:
        record("4.4 日志浏览接口 /data/content 关键字过滤命中（AC12）", False, "归档文件中未找到本次标记")

    # 4.5/4.6 故障注入 port_blackhole：端口仍可连（服务没死）但消息不落盘。
    # 这是**客户端可观测**的判定：只看配置里有没有 `stop` 无法区分「黑洞」与「服务挂了」。
    def archived(marker: str) -> bool:
        return bool(sh(
            f"docker exec bmc-rsyslog sh -c 'grep -rl {marker} /var/log/bmc 2>/dev/null | head -1'"
        ).strip())

    def tcp_open() -> bool:
        import socket as _socket

        try:
            with _socket.create_connection(("127.0.0.1", PORT_SYSLOG), timeout=6):
                return True
        except OSError:
            return False

    def send_syslog(marker: str) -> None:
        import socket as _socket

        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        try:
            sock.sendto(f"<134>{marker}".encode(), ("127.0.0.1", PORT_SYSLOG))
        finally:
            sock.close()

    bh_marker = f"BMC_BLACKHOLE_{int(time.time())}"
    put_config(token, "rsyslog", {**CFG_RSYSLOG, "fault_mode": "port_blackhole"})
    time.sleep(8)
    send_syslog(bh_marker)
    time.sleep(4)
    record("4.5 故障注入 port_blackhole：端口仍可连但消息不落盘",
           tcp_open() and not archived(bh_marker),
           f"TCP 可连={tcp_open()} 已落盘={archived(bh_marker)}")

    reset_marker = f"BMC_BLACKHOLE_RESET_{int(time.time())}"
    st, applied, _ = put_config(token, "rsyslog", CFG_RSYSLOG)
    record("4.6 复位正向配置生效", st == 200 and applied is True, f"status={st}")
    time.sleep(8)
    send_syslog(reset_marker)
    got = wait_for(lambda: True if archived(reset_marker) else None,
                   timeout=40, interval=5, label="复位后重新落盘")
    record("4.7 复位后消息重新落盘", bool(got), f"已落盘={archived(reset_marker)}")
    time.sleep(3)

def phase_webdav(token: str) -> None:
    """WebDAV 正向 PUT/GET 与 423/507 故障注入。"""
    print("\n>>> 阶段 5: WebDAV 正向交互与故障注入")
    st, applied, resp = put_config(token, "webdav", CFG_WEBDAV)
    record("5.1 正向配置提交并生效", st == 200 and applied is True, f"status={st} {resp[:140]}")
    time.sleep(4)

    content = b"BMC_WEBDAV_E2E_OK"
    got = wait_for(lambda: (lambda r: r if r[0] in (200, 201, 204) else None)(
        http_put(f"http://127.0.0.1:{PORT_WEBDAV}/e2e_test.txt", content)), timeout=25, label="WebDAV PUT")
    record("5.2 正向 PUT 写入文件", bool(got), f"HTTP {got[0]}" if got else "失败")
    st, body = http_get(f"http://127.0.0.1:{PORT_WEBDAV}/e2e_test.txt")
    record("5.3 正向 GET 读回内容一致", st == 200 and body == content.decode(), f"HTTP {st}")

    put_config(token, "webdav", {**CFG_WEBDAV, "fault_mode": "lock_conflict_423"})
    got = wait_for(lambda: (lambda r: r if r[0] == 423 else None)(
        http_put(f"http://127.0.0.1:{PORT_WEBDAV}/e2e_test.txt", content)), timeout=30, label="423 注入")
    record("5.4 故障注入：423 Locked（并发锁冲突）", bool(got), f"HTTP {got[0]}" if got else "未返回 423")

    put_config(token, "webdav", {**CFG_WEBDAV, "fault_mode": "quota_exceeded_507"})
    got = wait_for(lambda: (lambda r: r if r[0] == 507 else None)(
        http_put(f"http://127.0.0.1:{PORT_WEBDAV}/e2e_test.txt", content)), timeout=30, label="507 注入")
    record("5.5 故障注入：507 Insufficient Storage", bool(got), f"HTTP {got[0]}" if got else "未返回 507")

    # 5.6/5.7 故障注入 method_not_allowed_405：写入方法返回 405，读方法不受影响
    put_config(token, "webdav", {**CFG_WEBDAV, "fault_mode": "method_not_allowed_405"})
    got = wait_for(lambda: (lambda r: r if r[0] == 405 else None)(
        http_put(f"http://127.0.0.1:{PORT_WEBDAV}/e2e_test.txt", content)), timeout=30, label="405 注入")
    read_code = http_get(f"http://127.0.0.1:{PORT_WEBDAV}/e2e_test.txt")[0]
    record("5.6 故障注入：写入方法返回 405 Method Not Allowed",
           bool(got) and read_code != 405,
           f"PUT={got[0] if got else '非 405'} GET={read_code}")

    # 5.8/5.9 故障注入 auth_reject：即使带正确凭据也一律 401
    put_config(token, "webdav", {**CFG_WEBDAV, "auth_enabled": True, "fault_mode": "auth_reject"})
    got = wait_for(lambda: (lambda r: r if r[0] == 401 else None)(
        http_get(f"http://127.0.0.1:{PORT_WEBDAV}/e2e_test.txt", auth=("bmc", "ChangeMe123"))),
        timeout=30, label="401 注入")
    record("5.7 故障注入：正确凭据也被拒（401）", bool(got), f"HTTP {got[0]}" if got else "未被拒")

    st, applied, _ = put_config(token, "webdav", CFG_WEBDAV)
    record("5.8 复位正向配置生效", st == 200 and applied is True, f"status={st}")
    time.sleep(4)


def phase_sftp(token: str) -> None:
    """SFTP 密码登录与读写（AC11）。"""
    print("\n>>> 阶段 6: SFTP 密码认证与文件访问")
    st, applied, resp = put_config(token, "sftp", CFG_SFTP)
    record("6.1 用户与密码认证配置提交并生效", st == 200 and applied is True, f"status={st} {resp[:140]}")

    import paramiko

    def try_login():
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect("127.0.0.1", port=PORT_SFTP, username="bmce2e",
                           password="ChangeMe123", timeout=12, allow_agent=False, look_for_keys=False)
            sftp = client.open_sftp()
            # 服务按 sshd 的 ChrootDirectory 约束设计：家目录本身属 root 不可写，
            # 用户的实际读写区是家目录下的 data/ 子目录
            sftp.putfo(io.BytesIO(b"BMC_SFTP_E2E"), "data/e2e_sftp.txt")
            with sftp.open("data/e2e_sftp.txt") as fh:
                data = fh.read()
            sftp.close()
            client.close()
            return data == b"BMC_SFTP_E2E"
        except Exception:  # noqa: BLE001
            client.close()
            return False

    ok = wait_for(try_login, timeout=90, interval=5, label="SFTP 登录")
    record("6.2 平台下发的用户可密码登录并读写文件", bool(ok))

    put_config(token, "sftp", {**CFG_SFTP, "fault_mode": "auth_reject"})

    def expect_reject():
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect("127.0.0.1", port=PORT_SFTP, username="bmce2e",
                           password="ChangeMe123", timeout=12, allow_agent=False, look_for_keys=False)
            client.close()
            return False
        except paramiko.AuthenticationException:
            return True
        except Exception:  # noqa: BLE001
            return False

    ok = wait_for(expect_reject, timeout=60, interval=5, label="认证拒绝")
    record("6.3 故障注入：认证被拒", bool(ok))

    # 6.4–6.6 故障注入 readonly_reject：登录正常、上传被拒、读取仍可用
    put_config(token, "sftp", {**CFG_SFTP, "fault_mode": "readonly_reject"})

    def readonly_probe():
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        result = {}
        try:
            client.connect("127.0.0.1", port=PORT_SFTP, username="bmce2e",
                           password="ChangeMe123", timeout=12, allow_agent=False,
                           look_for_keys=False)
            sftp = client.open_sftp()
            try:
                sftp.putfo(io.BytesIO(b"RO_PROBE"), "data/e2e_ro_probe.txt")
                result["upload"] = "accepted"
            except Exception:  # noqa: BLE001
                result["upload"] = "rejected"
            try:
                with sftp.open("data/e2e_sftp.txt") as fh:
                    result["read"] = f"{len(fh.read())}B"
            except Exception:  # noqa: BLE001
                result["read"] = "failed"
            sftp.close()
            client.close()
        except Exception:  # noqa: BLE001
            client.close()
            return None
        # 上传必须被拒、读取必须仍可用（只读而非不可用）
        return result if result.get("upload") == "rejected" and result.get("read", "failed") != "failed" else None

    got = wait_for(readonly_probe, timeout=60, interval=5, label="只读拒绝")
    record("6.4 故障注入 readonly_reject：上传被拒但读取仍可用",
           bool(got), str(got) if got else "上传未被拒或读取也失败")

    st, applied, _ = put_config(token, "sftp", CFG_SFTP)
    record("6.5 复位正向配置生效", st == 200 and applied is True, f"status={st}")
    ok = wait_for(try_login, timeout=90, interval=5, label="复位后可读写")
    record("6.6 复位后恢复可读写", bool(ok))


def phase_vsftpd(token: str) -> None:
    """FTP 本地用户登录与上传（AC11）。"""
    print("\n>>> 阶段 7: FTP (vsftpd) 本地用户登录与上传")
    from ftplib import FTP

    st, applied, resp = put_config(token, "vsftpd", CFG_VSFTPD)
    record("7.1 本地用户配置提交并生效", st == 200 and applied is True, f"status={st} {resp[:140]}")

    def try_ftp():
        ftp = FTP()
        try:
            ftp.connect("127.0.0.1", PORT_FTP, timeout=15)
            ftp.login("bmce2e", "ChangeMe123")
            ftp.storbinary("STOR e2e_ftp.txt", io.BytesIO(b"BMC_FTP_E2E"))
            buf = io.BytesIO()
            ftp.retrbinary("RETR e2e_ftp.txt", buf.write)
            ftp.quit()
            return buf.getvalue() == b"BMC_FTP_E2E"
        except Exception:  # noqa: BLE001
            try:
                ftp.close()
            except Exception:  # noqa: BLE001
                pass
            return False

    ok = wait_for(try_ftp, timeout=90, interval=5, label="FTP 登录上传")
    record("7.2 平台下发的本地用户可登录并上传/下载", bool(ok))

    put_config(token, "vsftpd", {**CFG_VSFTPD, "fault_mode": "write_deny_550"})

    def expect_550():
        ftp = FTP()
        try:
            ftp.connect("127.0.0.1", PORT_FTP, timeout=15)
            ftp.login("bmce2e", "ChangeMe123")
            ftp.storbinary("STOR e2e_ftp_deny.txt", io.BytesIO(b"X"))
            ftp.quit()
            return False
        except Exception as e:  # noqa: BLE001
            try:
                ftp.close()
            except Exception:  # noqa: BLE001
                pass
            return "550" in str(e)

    ok = wait_for(expect_550, timeout=60, interval=5, label="550 拒写")
    record("7.3 故障注入：写入被 550 拒绝", bool(ok))

    # 7.6/7.7 故障注入 deny_pasv_data：关闭被动模式 → 登录正常但数据通道被拒
    put_config(token, "vsftpd", {**CFG_VSFTPD, "fault_mode": "deny_pasv_data"})

    def pasv_rejected():
        ftp = FTP()
        try:
            ftp.connect("127.0.0.1", PORT_FTP, timeout=15)
            ftp.login("bmce2e", "ChangeMe123")
            ftp.set_pasv(True)
            try:
                ftp.nlst()
                ftp.quit()
                return None  # 被动模式仍可用 → 故障未生效
            except Exception:  # noqa: BLE001
                ftp.close()
                return "passive-rejected"
        except Exception:  # noqa: BLE001
            try:
                ftp.close()
            except Exception:  # noqa: BLE001
                pass
            return None

    got = wait_for(pasv_rejected, timeout=60, interval=5, label="被动模式被拒")
    record("7.6 故障注入 deny_pasv_data：登录正常但被动数据通道被拒",
           bool(got), got or "被动模式仍可用")

    # 7.8/7.9 故障注入 extreme_throttle：500 B/s 限速 → 大文件在短超时内下不完。
    # 注意要用足够大的文件：小文件会被限速器的突发额度一次放完（实测 20KB 仍秒传），
    # 200KB 才能观察到「下不完」。
    put_config(token, "vsftpd", {**CFG_VSFTPD, "fault_mode": "extreme_throttle"})
    exec_in("bmc-vsftpd", "mkdir -p /data/bmce2e && dd if=/dev/urandom of=/data/bmce2e/throttle.bin bs=1024 count=200 2>/dev/null")
    time.sleep(6)

    def slow_download_incomplete():
        out = sh(f"curl -s --max-time 15 -u bmce2e:ChangeMe123 -o /dev/null "
                 f"-w '%{{size_download}}' ftp://127.0.0.1:{PORT_FTP}/throttle.bin 2>&1")
        # 限速生效时 curl 会在 15s 内被 --max-time 掐断，下载量远小于 200KB
        return out if out.isdigit() and int(out) < 200 * 1024 else None

    got = wait_for(slow_download_incomplete, timeout=60, interval=5, label="限速下大文件下不完")
    record("7.7 故障注入 extreme_throttle：限速生效（15s 内下不完 200KB）",
           bool(got), f"15s 内下载 {got} 字节" if got else "下载速度未被限制")

    st, applied, _ = put_config(token, "vsftpd", CFG_VSFTPD)
    record("7.8 复位正向配置生效", st == 200 and applied is True, f"status={st}")
    ok = wait_for(try_ftp, timeout=90, interval=5, label="复位后可读写")
    record("7.9 复位后恢复可读写", bool(ok))


def phase_tftpd(token: str) -> None:
    """TFTP 文件下载与创建拒绝（AC11）。"""
    print("\n>>> 阶段 8: TFTP (tftpd-hpa) 文件下载")
    st, applied, resp = put_config(token, "tftpd-hpa", CFG_TFTPD)
    record("8.1 配置提交并生效", st == 200 and applied is True, f"status={st} {resp[:140]}")

    marker = b"BMC_TFTP_E2E_PAYLOAD"
    exec_in("bmc-tftpd-hpa", f"mkdir -p /srv/tftp && printf '{marker.decode()}' > /srv/tftp/e2e_tftp.bin")
    time.sleep(2)

    def fetch():
        try:
            return tftp_get(HOST_ADDR, PORT_TFTP, "e2e_tftp.bin") == marker
        except Exception:  # noqa: BLE001
            return False

    ok = wait_for(fetch, timeout=45, interval=4, label="TFTP 下载")
    record("8.2 可经 TFTP 下载文件且内容一致", bool(ok))

    # 8.3/8.4 关闭「允许创建新文件」后，写请求必须被拒（用真实 WRQ 观察 ERROR 应答）
    try:
        st, applied, resp = put_config(token, "tftpd-hpa", {**CFG_TFTPD, "create_enabled": False})
        record("8.3 关闭创建开关的配置提交并生效",
               st == 200 and applied is True, f"status={st} {resp[:120]}")

        def denied():
            opcode, errcode = tftp_wrq_probe(HOST_ADDR, PORT_TFTP, "e2e_deny_probe.bin")
            return (opcode, errcode) if opcode == 5 else None

        got = wait_for(denied, timeout=45, interval=4, label="写请求被拒")
        # 判定「被拒」即可：tftpd-hpa 关闭创建后对 WRQ 回 ERROR 码 1（file not found），
        # 其它实现可能回 2（access violation）——码值属实现细节，故只断言 opcode=ERROR
        record("8.4 写请求被拒（ERROR 应答）",
               bool(got),
               f"opcode={got[0]} errcode={got[1]}（1=file not found / 2=access violation）"
               if got else "未收到 ERROR 应答")
    finally:
        # 复位：写请求必须重新被接受，否则后续阶段会踩到故障配置
        put_config(token, "tftpd-hpa", CFG_TFTPD)
        back = wait_for(lambda: (lambda r: r if r[0] == 4 else None)(
            tftp_wrq_probe(HOST_ADDR, PORT_TFTP, "e2e_reset_probe.bin")),
            timeout=45, interval=4, label="复位后写请求被接受")
        record("8.5 复位正向配置后写请求重新被接受", bool(back))

    # 8.6/8.7 故障注入 timeout_simulate：只绑回环 → 外部 RRQ 收不到应答（超时），
    # 而进程仍在、健康检查仍 healthy——这样才与「服务挂了」区分得开。
    put_config(token, "tftpd-hpa", {**CFG_TFTPD, "fault_mode": "timeout_simulate"})
    time.sleep(8)

    def external_rrq_times_out():
        try:
            tftp_get(HOST_ADDR, PORT_TFTP, "e2e_tftp.bin", timeout=6)
            return None  # 收到应答 → 故障未生效
        except Exception:  # noqa: BLE001
            return "timeout"

    got = wait_for(external_rrq_times_out, timeout=45, interval=5, label="外部 RRQ 超时")
    healthy = exec_in("bmc-tftpd-hpa", "grep -qs in.tftpd /proc/[0-9]*/comm && echo ok || echo no")
    record("8.6 故障注入 timeout_simulate：外部 RRQ 超时且进程仍健康",
           bool(got) and "ok" in healthy, f"{got} 进程={healthy.strip()}")

    st, applied, _ = put_config(token, "tftpd-hpa", CFG_TFTPD)
    record("8.7 复位正向配置生效", st == 200 and applied is True, f"status={st}")
    ok = wait_for(fetch, timeout=45, interval=4, label="复位后可下载")
    record("8.8 复位后恢复可下载", bool(ok))


def phase_samba(token: str) -> None:
    """SMB 共享读写（AC11）。"""
    print("\n>>> 阶段 9: SMB (samba) 共享读写")
    st, applied, resp = put_config(token, "samba", CFG_SAMBA)
    record("9.1 共享与用户配置提交并生效", st == 200 and applied is True, f"status={st} {resp[:140]}")

    def smb_roundtrip():
        sh("printf 'BMC_SMB_E2E' > /tmp/e2e_smb.txt")
        out = sh(
            f"smbclient //127.0.0.1/bmc_share -U smbe2e%ChangeMe123 "
            f"-c 'put /tmp/e2e_smb.txt e2e_smb.txt; ls e2e_smb.txt' 2>&1",
            timeout=45,
        )
        return out if "e2e_smb.txt" in out and "NT_STATUS" not in out else None

    ok = wait_for(smb_roundtrip, timeout=90, interval=5, label="SMB 读写")
    record("9.2 平台下发的用户可读写共享目录", bool(ok), (ok or "").replace("\n", " ")[:160])

    # 9.3 错误口令必须被拒（不改配置，用当前用户）
    bad = sh("smbclient //127.0.0.1/bmc_share -U smbe2e%WrongPassword -c 'ls' 2>&1", timeout=45)
    record("9.3 错误口令被拒（NT_STATUS_LOGON_FAILURE）",
           "LOGON_FAILURE" in bad, bad.replace("\n", " ")[:160])

    # 9.4/9.5 协议版本不匹配：服务端下限抬到 SMB3_11，客户端最高 SMB2 → 必须连不上
    try:
        st, applied, resp = put_config(token, "samba", {**CFG_SAMBA, "min_protocol": "SMB3_11"})
        record("9.4 抬高协议下限的配置提交并生效",
               st == 200 and applied is True, f"status={st} {resp[:120]}")

        # 先确认服务已按新下限就绪（用满足下限的客户端），否则会把「重启期间的连接被拒」
        # 误判成「协议被拒」——那样测试会假通过
        def high_protocol_ok():
            out = sh("smbclient //127.0.0.1/bmc_share -U smbe2e%ChangeMe123 -m SMB3_11 -c 'ls' 2>&1",
                     timeout=45)
            return out if "e2e_smb.txt" in out and "NT_STATUS" not in out else None

        ready = wait_for(high_protocol_ok, timeout=90, interval=5, label="高协议客户端可用")
        record("9.5 满足下限的客户端仍可正常访问", bool(ready))

        def low_protocol_rejected():
            out = sh("smbclient //127.0.0.1/bmc_share -U smbe2e%ChangeMe123 -m SMB2 -c 'ls' 2>&1",
                     timeout=45)
            if "CONNECTION_REFUSED" in out:
                return None  # 服务还没起来，继续等（不算通过）
            return out if ("NT_STATUS" in out or "protocol" in out.lower()) else None

        got = wait_for(low_protocol_rejected, timeout=60, interval=5, label="低协议被拒")
        record("9.6 客户端协议低于服务端下限时被拒",
               bool(got), (got or "仍可连接（未按预期拒绝）").replace("\n", " ")[:160])
    finally:
        # 复位：恢复正向配置后必须重新可读写
        put_config(token, "samba", CFG_SAMBA)
        back = wait_for(smb_roundtrip, timeout=90, interval=5, label="复位后 SMB 读写")
        record("9.7 复位正向配置后共享恢复可读写", bool(back))


def phase_nfs(token: str) -> None:
    """NFS 导出与挂载读写（AC11）。"""
    print("\n>>> 阶段 10: NFS (nfs-ganesha) 导出与挂载")
    st, applied, resp = put_config(token, "nfs-ganesha", CFG_NFS)
    record("10.1 导出配置提交并生效", st == 200 and applied is True, f"status={st} {resp[:140]}")

    mnt = "/mnt/bmc-e2e-nfs"
    sh(f"mkdir -p {mnt}; umount {mnt} 2>/dev/null; true")
    exec_in("bmc-nfs", "mkdir -p /data/nfs && chmod 777 /data/nfs")

    def mount_and_write():
        st1 = sh(f"mount -t nfs -o vers=4.1,port={PORT_NFS},nolock 127.0.0.1:/data/nfs {mnt} 2>&1")
        if sh(f"mountpoint -q {mnt} && echo yes") != "yes":
            return None
        write = sh(f"printf 'BMC_NFS_E2E' > {mnt}/e2e_nfs.txt && cat {mnt}/e2e_nfs.txt 2>&1")
        sh(f"umount {mnt} 2>/dev/null; true")
        return write if write == "BMC_NFS_E2E" else None

    ok = wait_for(mount_and_write, timeout=90, interval=6, label="NFS 挂载读写")
    record("10.2 可经 NFSv4 挂载并读写导出目录", bool(ok))

    def mount_once(after: str = ""):
        """挂载一次，可选在挂载成功后执行一段命令，返回 (是否挂上, 命令输出)。"""
        sh(f"umount {mnt} 2>/dev/null; true")
        sh(f"mount -t nfs -o vers=4.1,port={PORT_NFS},nolock 127.0.0.1:/data/nfs {mnt} 2>&1")
        mounted = sh(f"mountpoint -q {mnt} && echo yes") == "yes"
        out = sh(after) if (mounted and after) else ""
        sh(f"umount {mnt} 2>/dev/null; true")
        return mounted, out

    try:
        # 10.3/10.4 只读导出：挂得上但写不进去
        st, applied, resp = put_config(token, "nfs-ganesha", {**CFG_NFS, "access_type": "RO"})
        record("10.3 只读导出的配置提交并生效",
               st == 200 and applied is True, f"status={st} {resp[:120]}")

        def ro_blocks_write():
            mounted, out = mount_once(f"printf x > {mnt}/e2e_ro_probe.txt 2>&1 || true")
            if not mounted:
                return None
            return out if "ead-only" in out else None

        got = wait_for(ro_blocks_write, timeout=60, interval=6, label="只读导出拒绝写入")
        record("10.4 只读导出下写入被拒（Read-only file system）",
               bool(got), (got or "写入未被拒绝").replace("\n", " ")[:160])

        # 10.5/10.6 客户端地址不在允许网段内时必须挂载失败（经端口映射，
        # ganesha 看到的客户端是网桥网关（默认 bridge 172.17.0.1 / compose 172.30.x.1），
        # 故用不含这两个网段的地址）
        st, applied, resp = put_config(token, "nfs-ganesha",
                                       {**CFG_NFS, "allowed_clients": "10.99.0.0/16"})
        record("10.5 收紧允许网段的配置提交并生效",
               st == 200 and applied is True, f"status={st} {resp[:120]}")

        def denied_mount():
            mounted, _ = mount_once()
            return True if not mounted else None

        got = wait_for(denied_mount, timeout=60, interval=6, label="网段外挂载被拒")
        record("10.6 允许网段外的客户端挂载被拒", bool(got),
               "仍能挂载（未按预期拒绝）" if not got else "")

        # 10.7 故障注入 access_denied：等价于把客户端限制到不存在的网段
        st, applied, resp = put_config(token, "nfs-ganesha",
                                       {**CFG_NFS, "fault_mode": "access_denied"})
        record("10.7 故障注入 access_denied 配置提交并生效",
               st == 200 and applied is True, f"status={st} {resp[:120]}")
        got = wait_for(denied_mount, timeout=60, interval=6, label="故障注入后挂载被拒")
        record("10.8 故障注入 access_denied 下挂载被拒", bool(got),
               "仍能挂载（未按预期拒绝）" if not got else "")
    finally:
        # 复位：恢复正向配置后必须重新可读写
        put_config(token, "nfs-ganesha", CFG_NFS)
        back = wait_for(mount_and_write, timeout=90, interval=6, label="复位后 NFS 挂载读写")
        record("10.9 复位正向配置后导出恢复可读写", bool(back))


def phase_snmptrapd(token: str) -> None:
    """SNMP Trap 接收落盘（AC11）。"""
    print("\n>>> 阶段 11: SNMP Trap (snmptrapd) 接收与落盘")
    st, applied, resp = put_config(token, "snmptrapd", CFG_SNMPTRAPD)
    record("11.1 社区与输出配置提交并生效", st == 200 and applied is True, f"status={st} {resp[:140]}")
    time.sleep(3)

    marker = f"BMC_TRAP_E2E_{int(time.time())}"
    exec_in("bmc-snmptrapd", "mkdir -p /var/log/snmp && : > /var/log/snmp/traps.log")
    snmp_v2c_trap(HOST_ADDR, PORT_SNMPTRAP, "public", marker)

    def trap_logged():
        out = exec_in("bmc-snmptrapd", "cat /var/log/snmp/traps.log 2>/dev/null")
        return out if marker in out else None

    ok = wait_for(trap_logged, timeout=45, interval=4, label="Trap 落盘")
    record("11.2 SNMPv2c Trap 被接收并写入日志文件", bool(ok), (ok or "").replace("\n", " ")[:160])

    put_config(token, "snmptrapd", {**CFG_SNMPTRAPD, "fault_mode": "reject_community"})
    time.sleep(3)
    marker2 = f"BMC_TRAP_DENY_{int(time.time())}"
    exec_in("bmc-snmptrapd", ": > /var/log/snmp/traps.log")
    snmp_v2c_trap(HOST_ADDR, PORT_SNMPTRAP, "public", marker2)
    time.sleep(5)
    logged = exec_in("bmc-snmptrapd", "cat /var/log/snmp/traps.log 2>/dev/null")
    record("11.3 故障注入：非授权社区被丢弃", marker2 not in logged)

    # 11.4/11.5 故障注入 force_v3_only：不配置 v1/v2c 社区 → v2c Trap 不落盘，但服务仍活着
    put_config(token, "snmptrapd", {**CFG_SNMPTRAPD, "fault_mode": "force_v3_only"})
    time.sleep(5)
    marker3 = f"BMC_TRAP_V3ONLY_{int(time.time())}"
    exec_in("bmc-snmptrapd", ": > /var/log/snmp/traps.log")
    snmp_v2c_trap(HOST_ADDR, PORT_SNMPTRAP, "bmctrap", marker3)
    time.sleep(5)
    logged3 = exec_in("bmc-snmptrapd", "cat /var/log/snmp/traps.log 2>/dev/null")
    record("11.4 故障注入 force_v3_only：v2c Trap 不落盘且容器仍在运行",
           marker3 not in logged3 and container_running("bmc-snmptrapd"))

    # 11.5/11.6 故障注入 blackhole_drop：只监听 127.0.0.1 → 外部 Trap 收不到，
    # 但进程与健康检查仍正常（与「服务挂了」可区分）。
    # 这条依赖 entrypoint 每轮重读 `# runtime:` 参数——修复前重启会沿用旧值，故障不生效。
    put_config(token, "snmptrapd", {**CFG_SNMPTRAPD, "fault_mode": "blackhole_drop"})

    def bound_to_loopback():
        row = exec_in("bmc-snmptrapd",
                      "grep -i :00A2 /proc/net/udp | head -1").split()
        # /proc/net/udp 第 2 列是 local_address:port 的小端十六进制；0100007F = 127.0.0.1
        return row[1] if len(row) > 1 and row[1].startswith("0100007F") else None

    got_bind = wait_for(bound_to_loopback, timeout=45, interval=4, label="只绑回环")
    marker_bh = f"BMC_TRAP_BLACKHOLE_{int(time.time())}"
    exec_in("bmc-snmptrapd", ": > /var/log/snmp/traps.log")
    snmp_v2c_trap(HOST_ADDR, PORT_SNMPTRAP, "bmctrap", marker_bh)
    time.sleep(5)
    logged_bh = exec_in("bmc-snmptrapd", "cat /var/log/snmp/traps.log 2>/dev/null")
    record("11.5 故障注入 blackhole_drop：只监听回环，外部 Trap 不落盘",
           bool(got_bind) and marker_bh not in logged_bh and container_running("bmc-snmptrapd"),
           f"bind={got_bind or '非回环'} 已落盘={marker_bh in logged_bh}")

    st, applied, _ = put_config(token, "snmptrapd", CFG_SNMPTRAPD)
    record("11.6 复位正向配置生效", st == 200 and applied is True, f"status={st}")
    time.sleep(10)
    marker4 = f"BMC_TRAP_RESET_{int(time.time())}"
    snmp_v2c_trap(HOST_ADDR, PORT_SNMPTRAP, "bmctrap", marker4)
    got = wait_for(lambda: True if marker4 in exec_in(
        "bmc-snmptrapd", "cat /var/log/snmp/traps.log 2>/dev/null") else None,
        timeout=40, interval=5, label="复位后 Trap 落盘")
    record("11.7 复位后 Trap 重新落盘", bool(got))


def phase_postfix(token: str) -> None:
    """SMTP 投递与拒收故障（AC11）：问候语、mynetworks 白名单、554 故障。"""
    print("\n>>> 阶段 12: SMTP (postfix) 投递与故障注入")
    st, applied, resp = put_config(token, "postfix", CFG_POSTFIX)
    record("12.1 中继与网络配置提交并生效", st == 200 and applied is True, f"status={st} {resp[:140]}")

    ok = wait_for(
        lambda: (lambda b: b if "bmc-mail.local" in b else None)(smtp_banner("127.0.0.1", PORT_SMTP)),
        timeout=60, interval=5, label="SMTP banner")
    record("12.2 SMTP 问候语反映平台配置的主机名", bool(ok), (ok or "").replace("\n", " ")[:120])

    marker = f"BMC_SMTP_E2E_{int(time.time())}"
    accepted, detail = smtp_send("127.0.0.1", PORT_SMTP, "bmc-e2e@bmc-mail.local",
                                 "root@bmc-mail.local", marker)
    record("12.3 源地址不在 mynetworks 白名单时中继被拒",
           not accepted and "Relay access denied" in detail, detail)

    st, applied, _ = put_config(token, "postfix", CFG_POSTFIX_RELAY)
    record("12.4 把宿主机网段加入 mynetworks 的配置生效", st == 200 and applied is True, f"status={st}")
    time.sleep(5)
    accepted, detail = smtp_send("127.0.0.1", PORT_SMTP, "bmc-e2e@bmc-mail.local",
                                 "root@bmc-mail.local", f"{marker}_ok")
    record("12.5 白名单放行后邮件被 SMTP 接受投递（250）", accepted, detail)

    put_config(token, "postfix", {**CFG_POSTFIX_RELAY, "fault_mode": "reject_554"})
    time.sleep(5)
    accepted, detail = smtp_send("127.0.0.1", PORT_SMTP, "bmc-e2e@bmc-mail.local",
                                 "root@bmc-mail.local", f"{marker}_deny")
    record("12.6 故障注入：邮件被 554 永久拒收", not accepted and "554" in detail, detail)

    # 12.7–12.9 故障注入 force_tls：明文被拒、TLS 客户端仍可投递。
    # 注意这条故障模式曾经是坏的——模板只写 encrypt 不配证书时，postfix 会广告 STARTTLS
    # 但握手报 454 TLS not available，结果明文和 TLS 都发不进来（服务对任何客户端不可用）。
    st, applied, _ = put_config(token, "postfix",
                                {**CFG_POSTFIX_RELAY, "fault_mode": "force_tls"})
    record("12.7 force_tls 故障模式配置提交并生效",
           st == 200 and applied is True, f"status={st}")
    time.sleep(5)

    def plaintext_rejected():
        accepted, detail = smtp_send("127.0.0.1", PORT_SMTP, "bmc-e2e@bmc-mail.local",
                                     "root@bmc-mail.local", f"{marker}_plain")
        return detail if (not accepted and "STARTTLS" in detail) else None

    got = wait_for(plaintext_rejected, timeout=60, interval=5, label="明文被拒")
    record("12.8 force_tls 下明文投递被拒（要求先 STARTTLS）",
           bool(got), (got or "明文未被拒").replace("\n", " ")[:140])

    def tls_accepted():
        ok_tls, detail = smtp_send_starttls("127.0.0.1", PORT_SMTP, "bmc-e2e@bmc-mail.local",
                                            "root@bmc-mail.local", f"{marker}_tls")
        return detail if ok_tls else None

    got_tls = wait_for(tls_accepted, timeout=60, interval=5, label="STARTTLS 投递")
    record("12.9 force_tls 下 STARTTLS 投递成功（TLS 真的可用）",
           bool(got_tls), (got_tls or "STARTTLS 投递失败").replace("\n", " ")[:140])

    # 12.10/12.11 故障注入 greylist_451：投递被临时拒绝（4xx，客户端应稍后重试）。
    # 实测 postfix 回的是 450 4.3.2（而非模式名里的 451），故按「4xx 临时失败」判定，
    # 码值记入 detail——名字与实际码值的差异已在任务记录里说明。
    put_config(token, "postfix", {**CFG_POSTFIX, "fault_mode": "greylist_451"})
    time.sleep(5)

    def temporary_reject():
        accepted, detail = smtp_send("127.0.0.1", PORT_SMTP, "bmc-e2e@bmc-mail.local",
                                     "root@bmc-mail.local", f"{marker}_grey")
        return detail if (not accepted and " 4" in detail) else None

    got = wait_for(temporary_reject, timeout=60, interval=5, label="临时拒绝")
    record("12.10 故障注入 greylist_451：投递被临时拒绝（4xx）",
           bool(got), (got or "未被临时拒绝").replace("\n", " ")[:140])

    # 12.12/12.13 故障注入 tarpit_delay：**出错**会话被拖慢（smtpd_error_sleep_time）。
    # 注意必须触发错误（RCPT 被拒）才走 sleep 路径；正常投递不受影响。
    put_config(token, "postfix", {**CFG_POSTFIX, "fault_mode": "tarpit_delay"})
    time.sleep(5)
    start = time.time()
    smtp_send("127.0.0.1", PORT_SMTP, "bmc-e2e@bmc-mail.local", "root@bmc-mail.local",
              f"{marker}_tarpit", timeout=90)
    slow = time.time() - start
    record("12.11 故障注入 tarpit_delay：出错会话被拖慢（>=15s）",
           slow >= 15, f"耗时 {slow:.1f}s")

    st, applied, _ = put_config(token, "postfix", CFG_POSTFIX)
    record("12.12 复位正向配置生效", st == 200 and applied is True, f"status={st}")
    time.sleep(5)
    start = time.time()
    accepted, detail = smtp_send("127.0.0.1", PORT_SMTP, "bmc-e2e@bmc-mail.local",
                                 "root@bmc-mail.local", f"{marker}_after")
    fast = time.time() - start
    record("12.13 复位后同样出错会话恢复快速响应",
           fast < 15 and "4" in detail, f"耗时 {fast:.1f}s {detail[:80]}")


def phase_secrets(token: str) -> None:
    """AC13：secret 字段脱敏读取、掩码回填与审计不含明文。"""
    print("\n>>> 阶段 13: secret 敏感字段脱敏与掩码回填（AC13）")
    put_config(token, "nginx", {**CFG_NGINX, "auth_basic_enabled": True,
                                "auth_basic_password": "bmc-fixture-pass"})
    st, resp = api("GET", "/api/v1/services/nginx", token=token)
    values = json.loads(resp).get("config", {}).get("values", {}) if st == 200 else {}
    record("13.1 详情读取时 secret 字段已脱敏",
           values.get("auth_basic_password") == "********" and values.get("ssl_key_pem") in ("********", ""),
           f"auth_basic_password={values.get('auth_basic_password')!r}")

    st, applied, resp = put_config(token, "nginx",
                                   {**CFG_NGINX, "auth_basic_enabled": True,
                                    "auth_basic_password": "********"})
    record("13.2 提交掩码值时保留库中原值（不回写为掩码）",
           st == 200 and applied is True, f"status={st} {resp[:140]}")

    st, resp = api("GET", "/api/v1/audit-logs?limit=100", token=token)
    text = resp if isinstance(resp, str) else ""
    record("13.3 审计日志不含 secret 明文",
           st == 200 and "bmc-fixture-pass" not in text, f"status={st} 命中明文={'bmc-fixture-pass' in text}")

    put_config(token, "nginx", CFG_NGINX)
    time.sleep(3)


def phase_audit(token: str) -> None:
    """审计日志页依赖的查询能力：动作过滤、服务过滤、关键字、动作字典与分页上限。"""
    print("\n>>> 阶段 15: 审计日志查询（动作/服务过滤、关键字、分页上限）")
    # 先造两条可区分的记录：一条服务配置变更、一条平台更新（不实际执行，仅走配置变更）
    put_config(token, "nginx", CFG_NGINX)

    st, resp = api("GET", "/api/v1/audit-logs/actions", token=token)
    actions = json.loads(resp).get("data", []) if st == 200 else []
    record("15.1 动作字典去重且升序",
           st == 200 and actions == sorted(set(actions)) and "config.update" in actions,
           f"status={st} actions={actions}")

    st, resp = api("GET", "/api/v1/audit-logs?action=config.update&limit=100", token=token)
    doc = json.loads(resp) if st == 200 else {}
    entries = doc.get("data", [])
    record("15.2 按动作过滤只返回该动作，且 count 为过滤后总数",
           st == 200 and bool(entries)
           and all(e["action"] == "config.update" for e in entries)
           and doc.get("count", 0) >= len(entries),
           f"status={st} count={doc.get('count')} 命中={len(entries)}")

    st, resp = api("GET", "/api/v1/audit-logs?service_name=nginx&limit=100", token=token)
    entries = json.loads(resp).get("data", []) if st == 200 else []
    record("15.3 按服务过滤只返回该服务的记录",
           st == 200 and bool(entries) and all(e["service_name"] == "nginx" for e in entries),
           f"status={st} 命中={len(entries)}")

    st, resp = api("GET", "/api/v1/audit-logs?q=CONFIG.UPDATE&limit=100", token=token)
    entries = json.loads(resp).get("data", []) if st == 200 else []
    record("15.4 关键字大小写不敏感且能命中 detail",
           st == 200 and bool(entries), f"status={st} 命中={len(entries)}")

    st, resp = api("GET", "/api/v1/audit-logs?offset=1&limit=1", token=token)
    doc = json.loads(resp) if st == 200 else {}
    record("15.5 分页返回单页且 count 仍为全量总数",
           st == 200 and len(doc.get("data", [])) <= 1 and doc.get("count", 0) >= 1,
           f"status={st} count={doc.get('count')} 本页={len(doc.get('data', []))}")

    st, resp = api("GET", "/api/v1/audit-logs?limit=10000", token=token)
    record("15.6 单页条数超上限被拒（审计表只增，不能一次拖走整表）",
           st == 422, f"status={st}")


def _config_versions(token: str, service: str) -> list:
    """读取服务的配置版本列表（最新在前）；接口异常时返回空列表，由用例判失败。"""
    st, resp = api("GET", f"/api/v1/services/{service}/config/versions?limit=100",
                   token=token)
    if st != 200:
        return []
    return json.loads(resp).get("data", [])


def phase_versions(token: str) -> None:
    """AC1–AC4：版本历史、详情脱敏、回滚真实生效、超限裁剪。

    回滚的验收落在协议上而不是配置对比：回滚到「stratum_16」那一版后，NTP 应答必须
    重新宣告未同步——配置对比只能证明库里的值变了，证明不了容器真的重读了配置。
    """
    print("\n>>> 阶段 16: 配置版本历史与回滚（AC1–AC4）")
    service = "chrony"
    # 版本号由历史累积决定，先取当前最大版本号再往后造，避免依赖「干净库」的假设
    base = max((v["version"] for v in _config_versions(token, service)), default=0)

    # 造 3 个可区分的版本：正向授时 → stratum_16 故障 → 正向授时
    for values in (CFG_CHRONY, {**CFG_CHRONY, "fault_mode": "stratum_16"}, CFG_CHRONY):
        st, applied, resp = put_config(token, service, values)
        if not (st == 200 and applied is True):
            record("16.1 连续 3 次下发配置均生效", False, f"status={st} {resp[:140]}")
            return

    versions = _config_versions(token, service)
    newest = [v["version"] for v in versions][:3]
    record("16.1 历史列表按版本倒序返回最近 3 次下发（含操作者与时间）",
           newest == [base + 3, base + 2, base + 1]
           and all(v.get("user_email") and v.get("created_at") for v in versions),
           f"版本={newest} 期望={[base + 3, base + 2, base + 1]} 总数={len(versions)}")

    fault_version = base + 2
    st, resp = api("GET", f"/api/v1/services/{service}/config/versions/{fault_version}",
                   token=token)
    detail = json.loads(resp) if st == 200 else {}
    record("16.2 版本详情返回该版本的配置值",
           st == 200 and detail.get("version") == fault_version
           and detail.get("values", {}).get("fault_mode") == "stratum_16",
           f"status={st} fault_mode={detail.get('values', {}).get('fault_mode')!r}")

    # secret 字段在版本详情里同样是掩码：历史接口不能成为绕过脱敏的后门
    put_config(token, "nginx", {**CFG_NGINX, "auth_basic_enabled": True,
                                "auth_basic_password": "bmc-fixture-pass"})
    nginx_versions = _config_versions(token, "nginx")
    if nginx_versions:
        newest_nginx = nginx_versions[0]["version"]
        st, resp = api("GET",
                       f"/api/v1/services/nginx/config/versions/{newest_nginx}",
                       token=token)
        values = json.loads(resp).get("values", {}) if st == 200 else {}
        record("16.3 版本详情里 secret 字段为掩码",
               st == 200 and values.get("auth_basic_password") == "********",
               f"status={st} auth_basic_password={values.get('auth_basic_password')!r}")
    else:
        record("16.3 版本详情里 secret 字段为掩码", False, "nginx 无版本记录")
    put_config(token, "nginx", CFG_NGINX)

    st, resp = api("POST",
                   f"/api/v1/services/{service}/config/versions/{fault_version}/rollback",
                   token=token)
    applied = json.loads(resp).get("applied") if st == 200 else None
    record("16.4 回滚接口接受并重新渲染下发", st == 200 and applied is True,
           f"status={st} {resp[:140]}")

    got = wait_for(lambda: (lambda r: r if r[0] == 3 else None)(ntp_query()),
                   timeout=45, label="回滚后未同步宣告")
    record("16.5 回滚后协议表现回到该版本（NTP 宣告未同步）", bool(got),
           f"stratum={got[1]} leap={got[0]}" if got else f"实测 leap={ntp_query()[0]}")

    st, resp = api("GET", f"/api/v1/services/{service}", token=token)
    values = json.loads(resp).get("config", {}).get("values", {}) if st == 200 else {}
    record("16.6 回滚后当前配置等于该版本的值",
           st == 200 and values.get("fault_mode") == "stratum_16",
           f"status={st} fault_mode={values.get('fault_mode')!r}")

    after = _config_versions(token, service)
    # 回滚写的是「当前最新版本 + 1」，不是「来源版本 + 1」：中间可能还有别的下发
    record("16.7 回滚写入新版本并标注来源版本",
           bool(after) and after[0]["version"] == base + 4
           and after[0]["rolled_back_from"] == fault_version,
           f"最新={after[0] if after else None}")

    st, resp = api("GET", "/api/v1/audit-logs?action=config.rollback&limit=20",
                   token=token)
    entries = json.loads(resp).get("data", []) if st == 200 else []
    record("16.8 审计记录回滚操作",
           st == 200 and any(e["service_name"] == service for e in entries),
           f"status={st} 命中={len(entries)}")

    # 复位：回滚到正向版本，协议上必须恢复同步（不能把故障模式留在机器上）
    st, resp = api("POST",
                   f"/api/v1/services/{service}/config/versions/{base + 3}/rollback",
                   token=token)
    got = wait_for(lambda: (lambda r: r if r[0] == 0 and abs(r[2]) < 300 else None)(ntp_query()),
                   timeout=60, label="复位后授时")
    record("16.9 复位回滚到正向版本后恢复同步授时", bool(got),
           f"stratum={got[1]} offset={got[2]:.1f}s" if got else f"status={st} {resp[:120]}")

    # 上限裁剪：下发次数超过上限后，历史条数收敛到上限（旧版本被删，版本号不重排）
    for _ in range(VERSION_LIMIT + 2):
        put_config(token, service, CFG_CHRONY)
    versions = _config_versions(token, service)
    # 裁剪口径：保留最新的 N 条，版本号不重排（最旧一条 = 最新 - (N-1)）
    newest = versions[0]["version"] if versions else None
    oldest = versions[-1]["version"] if versions else None
    record(f"16.10 版本数超过上限后只保留最新 {VERSION_LIMIT} 条（版本号不重排）",
           len(versions) == VERSION_LIMIT
           and newest is not None
           and oldest == newest - (VERSION_LIMIT - 1),
           f"总数={len(versions)} 最新={newest} 最旧={oldest}")


# --------------------------------------------------------------------------- #
# IPv6 阶段：宿主没有全局 IPv6 地址，探针跑在「挂在服务网络里的客户端容器」内。
# 每张服务网络在 compose 里都开了 enable_ipv6 + fd00:30:<n>::/64，客户端用服务名解析 v6 地址。
# 探针实现见同目录 scripts/v6_probe.py（真实协议请求，不是配置比对）。
# --------------------------------------------------------------------------- #
V6_CASES = [
    ("nginx", "nginx-net", 80, "http"),  # 容器网络内用容器端口，不是宿主发布端口
    ("rsyslog", "rsyslog-net", 514, "syslog"),
    ("sftp", "sftp-net", 22, "ssh"),
    ("vsftpd", "vsftpd-net", 21, "ftp"),
    ("tftpd-hpa", "tftpd-net", 69, "tftp"),
    ("samba", "samba-net", 445, "tcp"),
    ("nfs-ganesha", "nfs-net", 2049, "tcp"),
    ("snmptrapd", "snmptrapd-net", 162, "trap"),
    ("postfix", "postfix-net", 25, "smtp"),
]

# 探针容器需要 python3：优先用平台镜像（部署机上都已有），其次用拉取态的 GHCR 标签。
V6_CLIENT_IMAGES = [
    "bmc-platform:latest",
    "ghcr.nju.edu.cn/aini123152011/bmc-platform:latest",
    "ghcr.io/aini123152011/bmc-platform:latest",
]


def _v6_client_image() -> str:
    for image in V6_CLIENT_IMAGES:
        if sh(f"docker image inspect {image} >/dev/null 2>&1 && echo ok") == "ok":
            return image
    return ""


def phase_ipv6(token: str) -> None:
    """每个支持 IPv6 的服务一条「IPv6 客户端可观测行为」用例。

    判定口径与其它阶段一致：客户端能拿到正确响应（HTTP 状态行、SSH 横幅、TFTP DATA 块、
    NTP leap/stratum、FTP 230 登录成功等），而不是去看配置文件里有没有 [::]。
    """
    print()
    print(">>> 阶段 17: IPv6 支持（容器网络内真实 v6 客户端）")
    # chrony 4.3 的 NTP 套接字实测只绑定 IPv4（官方文档说 bindaddress 支持 v6 且每协议族一条，
    # 但本镜像里加了 :: 仍不产生 v6 套接字），故不列入用例，作为已知限制记录在验证矩阵。
    print("    (已知限制：chrony 的 NTP 套接字只监听 IPv4；webdav 的 Apache 设 IPV6_V6ONLY 失败 → 只 v4)")
    probe = os.path.join(os.path.dirname(os.path.abspath(__file__)), "v6_probe.py")
    if not os.path.isfile(probe):
        record("17.0 探针脚本就位", False, f"缺少 {probe}")
        return
    image = _v6_client_image()
    if not image:
        record("17.0 探针客户端镜像可用", False, "没有 bmc-platform 镜像（探针需要 python3）")
        return

    for index, (svc, net, port, kind) in enumerate(V6_CASES, start=1):
        cmd = (
            f"docker run --rm --network servicesmgt_{net} "
            f"-v {probe}:/probe.py:ro {image} python3 /probe.py {svc} {port} {kind}"
        )
        out = sh(cmd, timeout=90)
        ok = "PASS" in out
        detail = out.strip().splitlines()[-1][:140] if out.strip() else "(无输出)"
        record(f"17.{index} {svc}: IPv6 客户端可观测（{kind}）", ok, detail)


# --------------------------------------------------------------------------- #
# DHCP 阶段：DHCP 走二层广播，宿主机经 NAT 端口映射根本收不到（见 compose.yaml 文件头），
# 所以探针必须跑在「与 dhcp 容器同一网络（servicesmgt_dhcp-net）的客户端容器」里，用真实报文取址。
# 探针实现见同目录 scripts/dhcp_probe.py：自建 DHCPv4 帧（源地址 0.0.0.0 + 广播标志，与真实
# 「无地址客户端」一致）、DHCPv6 SOLICIT、ICMPv6 Router Solicitation、最小 DNS 查询。
# --------------------------------------------------------------------------- #
def dhcp_probe(*args: str, timeout: int = 120) -> str:
    """在 dhcp 网络的客户端容器里跑一次 DHCP/RA/DNS 探针，返回探针输出行。"""
    probe = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dhcp_probe.py")
    if not os.path.isfile(probe):
        return f"FAIL 缺少探针脚本 {probe}"
    image = _v6_client_image()
    if not image:
        return "FAIL 没有 bmc-platform 镜像（探针需要 python3）"
    # NET_ADMIN：让探针能把网卡置为混杂模式，静态绑定用例里服务端可能按 chaddr 单播到别的 MAC
    cmd = (
        f"docker run --rm --network {DHCP_NETWORK} --cap-add NET_ADMIN "
        f"-v {probe}:/probe.py:ro {image} python3 /probe.py " + " ".join(args)
    )
    return sh(cmd, timeout=timeout).strip()


def clear_dhcp_leases() -> None:
    """清空租约库，让 dnsmasq 重启后从空开始。

    只删文件不够：dnsmasq 的租约在内存里，必须重启一次才会按空文件重新开始。不清的话，
    上一次运行留下的 12 小时租约会污染「地址池耗尽」「取到的地址在池内」这类用例。
    """
    exec_in(DHCP_CONTAINER, "rm -f /var/lib/dnsmasq/dnsmasq.leases")
    exec_in(DHCP_CONTAINER, "/reload.sh")


def dhcp_container_ipv6() -> str:
    """dhcp 容器在 dhcp-net 上的全局 IPv6 地址（用于验证 DNS 的 v6 监听）。"""
    return sh(
        f"docker inspect {DHCP_CONTAINER} --format "
        "'{{range .NetworkSettings.Networks}}{{.GlobalIPv6Address}} {{end}}'"
    ).strip().split(" ")[0]


def phase_dhcp(token: str) -> None:
    """DHCPv4 / DHCPv6 / SLAAC / DNS 正向能力与 5 种故障注入，全部以真实报文判定。"""
    print("\n>>> 阶段 18: DHCP/DNS (dnsmasq) 取址、解析与故障注入")
    st, applied, resp = put_config(token, "dhcp", CFG_DHCP)
    record("18.1 正向配置提交并生效", st == 200 and applied is True, f"status={st} {resp[:140]}")
    clear_dhcp_leases()

    out = dhcp_probe("v4", "--expect-addr-prefix", DHCP_POOL_PREFIX,
                     "--expect-router", CFG_DHCP["gateway"],
                     "--expect-lease", str(DHCP_LEASE_SECONDS))
    record("18.2 DHCPv4 取址：地址在池内、网关与租约时长正确（BMC 视角）",
           out.startswith("PASS"), out[:150])

    out = dhcp_probe("ra", "--expect-prefix", DHCP_PREFIX_V6)
    record("18.3 SLAAC：RA 通告出配置的 IPv6 前缀", out.startswith("PASS"), out[:150])

    out = dhcp_probe("dns", "--server", "dhcp", "--name", "bmc-01.bmc.lab", "--type", "A",
                     "--expect", "172.30.12.10")
    record("18.4 DNS 正向解析 A 记录（v4 查询，地址即 DHCP 下发的 DNS）",
           out.startswith("PASS"), out[:150])

    out = dhcp_probe("dns", "--server", "dhcp", "--name", "bmc-01.bmc.lab", "--type", "AAAA",
                     "--expect", "fd00:30:12::10")
    record("18.5 DNS 正向解析 AAAA 记录（同名的 v6 地址）", out.startswith("PASS"), out[:150])

    v6_addr = dhcp_container_ipv6()
    out = dhcp_probe("dns", "--server", v6_addr, "--addr", "172.30.12.10") if v6_addr \
        else "FAIL 取不到 dhcp 容器的 IPv6 地址"
    record("18.6 DNS 反向解析 PTR，且走 IPv6 访问 53 端口（双栈监听）",
           out.startswith("PASS") and "bmc-01" in out, out[:150])

    # 静态绑定（BMC 侧「按 MAC 保留地址」的等价能力）
    st, applied, resp = put_config(token, "dhcp",
                                   {**CFG_DHCP, "static_hosts": [
                                       f"{DHCP_RESERVED_MAC},{DHCP_RESERVED_ADDR},bmc-09"]})
    out = dhcp_probe("v4", "--mac", DHCP_RESERVED_MAC, "--expect-addr", DHCP_RESERVED_ADDR) \
        if st == 200 and applied else f"FAIL 配置未生效 status={st}"
    record("18.7 静态绑定：按 MAC 下发固定地址 dhcp-host",
           out.startswith("PASS"), out[:150])

    # PXE 引导参数（与 tftpd-hpa 夹具联动的入口）
    st, applied, _ = put_config(token, "dhcp",
                                {**CFG_DHCP, "next_server": "192.168.1.50",
                                 "boot_file": "ipxe.efi"})
    out = dhcp_probe("v4") if st == 200 and applied else f"FAIL 配置未生效 status={st}"
    record("18.8 PXE 引导参数下发：bootfile + next-server",
           "boot=ipxe.efi@192.168.1.50" in out, out[:150])

    # 有状态 DHCPv6
    st, applied, _ = put_config(token, "dhcp", {**CFG_DHCP, "ra_mode": "stateful"})
    out = dhcp_probe("v6", "--expect-addr-prefix", "fd00:30:12::") \
        if st == 200 and applied else f"FAIL 配置未生效 status={st}"
    record("18.9 DHCPv6 有状态取址：SOLICIT 拿到池内 v6 地址",
           out.startswith("PASS"), out[:150])

    # ra_mode=off：完全不下发 IPv6 参数
    st, applied, _ = put_config(token, "dhcp", {**CFG_DHCP, "ra_mode": "off"})
    out = dhcp_probe("ra", "--expect-absent") if st == 200 and applied \
        else f"FAIL 配置未生效 status={st}"
    record("18.10 ra_mode=off：不再发 RA（IPv6 下发可关闭）", out.startswith("PASS"), out[:150])

    # ---- 故障注入 ----
    st, applied, _ = put_config(token, "dhcp", {**CFG_DHCP, "fault_mode": "pool_exhausted"})
    record("18.11 故障注入 pool_exhausted 配置生效",
           st == 200 and applied is True and container_running(DHCP_CONTAINER), f"status={st}")
    clear_dhcp_leases()
    out = dhcp_probe("v4", "--expect-addr", CFG_DHCP["pool_start"])
    record("18.12 pool_exhausted：第一个客户端仍能拿到那唯一一个地址",
           out.startswith("PASS"), out[:150])
    out = dhcp_probe("v4", "--mac", DHCP_OTHER_MAC, "--expect-absent", "--timeout", "8")
    record("18.13 pool_exhausted：第二个客户端拿不到地址（池已耗尽）",
           out.startswith("PASS"), out[:150])

    st, applied, _ = put_config(token, "dhcp", {**CFG_DHCP, "fault_mode": "blackhole"})
    record("18.14 故障注入 blackhole 配置生效", st == 200 and applied is True, f"status={st}")
    out = dhcp_probe("v4", "--expect-absent", "--timeout", "8")
    record("18.15 blackhole：DHCP 完全无应答（BMC 取不到地址）", out.startswith("PASS"), out[:150])
    out = dhcp_probe("dns", "--server", "dhcp", "--name", "bmc-01.bmc.lab", "--type", "A",
                     "--expect", "172.30.12.10")
    record("18.16 blackhole 下 DNS 仍正常（能区分「取址失败」与「服务已挂」）",
           out.startswith("PASS"), out[:150])

    st, applied, _ = put_config(token, "dhcp", {**CFG_DHCP, "fault_mode": "wrong_gateway"})
    out = dhcp_probe("v4", "--expect-router", "192.0.2.1") if st == 200 and applied \
        else f"FAIL 配置未生效 status={st}"
    record("18.17 故障注入 wrong_gateway：下发一个不存在的网关", out.startswith("PASS"), out[:150])

    # IPv6 侧的两个故障候选都被实测否掉了（细节见 services/dhcp/templates/dnsmasq.conf.j2 末尾注释）：
    # 「RA 错误前缀」dnsmasq 对不在接口子网内的前缀一个 RA 都不发；「DHCPv6 池耗尽」dnsmasq 会把
    # 同一个地址发给多个客户端（两个 DUID 都拿到 fd00:30:12::100），BMC 侧没有可观测差异。
    # 因此第 5 个故障换成同样有价值且可观测的「DNS 解析到错误地址」。
    st, applied, _ = put_config(token, "dhcp", {**CFG_DHCP, "fault_mode": "dns_wrong_answer"})
    record("18.18 故障注入 dns_wrong_answer 配置生效", st == 200 and applied is True, f"status={st}")
    out = dhcp_probe("dns", "--server", "dhcp", "--name", "bmc-01.bmc.lab", "--type", "A",
                     "--expect", "192.0.2.99")
    record("18.19 dns_wrong_answer：域内名字解析到不可达地址（BMC 解析成功但连不上）",
           out.startswith("PASS"), out[:150])
    out = dhcp_probe("v4", "--expect-addr-prefix", DHCP_POOL_PREFIX)
    record("18.20 dns_wrong_answer 下 DHCP 取址仍正常（故障只影响解析）",
           out.startswith("PASS"), out[:150])

    st, applied, _ = put_config(token, "dhcp", {**CFG_DHCP, "fault_mode": "short_lease"})
    # dnsmasq 的租约下限是 2 分钟：写 60 会被静默抬到 120（实测日志 lease time 2m），
    # 所以按「可实现的下限」注入与断言，而不是按理想值 60
    out = dhcp_probe("v4", "--expect-lease", "120") if st == 200 and applied \
        else f"FAIL 配置未生效 status={st}"
    record("18.21 故障注入 short_lease：租约压到 dnsmasq 下限 120 秒（观察 BMC 反复续租）",
           out.startswith("PASS"), out[:150])

    # ---- 复位 ----
    st, applied, resp = put_config(token, "dhcp", CFG_DHCP)
    record("18.22 复位正向配置生效", st == 200 and applied is True, f"status={st} {resp[:140]}")
    clear_dhcp_leases()
    out = dhcp_probe("v4", "--expect-addr-prefix", DHCP_POOL_PREFIX,
                     "--expect-router", CFG_DHCP["gateway"])
    record("18.23 复位后取址恢复：地址、网关回到正向配置",
           out.startswith("PASS"), out[:150])

    # 数据卷可浏览（租约文件就是「谁拿了哪个地址」的证据）
    tree = wait_for(lambda: json.loads(
        api("GET", "/api/v1/services/dhcp/data/tree", token=token)[1] or "{}"), timeout=25,
        label="dhcp data/tree")
    record("18.24 数据浏览接口能列出租约目录（data_dir=/var/lib/dnsmasq）",
           bool(tree), str(tree)[:150])


def phase_frontend(token: str) -> None:
    """前端 SPA 静态分发。"""
    print("\n>>> 阶段 14: 前端 SPA 分发")
    st, html = http_get(f"{BASE_URL}/")
    record("14.1 前端根路由返回 SPA 页面",
           st == 200 and ("<div id=\"root\">" in html or "<script type=\"module\"" in html),
           f"HTTP {st}")


PHASES = {
    "platform": phase_platform,
    "chrony": phase_chrony,
    "nginx": phase_nginx,
    "rsyslog": phase_rsyslog,
    "webdav": phase_webdav,
    "sftp": phase_sftp,
    "vsftpd": phase_vsftpd,
    "tftpd": phase_tftpd,
    "samba": phase_samba,
    "nfs": phase_nfs,
    "snmptrapd": phase_snmptrapd,
    "postfix": phase_postfix,
    "secrets": phase_secrets,
    "frontend": phase_frontend,
    "audit": phase_audit,
    "versions": phase_versions,
    "ipv6": phase_ipv6,
    "dhcp": phase_dhcp,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="BMC 服务管理平台实机验收套件")
    parser.add_argument("--phase", action="append", choices=sorted(PHASES),
                        help="只运行指定阶段（可重复）；缺省运行全部")
    args = parser.parse_args()

    print("=" * 68)
    print("  BMC Services Platform 实机验收套件")
    print(f"  目标机 {host_address()} ({sys.platform} {platform.machine()})")
    print("=" * 68)

    load_credentials()
    token = login()
    print(f"[login] 已登录: {EMAIL}\n")

    selected = args.phase or list(PHASES)
    for name in selected:
        try:
            PHASES[name](token)
        except Exception as e:  # noqa: BLE001
            record(f"{name}: 阶段执行异常", False, f"{type(e).__name__}: {e}")

    total = len(results)
    passed = sum(1 for _, s, _ in results if s == "PASS")
    print("\n" + "=" * 68)
    print(f"  测试汇总: 总用例 {total} | 通过 {passed} | 失败 {total - passed}")
    if failures:
        print("  失败用例:")
        for name in failures:
            print(f"    - {name}")
    print("=" * 68)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
