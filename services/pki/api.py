#!/usr/bin/env python3
"""证书服务（CA）夹具：内置 CA 按需签发证书，并可生成故意失败的变体。

只依赖 Python 标准库 + openssl CLI（镜像刻意不引入任何框架）：

- 公开产物（根证书、签发出的证书、证书链、CSR、清单）写 `/data/pki`（平台数据浏览可见）；
- **私钥一律只写 `/srv/pki-secrets`**（CA 私钥、签发出的私钥）。平台的数据浏览接口按
  manifest.data_dir 递归读文件并原样返回，任何登录用户都能看——把私钥放进 data_dir
  等于交给所有人。签发出的私钥只在 HTTP 响应里回传一次，不落公开卷。

进程模型：entrypoint.sh 是 1 号进程，在监督循环里拉起本脚本；reload.sh 杀掉本进程后，
循环带新配置重启它（配置在进程启动时读取一次，因此重启即重读）。

接口契约见 design.md §4：
    GET  /healthz                      健康检查：真的签一次一次性 CSR 自检，失败返回 503
    GET  /api/v1/ca                    取根证书（供 openssl verify -CAfile）
    POST /api/v1/sign                  签发：给 CSR，或不给则生成密钥对并回传私钥
    GET  /api/v1/issued                已签发清单
    GET  /api/v1/issued/{name}         取某次签发的产物（证书/链）
    GET  /api/v1/variants              列出可用失败变体与各自「要验什么」
    POST /api/v1/variants/{name}       生成指定失败变体（与 /sign 同形）

业务错误一律返回 JSON `{"error": ...}` 并配正确状态码（400/404/500），不用 send_error，
否则客户端拿不到失败原因。
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

# --------------------------------------------------------------------------- #
# 路径：容器内固定；环境变量覆盖只为本地冒烟测试（镜像里不设）
# --------------------------------------------------------------------------- #
SECRETS_DIR = Path(os.environ.get("PKI_SECRETS_DIR", "/srv/pki-secrets"))
DATA_DIR = Path(os.environ.get("PKI_DATA_DIR", "/data/pki"))
DEFAULT_CONFIG_PATH = Path(os.environ.get("PKI_CONF", "/etc/pki-bmc/pki.conf"))

CA_SECRETS_DIR = SECRETS_DIR / "ca"
CA_PUBLIC_DIR = DATA_DIR / "ca"
ISSUED_SECRETS_DIR = SECRETS_DIR / "issued"
ISSUED_PUBLIC_DIR = DATA_DIR / "issued"

# 串行化签发：openssl 用 CAcreateserial 维护序列号文件，并发写会互相覆盖
SIGN_LOCK = threading.Lock()

# 合法字符集（防注入：common_name 进 -subj，sans 进 extfile，都不允许出现分隔符）
CN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._*@-]{0,63}$")
DNS_PATTERN = re.compile(
    r"^(\*\.)?[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)

VARIANTS: list[dict[str, str]] = [
    {
        "name": "expired",
        "expect": "证书已过期（notAfter 在过去）：验 BMC 上传/替换时是否拒绝已过期证书",
    },
    {
        "name": "not_yet_valid",
        "expect": "证书尚未生效（notBefore 在未来）：验 BMC 是否拒绝未生效证书",
    },
    {
        "name": "cn_mismatch",
        "expect": "证书实际 CN 与请求不符：验 BMC 是否校验主机名/CN 一致性",
    },
    {
        "name": "missing_intermediate",
        "expect": "叶证书由中间 CA 签发但链里不给中间证书：验 BMC 是否拒绝不完整证书链",
    },
    {
        "name": "key_mismatch",
        "expect": "回传的私钥与证书不配对：验 BMC 上传证书时是否校验证书与私钥配对",
    },
    {
        "name": "weak_signature",
        "expect": "弱签名/短密钥（SHA-1 + RSA-1024，需 allow_weak_keys=true）：验 BMC 是否接受弱算法",
    },
]


class BadRequest(Exception):
    """客户端请求/配置问题（400）。"""


class NotFound(Exception):
    """资源不存在（404）。"""


class ConfigError(Exception):
    """pki.conf 结构或取值不可用（启动阶段失败）。"""


class OpensslError(Exception):
    """openssl 命令非零退出（500，附原始 stderr）。"""


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def parse_conf(path: Path) -> dict[str, str]:
    """解析平台渲染的 pki.conf。

    格式刻意保持行基：`key = value` 一行一个标量；两个 PEM 字段用
    `key_begin` / `key_end` 包裹多行内容（PEM 本身是多行的，行基格式必须给它边界）。
    """
    values: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        index += 1
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("_begin"):
            key = line[: -len("_begin")]
            block: list[str] = []
            while index < len(lines) and lines[index].strip() != f"{key}_end":
                block.append(lines[index])
                index += 1
            if index >= len(lines):
                raise ConfigError(f"pki.conf: '{key}' 缺少结束标记 '{key}_end'")
            index += 1
            values[key] = "\n".join(block).strip()
            continue
        key, separator, value = line.partition("=")
        if not separator:
            raise ConfigError(f"pki.conf: 无法解析的行: {raw!r}")
        values[key.strip()] = value.strip()
    return values


def _as_bool(text: str, default: bool = False) -> bool:
    if text == "":
        return default
    return text.strip().lower() in {"true", "1", "yes", "on"}


def _as_int(text: str, default: int) -> int:
    try:
        return int(text)
    except ValueError:
        return default


@dataclass
class Config:
    listen_port: int = 8090
    ca_common_name: str = "FX Test Root CA"
    ca_days: int = 3650
    ca_cert_pem: str = ""
    ca_key_pem: str = ""
    default_days: int = 365
    default_algorithm: str = "rsa2048"
    include_intermediate: bool = False
    allow_weak_keys: bool = False
    fault_mode: str = "none"
    slow_seconds: int = 30

    @classmethod
    def from_values(cls, values: dict[str, str]) -> Config:
        return cls(
            listen_port=_as_int(values.get("listen_port", ""), 8090),
            ca_common_name=values.get("ca_common_name", "").strip() or "FX Test Root CA",
            ca_days=_as_int(values.get("ca_days", ""), 3650),
            ca_cert_pem=values.get("ca_cert_pem", "").strip(),
            ca_key_pem=values.get("ca_key_pem", "").strip(),
            default_days=_as_int(values.get("default_days", ""), 365),
            default_algorithm=values.get("default_algorithm", "").strip() or "rsa2048",
            include_intermediate=_as_bool(values.get("include_intermediate", ""), False),
            allow_weak_keys=_as_bool(values.get("allow_weak_keys", ""), False),
            fault_mode=values.get("fault_mode", "").strip() or "none",
            slow_seconds=_as_int(values.get("slow_seconds", ""), 30),
        )


# --------------------------------------------------------------------------- #
# openssl 封装
# --------------------------------------------------------------------------- #
def run_openssl(args: list[str]) -> str:
    """执行 openssl，非零退出抛 OpensslError（附命令与 stderr）。"""
    proc = subprocess.run(
        ["openssl", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise OpensslError(f"openssl {' '.join(args)} 失败: {stderr}")
    return proc.stdout.decode("utf-8", errors="replace")


def validate_pem(text: str, kind: str) -> None:
    """校验 PEM 头尾（kind 如 'CERTIFICATE' / 'PRIVATE KEY' / 'CERTIFICATE REQUEST'）。"""
    stripped = text.strip()
    if not stripped.startswith("-----BEGIN ") or f"-----END {kind}-----" not in stripped:
        raise BadRequest(f"不是合法的 {kind} PEM 内容")


def cert_fingerprint(path: Path) -> str:
    """证书的 SHA256 指纹（openssl 大写冒号分隔写法）。"""
    out = run_openssl(["x509", "-in", str(path), "-noout", "-fingerprint", "-sha256"])
    return out.strip().split("=", 1)[-1].strip()


def cert_subject(path: Path) -> str:
    out = run_openssl(["x509", "-in", str(path), "-noout", "-subject"])
    return out.strip().split("subject=", 1)[-1].strip()


def _iso_utc(value: str) -> str:
    """把 openssl 的时间串（Sep 27 16:00:00 2036 GMT）转成 ISO8601；失败则原样返回。"""
    try:
        parsed = datetime.strptime(value, "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc
        )
        return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return value


def cert_not_after(path: Path) -> str:
    out = run_openssl(["x509", "-in", str(path), "-noout", "-enddate"])
    return _iso_utc(out.strip().split("=", 1)[-1].strip())


def cert_not_before(path: Path) -> str:
    out = run_openssl(["x509", "-in", str(path), "-noout", "-startdate"])
    return _iso_utc(out.strip().split("=", 1)[-1].strip())


def cert_serial(path: Path) -> str:
    out = run_openssl(["x509", "-in", str(path), "-noout", "-serial"])
    return out.strip().split("=", 1)[-1].strip()


# --------------------------------------------------------------------------- #
# CA 准备（生成 / 安装自有根 / 校验配对）
# --------------------------------------------------------------------------- #
@dataclass
class CaMaterial:
    cert_path: Path  # 私钥旁的自有根证书（secrets/ca/root.crt）
    key_path: Path  # CA 私钥（secrets/ca/root.key，0600）
    public_cert_path: Path  # 公开副本（data/ca/root.crt）
    intermediate_cert_path: Path
    intermediate_key_path: Path
    intermediate_public_path: Path
    fingerprint: str = ""
    subject: str = ""
    not_after: str = ""


def _write_private(path: Path, text: str) -> None:
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def _write_public(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    os.chmod(path, 0o644)


def _public_key_of(tmpdir: Path, pem: str, *, is_key: bool) -> str:
    """取 PEM 的公钥（证书用 -pubkey，私钥用 -pubout），用于比对配对。

    不用 `-modulus`：那只对 RSA 有效，自有根可能是 ECDSA（证书与私钥都是 PEM，
    公钥比对对两种算法都成立）。
    """
    path = tmpdir / ("key.pem" if is_key else "cert.pem")
    path.write_text(pem if pem.endswith("\n") else pem + "\n", encoding="utf-8")
    if is_key:
        return run_openssl(["pkey", "-in", str(path), "-pubout"])
    return run_openssl(["x509", "-in", str(path), "-noout", "-pubkey"])


def _verify_pair(cert_pem: str, key_pem: str) -> None:
    with tempfile.TemporaryDirectory(prefix="pki-pair-") as tmp:
        tmpdir = Path(tmp)
        cert_pub = _public_key_of(tmpdir, cert_pem, is_key=False)
        key_pub = _public_key_of(tmpdir, key_pem, is_key=True)
        if cert_pub.strip() != key_pub.strip():
            raise ConfigError(
                "ca_cert_pem 与 ca_key_pem 不配对：证书公钥与私钥公钥不一致，拒绝带病启动"
            )


def _generate_root(cfg: Config, cert_path: Path, key_path: Path) -> None:
    run_openssl(
        [
            "req",
            "-x509",
            "-newkey",
            "rsa:4096",
            "-nodes",
            "-sha256",
            "-days",
            str(cfg.ca_days),
            "-subj",
            f"/CN={cfg.ca_common_name}",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
        ]
    )
    os.chmod(key_path, 0o600)
    os.chmod(cert_path, 0o644)


def ensure_intermediate(cfg: Config, ca: CaMaterial) -> None:
    """按需生成中间 CA（由根签发），供 include_intermediate / missing_intermediate 使用。"""
    if ca.intermediate_cert_path.is_file() and ca.intermediate_key_path.is_file():
        _publish_intermediate(ca)
        return
    with tempfile.TemporaryDirectory(prefix="pki-int-") as tmp:
        tmpdir = Path(tmp)
        csr = tmpdir / "intermediate.csr"
        ext = tmpdir / "intermediate.ext"
        ext.write_text(
            "basicConstraints=critical,CA:TRUE,pathlen:0\n"
            "keyUsage=critical,keyCertSign,cRLSign\n",
            encoding="utf-8",
        )
        run_openssl(
            [
                "req",
                "-new",
                "-newkey",
                "rsa:4096",
                "-nodes",
                "-sha256",
                "-subj",
                f"/CN={cfg.ca_common_name} Intermediate",
                "-keyout",
                str(ca.intermediate_key_path),
                "-out",
                str(csr),
            ]
        )
        run_openssl(
            [
                "x509",
                "-req",
                "-in",
                str(csr),
                "-CA",
                str(ca.cert_path),
                "-CAkey",
                str(ca.key_path),
                "-CAcreateserial",
                "-days",
                str(cfg.ca_days),
                "-sha256",
                "-extfile",
                str(ext),
                "-out",
                str(ca.intermediate_cert_path),
            ]
        )
    os.chmod(ca.intermediate_key_path, 0o600)
    os.chmod(ca.intermediate_cert_path, 0o644)
    _publish_intermediate(ca)


def _publish_intermediate(ca: CaMaterial) -> None:
    _write_public(ca.intermediate_public_path, ca.intermediate_cert_path.read_text("utf-8"))


def prepare_ca(cfg: Config) -> CaMaterial:
    """准备并校验根：优先用配置里粘贴的自有根，否则复用已有根，最后才自动生成。"""
    CA_SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    ISSUED_SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    CA_PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    ISSUED_PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SECRETS_DIR, 0o700)
    os.chmod(CA_SECRETS_DIR, 0o700)
    os.chmod(ISSUED_SECRETS_DIR, 0o700)

    cert_path = CA_SECRETS_DIR / "root.crt"
    key_path = CA_SECRETS_DIR / "root.key"
    ca = CaMaterial(
        cert_path=cert_path,
        key_path=key_path,
        public_cert_path=CA_PUBLIC_DIR / "root.crt",
        intermediate_cert_path=CA_SECRETS_DIR / "intermediate.crt",
        intermediate_key_path=CA_SECRETS_DIR / "intermediate.key",
        intermediate_public_path=CA_PUBLIC_DIR / "intermediate.crt",
    )

    if cfg.ca_cert_pem or cfg.ca_key_pem:
        if not (cfg.ca_cert_pem and cfg.ca_key_pem):
            raise ConfigError(
                "ca_cert_pem 与 ca_key_pem 必须成对提供（只填一个无法启动）"
            )
        validate_pem(cfg.ca_cert_pem, "CERTIFICATE")
        validate_pem(cfg.ca_key_pem, "PRIVATE KEY")
        _verify_pair(cfg.ca_cert_pem, cfg.ca_key_pem)
        _write_public(cert_path, cfg.ca_cert_pem)
        _write_private(key_path, cfg.ca_key_pem)
        _write_public(ca.public_cert_path, cfg.ca_cert_pem)
        source = "配置粘贴的自有根"
    elif cert_path.is_file() and key_path.is_file():
        _write_public(ca.public_cert_path, cert_path.read_text("utf-8"))
        source = "复用已持久化的根"
    else:
        _generate_root(cfg, cert_path, key_path)
        _write_public(ca.public_cert_path, cert_path.read_text("utf-8"))
        source = "自动生成新根"

    ca.fingerprint = cert_fingerprint(cert_path)
    ca.subject = cert_subject(cert_path)
    ca.not_after = cert_not_after(cert_path)
    print(
        f"pki: CA 就绪（{source}）subject={ca.subject} "
        f"sha256={ca.fingerprint} not_after={ca.not_after}",
        flush=True,
    )
    return ca


# --------------------------------------------------------------------------- #
# 签发
# --------------------------------------------------------------------------- #
def _validate_cn(name: str) -> str:
    if not CN_PATTERN.fullmatch(name):
        raise BadRequest(f"common_name 非法: {name!r}（仅允许字母数字与 . _ - 空格 * @）")
    return name


def normalize_sans(sans: list[str]) -> list[str]:
    normalized: list[str] = []
    for item in sans:
        value = str(item).strip()
        if not value:
            continue
        try:
            ipaddress.ip_address(value)
        except ValueError:
            if not DNS_PATTERN.fullmatch(value):
                raise BadRequest(f"san 既不是 IP 也不是合法 DNS 名: {value!r}")
        normalized.append(value)
    return normalized


def _san_entries(sans: list[str]) -> list[str]:
    entries: list[str] = []
    for value in sans:
        try:
            ipaddress.ip_address(value)
        except ValueError:
            entries.append(f"DNS:{value}")
        else:
            entries.append(f"IP:{value}")
    return entries


def _extensions(sans: list[str]) -> str:
    lines = [
        "basicConstraints=critical,CA:FALSE",
        "keyUsage=critical,digitalSignature,keyEncipherment",
        "extendedKeyUsage=serverAuth",
    ]
    entries = _san_entries(sans)
    if entries:
        lines.append("subjectAltName=" + ",".join(entries))
    return "\n".join(lines) + "\n"


def _keygen_args(algorithm: str, key_size: int | None) -> list[str]:
    """返回 genpkey 的算法参数。

    注意 genpkey 的 RSA 位数选项是 `rsa_keygen_bits:<n>`（不是 req -newkey 的 `rsa:<n>`；
    后者在 genpkey 里会被拒："Error setting rsa:2048 parameter ... command not supported"）。
    """
    if algorithm in ("rsa", "rsa2048", "rsa4096"):
        bits = key_size if key_size else (2048 if algorithm == "rsa" else int(algorithm[3:]))
        return ["-algorithm", "RSA", "-pkeyopt", f"rsa_keygen_bits:{bits}"]
    if algorithm == "ec_p256":
        return ["-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"]
    raise BadRequest(f"未知的 algorithm: {algorithm!r}（可选 rsa2048 / rsa4096 / ec_p256）")


def _generate_key(algorithm: str, key_size: int | None, key_path: Path) -> None:
    run_openssl(["genpkey", *_keygen_args(algorithm, key_size), "-out", str(key_path)])


def _make_csr(key_path: Path, common_name: str, sans: list[str], csr_path: Path) -> None:
    args = [
        "req",
        "-new",
        "-key",
        str(key_path),
        "-sha256",
        "-subj",
        f"/CN={common_name}",
        "-out",
        str(csr_path),
    ]
    entries = _san_entries(sans)
    if entries:
        args += ["-addext", "subjectAltName=" + ",".join(entries)]
    run_openssl(args)


def _csr_common_name(csr_path: Path) -> str | None:
    out = run_openssl(["req", "-in", str(csr_path), "-noout", "-subject"])
    match = re.search(r"CN\s*=\s*([^,/\n]+)", out)
    return match.group(1).strip() if match else None


def _csr_sans(csr_path: Path) -> list[str]:
    """从 CSR 里解出 SAN（BMC GenerateCSR 会把主机名/IP 放进 SAN）。

    用 `-text` 而不是 `-ext subjectAltName`：后者是 OpenSSL 3.0 才有的 req 选项，
    而 `-text` 在所有版本都在（输出里那段 "Subject Alternative Name" 后面就是条目）。
    """
    try:
        out = run_openssl(["req", "-in", str(csr_path), "-noout", "-text"])
    except OpensslError:
        return []
    sans: list[str] = []
    match = re.search(r"Subject Alternative Name:?[^\n]*\n([^\n]*)", out)
    if match:
        line = match.group(1)
        sans.extend(re.findall(r"DNS:([^,\s]+)", line))
        sans.extend(re.findall(r"IP Address:([^,\s]+)", line))
    return sans


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%SZ")


def _variant_window(variant: str) -> tuple[str, str]:
    """过期 / 未生效变体的有效期窗口（notBefore, notAfter）。"""
    now = datetime.now(timezone.utc)
    if variant == "expired":
        return _fmt_time(now - timedelta(days=730)), _fmt_time(now - timedelta(days=365))
    return _fmt_time(now + timedelta(days=365)), _fmt_time(now + timedelta(days=730))


def _sign_with_explicit_dates(
    *,
    csr_path: Path,
    issuer_cert: Path,
    issuer_key: Path,
    out_path: Path,
    ext_text: str,
    digest: str,
    not_before: str,
    not_after: str,
) -> None:
    """用 `openssl ca` 显式指定有效期签发（用于过期 / 未生效变体）。

    为什么不用 `openssl x509 -req -not_before/-not_after`：那两个选项是 OpenSSL 3.2 才加的，
    而本镜像基于 Debian bookworm（OpenSSL 3.0，实测 3.0.20 上 `x509 -not_before` 直接报
    "Unknown option"）。`openssl ca` 一直在，且 -startdate/-enddate 能精确指定窗口。

    `openssl ca` 需要一个最小的 CA 工作目录（database / serial / new_certs_dir）与配置文件；
    这里在临时目录里现造，不碰宿主 CA 目录。调用方已持 SIGN_LOCK。
    """
    with tempfile.TemporaryDirectory(prefix="pki-ca-") as tmp:
        work = Path(tmp)
        (work / "newcerts").mkdir()
        (work / "index.txt").write_text("", encoding="utf-8")
        (work / "serial").write_text("01\n", encoding="utf-8")
        config_path = work / "ca.cnf"
        config_path.write_text(
            "[ca]\n"
            "default_ca = CA_default\n"
            "[CA_default]\n"
            # 用 as_posix()：OpenSSL 配置里反斜杠是转义字符，Windows 路径会被吃掉
            # （本地冒烟踩到：D:\... 变成 D:PersonalTemppki-...）
            f"dir = {work.as_posix()}\n"
            "database = $dir/index.txt\n"
            "new_certs_dir = $dir/newcerts\n"
            "serial = $dir/serial\n"
            f"default_md = {digest}\n"
            "policy = policy_any\n"
            "unique_subject = no\n"
            "[policy_any]\n"
            # 只要求 CSR 里必须带 commonName，其余 DN 字段原样保留
            "commonName = supplied\n"
            "[server_ext]\n"
            f"{ext_text}",
            encoding="utf-8",
        )
        run_openssl(
            [
                "ca",
                "-batch",
                "-notext",
                "-config",
                str(config_path),
                "-cert",
                str(issuer_cert),
                "-keyfile",
                str(issuer_key),
                "-in",
                str(csr_path),
                "-out",
                str(out_path),
                "-extensions",
                "server_ext",
                "-md",
                digest,
                "-startdate",
                not_before,
                "-enddate",
                not_after,
            ]
        )


def _next_name() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = stamp
    seq = 1
    while (ISSUED_PUBLIC_DIR / name).exists():
        seq += 1
        name = f"{stamp}-{seq}"
    return name


def issue(
    cfg: Config,
    ca: CaMaterial,
    request: dict[str, Any],
    variant: str | None = None,
    record: bool = True,
) -> dict[str, Any]:
    """签发一次（正向或变体），返回接口响应体；record=True 时落盘并登记清单。"""
    csr_pem = request.get("csr_pem") or ""
    requested_cn = (request.get("common_name") or "").strip()
    requested_sans = request.get("sans") or []
    days = int(request.get("days") or cfg.default_days)
    algorithm = (request.get("algorithm") or cfg.default_algorithm).strip()
    key_size = request.get("key_size")
    key_size = int(key_size) if key_size else None
    include_chain = request.get("include_chain")
    if include_chain is None:
        include_chain = cfg.include_intermediate

    if variant == "weak_signature" and not cfg.allow_weak_keys:
        raise BadRequest(
            "weak_signature 变体被禁用：需先在服务配置里打开 allow_weak_keys"
        )

    with tempfile.TemporaryDirectory(prefix="pki-issue-") as tmp:
        tmpdir = Path(tmp)
        leaf_path = tmpdir / "leaf.crt"
        csr_path = tmpdir / "leaf.csr"
        generated_key_path: Path | None = None
        csr_sans: list[str] = []

        if csr_pem:
            validate_pem(csr_pem, "CERTIFICATE REQUEST")
            csr_path.write_text(
                csr_pem if csr_pem.endswith("\n") else csr_pem + "\n", encoding="utf-8"
            )
            # -verify 校验 CSR 自签名（BMC 生成的是自签 CSR）。失败属**客户端输入错误** → 400：
            # 让它冒成 500 会把人引去查夹具，实际是 CSR 本身坏了（实测一次就复现）
            try:
                run_openssl(["req", "-in", str(csr_path), "-noout", "-verify"])
            except OpensslError as e:
                raise BadRequest(f"csr_pem 不是合法的证书请求（自签名校验失败）: {e}") from e
            csr_cn = _csr_common_name(csr_path)
            csr_sans = _csr_sans(csr_path)
        else:
            csr_cn = None

        common_name = requested_cn or csr_cn or ""
        if not common_name:
            raise BadRequest("common_name 必填（或提供带 CN 的 csr_pem）")
        _validate_cn(common_name)

        sans = normalize_sans(list(requested_sans)) if requested_sans else normalize_sans(csr_sans)

        if variant == "weak_signature":
            algorithm, key_size = "rsa", 1024

        if not csr_pem:
            generated_key_path = tmpdir / "leaf.key"
            _generate_key(algorithm, key_size, generated_key_path)
            _make_csr(generated_key_path, common_name, sans, csr_path)

        # 选用签发者：默认根；开启中间证书（或 missing_intermediate 变体）时用中间 CA
        use_intermediate = cfg.include_intermediate or variant == "missing_intermediate"
        if use_intermediate:
            ensure_intermediate(cfg, ca)
            issuer_cert = ca.intermediate_cert_path
            issuer_key = ca.intermediate_key_path
        else:
            issuer_cert = ca.cert_path
            issuer_key = ca.key_path

        ext_path = tmpdir / "leaf.ext"
        ext_path.write_text(_extensions(sans), encoding="utf-8")

        digest = "sha1" if variant == "weak_signature" else "sha256"
        if variant in ("expired", "not_yet_valid"):
            # 有效期精确指定的两种变体走 openssl ca（见 _sign_with_explicit_dates 的说明：
            # bookworm 的 OpenSSL 3.0 没有 x509 -not_before/-not_after）
            not_before, not_after = _variant_window(variant)
            with SIGN_LOCK:
                _sign_with_explicit_dates(
                    csr_path=csr_path,
                    issuer_cert=issuer_cert,
                    issuer_key=issuer_key,
                    out_path=leaf_path,
                    ext_text=ext_path.read_text("utf-8"),
                    digest=digest,
                    not_before=not_before,
                    not_after=not_after,
                )
        else:
            args = [
                "x509",
                "-req",
                "-in",
                str(csr_path),
                "-CA",
                str(issuer_cert),
                "-CAkey",
                str(issuer_key),
                "-CAcreateserial",
                "-out",
                str(leaf_path),
                "-extfile",
                str(ext_path),
                f"-{digest}",
                "-days",
                str(days),
            ]
            if variant == "cn_mismatch":
                args += ["-subj", f"/CN=mismatch.{common_name}"]
            with SIGN_LOCK:
                run_openssl(args)

        cert_pem = leaf_path.read_text("utf-8")
        if not cert_pem.endswith("\n"):
            cert_pem += "\n"

        # 证书链：missing_intermediate 故意只给根（叶由中间签发 → 链不完整）
        chain_pem: str | None = None
        if variant == "missing_intermediate":
            chain_pem = ca.public_cert_path.read_text("utf-8")
        elif include_chain:
            parts = []
            if use_intermediate:
                parts.append(ca.intermediate_public_path.read_text("utf-8"))
            parts.append(ca.public_cert_path.read_text("utf-8"))
            chain_pem = "".join(p if p.endswith("\n") else p + "\n" for p in parts)

        # 私钥：CSR 路径不给（私钥在调用方）；否则回传服务生成的私钥。
        # key_mismatch 变体故意回传一把与证书不配对的新钥匙。
        key_pem: str | None = None
        key_material_path: Path | None = None
        if variant == "key_mismatch":
            mismatched = tmpdir / "mismatched.key"
            _generate_key("rsa2048", None, mismatched)
            key_pem = mismatched.read_text("utf-8")
            key_material_path = mismatched
        elif generated_key_path is not None:
            key_pem = generated_key_path.read_text("utf-8")
            key_material_path = generated_key_path

        serial = cert_serial(leaf_path)
        response: dict[str, Any] = {
            "cert_pem": cert_pem,
            "chain_pem": chain_pem,
            "key_pem": key_pem,
            "serial": serial,
            "fingerprint_sha256": cert_fingerprint(leaf_path),
            "not_before": cert_not_before(leaf_path),
            "not_after": cert_not_after(leaf_path),
        }

        if record:
            with SIGN_LOCK:
                name = _next_name()
                public_dir = ISSUED_PUBLIC_DIR / name
                public_dir.mkdir(parents=True, exist_ok=True)
                _write_public(public_dir / "leaf.crt", cert_pem)
                if chain_pem:
                    _write_public(public_dir / "chain.pem", chain_pem)
                if csr_pem:
                    _write_public(public_dir / "leaf.csr", csr_path.read_text("utf-8"))
                if key_material_path is not None:
                    _write_private(
                        ISSUED_SECRETS_DIR / f"{name}.key",
                        key_material_path.read_text("utf-8"),
                    )
                meta = {
                    "name": name,
                    "common_name": common_name,
                    "variant": variant,
                    "sans": sans,
                    "algorithm": algorithm,
                    "serial": serial,
                    "fingerprint_sha256": response["fingerprint_sha256"],
                    "not_before": response["not_before"],
                    "not_after": response["not_after"],
                    "has_chain": chain_pem is not None,
                    "path": str(public_dir),
                }
                (public_dir / "meta.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            response["name"] = name
        return response


# --------------------------------------------------------------------------- #
# HTTP API
# --------------------------------------------------------------------------- #
CONFIG: Config | None = None
CA: CaMaterial | None = None


def _config() -> Config:
    assert CONFIG is not None
    return CONFIG


def _ca() -> CaMaterial:
    assert CA is not None
    return CA


def _list_issued() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not ISSUED_PUBLIC_DIR.is_dir():
        return entries
    for meta_path in sorted(ISSUED_PUBLIC_DIR.glob("*/meta.json")):
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries.append(meta)
    entries.sort(key=lambda item: str(item.get("name", "")), reverse=True)
    return entries


class Handler(BaseHTTPRequestHandler):
    server_version = "fx-pki/1.0"
    protocol_version = "HTTP/1.1"

    # ---------------- 基础输出 ----------------
    def log_message(self, fmt: str, *args: Any) -> None:
        # 日志统一进 stdout（平台「查看日志」走 docker logs；stderr 也在里面，但保持单一出口）
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sys.stdout.write(f"{stamp} {self.address_string()} {fmt % args}\n")
        sys.stdout.flush()

    def _send_json(self, status: int, payload: dict[str, Any] | list[Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise BadRequest(f"请求体不是合法 JSON: {e}") from e
        if not isinstance(body, dict):
            raise BadRequest("请求体必须是 JSON 对象")
        return body

    def _handle(self, func: Any) -> None:
        try:
            func()
        except BadRequest as e:
            self._error(400, str(e))
        except NotFound as e:
            self._error(404, str(e))
        except OpensslError as e:
            self._error(500, str(e))
        except Exception as e:  # noqa: BLE001 - 兜底：任何未预期错误也给 JSON 而非裸断连
            self._error(500, f"internal error: {e}")

    # ---------------- 路由 ----------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/healthz":
            self._handle(self._healthz)
        elif path == "/api/v1/ca":
            self._handle(self._get_ca)
        elif path == "/api/v1/issued":
            self._handle(self._get_issued)
        elif path.startswith("/api/v1/issued/"):
            name = unquote(path[len("/api/v1/issued/") :])
            self._handle(lambda: self._get_issued_one(name))
        elif path == "/api/v1/variants":
            self._handle(self._get_variants)
        else:
            self._error(404, f"未找到路径: {path}")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/api/v1/sign":
            self._handle(self._post_sign)
        elif path.startswith("/api/v1/variants/"):
            name = unquote(path[len("/api/v1/variants/") :])
            self._handle(lambda: self._post_variant(name))
        else:
            self._error(404, f"未找到路径: {path}")

    # ---------------- 处理器 ----------------
    def _healthz(self) -> None:
        """真的签一次一次性 CSR 自检：CA 可用且能签发才算健康（不是进程活着）。"""
        cfg = _config()
        try:
            issue(
                cfg,
                _ca(),
                {
                    "common_name": "healthcheck.invalid",
                    "sans": ["healthcheck.invalid"],
                    "days": 1,
                },
                record=False,
            )
        except Exception as e:  # noqa: BLE001 - 健康检查只回 ok/原因
            self._send_json(503, {"ok": False, "error": f"签发自检失败: {e}"})
            return
        self._send_json(200, {"ok": True, "ca_fingerprint": _ca().fingerprint})

    def _fault_guard(self) -> None:
        """在签发路径上施加故障注入（健康检查不受影响）。"""
        cfg = _config()
        if cfg.fault_mode == "slow_response":
            import time

            time.sleep(max(cfg.slow_seconds, 0))
        elif cfg.fault_mode == "sign_error":
            raise OpensslError(
                "fault injection: fault_mode=sign_error，本服务被配置为签发必失败"
            )

    def _get_ca(self) -> None:
        ca = _ca()
        chain_pem = None
        if ca.intermediate_public_path.is_file():
            chain_pem = ca.intermediate_public_path.read_text("utf-8")
        self._send_json(
            200,
            {
                "cert_pem": ca.public_cert_path.read_text("utf-8"),
                "chain_pem": chain_pem,
                "fingerprint_sha256": ca.fingerprint,
                "subject": ca.subject,
                "not_after": ca.not_after,
            },
        )

    def _get_issued(self) -> None:
        self._send_json(200, _list_issued())

    def _get_issued_one(self, name: str) -> None:
        if not name or "/" in name or ".." in name or Path(name).name != name:
            raise BadRequest("非法的签发名")
        public_dir = ISSUED_PUBLIC_DIR / name
        leaf = public_dir / "leaf.crt"
        if not leaf.is_file():
            raise NotFound(f"未找到已签发产物: {name}")
        chain = public_dir / "chain.pem"
        self._send_json(
            200,
            {
                "cert_pem": leaf.read_text("utf-8"),
                "chain_pem": chain.read_text("utf-8") if chain.is_file() else None,
            },
        )

    def _get_variants(self) -> None:
        self._send_json(200, VARIANTS)

    def _post_sign(self) -> None:
        self._fault_guard()
        body = self._read_json_body()
        response = issue(_config(), _ca(), body, record=True)
        self._send_json(200, response)

    def _post_variant(self, name: str) -> None:
        names = {entry["name"] for entry in VARIANTS}
        if name not in names:
            raise NotFound(f"未知变体: {name}（可用: {sorted(names)}）")
        self._fault_guard()
        body = self._read_json_body()
        if not body.get("common_name") and not body.get("csr_pem"):
            body["common_name"] = f"variant-{name}.test.lab"
        response = issue(_config(), _ca(), body, variant=name, record=True)
        self._send_json(200, response)


def make_server(port: int) -> ThreadingHTTPServer:
    """监听服务器：优先双栈（:: + IPV6_V6ONLY=0，v4 走 v4-mapped），失败回落 IPv4。

    项目规范（.trellis/spec/services/index.md）默认要求双栈；Python 的
    http.server 默认只绑 IPv4，所以这里显式绑 :: 并关掉 v6only。宿主/内核不支持
    IPv6 时退回 0.0.0.0，不影响 v4 使用。
    """
    if socket.has_ipv6:

        class DualStackServer(ThreadingHTTPServer):
            address_family = socket.AF_INET6

            def server_bind(self) -> None:
                try:
                    self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                except OSError:
                    pass
                super().server_bind()

        try:
            return DualStackServer(("::", port), Handler)
        except OSError as e:
            print(f"pki: 双栈监听失败（{e}），回落 IPv4", flush=True)
    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


def healthcheck(conf_path: Path) -> int:
    """供 Docker HEALTHCHECK 用的最小探针：读配置取端口，GET /healthz。"""
    try:
        cfg = Config.from_values(parse_conf(conf_path))
    except (OSError, ConfigError) as e:
        print(f"healthcheck: 无法读取配置: {e}", flush=True)
        return 1
    url = f"http://127.0.0.1:{cfg.listen_port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        print(f"healthcheck: {url} 请求失败: {e}", flush=True)
        return 1
    if payload.get("ok") is True:
        print(f"healthcheck: ok ca_fingerprint={payload.get('ca_fingerprint')}", flush=True)
        return 0
    print(f"healthcheck: 不健康: {payload}", flush=True)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="证书服务（CA）夹具 API")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="只做配置解析与 CA 准备/校验，不启动 HTTP 服务（entrypoint 用它做启动前自检）",
    )
    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help="读取配置并探测本机 /healthz，供容器 HEALTHCHECK 使用",
    )
    args = parser.parse_args(argv)

    conf_path = Path(args.config)
    if args.healthcheck:
        return healthcheck(conf_path)

    global CONFIG, CA  # noqa: PLW0603 - 单进程夹具，全局持有配置与 CA
    try:
        values = parse_conf(conf_path)
        CONFIG = Config.from_values(values)
        CA = prepare_ca(CONFIG)
    except (OSError, ConfigError, OpensslError) as e:
        print(f"pki: 启动前检查失败，拒绝带病启动: {e}", file=sys.stderr, flush=True)
        return 2
    if args.prepare_only:
        return 0

    server = make_server(CONFIG.listen_port)
    server.daemon_threads = True
    print(
        f"pki: API 监听双栈（:: 与 IPv4）端口 {CONFIG.listen_port} "
        f"fault_mode={CONFIG.fault_mode} allow_weak_keys={CONFIG.allow_weak_keys}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
