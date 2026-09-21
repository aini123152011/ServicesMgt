from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app.core.config import settings
from app.core.db import engine, init_db
from app.initial_data import seed
from app.main import app
from app.models import AuditLog, Role, User, UserRole
from tests.utils.user import (
    authentication_token_from_email,
    create_user_token_headers,
)
from tests.utils.utils import get_superuser_token_headers


@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[Session]:
    with Session(engine) as session:
        init_db(session)
        # 角色种子与首超管 admin 角色：RBAC 测试依赖 role 表非空
        seed(session)
        yield session
        # 清理顺序按外键依赖：审计/关联先删，再删用户与角色
        statement = delete(AuditLog)
        session.exec(statement)
        statement = delete(UserRole)
        session.exec(statement)
        statement = delete(User)
        session.exec(statement)
        statement = delete(Role)
        session.exec(statement)
        session.commit()


@pytest.fixture(scope="module")
def client() -> Generator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture(scope="module")
def normal_user_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )


@pytest.fixture(scope="module")
def operator_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    """带 operator 角色的随机用户及其认证头。"""
    return create_user_token_headers(client=client, db=db, roles=["operator"])


@pytest.fixture(scope="module")
def readonly_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    """带 readonly 角色的随机用户及其认证头。"""
    return create_user_token_headers(client=client, db=db, roles=["readonly"])
