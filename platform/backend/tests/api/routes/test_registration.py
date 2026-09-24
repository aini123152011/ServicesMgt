"""自助注册与邮箱验证接口。

覆盖 AC1（注册 → 收码 → 验证 → 登录）、AC4（重复注册不覆盖密码）、AC5（重发节流）、
AC9（验证码校验）、AC10（注册开关）、AC11（邮件未配置时不 500、不建半成品账号）。
"""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.main import app
from app.models import AuditLog, User
from tests.utils.access import add_rule, set_registration_enabled, wipe_access_state
from tests.utils.utils import (
    captcha_form,
    login_form,
    random_email,
    random_lower_string,
)

REGISTER = f"{settings.API_V1_STR}/auth/register"
VERIFY = f"{settings.API_V1_STR}/auth/verify-email"
RESEND = f"{settings.API_V1_STR}/auth/resend-verification"
AVAILABLE = f"{settings.API_V1_STR}/auth/registration-available"
LOGIN = f"{settings.API_V1_STR}/login/access-token"


@pytest.fixture(autouse=True)
def clean_state(db: Session) -> Generator[None]:
    wipe_access_state(db)
    yield
    wipe_access_state(db)


def _register(
    client: TestClient, db: Session, *, email: str, password: str
) -> dict[str, Any]:
    payload: dict[str, object] = {
        "email": email,
        "password": password,
        "full_name": "测试用户",
        **captcha_form(db, scope="register"),
    }
    response = client.post(REGISTER, json=payload)
    return {"status": response.status_code, "body": response.json()}


def _audit_actions(db: Session) -> list[str]:
    db.expire_all()
    rows = db.exec(select(AuditLog)).all()
    return [row.action for row in rows]


@pytest.mark.usefixtures("smtp_configured")
def test_register_then_verify_then_login(
    client: TestClient, db: Session, sent_codes: list[str]
) -> None:
    """主链路：注册 → 收到 6 位码 → 验证 → 登录成功。"""
    email = random_email()
    password = random_lower_string()

    result = _register(client, db, email=email, password=password)
    assert result["status"] == 200, result
    assert len(sent_codes) == 1
    code = sent_codes[0]
    assert len(code) == 6 and code.isdigit()

    user = db.exec(select(User).where(User.email == email)).first()
    assert user is not None
    assert user.email_verified_at is None
    assert user.is_active is False, "未验证账号必须同时被 is_active 拦住"

    # 未验证不能登录，且提示要说明是「未验证」而不是「密码错」
    denied = client.post(LOGIN, data=login_form(db, email=email, password=password))
    assert denied.status_code == 400
    assert denied.json()["detail"] == "Email is not verified"

    verified = client.post(VERIFY, json={"email": email, "code": code})
    assert verified.status_code == 200, verified.text

    db.refresh(user)
    assert user.email_verified_at is not None
    assert user.is_active is True

    ok = client.post(LOGIN, data=login_form(db, email=email, password=password))
    assert ok.status_code == 200
    assert ok.json()["access_token"]


