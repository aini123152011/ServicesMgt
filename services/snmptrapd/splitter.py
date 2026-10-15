#!/usr/bin/env python3
"""日志分流器：让「不支持按来源动态命名」的守护进程也能按 <根>/<日期段>/<来源标识>.log 落盘。

为什么要它（实测依据见 .trellis/spec/services/index.md「日志落盘约定」）：
- snmptrapd 只有 `-Lf <固定文件>`，自身没有「按来源决定文件名」的能力；
- FreeRADIUS 的 `log {}` 段只支持固定目标（`destination`/`file`）。
两者都能把记录打到 stdout，于是由本脚本托管：逐行解析来源，追加写
`<log_root>/<日期段>/<来源标识>.log`，**同时把原行透传到自己的 stdout**，
保证 `docker logs` 里仍能看到全部日志（排障能力不退化）。

用法（由 entrypoint.sh 调用；`--` 之后是被托管的守护进程及其完整命令行）：
    splitter.py --service snmptrapd --log-root /var/log/snmp --date-dir daily \
        --filename-by-ip true -- snmptrapd -Lf /dev/stdout -f -n -F '%b\t...' 0.0.0.0:162

分流规则：
- 解析不出来源的行（守护进程启动信息等）落到 `<log_root>/<日期段>/unknown.log`；
- 日期段每行按当前时间计算，跨零点自动换到新目录（无需重启进程）；
- `--filename-by-ip false` 时按来源主机名命名：snmptrapd 取 Trap 载荷里发送方自报的
  sysName.0（与 rsyslog 的 %HOSTNAME% 同源），FreeRADIUS 取 clients.conf 里匹配到的
  客户端名；都取不到时回落来源 IP。**不做反解 DNS**：容器内没有可靠解析，
  且 BMC 场景下解析失败会拖慢逐条落盘。
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import signal
import subprocess
import sys
from typing import IO

# snmptrapd 记录行的首字段是 -F 的 %b（传输地址，实测 net-snmp 5.9.3）：
#   v4  "UDP: [172.17.0.1]:33928->[172.17.0.4]:162"
#   v6  "UDP/IPv6: [fd00:99::3]:54890"（本地端点缺失时没有 "->" 段）
# 只认行首这一段，避免把载荷里的方括号内容误判成来源。
_SNMP_TRANSPORT = re.compile(r"^(?:UDP|TCP)(?:/IPv6)?:\s*\[([^\]]+)\]")
# snmptrapd 记录里发送方自报的主机名（sysName.0）；-Ov(brief) 下取不到，会回落 IP
_SNMP_SYS_NAME = re.compile(r'1\.3\.6\.1\.2\.1\.1\.5\.0\s*=\s*STRING:\s*"([^"]*)"')
# FreeRADIUS 认证结果行："(0) Login OK: [bmcuser] (from client fx-nas port 0)"
_FR_CLIENT_NAME = re.compile(r"\(from client\s+([^\s)]+)\s+port\s+\d+\)")
# 未配置来源的请求会带 IP："Ignoring request from unknown client 1.2.3.4:5678"
_FR_SRC_IP = re.compile(
    r"\bfrom\s+(?:unknown client\s+)?"
    r"((?:\d{1,3}\.){3}\d{1,3}|[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7})\s*:\s*\d+\b"
)

# 文件名里出现的其它字符一律换成下划线，防目录穿越与非法字符
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._:-]")

# 同时打开的文件句柄上限；超过就整批关掉，避免大量来源把 fd 用尽
_MAX_OPEN_FILES = 256


def sanitize(name: str) -> str:
    """把来源标识转成安全的文件名（保留 IPv6 的 ':'，Linux 文件名允许它）。"""
    cleaned = _UNSAFE_CHARS.sub("_", name.strip())
    return cleaned or "unknown"


def resolve_source(service: str, line: str, filename_by_ip: bool) -> str | None:
    """从一行日志里解析「来源标识」；解析不出来源返回 None。"""
    if service == "snmptrapd":
        head = line.split("\t", 1)[0].strip()
        match = _SNMP_TRANSPORT.match(head)
        if not match:
            return None
        source_ip = sanitize(match.group(1))
        if filename_by_ip:
            return source_ip
        host_match = _SNMP_SYS_NAME.search(line)
        host = host_match.group(1).strip() if host_match else ""
        return sanitize(host) if host else source_ip

    # freeradius：认证结果行给的是 clients.conf 里匹配到的客户端名（原生日志不带源 IP）
    ip_match = _FR_SRC_IP.search(line)
    if filename_by_ip and ip_match:
        return sanitize(ip_match.group(1))
    name_match = _FR_CLIENT_NAME.search(line)
    if name_match:
        return sanitize(name_match.group(1))
    if ip_match:
        return sanitize(ip_match.group(1))
    return None


def date_segment(date_dir: str) -> str:
    """按 date_dir 取当前日期的目录段；none 返回空串（不建日期目录）。"""
    now = datetime.datetime.now()
    if date_dir == "daily":
        return now.strftime("%Y-%m-%d")
    if date_dir == "monthly":
        return now.strftime("%Y-%m")
    return ""


class SourceFiles:
    """按来源分文件追加写；每行都按当前时间算日期段，句柄做了上限保护。"""

    def __init__(self, log_root: str, date_dir: str) -> None:
        self.log_root = log_root
        self.date_dir = date_dir
        self._handles: dict[str, IO[str]] = {}

    def _path(self, name: str) -> str:
        segment = date_segment(self.date_dir)
        if segment:
            return os.path.join(self.log_root, segment, f"{name}.log")
        return os.path.join(self.log_root, f"{name}.log")

    def write(self, name: str, line: str) -> None:
        path = self._path(name)
        handle = self._handles.get(path)
        if handle is None:
            if len(self._handles) >= _MAX_OPEN_FILES:
                self.close()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # buffering=1：行缓冲，落盘对 tail -f 可见
            handle = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
            self._handles[path] = handle
        handle.write(line)

    def close(self) -> None:
        for handle in self._handles.values():
            try:
                handle.close()
            except OSError:
                pass
        self._handles.clear()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把被托管守护进程的 stdout 按来源分流写文件，并原样透传到 stdout"
    )
    parser.add_argument("--service", required=True, choices=["snmptrapd", "freeradius"])
    parser.add_argument("--log-root", required=True, help="日志根目录（容器内绝对路径）")
    parser.add_argument("--date-dir", required=True, choices=["daily", "monthly", "none"])
    parser.add_argument("--filename-by-ip", required=True, choices=["true", "false"])
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="-- 之后：被托管的守护进程及其参数",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("缺少被托管的守护进程命令行（形如 -- snmptrapd -Lf /dev/stdout ...）")

    sink = SourceFiles(args.log_root, args.date_dir)
    os.makedirs(args.log_root, exist_ok=True)
    print(
        f"splitter: 托管 {args.service}，按 {args.log_root}/<{args.date_dir} 日期段>/"
        f"<来源>.log 落盘（filename_by_ip={args.filename_by_ip}）",
        file=sys.stderr,
        flush=True,
    )

    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def forward_signal(signum: int, _frame: object) -> None:
        """docker stop / reload 时把信号转给被托管的守护进程，避免留下孤儿进程。"""
        print(f"splitter: 收到信号 {signum}，终止 {command[0]}", file=sys.stderr, flush=True)
        try:
            child.terminate()
        except OSError:
            pass

    signal.signal(signal.SIGTERM, forward_signal)
    signal.signal(signal.SIGINT, forward_signal)

    out = sys.stdout
    assert child.stdout is not None  # subprocess.PIPE 保证非 None
    try:
        for raw in iter(child.stdout.readline, b""):
            line = raw.decode("utf-8", "replace")
            # 原行透传：docker logs 里看到的与改动前一致
            out.write(line)
            out.flush()
            by_ip = args.filename_by_ip == "true"
            sink.write(resolve_source(args.service, line, by_ip) or "unknown", line)
    finally:
        child.stdout.close()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        sink.close()
    return child.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
