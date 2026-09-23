#!/usr/bin/env bash
# 诊断：容器间 UDP 是否通、freeradius 是否收到报文
set -u
NET=servicesmgt_freeradius-net
IMG=ghcr.nju.edu.cn/aini123152011/bmc-freeradius:latest
PLATFORM=bmc-platform:latest

cat > /tmp/udp_echo.py <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("0.0.0.0", 9999))
s.settimeout(20)
d, a = s.recvfrom(200)
print("got", d, a, flush=True)
PY

cat > /tmp/udp_send.py <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(3)
try:
    s.sendto(b"hello", (host, port))
    print("sent to", host, port)
except Exception as exc:
    print("send fail", type(exc).__name__, exc)
PY

docker rm -f udpecho frdbg2 >/dev/null 2>&1 || true
docker run -d --rm --name udpecho --network "$NET" -v /tmp/udp_echo.py:/e.py:ro "$PLATFORM" python3 /e.py >/dev/null
docker run -d --rm --name frdbg2 --network "$NET" \
  -v /opt/bmc-servicesmgt-deploy/volumes/freeradius-config:/etc/freeradius/3.0/bmc:ro \
  "$IMG" freeradius -X -l stdout >/dev/null
sleep 5

echo "=== frdbg2 监听的 1812 套接字 ==="
docker exec frdbg2 grep ":0714 " /proc/net/udp || echo "（没有 1812 的 v4 套接字！）"

echo "=== 发 UDP ==="
docker run --rm --network "$NET" -v /tmp/udp_send.py:/s.py:ro "$PLATFORM" python3 /s.py udpecho 9999
docker run --rm --network "$NET" -v /tmp/udp_send.py:/s.py:ro "$PLATFORM" python3 /s.py frdbg2 1812
sleep 2

echo "=== echo 容器日志 ==="
docker logs udpecho 2>&1 | tail -3
echo "=== frdbg2 日志 ==="
docker logs frdbg2 2>&1 | tail -10

docker rm -f udpecho frdbg2 >/dev/null 2>&1 || true