@pytest.mark.usefixtures("smtp_configured")
def test_register_wrong_captcha_rejected(
    client: TestClient, db: Session, sent_codes: list[str]
) -> None:
    issue = captcha_form(db, scope="register")
    response = client.post(
        REGISTER,
        json={
            "email": random_email(),
            "password": random_lower_string(),
            "full_name": None,
            "captcha_id": issue["captcha_id"],
            "captcha_answer": "WRONG",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Captcha is incorrect or has expired"
    assert sent_codes == []


def test_register_rejected_when_email_service_missing(
    client: TestClient, db: Session
) -> None:
    """邮件没配时必须明确报错，且**不建账号**（否则会留下永远收不到码的账号）。"""
    email = random_email()
    result = _register(client, db, email=email, password=random_lower_string())
    assert result["status"] == 503
    assert result["body"]["detail"] == "Email service is not configured"
    assert db.exec(select(User).where(User.email == email)).first() is None


@pytest.mark.usefixtures("smtp_configured")
def test_register_rejected_when_disabled(client: TestClient, db: Session) -> None:
    set_registration_enabled(db, enabled=False)
    result = _register(client, db, email=random_email(), password=random_lower_string())
    assert result["status"] == 403
    assert result["body"]["detail"] == "Self-service registration is disabled"


def test_registration_available_reflects_state(client: TestClient, db: Session) -> None:
    body = client.get(AVAILABLE).json()
    assert body == {"enabled": True, "email_configured": False}

    set_registration_enabled(db, enabled=False)
    assert client.get(AVAILABLE).json()["enabled"] is False


@pytest.mark.usefixtures("smtp_configured")
def test_deny_suffix_blocks_registration(client: TestClient, db: Session) -> None:
    add_rule(db, kind="email_suffix", list_type="deny", value="evil.com")
    result = _register(
        client,
        db,
        email=f"a@{random_lower_string()}.evil.com",
        password=random_lower_string(),
    )
    assert result["status"] == 400
    assert "evil.com" in result["body"]["detail"]
    assert "auth.register" in _audit_actions(db)


@pytest.mark.usefixtures("smtp_configured")
def test_allowlist_blocks_unlisted_suffix(client: TestClient, db: Session) -> None:
    add_rule(db, kind="email_suffix", list_type="allow", value="schkzy.cn")
    blocked = _register(
        client, db, email=random_email(), password=random_lower_string()
    )
    assert blocked["status"] == 400
    assert "not in the allowed list" in blocked["body"]["detail"]

    allowed = _register(
        client,
        db,
        email=f"u{random_lower_string()}@schkzy.cn",
        password=random_lower_string(),
    )
    assert allowed["status"] == 200, allowed


@pytest.mark.usefixtures("smtp_configured")
def test_duplicate_registration_does_not_overwrite_password(
    client: TestClient, db: Session, sent_codes: list[str]
) -> None:
    """已注册且已验证：绝不覆盖密码（否则注册接口等于改别人的密码）。"""
    email = random_email()
    original_password = random_lower_string()
    assert (
        _register(client, db, email=email, password=original_password)["status"] == 200
    )
    client.post(VERIFY, json={"email": email, "code": sent_codes[0]})

    again = _register(client, db, email=email, password=random_lower_string())
    assert again["status"] == 409
    assert again["body"]["detail"] == "User with this email already exists"

    # 原密码仍然有效
    login = client.post(
        LOGIN, data=login_form(db, email=email, password=original_password)
    )
    assert login.status_code == 200


@pytest.mark.usefixtures("smtp_configured")
def test_duplicate_pending_registration_resends_without_overwriting(
    client: TestClient, db: Session, sent_codes: list[str]
) -> None:
    """未验证账号重复提交：重发新码，且不改密码。"""
    email = random_email()
    original_password = random_lower_string()
    assert (
        _register(client, db, email=email, password=original_password)["status"] == 200
    )
    first_code = sent_codes[0]

    again = _register(client, db, email=email, password=random_lower_string())
    assert again["status"] == 200
    # 立即重复提交会撞上 60 秒重发节流，因此不再发第二封（响应文案仍是"已发送"）
    assert len(sent_codes) == 1

    # 把上次发送时间挪过节流窗口后再提交，才会重发新码
    user = db.exec(select(User).where(User.email == email)).first()
    assert user is not None
    user.verification_sent_at = datetime.now(UTC) - timedelta(seconds=120)
    db.add(user)
    db.commit()

    resent = _register(client, db, email=email, password=random_lower_string())
    assert resent["status"] == 200
    assert len(sent_codes) == 2, "过了节流窗口应当重发新码"
    assert sent_codes[1] != first_code

    # 旧码已作废，新码可用
    assert (
        client.post(VERIFY, json={"email": email, "code": first_code}).status_code
        == 400
    )
    assert (
        client.post(VERIFY, json={"email": email, "code": sent_codes[1]}).status_code
        == 200
    )

    # 密码仍是第一次设置的那个
    assert (
        client.post(
            LOGIN, data=login_form(db, email=email, password=original_password)
        ).status_code
        == 200
    )


@pytest.mark.usefixtures("smtp_configured")
def test_verify_wrong_code_fails_and_audits(client: TestClient, db: Session) -> None:
    email = random_email()
    assert (
        _register(client, db, email=email, password=random_lower_string())["status"]
        == 200
    )

    response = client.post(VERIFY, json={"email": email, "code": "000000"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Verification code is incorrect or has expired"
    assert "auth.verify_email_failed" in _audit_actions(db)


def test_verify_unknown_email_does_not_reveal_existence(client: TestClient) -> None:
    response = client.post(VERIFY, json={"email": random_email(), "code": "123456"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Verification code is incorrect or has expired"


def test_verify_short_code_rejected_by_schema(client: TestClient) -> None:
    response = client.post(VERIFY, json={"email": random_email(), "code": "123"})
    assert response.status_code == 422


@pytest.mark.usefixtures("smtp_configured")
def test_resend_is_throttled_and_does_not_reveal_existence(
    client: TestClient, db: Session, sent_codes: list[str]
) -> None:
    """重发接口：节流是静默的（返回 429 会变成账号存在性信号），响应恒定。"""
    email = random_email()
    assert (
        _register(client, db, email=email, password=random_lower_string())["status"]
        == 200
    )
    assert len(sent_codes) == 1

    # 立即重发：60 秒节流生效，不再发信
    first = client.post(
        RESEND, json={"email": email, **captcha_form(db, scope="resend")}
    )
    assert first.status_code == 200
    assert len(sent_codes) == 1

    # 不存在的邮箱返回同一句话
    unknown = client.post(
        RESEND, json={"email": random_email(), **captcha_form(db, scope="resend")}
    )
    assert unknown.status_code == 200
    assert unknown.json() == first.json()


@pytest.mark.usefixtures("smtp_configured")
def test_resend_requires_captcha(client: TestClient, db: Session) -> None:
    issue = captcha_form(db, scope="resend")
    response = client.post(
        RESEND,
        json={
            "email": random_email(),
            "captcha_id": issue["captcha_id"],
            "captcha_answer": "NOPE",
        },
    )
    assert response.status_code == 400


@pytest.mark.usefixtures("smtp_configured")
def test_ip_deny_blocks_registration_and_is_audited(db: Session) -> None:
    """IP 拒绝规则命中时注册被拒，并留下带来源 IP 的审计。

    必须自建 TestClient：默认客户端的 peer 是 "testclient"（非 IP），
    任何网段规则都匹配不到它——那样测的就不是 IP 规则本身了。
    """
    add_rule(db, kind="ip", list_type="deny", value="203.0.113.0/24")

    with TestClient(app, client=("203.0.113.7", 51000)) as blocked_client:
        result = _register(
            blocked_client, db, email=random_email(), password=random_lower_string()
        )
    assert result["status"] == 403
    assert "203.0.113.0/24" in result["body"]["detail"]

    entries = db.exec(
        select(AuditLog).where(AuditLog.action == "auth.ip_blocked")
    ).all()
    assert entries, "IP 拦截必须留痕"
    assert entries[0].ip == "203.0.113.7"


@pytest.mark.usefixtures("smtp_configured")
def test_ip_allowlist_blocks_unlisted_source(db: Session) -> None:
    add_rule(db, kind="ip", list_type="allow", value="10.0.0.0/8")
    with TestClient(app, client=("203.0.113.7", 51001)) as blocked_client:
        result = _register(
            blocked_client, db, email=random_email(), password=random_lower_string()
        )
    assert result["status"] == 403
