"""二层夹具路由测试：状态读取、预检拦截、启用/切换、停用与失败回滚。

Docker 交互全部打桩（单测不连真实 daemon），但 `.env` 用 tmp_path 上的真实文件跑一遍读写——
「只改白名单键、其余字节不动」这条契约只有真读写才能验。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app import host_network, l2_network, lifecycle
from app.api.routes import l2
from app.core.config import settings
from app.crud import upsert_service_config
from app.models import AuditLog, ServiceConfig, get_datetime_utc

ENV_SAMPLE = """# 部署变量（这段注释必须原样保留）
PROJECT_NAME=BMC Services Platform
SECRET_KEY=do-not-touch-me
POSTGRES_PASSWORD=also-do-not-touch

DHCP_PARENT_IFACE=enp125s0f1
L2_SUBNET=192.168.90.0/24
L2_GATEWAY=192.168.90.1
L2_SUBNET_V6=fd00:90::/64
L2_GATEWAY_V6=fd00:90::1
L2_SERVICES=dhcp,tftpd-hpa,rsyslog,chrony
"""

L2_NETWORK = "fx_dhcp-l2-net"

# dhcp 服务当前配置（旧网段 192.168.90.0/24）
DHCP_VALUES: dict[str, Any] = {
    "domain": "bmc.lab",
    "pool_start": "192.168.90.100",
    "pool_end": "192.168.90.200",
    "lease_time": "12h",
    "gateway": "192.168.90.1",
    "dns_servers": [],
    "dns_records": ["bmc-01,192.168.90.10,fd00:90::10"],
    "static_hosts": [],
    "ra_mode": "stateful",
    "ipv6_prefix": "fd00:90::/64",
    "ipv6_pool_start": "fd00:90::100",
    "ipv6_pool_end": "fd00:90::200",
    "next_server": "",
    "boot_file": "",
    "fault_mode": "none",
}

# 目标参数：换网口 + 换网段（联动应把池子/网关/前缀一起改）
TARGET = {
    "parent_iface": "enp125s0f3",
    "l2_subnet": "192.168.95.0/24",
    "l2_gateway": "192.168.95.1",
    "l2_subnet_v6": "fd00:95::/64",
    "l2_gateway_v6": "fd00:95::1",
    "sync_service_config": True,
}


@pytest.fixture(autouse=True)
def _env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """把部署目录挂载点指到 tmp_path，并在其中放一份与目标机同构的 .env。"""
    path = tmp_path / ".env"
    path.write_text(ENV_SAMPLE, encoding="utf-8")
    monkeypatch.setattr(settings, "HOST_DEPLOY_DIR", str(tmp_path))
    return path


@pytest.fixture(autouse=True)
def _clear_audit_logs(db: Session) -> None:
    """每个用例前清审计，断言只看本次操作写入的记录。"""
    db.exec(delete(AuditLog))
    db.commit()


@pytest.fixture(autouse=True)
def _clear_service_configs(db: Session) -> None:
    """清空服务配置表，避免用例之间串味。"""
    for row in db.exec(select(ServiceConfig)).all():
        db.delete(row)
    db.commit()


@pytest.fixture(autouse=True)
def _fake_host_facts(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """替身宿主事实：管理口（带默认路由）、测试口、没插线的口各一个。"""
    facts: dict[str, Any] = {
        "ip_source": "ok",
        "default_iface": "enp125s0f0",
        "interfaces": [
            {
                "name": "enp125s0f0",
                "carrier": 1,
                "speed_mbps": 1000,
                "mac": "aa:aa:aa:aa:aa:01",
                "ipv4": [
                    {
                        "address": "192.168.0.10",
                        "netmask": "255.255.0.0",
                        "cidr": "192.168.0.0/16",
                    }
                ],
                "ipv6": [],
            },
            {
                "name": "enp125s0f1",
                "carrier": 1,
                "speed_mbps": 1000,
                "mac": "aa:aa:aa:aa:aa:02",
                "ipv4": [
                    {
                        "address": "192.168.90.1",
                        "netmask": "255.255.255.0",
                        "cidr": "192.168.90.0/24",
                    }
                ],
                "ipv6": [
                    {"address": "fd00:90::1", "prefix": 64, "cidr": "fd00:90::/64"}
                ],
            },
            {
                "name": "enp125s0f3",
                "carrier": 1,
                "speed_mbps": 10000,
                "mac": "aa:aa:aa:aa:aa:04",
                "ipv4": [],
                "ipv6": [],
            },
            {
                "name": "enp125s0f2",
                "carrier": 0,
                "speed_mbps": None,
                "mac": "aa:aa:aa:aa:aa:03",
                "ipv4": [],
                "ipv6": [],
            },
        ],
    }
    monkeypatch.setattr(host_network, "host_facts", lambda: facts)
    return facts


@pytest.fixture(autouse=True)
def _fake_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    """替身绑定：dhcp 挂在 macvlan 上，地址是 .2 / ::2。"""
    monkeypatch.setattr(
        host_network,
        "service_bindings",
        lambda names: [
            {
                "service": "dhcp",
                "container": "fx-dhcp",
                "network": L2_NETWORK,
                "parent": "enp125s0f1",
                "attached": True,
                "address": "192.168.90.2",
                "address_v6": "fd00:90::2",
            }
        ],
    )


@pytest.fixture(autouse=True)
def _fake_docker_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """替身 Docker 客户端：路由只把它透传给 l2_network 的替身函数。"""
    monkeypatch.setattr(l2_network, "get_client", lambda: object())


@pytest.fixture(autouse=True)
def _fake_archive_leases(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """替身租约归档：记录调用并返回一个归档路径（网段变更时会走这里）。"""
    calls: list[str] = []

    def _fake(*_args: Any, **_kwargs: Any) -> str:
        calls.append("archived")
        return "/var/lib/dnsmasq/dnsmasq.leases.bak.test"

    monkeypatch.setattr(l2_network, "archive_leases", _fake)
    return calls


@pytest.fixture(autouse=True)
def _default_l2_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认网络状态：已启用且与 .env 一致；用例需要别的状态时自行覆盖 read_state。"""
    monkeypatch.setattr(
        l2_network,
        "read_state",
        lambda *a, **k: l2_network.L2NetworkState(
            network=L2_NETWORK,
            parent="enp125s0f1",
            attached=True,
            exists=True,
            subnet="192.168.90.0/24",
            gateway="192.168.90.1",
            subnet_v6="fd00:90::/64",
            gateway_v6="fd00:90::1",
            address="192.168.90.2",
            address_v6="fd00:90::2",
        ),
    )


