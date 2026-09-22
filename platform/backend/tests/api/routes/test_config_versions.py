"""配置版本与回滚路由测试（AC1–AC4）。

Docker 交互用 FakeLifecycle 替身；服务目录与配置卷指到受控位置；
数据库用 conftest 的 PostgreSQL fixture（表由迁移/元数据建好）。
"""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, delete, select

from app import config_renderer, config_versions, lifecycle, registry
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


def test_failed_reload_still_records_version_and_audit(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reload 失败（502）时版本与审计仍要留痕。

    渲染产物此时已经落盘，服务下次启动就会读到它——不留痕的话「最新版本 = 当前生效配置」
    这条不变量不成立，用户按历史列表挑回滚基线会挑错版本，审计里也查不到这次变更。
    """
    monkeypatch.setattr(
        lifecycle,
        "exec_reload",
        lambda _manifest: (_ for _ in ()).throw(lifecycle.LifecycleError("boom")),
    )
    response = client.put(
        f"{settings.API_V1_STR}/services/chrony/config",
        headers=superuser_token_headers,
        json={"values": CHRONY_V1},
    )
    assert response.status_code == 502

    rows = db.exec(select(ServiceConfigVersion)).all()
    assert len(rows) == 1
    assert rows[0].applied is False
    # 版本里存的是归一化后的完整配置（缺失字段由 schema 默认值补齐），不是提交时的残缺入参
    assert rows[0].values["maxdistance"] == 3

    audits = db.exec(
        select(AuditLog).where(col(AuditLog.action) == "config.update")
    ).all()
    assert len(audits) == 1
    assert "reload_failed" in (audits[0].detail or "")


def test_version_detail_masks_with_secret_fields_recorded_at_write_time(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """版本详情按「写入时的 secret 名单」脱敏：schema 后来不再标 secret 也不能明文返回。"""
    _put(client, superuser_token_headers, "nginx", NGINX_WITH_SECRET)
    row = db.exec(select(ServiceConfigVersion)).one()
    assert "auth_basic_password" in row.secret_fields

    # 模拟 schema 演进：字段还在，但不再标 secret（旧实现会因此明文返回历史密文）
    plugin = registry.get_service("nginx")
    assert plugin is not None
    evolved = {
        "fields": [
            {**field, "secret": False}
            if field.get("name") == "auth_basic_password"
            else field
            for field in plugin.schema["fields"]
        ]
    }
    monkeypatch.setattr(
        registry, "get_service", lambda _name: replace(plugin, schema=evolved)
    )

    response = client.get(
        f"{settings.API_V1_STR}/services/nginx/config/versions/1",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    assert response.json()["values"]["auth_basic_password"] == "********"
    # 库里仍是真值（渲染与回滚要用）
    assert row.values["auth_basic_password"] == "Sup3rSecret"


def test_version_detail_unknown_version_is_404(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """未知版本号 404，而不是空对象。"""
    _put(client, superuser_token_headers, "chrony", CHRONY_V1)
    response = client.get(
        f"{settings.API_V1_STR}/services/chrony/config/versions/99",
        headers=superuser_token_headers,
    )
    assert response.status_code == 404


def test_rollback_rejects_values_that_no_longer_match_schema(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """历史值不再满足当前 schema 时回滚 400，且不写新版本（schema 演进后的安全网）。"""
    _put(client, superuser_token_headers, "chrony", CHRONY_V1)
    plugin = registry.get_service("chrony")
    assert plugin is not None
    # 模拟 schema 收紧：maxdistance 上限降到 2，历史值 3 不再合法
    tightened = {
        "fields": [
            {**field, "max": 2} if field.get("name") == "maxdistance" else field
            for field in plugin.schema["fields"]
        ]
    }
    monkeypatch.setattr(
        registry, "get_service", lambda _name: replace(plugin, schema=tightened)
    )

    response = client.post(
        f"{settings.API_V1_STR}/services/chrony/config/versions/1/rollback",
        headers=superuser_token_headers,
    )
    assert response.status_code == 400
    assert len(db.exec(select(ServiceConfigVersion)).all()) == 1


def test_version_allocation_retries_on_unique_conflict(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """并发下发撞唯一约束时重算版本号重试，而不是把 500 抛给用户。

    构造方式：先落 v1，再让「取下一个版本号」第一次返回 1（模拟并发下读到别人提交前的
    旧最大值）——第一次插入真的会撞 uq_config_version，第二次读到 2 才成功。
    """
    config_versions.record_config_version(
        session=db,
        service_name="chrony",
        values=CHRONY_V1,
        applied=True,
        user_email="op@example.com",
    )
    real_next_version = config_versions._next_version
    calls = {"n": 0}

    def stale_once(session: Session, service_name: str) -> int:
        calls["n"] += 1
        return 1 if calls["n"] == 1 else real_next_version(session, service_name)

    monkeypatch.setattr(config_versions, "_next_version", stale_once)

    entry = config_versions.record_config_version(
        session=db,
        service_name="chrony",
        values=CHRONY_V2,
        applied=True,
        user_email="op@example.com",
    )
    assert calls["n"] == 2
    assert entry.version == 2


def test_version_allocation_conflict_is_409(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重试用尽仍冲突时返回 409（项目口径：唯一性冲突一律 409，不是 500）。"""
    monkeypatch.setattr(
        config_versions,
        "record_config_version",
        lambda **_kwargs: (_ for _ in ()).throw(
            config_versions.ConfigVersionConflictError("no version for you")
        ),
    )
    response = client.put(
        f"{settings.API_V1_STR}/services/chrony/config",
        headers=superuser_token_headers,
        json={"values": CHRONY_V1},
    )
    assert response.status_code == 409


def test_version_list_rejects_out_of_range_pagination(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """分页参数越界返回 422（负数会让 PostgreSQL 报错变成 500）。"""
    for params in ("offset=-1", "limit=0", "limit=100000"):
        response = client.get(
            f"{settings.API_V1_STR}/services/chrony/config/versions?{params}",
            headers=superuser_token_headers,
        )
        assert response.status_code == 422, params
