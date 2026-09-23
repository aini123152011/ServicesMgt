#!/usr/bin/env bash
# 二分定位 slapd 启动崩溃（ch_calloc 1GB）是配置里哪一项引起的
set -u
CFG=/etc/ldap-bmc/slapd.conf
test_variant() {
  local name="$1" file="$2"
  slapd -f "$file" -h "ldap://127.0.0.1:3389/" -d 256 >/tmp/out.log 2>&1 &
  local pid=$!
  sleep 3
  if kill -0 "$pid" 2>/dev/null; then
    echo "OK   $name （存活）"
    kill "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
  else
    echo "FAIL $name -> $(tail -1 /tmp/out.log)"
  fi
}

mkdir -p /var/lib/ldap-bmc
cp "$CFG" /tmp/c0.conf
echo "== 原样 =="; test_variant "原样" /tmp/c0.conf

grep -v '^maxsize' /tmp/c0.conf > /tmp/c1.conf
echo "== 去掉 maxsize =="; test_variant "无 maxsize" /tmp/c1.conf

grep -v '^index' /tmp/c0.conf > /tmp/c2.conf
echo "== 去掉 index =="; test_variant "无 index" /tmp/c2.conf

grep -v '^TLS' /tmp/c0.conf > /tmp/c3.conf
echo "== 去掉 TLS =="; test_variant "无 TLS" /tmp/c3.conf

grep -v -E '^access|^\tby' /tmp/c0.conf > /tmp/c4.conf
echo "== 去掉 access =="; test_variant "无 access" /tmp/c4.conf

grep -v '^moduleload' /tmp/c0.conf > /tmp/c5.conf
echo "== 去掉 moduleload（预期失败：mdb 未知）=="; test_variant "无 moduleload" /tmp/c5.conf

echo "== 完整日志尾（原样那次的）=="
tail -4 /tmp/out.log