@pytest.fixture()
def fake_l2_state(monkeypatch: pytest.MonkeyPatch) -> l2_network.L2NetworkState:
    """替身网络状态：已启用且参数与 .env 一致。"""
    state = l2_network.L2NetworkState(
        network=L2_NETWORK,
        parent="enp125s0f1",
        attached=True,
        exists=True,
        subnet="192.168.90.0/24",
        gateway="192.168.90.1",
        subnet_v6="fd00:90::/64",
        gateway_v6="fd00:90::1",
        address="192.168.90.2",
        address_v6="fd00:90::2",
    )
    monkeypatch.setattr(l2_network, "read_state", lambda *a, **k: state)
    return state


@pytest.fixture()
def fake_restart(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """替身容器重启：记录被重启的容器名。"""
    calls: list[str] = []
    monkeypatch.setattr(lifecycle, "restart", lambda name: calls.append(name))
    return calls


@pytest.fixture()
def fake_apply_config(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """替身服务配置下发：记录调用参数并返回 applied=True。"""
    calls: list[dict[str, Any]] = []

    def _fake(**kwargs: Any) -> bool:
        calls.append(kwargs)
        return True

    monkeypatch.setattr(l2, "apply_config_values", _fake)
    return calls


@pytest.fixture()
def fake_apply_network(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """替身网络重建：记录参数；默认成功。"""
    calls: list[dict[str, Any]] = []

    def _fake(*_args: Any, **kwargs: Any) -> tuple[Any, list[str]]:
        calls.append(kwargs)
        return (
            l2_network.L2NetworkState(
                network=L2_NETWORK, parent=kwargs["parent"], attached=True, exists=True
            ),
            [f"已创建网络 {L2_NETWORK}（parent={kwargs['parent']}）"],
        )

    monkeypatch.setattr(l2_network, "apply_network", _fake)
    return calls


def _seed_dhcp_config(db: Session, values: dict[str, Any] | None = None) -> None:
    upsert_service_config(
        session=db,
        service_name="dhcp",
        values=dict(values or DHCP_VALUES),
        rendered_at=get_datetime_utc(),
        applied=True,
    )


def test_read_status_requires_auth(client: TestClient) -> None:
    """未登录访问 /l2/status 保持模板现状：401。"""
    assert client.get(f"{settings.API_V1_STR}/l2/status").status_code == 401


def test_read_status_returns_env_network_and_candidates(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    fake_l2_state: l2_network.L2NetworkState,  # noqa: ARG001 - 只用于激活 monkeypatch
) -> None:
    """状态接口把 .env 权威值、Docker 实际值、候选网口与命令一次给全。"""
    _seed_dhcp_config(db)

    response = client.get(
        f"{settings.API_V1_STR}/l2/status", headers=superuser_token_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["env_available"] is True
    assert body["parent_iface"] == "enp125s0f1"
    assert body["l2_subnet"] == "192.168.90.0/24"
    assert body["l2_services"] == ["chrony", "dhcp", "rsyslog", "tftpd-hpa"]
    assert body["network_parent"] == "enp125s0f1"
    assert body["address"] == "192.168.90.2"
    assert body["drift"] == []
    assert [item["code"] for item in body["checks"]] == ["l2_ok"]

    candidates = {item["name"]: item for item in body["candidates"]}
    assert candidates["enp125s0f1"]["is_parent"] is True
    assert candidates["enp125s0f1"]["selectable"] is True
    assert candidates["enp125s0f3"]["selectable"] is True
    assert candidates["enp125s0f2"]["selectable"] is False
    assert "没有链路" in candidates["enp125s0f2"]["reason"]
    assert candidates["enp125s0f0"]["selectable"] is False
    assert "默认路由" in candidates["enp125s0f0"]["reason"]

    assert body["nmcli_commands"] == [
        'nmcli con mod enp125s0f1 ipv4.addresses 192.168.90.1/24 ipv4.gateway "" '
        "ipv4.never-default yes ipv6.method manual ipv6.addresses fd00:90::1/64",
        "nmcli con up enp125s0f1",
    ]


def test_read_status_reports_drift(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`.env` 与实际网络不一致时必须显式提示（否则页面显示 A、实际是 B）。"""
    _seed_dhcp_config(db)
    monkeypatch.setattr(
        l2_network,
        "read_state",
        lambda *a, **k: l2_network.L2NetworkState(
            network=L2_NETWORK,
            parent="enp125s0f2",
            attached=True,
            exists=True,
            subnet="192.168.91.0/24",
            gateway="192.168.91.1",
            subnet_v6="fd00:91::/64",
            gateway_v6="fd00:91::1",
        ),
    )

    body = client.get(
        f"{settings.API_V1_STR}/l2/status", headers=superuser_token_headers
    ).json()

    assert len(body["drift"]) == 3
    assert any("父口" in item for item in body["drift"])
    assert any("192.168.90.0/24" in item for item in body["drift"])


def test_read_status_degrades_when_env_missing(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,  # noqa: ARG001 - 只用于激活 monkeypatch
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """部署目录没挂载时降级用容器环境变量，并把原因带出去（而不是 500）。"""
    monkeypatch.setattr(settings, "HOST_DEPLOY_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(settings, "DHCP_PARENT_IFACE", "enp125s0f1")
    monkeypatch.setattr(settings, "L2_SUBNET", "192.168.90.0/24")

    body = client.get(
        f"{settings.API_V1_STR}/l2/status", headers=superuser_token_headers
    ).json()

    assert body["env_available"] is False
    assert body["env_error"]
    assert body["parent_iface"] == "enp125s0f1"


def test_preflight_rejects_invalid_subnet(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """网段解析不了直接 400，文案与实现精确对应。"""
    response = client.post(
        f"{settings.API_V1_STR}/l2/preflight",
        headers=superuser_token_headers,
        json={**TARGET, "l2_subnet": "not-a-subnet"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid L2 subnet: 'not-a-subnet'"


def test_preflight_requires_admin(
    client: TestClient, readonly_token_headers: dict[str, str]
) -> None:
    """预检也要 admin（它能读 .env 里的网段与宿主网口事实）。"""
    response = client.post(
        f"{settings.API_V1_STR}/l2/preflight",
        headers=readonly_token_headers,
        json=TARGET,
    )

    assert response.status_code == 403


def test_preflight_blocks_default_route_parent(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """把承载默认路由的口当父口必须判为阻断（ok=false）。"""
    _seed_dhcp_config(db)

    body = client.post(
        f"{settings.API_V1_STR}/l2/preflight",
        headers=superuser_token_headers,
        json={
            **TARGET,
            "parent_iface": "enp125s0f0",
            "l2_subnet": "192.168.0.0/16",
            "l2_gateway": "192.168.0.10",
            "l2_subnet_v6": "fd00:90::/64",
            "l2_gateway_v6": "fd00:90::1",
        },
    ).json()

    assert body["ok"] is False
    assert "parent_has_default_route" in [item["code"] for item in body["blocking"]]


def test_preflight_plans_service_config_changes(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """预检要给出将要联动的 dhcp 配置差异，供页面确认。"""
    _seed_dhcp_config(db)

    body = client.post(
        f"{settings.API_V1_STR}/l2/preflight",
        headers=superuser_token_headers,
        json=TARGET,
    ).json()

    assert body["service_config"] == {
        "gateway": "192.168.95.1",
        "ipv6_prefix": "fd00:95::/64",
        "pool_start": "192.168.95.100",
        "pool_end": "192.168.95.200",
        "ipv6_pool_start": "fd00:95::100",
        "ipv6_pool_end": "fd00:95::200",
    }
    assert any("重建 macvlan 网络" in step for step in body["steps"])


def test_apply_requires_admin(
    client: TestClient, readonly_token_headers: dict[str, str]
) -> None:
    """改数据面的操作只允许 admin。"""
    response = client.put(
        f"{settings.API_V1_STR}/l2/config",
        headers=readonly_token_headers,
        json=TARGET,
    )

    assert response.status_code == 403


def test_apply_blocks_dangerous_params_without_any_change(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    _env_file: Path,
    fake_apply_network: list[dict[str, Any]],
    fake_restart: list[str],
) -> None:
    """被预检拦下时零变更：.env 没动、网络没动、容器没重启。"""
    _seed_dhcp_config(db)
    before = _env_file.read_text(encoding="utf-8")

    response = client.put(
        f"{settings.API_V1_STR}/l2/config",
        headers=superuser_token_headers,
        json={
            **TARGET,
            "parent_iface": "enp125s0f0",
            "l2_subnet": "192.168.0.0/16",
            "l2_gateway": "192.168.0.10",
        },
    )

    assert response.status_code == 400
    assert (
        response.json()["detail"]
        == "Blocked by preflight checks: parent_has_default_route"
    )
    assert _env_file.read_text(encoding="utf-8") == before
    assert fake_apply_network == []
    assert fake_restart == []


def test_apply_writes_env_rebuilds_network_and_syncs_config(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    _env_file: Path,
    fake_l2_state: l2_network.L2NetworkState,  # noqa: ARG001 - 只用于激活 monkeypatch
    fake_apply_network: list[dict[str, Any]],
    fake_apply_config: list[dict[str, Any]],
    fake_restart: list[str],
) -> None:
    """切换的正常流：写 .env → 重建网络 → 重启 → 联动服务配置 → 校验 → 审计。"""
    _seed_dhcp_config(db)

    body = client.put(
        f"{settings.API_V1_STR}/l2/config",
        headers=superuser_token_headers,
        json=TARGET,
    ).json()

    assert body["applied"] is True
    assert body["rolled_back"] is False
    assert body["service_config"]["applied"] is True
    assert body["service_config"]["changed"]["pool_start"] == "192.168.95.100"

    env_text = _env_file.read_text(encoding="utf-8")
    assert "DHCP_PARENT_IFACE=enp125s0f3\n" in env_text
    assert "L2_SUBNET=192.168.95.0/24\n" in env_text
    assert "L2_SUBNET_V6=fd00:95::/64\n" in env_text
    # 密钥与注释必须原样保留
    assert "SECRET_KEY=do-not-touch-me\n" in env_text
    assert "# 部署变量（这段注释必须原样保留）\n" in env_text

    assert fake_apply_network[0]["parent"] == "enp125s0f3"
    assert fake_apply_network[0]["subnet"] == "192.168.95.0/24"
    assert fake_apply_network[0]["subnet_v6"] == "fd00:95::/64"
    assert fake_restart == ["fx-dhcp"]
    assert fake_apply_config[0]["name"] == "dhcp"
    assert fake_apply_config[0]["values"]["pool_end"] == "192.168.95.200"

    logs = db.exec(select(AuditLog)).all()
    assert [row.action for row in logs] == ["l2.update"]
    assert "applied=true" in (logs[0].detail or "")


def test_apply_enable_from_disabled_state(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    _env_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_apply_network: list[dict[str, Any]],  # noqa: ARG001 - 只用于激活 monkeypatch
    fake_restart: list[str],  # noqa: ARG001 - 只用于激活 monkeypatch
) -> None:
    """从未启用状态启用：动作名是 l2.enable，且不需要联动（池子本来就在目标网段）。"""
    _seed_dhcp_config(
        db,
        {
            **DHCP_VALUES,
            "pool_start": "192.168.95.100",
            "pool_end": "192.168.95.200",
            "gateway": "192.168.95.1",
            "ipv6_prefix": "fd00:95::/64",
            "ipv6_pool_start": "fd00:95::100",
            "ipv6_pool_end": "fd00:95::200",
        },
    )
    _env_file.write_text(
        ENV_SAMPLE.replace("DHCP_PARENT_IFACE=enp125s0f1\n", "DHCP_PARENT_IFACE=\n"),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        l2_network,
        "read_state",
        lambda *a, **k: l2_network.L2NetworkState(network=L2_NETWORK),
    )

    body = client.put(
        f"{settings.API_V1_STR}/l2/config",
        headers=superuser_token_headers,
        json=TARGET,
    ).json()

    assert body["applied"] is True
    assert body["service_config"] is None
    logs = db.exec(select(AuditLog)).all()
    assert [row.action for row in logs] == ["l2.enable"]


def test_apply_failure_rolls_back_env_and_network(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    _env_file: Path,
    fake_l2_state: l2_network.L2NetworkState,  # noqa: ARG001 - 只用于激活 monkeypatch
    monkeypatch: pytest.MonkeyPatch,
    fake_restart: list[str],  # noqa: ARG001 - 只用于激活 monkeypatch
    fake_apply_config: list[dict[str, Any]],
) -> None:
    """网络重建失败时按逆序回滚：.env 回到原值、网络回到原父口、结果如实上报。"""
    _seed_dhcp_config(db)
    calls: list[dict[str, Any]] = []

    def _fake_apply_network(*_args: Any, **kwargs: Any) -> tuple[Any, list[str]]:
        calls.append(kwargs)
        if len(calls) == 1:
            raise l2_network.L2NetworkError("Failed to create network: boom")
        return (l2_network.L2NetworkState(network=L2_NETWORK), ["已回滚网络"])

    monkeypatch.setattr(l2_network, "apply_network", _fake_apply_network)

    body = client.put(
        f"{settings.API_V1_STR}/l2/config",
        headers=superuser_token_headers,
        json=TARGET,
    ).json()

    assert body["applied"] is False
    assert body["rolled_back"] is True
    assert "boom" in body["message"]
    # .env 已回滚
    env_text = _env_file.read_text(encoding="utf-8")
    assert "DHCP_PARENT_IFACE=enp125s0f1\n" in env_text
    assert "L2_SUBNET=192.168.90.0/24\n" in env_text
    # 回滚时用旧参数重建了网络（第二次调用）
    assert calls[1]["parent"] == "enp125s0f1"
    assert calls[1]["subnet"] == "192.168.90.0/24"
    # 失败发生在联动之前（网络步骤），所以服务配置根本没被改过，也就无需回滚
    assert fake_apply_config == []
    logs = db.exec(select(AuditLog)).all()
    assert logs[0].action == "l2.update"
    assert "applied=false rolled_back=true" in (logs[0].detail or "")


def test_disable_removes_network_and_clears_parent(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    _env_file: Path,
    fake_l2_state: l2_network.L2NetworkState,  # noqa: ARG001 - 只用于激活 monkeypatch
    monkeypatch: pytest.MonkeyPatch,
    fake_restart: list[str],
) -> None:
    """停用：断开容器（网络对象保留）、清空 .env 的父口（其余 L2 参数保留便于预填）、记审计。"""
    removed: list[str] = []

    def _fake_detach_l2(*_args: Any) -> list[str]:
        removed.append(L2_NETWORK)
        return [f"已断开容器与 {L2_NETWORK} 的连接（网络对象保留，容器仍可重启）"]

    monkeypatch.setattr(l2_network, "detach_l2", _fake_detach_l2)

    body = client.post(
        f"{settings.API_V1_STR}/l2/disable", headers=superuser_token_headers
    ).json()

    assert body["applied"] is True
    assert removed == [L2_NETWORK]
    env_text = _env_file.read_text(encoding="utf-8")
    assert "DHCP_PARENT_IFACE=\n" in env_text
    assert "L2_SUBNET=192.168.90.0/24\n" in env_text
    assert fake_restart == ["fx-dhcp"]
    logs = db.exec(select(AuditLog)).all()
    assert [row.action for row in logs] == ["l2.disable"]


def test_disable_is_noop_when_not_enabled(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    _env_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_restart: list[str],
) -> None:
    """未启用时停用是安全空操作，不碰网络、不重启容器。"""
    _env_file.write_text(
        ENV_SAMPLE.replace("DHCP_PARENT_IFACE=enp125s0f1\n", "DHCP_PARENT_IFACE=\n"),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        l2_network, "read_state", lambda *a, **k: l2_network.L2NetworkState()
    )

    body = client.post(
        f"{settings.API_V1_STR}/l2/disable", headers=superuser_token_headers
    ).json()

    assert body["applied"] is True
    assert body["steps"] == ["无需变更：二层夹具未启用"]
    assert fake_restart == []
    assert db.exec(select(AuditLog)).all() == []


def test_disable_requires_admin(
    client: TestClient, readonly_token_headers: dict[str, str]
) -> None:
    """停用同样只允许 admin。"""
    response = client.post(
        f"{settings.API_V1_STR}/l2/disable", headers=readonly_token_headers
    )

    assert response.status_code == 403


def test_status_never_leaks_env_secrets(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """状态响应里不得出现 .env 的其它键值（那是含密钥的文件）。"""
    _seed_dhcp_config(db)

    text = client.get(
        f"{settings.API_V1_STR}/l2/status", headers=superuser_token_headers
    ).text

    assert "do-not-touch-me" not in text
    assert "also-do-not-touch" not in text
    assert "SECRET_KEY" not in text
    assert "POSTGRES_PASSWORD" not in text


def test_apply_failure_after_sync_rolls_back_service_config(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    _env_file: Path,
    fake_apply_network: list[dict[str, Any]],  # noqa: ARG001 - 只用于激活 monkeypatch
    fake_apply_config: list[dict[str, Any]],
    fake_restart: list[str],  # noqa: ARG001 - 只用于激活 monkeypatch
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """联动之后才失败（例如落地校验发现地址冲突）时，已下发的服务配置也要回滚。"""
    _seed_dhcp_config(db)
    calls: dict[str, int] = {"n": 0}
    real_evaluate = host_network.evaluate_checks

    def _fake_evaluate(**kwargs: Any) -> list[dict[str, Any]]:
        calls["n"] += 1
        # 第 1 次是执行前的阻断预检，第 2 次是落地后的校验：这里模拟后者发现 error
        if calls["n"] >= 2:
            return [
                {
                    "level": "error",
                    "code": "address_conflict",
                    "service": "dhcp",
                    "message": "simulated conflict after apply",
                }
            ]
        return real_evaluate(**kwargs)

    monkeypatch.setattr(host_network, "evaluate_checks", _fake_evaluate)

    body = client.put(
        f"{settings.API_V1_STR}/l2/config",
        headers=superuser_token_headers,
        json=TARGET,
    ).json()

    assert body["applied"] is False
    assert body["rolled_back"] is True
    assert "address_conflict" in body["message"]
    # 两次下发：一次同步到新网段、一次回滚回旧网段
    assert len(fake_apply_config) == 2
    assert fake_apply_config[0]["values"]["pool_start"] == "192.168.95.100"
    assert fake_apply_config[-1]["values"]["pool_start"] == "192.168.90.100"
    env_text = _env_file.read_text(encoding="utf-8")
    assert "DHCP_PARENT_IFACE=enp125s0f1" in env_text
    assert "L2_SUBNET=192.168.90.0/24" in env_text


def test_disable_failure_rolls_back_network_and_env(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,  # noqa: ARG001 - 只用于激活 monkeypatch
    _env_file: Path,
    fake_l2_state: l2_network.L2NetworkState,  # noqa: ARG001 - 只用于激活 monkeypatch
    fake_apply_network: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    fake_restart: list[str],  # noqa: ARG001 - 只用于激活 monkeypatch
) -> None:
    """停用中途失败（例如重启容器失败）时，网络与 .env 都要回到原状。"""

    def _fake_detach_l2(*_args: Any) -> list[str]:
        raise l2_network.L2NetworkError("Failed to disconnect: boom")

    monkeypatch.setattr(l2_network, "detach_l2", _fake_detach_l2)

    body = client.post(
        f"{settings.API_V1_STR}/l2/disable", headers=superuser_token_headers
    ).json()

    assert body["applied"] is False
    assert body["rolled_back"] is True
    assert "boom" in body["message"]
    # .env 未被清空（失败发生在写 .env 之前，回滚仍按快照恢复一次）
    env_text = _env_file.read_text(encoding="utf-8")
    assert "DHCP_PARENT_IFACE=enp125s0f1" in env_text
    # 网络按快照恢复
    assert fake_apply_network and fake_apply_network[0]["parent"] == "enp125s0f1"


def test_apply_is_noop_when_already_at_target(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    _env_file: Path,
    fake_l2_state: l2_network.L2NetworkState,  # noqa: ARG001 - 只用于激活 monkeypatch
    fake_apply_network: list[dict[str, Any]],
    fake_restart: list[str],
) -> None:
    """提交与当前状态完全相同的参数时是空操作：不重建网络、不重启容器、不写审计。"""
    _seed_dhcp_config(db)

    body = client.put(
        f"{settings.API_V1_STR}/l2/config",
        headers=superuser_token_headers,
        json={
            "parent_iface": "enp125s0f1",
            "l2_subnet": "192.168.90.0/24",
            "l2_gateway": "192.168.90.1",
            "l2_subnet_v6": "fd00:90::/64",
            "l2_gateway_v6": "fd00:90::1",
            "sync_service_config": True,
        },
    ).json()

    assert body["applied"] is True
    assert body["steps"] == ["无需变更：.env、macvlan 网络与容器连接都已与目标一致"]
    assert fake_apply_network == []
    assert fake_restart == []
    assert db.exec(select(AuditLog)).all() == []


def test_apply_archives_leases_when_subnet_changes(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    _env_file: Path,
    fake_l2_state: l2_network.L2NetworkState,  # noqa: ARG001 - 只用于激活 monkeypatch
    fake_apply_network: list[dict[str, Any]],  # noqa: ARG001 - 只用于激活 monkeypatch
    fake_apply_config: list[dict[str, Any]],  # noqa: ARG001 - 只用于激活 monkeypatch
    fake_restart: list[str],  # noqa: ARG001 - 只用于激活 monkeypatch
    _fake_archive_leases: list[str],
) -> None:
    """网段变更时归档旧租约：否则 BMC 会拿着旧网段地址一直到续租失败（默认 12h）。"""
    _seed_dhcp_config(db)

    body = client.put(
        f"{settings.API_V1_STR}/l2/config",
        headers=superuser_token_headers,
        json=TARGET,
    ).json()

    assert body["applied"] is True
    assert _fake_archive_leases == ["archived"]
    assert any("已归档 dnsmasq 旧租约" in step for step in body["steps"])
