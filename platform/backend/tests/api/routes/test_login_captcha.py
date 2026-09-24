"""登录入口的验证码与状态分层。

覆盖 AC2（未验证 / 已停用 / 凭证错三种提示可区分）、AC3、AC9（验证码一次性、必填），
以及登录顺序不可绕过：**密码没对之前不能透露账号状态**。
"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import crud
from app.core.config import settings
from app.main import app
from app.models import AuditLog, UserCreate
from tests.utils.access import add_rule, wipe_access_state
from tests.utils.utils import (
    captcha_form,
    login_form,
    random_email,
    random_lower_string,
)

LOGIN = f"{settings.API_V1_STR}/login/access-token"


@pytest.fixture(autouse=True)
def clean_state(db: Session) -> Generator[None]:
    wipe_access_state(db)
    yield
    wipe_access_state(db)


def _make_user(
    db: Session, *, verified: bool = True, active: bool = True
) -> tuple[str, str]:
    """建一个测试账号（默认已验证、启用）。"""
    email = random_email()
    password = random_lower_string()
    crud.create_user(
        session=db,
        user_create=UserCreate(email=email, password=password, is_active=active),
        email_verified=verified,
    )
    return email, password


def test_login_requires_captcha_fields(client: TestClient, db: Session) -> None:
    email, password = _make_user(db)
    response = client.post(LOGIN, data={"username": email, "password": password})
    assert response.status_code == 400
    assert response.json()["detail"] == "Captcha is incorrect or has expired"


def test_login_rejects_wrong_captcha(client: TestClient, db: Session) -> None:
    email, password = _make_user(db)
    issue = captcha_form(db, scope="login")
    response = client.post(
        LOGIN,
        data={
            "username": email,
            "password": password,
            "captcha_id": issue["captcha_id"],
            "captcha_answer": "WRONG",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Captcha is incorrect or has expired"


def test_captcha_is_single_use_across_logins(client: TestClient, db: Session) -> None:
    email, password = _make_user(db)
    form = login_form(db, email=email, password=password)
    assert client.post(LOGIN, data=form).status_code == 200
    # 同一张验证码第二次使用必然失败（一次性）
    second = client.post(LOGIN, data=form)
    assert second.status_code == 400
    assert second.json()["detail"] == "Captcha is incorrect or has expired"


def test_wrong_password_reports_generic_error(client: TestClient, db: Session) -> None:
    email, _ = _make_user(db)
    response = client.post(
        LOGIN, data=login_form(db, email=email, password="definitely-wrong")
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Incorrect email or password"


def test_unknown_email_reports_same_generic_error(
    client: TestClient, db: Session
) -> None:
    """不存在的邮箱与密码错误必须给出同一句，否则登录接口就是账号枚举工具。"""
    response = client.post(
        LOGIN, data=login_form(db, email=random_email(), password=random_lower_string())
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Incorrect email or password"


def test_unverified_user_is_told_to_verify(client: TestClient, db: Session) -> None:
    email, password = _make_user(db, verified=False)
    response = client.post(LOGIN, data=login_form(db, email=email, password=password))
    assert response.status_code == 400
    assert response.json()["detail"] == "Email is not verified"


def test_inactive_verified_user_reports_inactive(
    client: TestClient, db: Session
) -> None:
    email, password = _make_user(db, verified=True, active=False)
    response = client.post(LOGIN, data=login_form(db, email=email, password=password))
    assert response.status_code == 400
    assert response.json()["detail"] == "Inactive user"


def test_status_is_not_revealed_before_password_check(
    client: TestClient, db: Session
) -> None:
    """未验证 + 密码错误 → 必须是「凭证错」，不能因为状态是未验证就先说未验证。

    否则任何人都能拿一个邮箱地址探出「这个账号存在但还没验证」。
    """
    email, _ = _make_user(db, verified=False)
    response = client.post(
        LOGIN, data=login_form(db, email=email, password="wrong-password")
    )
    assert response.json()["detail"] == "Incorrect email or password"


def test_ip_deny_blocks_login_and_is_audited(db: Session) -> None:
    email, password = _make_user(db)
    add_rule(db, kind="ip", list_type="deny", value="203.0.113.0/24")

    with TestClient(app, client=("203.0.113.9", 52000)) as blocked_client:
        response = blocked_client.post(
            LOGIN, data=login_form(db, email=email, password=password)
        )
    assert response.status_code == 403
    assert "203.0.113.0/24" in response.json()["detail"]

    entries = db.exec(
        select(AuditLog).where(AuditLog.action == "auth.ip_blocked")
    ).all()
    assert entries
    assert entries[0].ip == "203.0.113.9"


def test_login_works_when_ip_rules_are_empty(client: TestClient, db: Session) -> None:
    """默认不启用：没有任何 IP 规则时，来源 IP 判不出来也不该拦住登录。"""
    email, password = _make_user(db)
    assert (
        client.post(
            LOGIN, data=login_form(db, email=email, password=password)
        ).status_code
        == 200
    )
