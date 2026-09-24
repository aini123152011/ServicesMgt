"""manifest.usage 契约测试：形状不对的服务不注册，形状对了页面还得真的拿到。

两段：① `registry._check_manifest` 对 usage 形状的接受/拒绝；② 仓库里 13 个服务的
真实 usage 内容（占位符、端口）。第 ② 段存在的理由：usage 是手写 YAML，
端口写错、占位符拼错都不会让任何东西报错，只会在测试人员照抄命令时失败。
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from app import registry
from app.core.config import settings

SERVICES_DIR = Path(settings.SERVICES_DIR).resolve()

# 命令里允许出现的占位符，渲染时由前端替换：{{host}}=页面所在主机，{{port}}=首个端口
ALLOWED_PLACEHOLDERS = {"{{host}}", "{{port}}"}
PLACEHOLDER_PATTERN = re.compile(r"\{\{[^}]*\}\}")

VALID_ENTRY: dict[str, str] = {
    "target": "BMC",
    "summary": "把 BMC 的 NTP 主服务器指向本服务",
    "command": "server {{host}} iburst",
}


def _manifest_with_usage(usage: Any) -> dict[str, Any]:
    """构造一份除 usage 外全部合法的 manifest，把被测字段隔离出来。"""
    manifest: dict[str, Any] = {
        "name": "chrony",
        "display_name": "NTP 时间同步",
        "category": "time",
        "description": "usage 校验测试用 manifest",
        "container_name": "fx-chrony",
        "config_dir": "/etc/chrony",
        "config_files": ["chrony.conf"],
        "ports": [{"port": 123, "protocol": "udp", "description": "NTP 服务端口"}],
        "reload_mode": "hot",
    }
    if usage is not None:
        manifest["usage"] = usage
    return manifest


def _manifests() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for manifest_path in sorted(SERVICES_DIR.glob("*/manifest.yaml")):
        out.append(
            (
                manifest_path.parent.name,
                yaml.safe_load(manifest_path.read_text(encoding="utf-8")),
            )
        )
    return out


MANIFESTS = _manifests()


def test_usage_absent_is_valid() -> None:
    """usage 是可选的：没有对外用法的服务不声明它也必须能注册。"""
    assert registry._check_manifest(_manifest_with_usage(None), "chrony") == []


def test_valid_usage_passes() -> None:
    usage = [VALID_ENTRY, {**VALID_ENTRY, "target": "Linux"}]
    assert registry._check_manifest(_manifest_with_usage(usage), "chrony") == []


@pytest.mark.parametrize(
    "usage",
    [
        pytest.param("not-a-list", id="not-a-list"),
        pytest.param([VALID_ENTRY, "not-an-object"], id="entry-not-an-object"),
        pytest.param(
            [{"target": "BMC", "summary": "缺 command"}], id="missing-command"
        ),
        pytest.param([{"target": "BMC", "command": "server x"}], id="missing-summary"),
        pytest.param([{"summary": "s", "command": "c"}], id="missing-target"),
        pytest.param([{**VALID_ENTRY, "command": "   "}], id="blank-command"),
        pytest.param([{**VALID_ENTRY, "target": ""}], id="empty-target"),
    ],
)
def test_invalid_usage_is_rejected(usage: Any) -> None:
    """缺字段/空字符串一律记问题：静默忽略会让作者以为写了就有。"""
    problems = registry._check_manifest(_manifest_with_usage(usage), "chrony")
    assert any(problem.startswith("usage") for problem in problems), problems


def test_manifests_discovered() -> None:
    """至少要能发现 13 个服务的 manifest，避免路径写错导致下面全部空跑。"""
    assert len(MANIFESTS) == 13, [name for name, _ in MANIFESTS]


@pytest.mark.parametrize(
    "service,manifest", MANIFESTS, ids=[name for name, _ in MANIFESTS]
)
def test_every_service_declares_valid_usage(
    service: str, manifest: dict[str, Any]
) -> None:
    """13 个服务的 manifest 必须整体合法，且 usage 条目数落在 2–3 条（AC1）。"""
    usage = manifest.get("usage")
    assert usage, f"{service} 未声明 usage"
    assert 2 <= len(usage) <= 3, (
        f"{service} 的 usage 条目数为 {len(usage)}，应为 2–3 条"
    )
    problems = registry._check_manifest(manifest, service)
    assert problems == [], f"{service} manifest 校验失败: {problems}"


@pytest.mark.parametrize(
    "service,manifest", MANIFESTS, ids=[name for name, _ in MANIFESTS]
)
def test_usage_commands_only_use_known_placeholders(
    service: str, manifest: dict[str, Any]
) -> None:
    """命令里只允许 {{host}}/{{port}}：别的占位符前端不会替换，会原样显示给用户。"""
    for index, entry in enumerate(manifest["usage"]):
        found = set(PLACEHOLDER_PATTERN.findall(entry["command"]))
        assert found <= ALLOWED_PLACEHOLDERS, (
            f"{service}.usage[{index}] 出现未知占位符: {sorted(found)}"
        )


@pytest.mark.parametrize(
    "service,manifest", MANIFESTS, ids=[name for name, _ in MANIFESTS]
)
def test_port_placeholder_requires_declared_port(
    service: str, manifest: dict[str, Any]
) -> None:
    """命令用了 {{port}} 就必须有端口可填：前端取的是 manifest 的第一个端口。"""
    uses_port = any("{{port}}" in entry["command"] for entry in manifest["usage"])
    if uses_port:
        assert manifest.get("ports"), (
            f"{service} 的命令用了 {{{{port}}}} 但没声明 ports"
        )


def test_detail_api_carries_usage(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """详情接口必须真的把 usage 交给前端。

    pydantic 默认丢弃未声明的键：漏声明时注册表校验照样通过、GET /services/{name}
    却少一个字段，页面上那张卡片永远不出现，而且没有任何报错。
    """
    missing: list[str] = []
    for name, _ in MANIFESTS:
        response = client.get(
            f"{settings.API_V1_STR}/services/{name}", headers=superuser_token_headers
        )
        assert response.status_code == 200, (name, response.text)
        usage = response.json()["manifest"].get("usage")
        if not usage or not usage[0]["command"]:
            missing.append(name)
    assert not missing, f"这些服务的 usage 没进详情响应（前端拿不到）: {missing}"
