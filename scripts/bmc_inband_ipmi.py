"""在  上做只读的「带内 ipmitool」查询。

带内 = 用本机 IPMI 接口（/dev/ipmi0，走 KCS/SMIC）直接读本机 BMC，不经网络、不需要 BMC 账号。
本脚本只做读取（mc info / lan print / lan6 print / channel info），不改任何设置。
"""

from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("BMC_HOST", "")
USER = os.environ.get("BMC_SSH_USER", "")
PASSWORD = os.environ.get("BMC_SSH_PASSWORD", "")

REMOTE = r"""
set -u
echo "=== 主机与身份 ==="
hostname; id
echo
echo "=== ipmitool 是否可用 ==="
if command -v ipmitool >/dev/null 2>&1; then ipmitool -V 2>&1 | head -1; else echo "未安装 ipmitool"; fi
echo
echo "=== 本地 IPMI 设备与内核模块（带内通道）==="
ls -l /dev/ipmi* 2>/dev/null || echo "（无 /dev/ipmi*）"
lsmod 2>/dev/null | grep -E "^ipmi" || echo "（ipmi 模块未加载）"
echo
echo "=== 免密 sudo 是否可用 ==="
if sudo -n true 2>/dev/null; then echo "可用"; else echo "不可用（可能需要密码）"; fi
"""

# 带内查询：先直接跑；失败时用 sudo -S 重试（/dev/ipmi0 通常要 root）
QUERY = r"""
set -u
RUN="ipmitool -I open"
if ! $RUN mc info >/dev/null 2>&1; then
  if sudo -n true 2>/dev/null; then RUN="sudo -n ipmitool -I open"
  else RUN="sudo -S ipmitool -I open"; fi
fi

echo "=== 带内 mc info（用 $RUN）==="
if [ "$RUN" = "sudo -S ipmitool -I open" ]; then
  printf '%s\n' "$SUDO_PW" | sudo -S ipmitool -I open mc info 2>&1 | head -14
else
  $RUN mc info 2>&1 | head -14
fi
echo
echo "=== 带内 channel info 1 ==="
if [ "$RUN" = "sudo -S ipmitool -I open" ]; then
  printf '%s\n' "$SUDO_PW" | sudo -S ipmitool -I open channel info 1 2>&1 | head -12
else
  $RUN channel info 1 2>&1 | head -12
fi
echo
echo "=== 带内 lan print 1（IPv4）==="
if [ "$RUN" = "sudo -S ipmitool -I open" ]; then
  printf '%s\n' "$SUDO_PW" | sudo -S ipmitool -I open lan print 1 2>&1 > /tmp/_lan4.txt
else
  $RUN lan print 1 > /tmp/_lan4.txt 2>&1
fi
grep -iE "IP Address|Subnet|MAC Address|Gateway|Source|VLAN|802" /tmp/_lan4.txt | head -14
echo
echo "=== 带内 lan6 print 1（IPv6）==="
if [ "$RUN" = "sudo -S ipmitool -I open" ]; then
  printf '%s\n' "$SUDO_PW" | sudo -S ipmitool -I open lan6 print 1 > /tmp/_lan6.txt 2>&1
else
  $RUN lan6 print 1 > /tmp/_lan6.txt 2>&1
fi
sed -n '1,14p' /tmp/_lan6.txt
echo "--- 已启用的 v6 地址（若有）---"
grep -E "Enabled: *yes" /tmp/_lan6.txt || echo "（没有 Enabled: yes 的静态地址）"
grep -E "Address: +[0-9a-fA-F]" /tmp/_lan6.txt | grep -v "::/0" || echo "（没有实际分配到的 v6 地址）"
"""


def run(client: paramiko.SSHClient, script: str, label: str, sudo_pw: str = "") -> None:
    stdin, stdout, stderr = client.exec_command(f"SUDO_PW={sudo_pw!r} bash -s", timeout=120)
    stdin.write(script)
    stdin.channel.shutdown_write()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    print(f"########## {label} ##########")
    print(out.strip())
    if err.strip():
        print("[stderr]", err.strip()[:400])
    print()


def main() -> int:
    if not PASSWORD:
        print("缺少 BMC_SSH_PASSWORD", file=sys.stderr)
        return 2
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(HOST, username=USER, password=PASSWORD, timeout=15,
                       allow_agent=False, look_for_keys=False)
    except Exception as exc:  # noqa: BLE001
        print(f"SSH 连接失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    try:
        run(client, REMOTE, "环境探测")
        run(client, QUERY, "带内 IPMI 查询", sudo_pw=PASSWORD)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
