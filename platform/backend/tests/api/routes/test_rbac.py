"""RBAC 与审计日志测试：角色×操作权限矩阵 + 审计写入断言。

权限矩阵（任务契约）：readonly 仅读；operator 读 + 配置/生命周期；
admin（非超管）与超管全通过；未登录 401。Docker 交互复用 test_services
的 FakeLifecycle 替身；审计表在每个相关用例前清空，断言只看本用例记录。
"""

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import crud
from app.core.config import settings
from app.models import AuditLog, UserCreate

# pytest 经模块命名空间收集 fixture，导入即对用例生效（用 usefixtures 引用）
from tests.api.routes.test_services import fake_lifecycle  # noqa: F401
from tests.utils.user import (
    create_user_token_headers,
    user_authentication_headers,
)
from tests.utils.utils import random_email, random_lower_string

# 服务目录统一取 settings.SERVICES_DIR（与 test_services 同一锚定约定）
REPO_SERVICES_DIR = Path(settings.SERVICES_DIR).resolve()


@pytest.fixture(autouse=True)
def _rbac_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """服务目录与配置卷指向受控位置：写操作测试不污染真实卷。"""
    monkeypatch.setattr(settings, "SERVICES_DIR", str(REPO_SERVICES_DIR))
    monkeypatch.setattr(settings, "VOLUMES_MOUNT_ROOT", str(tmp_path))


def _clear_audit_logs(db: Session) -> None:
    """清空审计表，让断言只看本用例产生的记录。"""
    for row in db.exec(select(AuditLog)).all():
        db.delete(row)
    db.commit()


def _admin_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    """创建 admin 角色（非超管）用户并返回其认证头。"""
    return create_user_token_headers(client=client, db=db, roles=["admin"])


def test_unauthenticated_gets_401(client: TestClient) -> None:
    """未登录访问受保护端点保持模板现状：401。"""
    response = client.get(f"{settings.API_V1_STR}/services/")
    assert response.status_code == 401


