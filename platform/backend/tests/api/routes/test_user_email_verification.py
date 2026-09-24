"""用户邮箱验证状态的管理端行为。

覆盖 AC12（管理员手动放行）、AC14（既有账号视为已验证）以及「改邮箱必须重新验证」——
后者是域名白名单不被绕过的关键。
"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import crud
from app.core.config import settings
from app.models import AuditLog, User, UserCreate
from tests.utils.access import add_rule, wipe_access_state
from tests.utils.utils import login_form, random_email, random_lower_string

USERS = f"{settings.API_V1_STR}/users"


@pytest.fixture(autouse=True)
def clean_state(db: Session) -> Generator[None]:
    wipe_access_state(db)
    yield
    wipe_access_state(db)


def _pending_user(db: Session) -> User:
    """模拟自助注册产生的「未验证」账号。"""
    email = random_email()
    return crud.create_user(
        session=db,
        user_create=UserCreate(
            email=email, password=random_lower_string(), is_active=False
        ),
        email_verified=False,
    )


def test_admin_creation_is_verified(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    """管理员建号视为可信来源：邮箱直接置为已验证，可立即登录。"""
    email = random_email()
    password = random_lower_string()
    created = client.post(
        USERS,
        headers=superuser_token_headers,
        json={"email": email, "password": password, "roles": ["readonly"]},
    )
    assert created.status_code == 200, created.text
    assert created.json()["email_verified_at"] is not None

    login = client.post(
        f"{settings.API_V1_STR}/login/access-token",
        data=login_form(db, email=email, password=password),
    )
    assert login.status_code == 200


def test_admin_creation_respects_suffix_rule(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    """建号也走域名规则：否则「管理员建号」就是绕过白名单的后门。"""
    add_rule(db, kind="email_suffix", list_type="deny", value="evil.com")
    response = client.post(
        USERS,
        headers=superuser_token_headers,
        json={
            "email": f"x@{random_lower_string()}.evil.com",
            "password": random_lower_string(),
        },
    )
    assert response.status_code == 400
    assert "evil.com" in response.json()["detail"]


def test_manual_verify_unlocks_pending_account(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    user = _pending_user(db)
    assert user.email_verified_at is None

    response = client.post(
        f"{USERS}/{user.id}/verify-email", headers=superuser_token_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["email_verified_at"] is not None

    db.refresh(user)
    assert user.is_active is True, "手动放行必须同时放开登录"

    assert "user.verify_email_manual" in [
        row.action for row in db.exec(select(AuditLog)).all()
    ]


def test_manual_verify_is_idempotent(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    user = _pending_user(db)
    first = client.post(
        f"{USERS}/{user.id}/verify-email", headers=superuser_token_headers
    )
    assert first.status_code == 200

    db.expire_all()
    before = len(db.exec(select(AuditLog)).all())
    second = client.post(
        f"{USERS}/{user.id}/verify-email", headers=superuser_token_headers
    )
    assert second.status_code == 200
    db.expire_all()
    # 已放行过就不再重复写审计
    assert len(db.exec(select(AuditLog)).all()) == before


def test_manual_verify_unknown_user_404(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    import uuid

    response = client.post(
        f"{USERS}/{uuid.uuid4()}/verify-email", headers=superuser_token_headers
    )
    assert response.status_code == 404


def test_manual_verify_requires_admin(
    client: TestClient, db: Session, readonly_token_headers: dict[str, str]
) -> None:
    user = _pending_user(db)
    response = client.post(
        f"{USERS}/{user.id}/verify-email", headers=readonly_token_headers
    )
    assert response.status_code == 403


def test_list_filters_by_email_verified(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    pending = _pending_user(db)

    unverified = client.get(
        USERS,
        headers=superuser_token_headers,
        params={"email_verified": "false", "limit": 500},
    ).json()
    ids = [row["id"] for row in unverified["data"]]
    assert str(pending.id) in ids

    verified = client.get(
        USERS,
        headers=superuser_token_headers,
        params={"email_verified": "true", "limit": 500},
    ).json()
    assert str(pending.id) not in [row["id"] for row in verified["data"]]
    # 计数与过滤同条件：否则前端算出的总页数是错的
    all_users = client.get(
        USERS, headers=superuser_token_headers, params={"limit": 500}
    ).json()["data"]
    expected_verified = len(
        [row for row in all_users if row["email_verified_at"] is not None]
    )
    assert verified["count"] == expected_verified


@pytest.mark.usefixtures("smtp_configured")
def test_email_change_restarts_verification(
    client: TestClient, db: Session, sent_codes: list[str]
) -> None:
    """改邮箱必须清掉验证状态并重发新码：否则「先注册合规域名再改任意邮箱」可绕过白名单。"""
    user = _pending_user(db)
    crud.mark_email_verified(session=db, user=user)
    new_email = random_email()

    response = client.patch(
        f"{USERS}/me",
        headers=_login_headers(client, db, user),
        json={"email": new_email},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"] == new_email
    assert body["email_verified_at"] is None, "改邮箱后必须回到未验证"

    db.refresh(user)
    assert user.is_active is False
    assert len(sent_codes) == 1, "应当把新验证码发到新邮箱"


@pytest.mark.usefixtures("smtp_configured")
def test_email_change_rejected_by_suffix_rule(client: TestClient, db: Session) -> None:
    user = _pending_user(db)
    crud.mark_email_verified(session=db, user=user)
    add_rule(db, kind="email_suffix", list_type="deny", value="evil.com")

    response = client.patch(
        f"{USERS}/me",
        headers=_login_headers(client, db, user),
        json={"email": f"x@{random_lower_string()}.evil.com"},
    )
    assert response.status_code == 400
    assert "evil.com" in response.json()["detail"]


def _login_headers(client: TestClient, db: Session, user: User) -> dict[str, str]:
    """给已放行的账号换一个可用 token（改邮箱用例需要先登录再改）。

    密码在 `_pending_user` 里是随机值，这里直接重置成已知值再登录。
    """
    from app.models import UserUpdate

    password = random_lower_string()
    crud.update_user(session=db, db_user=user, user_in=UserUpdate(password=password))
    r = client.post(
        f"{settings.API_V1_STR}/login/access-token",
        data=login_form(db, email=user.email, password=password),
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
