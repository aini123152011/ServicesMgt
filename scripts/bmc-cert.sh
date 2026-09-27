#!/usr/bin/env bash
# 把「BMC 证书生命周期」的三步收敛成一条命令：归档 → BMC 生成 CSR → 本平台证书服务签发 → 装回 BMC → 指纹比对。
#
# 为什么需要它：Redfish 那两个 action 的参数形状很挑（CertificateCollection 要传**集合**的 @odata.id 对象、
# CertificateUri 要传**证书成员**、Subject 五项全是必填，KeyPairAlgorithm 这台固件不认），
# 卡片上照着敲极易出错。这里把踩过的形状固化下来，卡片只写「跑这个脚本」。
#
# 用法：
#   bash scripts/bmc-cert.sh <BMC地址> <账号> <口令> [选项]
#
# 选项：
#   --pki HOST:PORT     证书服务地址（默认从 --pki 或环境变量 PKI 取，形如 192.168.235.153:8090）
#   --variant NAME      不发正常证书，改发一个故意失败的变体（expired / not_yet_valid / cn_mismatch /
#                       missing_intermediate / key_mismatch / weak_signature），用来观察 BMC 拦不拦
#   --dry-run           只打印将要做什么，不写 BMC
#   --yes               跳过交互确认（脚本化时用；不带它且非交互会直接拒绝执行）
#
# 注意：替换 BMC 的 Web 证书**不可逆**——旧证书的私钥在 BMC 内部拿不到，事后只能用 BMC 的
# 「SSL 设置 → 产生 SSL 认证」重签一份等价的自签证书。脚本第一步就会归档当前证书的指纹。
set -euo pipefail

BMC="${1:-}"; BMC_USER="${2:-}"; BMC_PASSWORD="${3:-}"
[ -n "$BMC" ] && [ -n "$BMC_USER" ] && [ -n "$BMC_PASSWORD" ] || {
  sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 2;
}
shift 3

PKI="${PKI:-}"
VARIANT=""
DRY_RUN=0
ASSUME_YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --pki) PKI="$2"; shift 2 ;;
    --variant) VARIANT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

[ -n "$PKI" ] || { echo "缺少 --pki（或环境变量 PKI），形如 192.168.235.153:8090" >&2; exit 2; }
command -v python3 >/dev/null || { echo "需要 python3 来拼 JSON（本机没有）" >&2; exit 2; }
command -v openssl >/dev/null || { echo "需要 openssl 来核对证书（本机没有）" >&2; exit 2; }

COLLECTION="/redfish/v1/Managers/0/NetworkProtocol/HTTPS/Certificates"
MEMBER="$COLLECTION/1"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }

# OCSP/CRL 不参与：BMC 用的是自签证书，-k 只是跳过校验，不影响读指纹
fingerprint_of_local() { openssl x509 -in "$1" -noout -fingerprint -sha256 | cut -d= -f2; }
fingerprint_of_live() {
  echo | timeout 15 openssl s_client -connect "$BMC:443" -servername "$BMC" 2>/dev/null \
    | openssl x509 -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2 || true
}

step "0/5 归档 BMC 当前证书（替换不可逆，这里先留底）"
BEFORE_LIVE="$(fingerprint_of_live)"
say "  实际 HTTPS 指纹: ${BEFORE_LIVE:-（读不到）}"
curl -sk -u "$BMC_USER:$BMC_PASSWORD" "https://$BMC$MEMBER" -o "$WORK/before.json"
python3 - "$WORK/before.json" "$WORK/before.crt" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
pem = data.get("CertificateString", "")
open(sys.argv[2], "w").write(pem)
print("  证书资源指纹:", (data.get("Fingerprint") or "见下表"))
PY
openssl x509 -in "$WORK/before.crt" -noout -subject -issuer -fingerprint -sha256 -dates 2>/dev/null | sed 's/^/  /'

if [ "$DRY_RUN" = "1" ]; then
  step "dry-run"
  say "  将要执行：GenerateCSR → 由 $PKI 签发${VARIANT:+（变体 $VARIANT）} → ReplaceCertificate → 比对指纹"
  say "  未写入 BMC。"
  exit 0
fi

if [ "$ASSUME_YES" != "1" ]; then
  printf '\n将替换 BMC(%s) 的 Web 证书，且旧的装不回去。继续？[y/N] ' "$BMC"
  read -r answer
  case "$answer" in [yY]*) ;; *) say "已取消。"; exit 1 ;; esac
fi

step "1/5 让 BMC 生成 CSR（私钥始终留在 BMC）"
python3 - "$WORK/csr-req.json" "$COLLECTION" "$BMC" <<'PY'
import json, sys
body = {
    "CertificateCollection": {"@odata.id": sys.argv[2]},
    "CommonName": sys.argv[3],
    "AlternativeNames": [sys.argv[3]],
    "Country": "CN", "State": "Beijing", "City": "Beijing",
    "Organization": "FX Test Lab", "OrganizationalUnit": "BMC Test",
    "KeyBitLength": 2048,
}
json.dump(body, open(sys.argv[1], "w"))
PY
code=$(curl -sk -u "$BMC_USER:$BMC_PASSWORD" -H 'Content-Type: application/json' \
  -X POST "https://$BMC/redfish/v1/CertificateService/Actions/CertificateService.GenerateCSR" \
  -d @"$WORK/csr-req.json" -o "$WORK/csr.json" -w '%{http_code}')
