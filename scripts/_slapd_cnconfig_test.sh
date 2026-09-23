#!/usr/bin/env bash
# 验证 cn=config 路线是否稳定：装配 + 连起 5 次
set -u
CONF=/etc/ldap-bmc/config.ldif
CONF_D=/etc/ldap-bmc/slapd.d
LDIF=/etc/ldap-bmc/seed.ldif
DATA=/var/lib/ldap-bmc

build_config() {
  rm -rf "$CONF_D"; mkdir -p "$CONF_D" "$DATA"
  slapadd -F "$CONF_D" -n0 -l "$CONF" >/tmp/cfg.log 2>&1 || { echo "装配 cn=config 失败:"; tail -3 /tmp/cfg.log; return 1; }
  rm -rf "$DATA"/*
  slapadd -F "$CONF_D" -n1 -l "$LDIF" >/tmp/db.log 2>&1 || { echo "灌数据失败:"; tail -3 /tmp/db.log; return 1; }
  return 0
}

ok=0; fail=0
for i in 1 2 3 4 5; do
  if ! build_config; then echo "第 $i 次: 装配失败"; fail=$((fail+1)); continue; fi
  slapd -F "$CONF_D" -h "ldap:/// ldaps:///" -d 256 >/tmp/s.log 2>&1 &
  pid=$!
  sleep 3
  if kill -0 "$pid" 2>/dev/null; then
    echo "第 $i 次: OK 存活"
    ok=$((ok+1))
    kill "$pid"; wait "$pid" 2>/dev/null
  else
    echo "第 $i 次: FAIL -> $(grep -o 'ch_calloc[^,]*' /tmp/s.log | tail -1)"
    fail=$((fail+1))
  fi
done
echo "汇总: OK=$ok FAIL=$fail"
