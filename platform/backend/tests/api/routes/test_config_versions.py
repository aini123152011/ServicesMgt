"""配置版本与回滚路由测试（AC1–AC4）。

Docker 交互用 FakeLifecycle 替身；服务目录与配置卷指到受控位置；
数据库用 conftest 的 PostgreSQL fixture（表由迁移/元数据建好）。
"""

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, delete, select

from app import config_renderer, lifecycle
from app.core.config import settings
from app.crud import get_service_config
from app.models import AuditLog, ServiceConfigVersion

REPO_SERVICES_DIR = Path(settings.SERVICES_DIR).resolve()

# chrony：字段简单（maxdistance 是 integer），便于构造「两次不同配置」
CHRONY_V1 = {"maxdistance": 3}
CHRONY_V2 = {"maxdistance": 8}
# nginx 有 secret 字段（auth_basic_password），用于验证版本详情脱敏
NGINX_WITH_SECRET = {
    "auth_basic_enabled": True,
    "auth_basic_user": "bmc",
    "auth_basic_password": "Sup3rSecret",
}


@pytest.fixture(autouse=True)
def _services_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """服务目录与配置卷指到受控位置，避免依赖进程工作目录。"""
    monkeypatch.setattr(settings, "SERVICES_DIR", str(REPO_SERVICES_DIR))
    monkeypatch.setattr(settings, "VOLUMES_MOUNT_ROOT", str(tmp_path))


@pytest.fixture(autouse=True)
def _clear_versions(db: Session) -> None:
    """每个用例前清空版本表与审计表，断言只看本用例写入的记录。"""
    db.exec(delete(ServiceConfigVersion))
    db.exec(delete(AuditLog))
    db.commit()


@pytest.fixture(autouse=True)
def fake_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """lifecycle 替身：容器视为运行中，reload 恒成功。"""
    monkeypatch.setattr(lifecycle, "get_status", lambda _name: {"running": True})
    monkeypatch.setattr(lifecycle, "exec_reload", lambda _manifest: "reload ok")


def _put(
    client: TestClient,
    headers: dict[str, str],
    service: str,
    values: Mapping[str, object],
) -> None:
    response = client.put(
        f"{settings.API_V1_STR}/services/{service}/config",
        headers=headers,
        json={"values": values},
    )
    assert response.status_code == 200, response.text


def test_apply_records_one_version(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """一次下发只产生一条版本，且带操作者、applied 与渲染摘要。"""
    _put(client, superuser_token_headers, "chrony", CHRONY_V1)

    rows = db.exec(select(ServiceConfigVersion)).all()
    assert len(rows) == 1
    assert rows[0].version == 1
    assert rows[0].applied is True
    assert rows[0].user_email is not None
    # 摘要非空：用于回答「两次下发的产物是否一致」
    assert rows[0].rendered_digest


def test_versions_listed_newest_first(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """AC1：连续下发 3 次后历史可见 3 个版本，新版本在前。"""
    for value in (3, 5, 7):
        _put(client, superuser_token_headers, "chrony", {"maxdistance": value})

    response = client.get(
        f"{settings.API_V1_STR}/services/chrony/config/versions",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    content = response.json()
    assert content["count"] == 3
    assert [entry["version"] for entry in content["data"]] == [3, 2, 1]
    # 列表不带配置内容（避免一页拖出大量 JSON）
    assert "values" not in content["data"][0]


def test_version_detail_masks_secret(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """AC3：版本详情里 secret 脱敏，而库里存的是真实值（回滚要用）。"""
    _put(client, superuser_token_headers, "nginx", NGINX_WITH_SECRET)

    response = client.get(
        f"{settings.API_V1_STR}/services/nginx/config/versions/1",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    values = response.json()["values"]
    assert values["auth_basic_password"] == config_renderer.MASKED_SECRET_PLACEHOLDER
    assert values["auth_basic_user"] == "bmc"

    stored = db.exec(select(ServiceConfigVersion)).one()
    assert stored.values["auth_basic_password"] == "Sup3rSecret"


def test_rollback_restores_values_and_writes_audit(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """AC2：回滚到第 1 版后当前配置回到该版本，并写一条回滚审计；回滚自身也是新版本。"""
    _put(client, superuser_token_headers, "chrony", CHRONY_V1)
    _put(client, superuser_token_headers, "chrony", CHRONY_V2)

    response = client.post(
        f"{settings.API_V1_STR}/services/chrony/config/versions/1/rollback",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["applied"] is True

    current = get_service_config(session=db, service_name="chrony")
    assert current is not None
    assert current.values["maxdistance"] == CHRONY_V1["maxdistance"]

    versions = db.exec(
        select(ServiceConfigVersion).order_by(col(ServiceConfigVersion.version))
    ).all()
    assert [row.version for row in versions] == [1, 2, 3]
    # 回滚产生的新版本记录来源，便于在历史里区分「改配置」与「回滚」
    assert versions[-1].rolled_back_from == 1

    audit = db.exec(select(AuditLog)).all()
    assert [row.action for row in audit] == [
        "config.update",
        "config.update",
        "config.rollback",
    ]
    assert "from_version=1" in (audit[-1].detail or "")


def test_rollback_rejects_unknown_version(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """回滚不存在的版本返回 404，而不是悄悄成功。"""
    _put(client, superuser_token_headers, "chrony", CHRONY_V1)
    response = client.post(
        f"{settings.API_V1_STR}/services/chrony/config/versions/99/rollback",
        headers=superuser_token_headers,
    )
    assert response.status_code == 404


def test_rollback_requires_operator(
    client: TestClient, readonly_token_headers: dict[str, str]
) -> None:
    """readonly 角色不能回滚（403）——回滚等价于一次下发。"""
    response = client.post(
        f"{settings.API_V1_STR}/services/chrony/config/versions/1/rollback",
        headers=readonly_token_headers,
    )
    assert response.status_code == 403


def test_prune_keeps_configured_limit(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4：超过保留上限后最旧版本被清理，历史不会无限增长。"""
    monkeypatch.setattr(settings, "CONFIG_VERSION_LIMIT", 3)

    for value in (1, 2, 3, 4, 5):
        _put(client, superuser_token_headers, "chrony", {"maxdistance": value})

    rows = db.exec(
        select(ServiceConfigVersion).order_by(col(ServiceConfigVersion.version))
    ).all()
    assert [row.version for row in rows] == [3, 4, 5]
    # 当前配置仍是最新一次下发的值
    current = get_service_config(session=db, service_name="chrony")
    assert current is not None and current.values["maxdistance"] == 5


def test_version_detail_masks_are_json_serializable(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """版本详情的 values 必须是可直接 JSON 序列化的普通字典（避免把内部类型漏给前端）。"""
    _put(client, superuser_token_headers, "chrony", CHRONY_V1)
    response = client.get(
        f"{settings.API_V1_STR}/services/chrony/config/versions/1",
        headers=superuser_token_headers,
    )
    json.dumps(response.json()["values"])
