"""密码找回接口的枚举缺陷修复（AC11 的后半段）。

原实现直接调 `send_email`，而它在邮件未配置时抛断言 → 500。于是
「已注册邮箱 → 500」「未注册邮箱 → 200」形成账号枚举差异。
现在三种情况（已注册且邮件可用 / 已注册但邮件没配 / 未注册）必须返回**完全一致**的响应。
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import UserCreate
from tests.utils.utils import random_email, random_lower_string

RECOVERY = f"{settings.API_V1_STR}/password-recovery"

_GENERIC = {"message": "If that email is registered, we sent a password recovery link"}


def _make_user(db: Session) -> str:
    email = random_email()
    crud.create_user(
        session=db,
        user_create=UserCreate(email=email, password=random_lower_string()),
    )
    return email


@pytest.mark.usefixtures("smtp_configured")
def test_recovery_for_known_email_sends_mail(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(
        "app.api.routes.login.send_email",
        lambda *, email_to, subject="", html_content="": sent.append(email_to),
    )
    email = _make_user(db)

    response = client.post(f"{RECOVERY}/{email}")
    assert response.status_code == 200
    assert response.json() == _GENERIC
    assert sent == [email]


def test_recovery_for_known_email_without_smtp_does_not_500(
    client: TestClient, db: Session
) -> None:
    """这就是修复点：邮件没配时也必须 200 + 同一句话，而不是抛断言变 500。"""
    email = _make_user(db)
    response = client.post(f"{RECOVERY}/{email}")
    assert response.status_code == 200
    assert response.json() == _GENERIC


@pytest.mark.usefixtures("smtp_configured")
def test_recovery_for_unknown_email_matches_known_email(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.api.routes.login.send_email", lambda **_kwargs: None)
    known = client.post(f"{RECOVERY}/{_make_user(db)}")
    unknown = client.post(f"{RECOVERY}/{random_email()}")

    # 状态码与响应体都一致 → 无法据响应判断邮箱是否存在
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() == _GENERIC


def test_recovery_without_smtp_matches_unknown_email(
    client: TestClient, db: Session
) -> None:
    """「已注册 + 邮件没配」与「未注册」也必须无法区分（原缺陷正是这两个不一样）。"""
    known = client.post(f"{RECOVERY}/{_make_user(db)}")
    unknown = client.post(f"{RECOVERY}/{random_email()}")
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