say "  GenerateCSR HTTP $code"
[ "$code" = "200" ] || { python3 -m json.tool "$WORK/csr.json" >&2; exit 1; }
python3 -c 'import json,sys;open(sys.argv[2],"w").write(json.load(open(sys.argv[1]))["CSRString"])' "$WORK/csr.json" "$WORK/bmc.csr"

step "2/5 交给证书服务签发${VARIANT:+（变体：$VARIANT）}"
if [ -n "$VARIANT" ]; then
  endpoint="$PKI/api/v1/variants/$VARIANT"
else
  endpoint="$PKI/api/v1/sign"
fi
python3 -c 'import json,sys;print(json.dumps({"csr_pem": open(sys.argv[1]).read()}))' "$WORK/bmc.csr" > "$WORK/sign-req.json"
curl -s -X POST "http://$endpoint" -H 'Content-Type: application/json' -d @"$WORK/sign-req.json" -o "$WORK/signed.json"
python3 -c '
import json, sys
data = json.load(open(sys.argv[1]))
if "cert_pem" not in data:
    print("  签发失败:", json.dumps(data, ensure_ascii=False)); sys.exit(1)
open(sys.argv[2], "w").write(data["cert_pem"])
print("  签发指纹:", data["fingerprint_sha256"])
print("  有效期  :", data["not_before"], "->", data["not_after"])
print("  是否回传私钥:", bool(data.get("key_pem")), "（应为 False：私钥留在 BMC）")
' "$WORK/signed.json" "$WORK/leaf.crt"

step "3/5 本地先验一遍（用证书服务的根）"
curl -s "http://$PKI/api/v1/ca" -o "$WORK/ca.json"
python3 -c 'import json,sys;open(sys.argv[2],"w").write(json.load(open(sys.argv[1]))["cert_pem"])' "$WORK/ca.json" "$WORK/root.crt"
# 变体本来就该验不过（过期/未生效/缺中间证书都会让 verify 非零），不能让它中断流程——
# 负向用例的重点是看 BMC 会不会拦，所以这里只报告、不 abort（脚本开了 pipefail，必须显式兜住）
if openssl verify -CAfile "$WORK/root.crt" "$WORK/leaf.crt" 2>&1 | sed 's/^/  /'; then
  say "  ✓ 链与有效期正常"
elif [ -n "$VARIANT" ]; then
  say "  ↑ 变体（$VARIANT）本就该验不过，继续交给 BMC 看它拦不拦"
else
  say "  ✗ 本地就没验过，仍然装回以便观察 BMC 的反应"
fi

step "4/5 装回 BMC（ReplaceCertificate；204 = 已受理）"
python3 - "$WORK/replace-req.json" "$MEMBER" "$WORK/leaf.crt" <<'PY'
import json, sys
body = {
    "CertificateType": "PEM",
    "CertificateUri": {"@odata.id": sys.argv[2]},
    "CertificateString": open(sys.argv[3]).read(),
}
json.dump(body, open(sys.argv[1], "w"))
PY
code=$(curl -sk -u "$BMC_USER:$BMC_PASSWORD" -H 'Content-Type: application/json' \
  -X POST "https://$BMC/redfish/v1/CertificateService/Actions/CertificateService.ReplaceCertificate" \
  -d @"$WORK/replace-req.json" -o "$WORK/replace.out" -w '%{http_code}')
say "  ReplaceCertificate HTTP $code"
if [ "$code" = "204" ]; then
  say "  ✓ 已受理"
else
  # 非 204 时 BMC 会用 @Message.ExtendedInfo 说明原因（例如过期证书的 CertificateFileExpired）
  python3 -c '
import json, sys
try:
    infos = json.load(open(sys.argv[1]))["error"]["@Message.ExtendedInfo"]
    for info in infos:
        print("  ✗", info.get("MessageId"), "|", info.get("Message"))
except Exception:
    print("  响应:", open(sys.argv[1]).read()[:300])
' "$WORK/replace.out"
fi

step "5/5 比对（资源槽位 vs 真实端口）"
EXPECT="$(fingerprint_of_local "$WORK/leaf.crt")"
AFTER_RESOURCE="$(curl -sk -u "$BMC_USER:$BMC_PASSWORD" "https://$BMC$MEMBER" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin).get("CertificateString",""))' \
  | openssl x509 -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2)"
AFTER_LIVE="$(fingerprint_of_live)"
say "  期望（本次签发）: ${EXPECT:-?}"
say "  归档（替换前）  : ${BEFORE_LIVE:-?}"
say "  证书资源现在    : ${AFTER_RESOURCE:-?}"
say "  真实 HTTPS 现在 : ${AFTER_LIVE:-?}"
if [ "$AFTER_RESOURCE" = "$EXPECT" ]; then
  say "  ✓ 资源槽位已换成新证书"
elif [ -n "$VARIANT" ]; then
  # 变体被拒是预期结果：BMC 没有改动，资源槽位当然还是上一次的证书
  say "  ✓ 变体（$VARIANT）被拒，BMC 未被改动"
else
  say "  ⚠ 资源槽位与预期不一致（看上面的 ReplaceCertificate 响应）"
fi
if [ "$AFTER_LIVE" = "$EXPECT" ]; then
  say "  ✓ 真实 HTTPS 已生效"
else
  say "  ⚠ 真实 HTTPS 仍是旧证书：ReplaceCertificate 只**暂存**，BMC 的 Web 服务重启后才切换"
fi
say ""
say "收尾提醒：旧证书无法原样恢复（密钥已换）。要回到自签可用状态，用 BMC 的「SSL 设置 → 产生 SSL 认证」。"
say "归档件留在本次的临时目录里，脚本退出即删；需要留档请自行重跑第 0 步并复制指纹。"
