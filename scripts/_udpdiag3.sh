#!/usr/bin/env bash
# 诊断 3：定论 —— 跨容器 UDP 通不通、freeradius 的 1812 收没收到包（看 /proc/net/udp 的 drops）
set -u
NET=servicesmgt_freeradius-net
PLATFORM=bmc-platform:latest
IMG=ghcr.nju.edu.cn/aini123152011/bmc-freeradius:latest

docker rm -f frdbg3 udpecho2 >/dev/null 2>&1 || true

echo "=== 起 debug freeradius（不加 --rm，避免看日志时容器已被删）==="
docker run -d --name frdbg3 --network "$NET" \
  -v /opt/bmc-servicesmgt-deploy/volumes/freeradius-config:/etc/freeradius/3.0/bmc:ro \
  "$IMG" freeradius -X -l stdout >/dev/null
sleep 5
docker ps --filter name=frdbg3 --format '{{.Names}} {{.Status}}'

echo "=== 起一个不回退的 UDP 回声容器 ==="
docker run -d --name udpecho2 --network "$NET" "$PLATFORM" python3 -c "
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(('0.0.0.0', 9999))
s.settimeout(30)
try:
    d, a = s.recvfrom(200)
    print('GOT', d, a, flush=True)
except socket.timeout:
    print('TIMEOUT', flush=True)
time.sleep(30)
" >/dev/null
sleep 2

echo "=== 回声测试（跨容器 UDP 是否通）==="
docker run --rm --network "$NET" "$PLATFORM" python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.sendto(b'hello', ('udpecho2', 9999))
print('sent')
"
sleep 2
docker logs udpecho2 2>&1 | tail -2

echo "=== freeradius 1812 套接字（发送前）==="
docker exec frdbg3 grep ":0714 " /proc/net/udp

echo "=== 发 RADIUS 请求 ==="
docker run --rm --network "$NET" -v /opt/bmc-servicesmgt-deploy/auth_probe.py:/probe.py:ro "$PLATFORM" \
  python3 /probe.py radius --host frdbg3 --secret bmc-radius-secret --user bmcuser \
  --password ChangeMe123 --expect accept --timeout 5

echo "=== freeradius 1812 套接字（发送后，看 drops 列）==="
docker exec frdbg3 grep ":0714 " /proc/net/udp

echo "=== freeradius -X 日志 ==="
docker logs frdbg3 2>&1 | tail -20

docker rm -f frdbg3 udpecho2 >/dev/null 2>&1 || true
