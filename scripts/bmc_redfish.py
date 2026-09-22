"""BMC Redfish 操作小工具：GET/PATCH（自动带 If-Match）+ 读回重试。

只用于 BMC 侧验证的探索与脚本化；凭据从环境变量取，不落仓库。
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

BMC_HOST = os.environ.get("BMC_HOST", "")
BMC_USER = os.environ.get("BMC_USER", "")
BMC_PASSWORD = os.environ.get("BMC_PASSWORD", "")
MANAGER_ID = os.environ.get("BMC_MANAGER_ID", "0")

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def _request(method: str, path: str, body: dict | None = None, etag: str | None = None):
    url = f"https://{BMC_HOST}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if BMC_USER:
        import base64

        token = base64.b64encode(f"{BMC_USER}:{BMC_PASSWORD}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    if etag:
        req.add_header("If-Match", etag)
    try:
        with urllib.request.urlopen(req, context=_CTX, timeout=25) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, resp.headers.get("ETag"), json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("ETag"), e.read().decode()[:300]


def get(path: str):
    """GET 一个资源，返回 (status, etag, json)。"""
    return _request("GET", path)


def patch(path: str, body: dict):
    """PATCH 一个资源：自动取 ETag 并带 If-Match（缺了会 428），返回 (status, 响应体文本)。"""
    status, etag, _ = get(path)
    if status != 200:
        return status, None
    status, _, doc = _request("PATCH", path, body, etag=etag)
    return status, doc


def read_back(path: str, pick, expect, attempts: int = 4, delay: float = 3.0):
    """读回并重试：BMC 设置成功后短时间内的 GET 可能仍是旧值（实测踩到过）。"""
    last = None
    for _ in range(attempts):
        _, _, doc = get(path)
        last = pick(doc)
        if last == expect:
            return True, last
        time.sleep(delay)
    return False, last


def manager_path(suffix: str = "") -> str:
    """Manager 下的资源路径；Manager 的 id 两台机器不一样（0/1），故走环境变量。"""
    return f"/redfish/v1/Managers/{MANAGER_ID}{suffix}"


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "get"
    path = sys.argv[2] if len(sys.argv) > 2 else manager_path()
    if action == "get":
        status, etag, doc = get(path)
        print(f"HTTP {status} etag={etag}")
        print(json.dumps(doc, ensure_ascii=False, indent=1)[:2000])
    elif action == "patch":
        body = json.loads(sys.argv[3])
        status, resp = patch(path, body)
        print(f"PATCH HTTP {status} {resp if isinstance(resp, str) else ''}")