def test_readonly_cannot_update_config(
    client: TestClient, readonly_token_headers: dict[str, str]
) -> None:
    """readonly 用户提交配置被拒：403，文案与权限依赖精确对应。"""
    response = client.put(
        f"{settings.API_V1_STR}/services/chrony/config",
        headers=readonly_token_headers,
        json={"values": {}},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Not enough permissions"


def test_readonly_cannot_run_service_action(
    client: TestClient, readonly_token_headers: dict[str, str]
) -> None:
    """readonly 用户执行生命周期动作被拒：403。"""
    response = client.post(
        f"{settings.API_V1_STR}/services/chrony/restart",
        headers=readonly_token_headers,
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Not enough permissions"


def test_readonly_can_read_services(
    client: TestClient, readonly_token_headers: dict[str, str]
) -> None:
    """readonly 用户可读服务列表（读操作不要求角色）。"""
    response = client.get(
        f"{settings.API_V1_STR}/services/", headers=readonly_token_headers
    )
    assert response.status_code == 200
    assert response.json()["count"] >= 1


def test_readonly_me_returns_roles(
    client: TestClient, readonly_token_headers: dict[str, str]
) -> None:
    """/users/me 响应带 roles 字段，值为创建时授予的角色。"""
    response = client.get(
        f"{settings.API_V1_STR}/users/me", headers=readonly_token_headers
    )
    assert response.status_code == 200
    assert response.json()["roles"] == ["readonly"]


@pytest.mark.usefixtures("fake_lifecycle")
def test_operator_can_update_config_and_writes_audit(
    client: TestClient,
    operator_token_headers: dict[str, str],
    db: Session,
) -> None:
    """operator 提交配置成功（applied=True）并写审计：action/service_name 正确。"""
    _clear_audit_logs(db)
    response = client.put(
        f"{settings.API_V1_STR}/services/chrony/config",
        headers=operator_token_headers,
        json={"values": {"maxdistance": 5}},
    )
    assert response.status_code == 200
    assert response.json()["applied"] is True
    entry = db.exec(select(AuditLog).where(AuditLog.action == "config.update")).one()
    assert entry.service_name == "chrony"
    assert entry.detail == "applied=true"
    # user_id 记操作者（操作者未被删除，不允许为空）
    assert entry.user_id is not None


@pytest.mark.usefixtures("fake_lifecycle")
def test_operator_can_run_service_action_and_writes_audit(
    client: TestClient,
    operator_token_headers: dict[str, str],
    db: Session,
) -> None:
    """operator 执行 start 成功并写审计：action=service.start。"""
    _clear_audit_logs(db)
    response = client.post(
        f"{settings.API_V1_STR}/services/chrony/start",
        headers=operator_token_headers,
    )
    assert response.status_code == 200
    entry = db.exec(select(AuditLog).where(AuditLog.action == "service.start")).one()
    assert entry.service_name == "chrony"
    assert entry.detail == "container=fx-chrony"


def test_operator_cannot_manage_users(
    client: TestClient, operator_token_headers: dict[str, str]
) -> None:
    """operator 访问用户管理（列表/创建/详情）一律 403。"""
    response = client.get(
        f"{settings.API_V1_STR}/users/", headers=operator_token_headers
    )
    assert response.status_code == 403
    data = {"email": random_email(), "password": random_lower_string()}
    response = client.post(
        f"{settings.API_V1_STR}/users/", headers=operator_token_headers, json=data
    )
    assert response.status_code == 403
    response = client.get(
        f"{settings.API_V1_STR}/users/{uuid.uuid4()}",
        headers=operator_token_headers,
    )
    assert response.status_code == 403


def test_operator_cannot_read_audit_logs_and_roles(
    client: TestClient, operator_token_headers: dict[str, str]
) -> None:
    """审计与角色查询是管理员专属：operator 访问 403。"""
    for path in ("/audit-logs", "/roles"):
        response = client.get(
            f"{settings.API_V1_STR}{path}", headers=operator_token_headers
        )
        assert response.status_code == 403, path


def test_readonly_cannot_read_audit_logs_and_roles(
    client: TestClient, readonly_token_headers: dict[str, str]
) -> None:
    """审计与角色查询是管理员专属：readonly 访问 403。"""
    for path in ("/audit-logs", "/roles"):
        response = client.get(
            f"{settings.API_V1_STR}{path}", headers=readonly_token_headers
        )
        assert response.status_code == 403, path


@pytest.mark.usefixtures("fake_lifecycle")
def test_admin_role_full_access(client: TestClient, db: Session) -> None:
    """admin 角色（非超管）配置/生命周期/用户管理/审计/角色全通过。"""
    headers = _admin_token_headers(client, db)
    response = client.put(
        f"{settings.API_V1_STR}/services/chrony/config",
        headers=headers,
        json={"values": {"maxdistance": 3}},
    )
    assert response.status_code == 200
    response = client.post(
        f"{settings.API_V1_STR}/services/chrony/start", headers=headers
    )
    assert response.status_code == 200
    response = client.get(f"{settings.API_V1_STR}/users/", headers=headers)
    assert response.status_code == 200
    response = client.get(f"{settings.API_V1_STR}/audit-logs", headers=headers)
    assert response.status_code == 200
    response = client.get(f"{settings.API_V1_STR}/roles", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"data": ["admin", "operator", "readonly"]}


def test_admin_create_user_writes_audit(client: TestClient, db: Session) -> None:
    """admin 创建用户（指定角色）成功并写审计：user_email=目标邮箱。"""
    _clear_audit_logs(db)
    headers = _admin_token_headers(client, db)
    target_email = random_email()
    response = client.post(
        f"{settings.API_V1_STR}/users/",
        headers=headers,
        json={
            "email": target_email,
            "password": random_lower_string(),
            "roles": ["operator"],
        },
    )
    assert response.status_code == 200
    assert response.json()["roles"] == ["operator"]
    entry = db.exec(select(AuditLog).where(AuditLog.action == "user.create")).one()
    assert entry.user_email == target_email
    assert entry.detail == "roles=operator"


def test_admin_create_user_default_readonly(client: TestClient, db: Session) -> None:
    """创建用户未传 roles 时缺省授予 readonly。"""
    headers = _admin_token_headers(client, db)
    response = client.post(
        f"{settings.API_V1_STR}/users/",
        headers=headers,
        json={"email": random_email(), "password": random_lower_string()},
    )
    assert response.status_code == 200
    assert response.json()["roles"] == ["readonly"]


def test_admin_update_user_roles_and_writes_audit(
    client: TestClient, db: Session
) -> None:
    """admin 更新用户角色整体替换，并写 user.update 审计（只记字段名）。"""
    _clear_audit_logs(db)
    headers = _admin_token_headers(client, db)
    target_email = random_email()
    target = crud.create_user(
        session=db,
        user_create=UserCreate(
            email=target_email, password=random_lower_string(), roles=["readonly"]
        ),
    )
    response = client.patch(
        f"{settings.API_V1_STR}/users/{target.id}",
        headers=headers,
        json={"roles": ["operator"]},
    )
    assert response.status_code == 200
    assert response.json()["roles"] == ["operator"]
    entry = db.exec(select(AuditLog).where(AuditLog.action == "user.update")).one()
    assert entry.user_email == target_email
    assert entry.detail == "fields=roles"


def test_admin_delete_user_writes_audit(client: TestClient, db: Session) -> None:
    """admin 删除用户写 user.delete 审计，user_email 留被删者邮箱。"""
    _clear_audit_logs(db)
    headers = _admin_token_headers(client, db)
    target_email = random_email()
    target = crud.create_user(
        session=db,
        user_create=UserCreate(email=target_email, password=random_lower_string()),
    )
    response = client.delete(
        f"{settings.API_V1_STR}/users/{target.id}", headers=headers
    )
    assert response.status_code == 200
    entry = db.exec(select(AuditLog).where(AuditLog.action == "user.delete")).one()
    assert entry.user_email == target_email


@pytest.mark.usefixtures("fake_lifecycle")
def test_superuser_still_has_full_access(
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    """is_superuser 兼容：无角色行的首超管全接口放行。"""
    response = client.get(
        f"{settings.API_V1_STR}/roles", headers=superuser_token_headers
    )
    assert response.status_code == 200
    response = client.get(
        f"{settings.API_V1_STR}/audit-logs", headers=superuser_token_headers
    )
    assert response.status_code == 200
    response = client.put(
        f"{settings.API_V1_STR}/services/chrony/config",
        headers=superuser_token_headers,
        json={"values": {"maxdistance": 2}},
    )
    assert response.status_code == 200


@pytest.mark.usefixtures("fake_lifecycle")
def test_audit_logs_listing_newest_first(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    """审计列表按 created_at 倒序，响应字段形状与 AuditLogPublic 一致。"""
    _clear_audit_logs(db)
    for action in ("start", "stop"):
        client.post(
            f"{settings.API_V1_STR}/services/chrony/{action}",
            headers=superuser_token_headers,
        )
    response = client.get(
        f"{settings.API_V1_STR}/audit-logs", headers=superuser_token_headers
    )
    assert response.status_code == 200
    content = response.json()
    assert content["count"] == 2
    assert [entry["action"] for entry in content["data"]] == [
        "service.stop",
        "service.start",
    ]
    assert set(content["data"][0]) == {
        "id",
        "user_email",
        "action",
        "service_name",
        "detail",
        "ip",
        "created_at",
    }


def test_user_with_unknown_role_rejected(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """创建用户携带未知角色名：请求体校验拦截，返回 422。"""
    response = client.post(
        f"{settings.API_V1_STR}/users/",
        headers=superuser_token_headers,
        json={
            "email": random_email(),
            "password": random_lower_string(),
            "roles": ["hacker"],
        },
    )
    assert response.status_code == 422


def test_login_token_returns_roles(client: TestClient, db: Session) -> None:
    """login/test-token 响应回填真实角色（与 /users/me 对齐）。"""
    email = random_email()
    password = random_lower_string()
    crud.create_user(
        session=db,
        user_create=UserCreate(email=email, password=password, roles=["operator"]),
    )
    headers = user_authentication_headers(
        client=client, db=db, email=email, password=password
    )
    response = client.post(f"{settings.API_V1_STR}/login/test-token", headers=headers)
    assert response.status_code == 200
    assert response.json()["roles"] == ["operator"]
