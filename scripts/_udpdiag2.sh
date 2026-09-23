#!/usr/bin/env bash
# 诊断 2：前台运行，暴露容器启动错误与连通性
set -u
NET=servicesmgt_freeradius-net
PLATFORM=bmc-platform:latest

echo "=== 网络里有哪些容器 ==="
docker network inspect "$NET" --format '{{range .Containers}}{{.Name}} {{.IPv4Address}}{{"\n"}}{{end}}'

echo "=== 客户端容器解析服务名 ==="
docker run --rm --network "$NET" "$PLATFORM" python3 -c "
import socket
for name in ('bmc-freeradius', 'frdbg2', 'udpecho'):
    try:
        print(name, socket.gethostbyname(name))
    except Exception as exc:
        print(name, 'resolve fail', exc)
"

echo "=== 前台起一个 UDP 监听容器（10s 后自己退出）==="
docker rm -f udpecho >/dev/null 2>&1 || true
docker run -d --rm --name udpecho --network "$NET" "$PLATFORM" python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(('0.0.0.0', 9999))
s.settimeout(10)
try:
    d, a = s.recvfrom(200)
    print('GOT', d, a, flush=True)
except socket.timeout:
    print('TIMEOUT no packet', flush=True)
"
sleep 2
docker ps --filter name=udpecho --format '{{.Names}} {{.Status}}'
docker logs udpecho 2>&1 | tail -3

echo "=== 从另一个容器发 UDP ==="
docker run --rm --network "$NET" "$PLATFORM" python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(3)
try:
    s.sendto(b'hello', ('udpecho', 9999))
    print('SENT to udpecho:9999')
except Exception as exc:
    print('SEND FAIL', type(exc).__name__, exc)
"
sleep 2
echo "=== udpecho 收到没 ==="
docker logs udpecho 2>&1 | tail -3
docker rm -f udpecho >/dev/null 2>&1 || true

echo "=== 服务容器自身的 1812 是否有响应（自测回环）==="
docker exec bmc-freeradius sh -c "grep -c ':0714 ' /proc/net/udp; grep -c ':0714 ' /proc/net/udp6" 2>&1
