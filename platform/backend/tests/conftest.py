import re
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
def superuser_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return get_superuser_token_headers(client, db)


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


@pytest.fixture
def smtp_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """把邮件配置成「已就绪」，并拦住真实发信。

    注册 / 重发 / 改邮箱都以 `settings.emails_enabled` 为前置（未配置直接 503），
    而真实 SMTP 调用在测试里必须替换掉，否则用例会依赖网络与外部邮箱。

    替换点选在**模块内引用名**上：`email_verification` 与 `users` 路由各自
    `from app.utils import send_email`，所以两个名字都要打。
    """
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "noreply@example.com")
    monkeypatch.setattr("app.email_verification.send_email", lambda **_kwargs: None)
    monkeypatch.setattr("app.api.routes.users.send_email", lambda **_kwargs: None)


@pytest.fixture
def sent_codes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """捕获发出的验证码明文，供断言使用（不依赖真实收信）。

    直接读库拿不到明文（只存哈希），所以从发信入口截获——这也是测试能验证
    「注册后能用收到的码完成验证」的唯一途径。
    """
    captured: list[str] = []

    def _fake_send_email(
        *, email_to: str, subject: str = "", html_content: str = ""
    ) -> None:
        del email_to, subject
        # 模板里码是独立的 6 位数字串，用正则抓出来
        match = re.search(r">(\d{6})<", html_content)
        if match:
            captured.append(match.group(1))

    monkeypatch.setattr("app.email_verification.send_email", _fake_send_email)
    return captured
