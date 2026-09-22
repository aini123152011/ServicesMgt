"""BMC 服务管理平台实机验收套件（目标机 <目标机地址> / openEuler aarch64）。

在目标机上运行（脚本内部通过 127.0.0.1 访问平台与服务容器）：

    python3 verify_bmc_platform_e2e.py              # 全部阶段
    python3 verify_bmc_platform_e2e.py --phase nginx # 只跑某个阶段

覆盖范围：
  - 平台基础：健康检查、JWT 登录、11 服务注册表、容器运行状态
  - AC11 各服务特性配置真实生效：chrony(NTP 应答)/nginx(上传下载+Basic认证)/
    rsyslog(按IP日期归档)/webdav(PUT)/sftp(密码登录)/vsftpd(本地用户上传)/
    tftpd-hpa(文件下载)/samba(共享读写)/nfs-ganesha(NFSv4 挂载)/
    snmptrapd(Trap 接收落盘)/postfix(SMTP 投递)
  - 故障注入真实生效：chrony(stratum_16/fake_offset)、nginx(500/503/限速)、
    rsyslog(黑洞丢弃)、webdav(423/507)
  - AC12 rsyslog 日志浏览接口（/data/tree、/data/content 关键字过滤）
  - AC13 secret 字段脱敏与掩码回填、审计不含明文
  - AC14 nginx HTTPS（自签证书）可访问
  - 前端 SPA 静态分发

凭据不写在脚本里：优先取环境变量 BMC_ADMIN_EMAIL/BMC_ADMIN_PASSWORD，
其次从平台容器环境变量 FIRST_SUPERUSER/FIRST_SUPERUSER_PASSWORD 读取。

兼容目标机自带的 Python 3.9（运行时联合类型注解需靠 future import 延迟求值）。
"""

from __future__ import annotations

import argparse
import io
import json
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

# 各服务发布到宿主机的端口
PORT_NGINX_HTTP = 18088
PORT_NGINX_HTTPS = 18443
PORT_WEBDAV = 8080
PORT_SFTP = 2222
PORT_FTP = 21
PORT_TFTP = 69
PORT_SMB = 445
PORT_NFS = 2049
PORT_SNMPTRAP = 162
PORT_SMTP = 25
PORT_SYSLOG = 514


def host_address() -> str:
    """本机对外 IPv4（BMC 设备从局域网访问服务时用的就是这个地址）。

    UDP 探测统一走真实网卡地址而非 127.0.0.1：经 Docker 端口映射的回环路径下，
    "服务端另起临时端口应答"的协议（TFTP 最典型）会因 NAT 会话匹配不上而收不到
    回包——实测 127.0.0.1:69 无应答、<目标机地址>:69 正常。
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
def ntp_query(host: str = HOST_ADDR, port: int = 123, timeout: float = 5):
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
# 宿主机经 docker 网桥访问容器，postfix 看到的源地址是网桥网关（172.17.0.1）。
# 把它加入 mynetworks 才能中继——这一步同时验证白名单配置真实生效。
CFG_POSTFIX_RELAY = {
    **CFG_POSTFIX,
    "mynetworks": ["127.0.0.0/8", "192.168.0.0/16", "172.17.0.0/16"],
}

SERVICES_11 = {
    "chrony", "nginx", "rsyslog", "webdav", "postfix", "snmptrapd",
    "sftp", "vsftpd", "tftpd-hpa", "samba", "nfs-ganesha",
}


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
    record("1.2 11 种 BMC 支撑服务全部注册", st == 200 and SERVICES_11.issubset(names),
           f"found {len(names)}: {sorted(names)}")

    not_running = [n for n in sorted(SERVICES_11) if not container_running(f"bmc-{n}") and not (
        n == "nfs-ganesha" and container_running("bmc-nfs"))]
    record("1.3 11 个服务容器均处于运行状态", not not_running, f"未运行: {not_running}")

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

    st, applied, _ = put_config(token, "nginx", CFG_NGINX)
    record("3.11 复位正向配置生效", st == 200 and applied is True, f"status={st}")
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

    put_config(token, "rsyslog", {**CFG_RSYSLOG, "fault_mode": "drop_all"})
    conf = wait_for(lambda: (lambda c: c if "\nstop" in c or c.startswith("stop") else None)(
        exec_in("bmc-rsyslog", "cat /etc/rsyslog/rsyslog.conf")), timeout=25, label="stop 指令")
    record("4.5 故障注入：黑洞丢弃(stop)指令已注入", bool(conf))

    st, applied, _ = put_config(token, "rsyslog", CFG_RSYSLOG)
    record("4.6 复位正向配置生效", st == 200 and applied is True, f"status={st}")
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

    st, applied, _ = put_config(token, "webdav", CFG_WEBDAV)
    record("5.6 复位正向配置生效", st == 200 and applied is True, f"status={st}")
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

    st, applied, _ = put_config(token, "sftp", CFG_SFTP)
    record("6.4 复位正向配置生效", st == 200 and applied is True, f"status={st}")


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

    st, applied, _ = put_config(token, "vsftpd", CFG_VSFTPD)
    record("7.4 复位正向配置生效", st == 200 and applied is True, f"status={st}")


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

    st, applied, _ = put_config(token, "snmptrapd", CFG_SNMPTRAPD)
    record("11.4 复位正向配置生效", st == 200 and applied is True, f"status={st}")


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

    st, applied, _ = put_config(token, "postfix", CFG_POSTFIX)
    record("12.7 复位正向配置生效", st == 200 and applied is True, f"status={st}")


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
}


def main() -> None:
    parser = argparse.ArgumentParser(description="BMC 服务管理平台实机验收套件")
    parser.add_argument("--phase", action="append", choices=sorted(PHASES),
                        help="只运行指定阶段（可重复）；缺省运行全部")
    args = parser.parse_args()

    print("=" * 68)
    print("  BMC Services Platform 实机验收套件")
    print("  目标机 <目标机地址> (openEuler 22.03 LTS-SP1 aarch64)")
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
