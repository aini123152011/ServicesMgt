#!/usr/bin/env bash
# 复现并定位：slapadd 灌库后 slapd 启动崩溃
set -u
CFG=/etc/ldap-bmc/slapd.conf
LDIF=/etc/ldap-bmc/seed.ldif

run_case() {
  local name="$1"; shift
  rm -rf /var/lib/ldap-bmc; mkdir -p /var/lib/ldap-bmc
  echo "---- $name ----"
  slapadd -f "$CFG" -l "$LDIF" >/tmp/add.log 2>&1 && echo "slapadd ok" || { echo "slapadd FAIL: $(tail -2 /tmp/add.log)"; return; }
  ls /var/lib/ldap-bmc | head -3 | tr '\n' ' '; echo
  slapd -f "$CFG" -h "ldap://127.0.0.1:3389/" -d 256 "$@" >/tmp/out.log 2>&1 &
  local pid=$!
  sleep 3
  if kill -0 "$pid" 2>/dev/null; then
    echo "RESULT OK 存活"
    kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  else
    echo "RESULT FAIL -> $(tail -2 /tmp/out.log | tr '\n' ' ')"
  fi
}

echo "== 当前容器身份 =="; id

run_case "slapadd 后 slapd（默认，root）"
run_case "slapadd 后 slapd（-u openldap）" -u openldap -g openldap

echo "== mdb 文件权限 =="; ls -la /var/lib/ldap-bmc | head -5
echo "== 内核/文件系统 =="; stat -f -c '%T' /var/lib/ldap-bmc
