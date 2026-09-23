#!/usr/bin/env bash
# 在「宿主绑定挂载的数据卷 + 清空 + slapadd」这个复现路径上做配置二分
set -u
SRC=/etc/ldap-bmc/slapd.conf

case_run() {
  local name="$1" file="$2"
  rm -rf /var/lib/ldap-bmc/*
  slapadd -f "$file" -l /etc/ldap-bmc/seed.ldif >/tmp/add.log 2>&1 || { echo "FAIL $name (slapadd: $(tail -1 /tmp/add.log))"; return; }
  slapd -f "$file" -h "ldap:/// ldaps:///" -d 256 >/tmp/o.log 2>&1 &
  local pid=$!
  sleep 3
  if kill -0 "$pid" 2>/dev/null; then
    echo "OK   $name"
    kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  else
    echo "FAIL $name -> $(grep -o 'ch_calloc[^,]*' /tmp/o.log | tail -1)"
  fi
}

cp "$SRC" /tmp/b0.conf
case_run "原样" /tmp/b0.conf
grep -v '^maxsize' /tmp/b0.conf > /tmp/b1.conf;        case_run "去掉 maxsize" /tmp/b1.conf
sed 's/^maxsize.*/maxsize\t1073741824/' /tmp/b0.conf > /tmp/b2.conf; case_run "maxsize=1G" /tmp/b2.conf
sed 's/^maxsize.*/maxsize\t10485760/' /tmp/b0.conf > /tmp/b3.conf;   case_run "maxsize=10M" /tmp/b3.conf
grep -v '^index' /tmp/b0.conf > /tmp/b4.conf;          case_run "去掉 index" /tmp/b4.conf
grep -v -E '^access|^\tby' /tmp/b0.conf > /tmp/b5.conf; case_run "去掉 access" /tmp/b5.conf
grep -v '^TLS' /tmp/b0.conf > /tmp/b6.conf;            case_run "去掉 TLS" /tmp/b6.conf
grep -v '^moduleload' /tmp/b0.conf > /tmp/b7.conf;     case_run "去掉 moduleload" /tmp/b7.conf
