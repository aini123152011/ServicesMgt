"""邮箱验证码：签发、一次性、尝试次数上限、过期、重发节流。

**尝试次数上限是这套机制的安全底线**：6 位码只有 10^6 空间，没有它等于没有防护。
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, col, select

from app import email_verification
from app.core.config import settings
from app.models import EmailVerificationCode, User
from tests.utils.user import create_random_user


def _active_row(db: Session, user: User) -> EmailVerificationCode:
    row = db.exec(
        select(EmailVerificationCode).where(
            EmailVerificationCode.user_id == user.id,
            col(EmailVerificationCode.used_at).is_(None),
        )
    ).first()
    assert row is not None
    return row


def test_issue_returns_digits_and_stores_hash(db: Session) -> None:
    user = create_random_user(db)
    code = email_verification.issue_code(db, user=user)

    assert len(code) == email_verification.CODE_LENGTH
    assert code.isdigit()

    row = _active_row(db, user)
    assert row.code_hash != code
    assert len(row.code_hash) == 64
    assert row.attempts == 0
    assert row.used_at is None


def test_issue_invalidates_previous_code(db: Session) -> None:
    """同一账号同时只有一张有效码：发新码即作废旧码。"""
    user = create_random_user(db)
    first = email_verification.issue_code(db, user=user)
    second = email_verification.issue_code(db, user=user)

    assert not email_verification.verify_code(db, user=user, code=first)
    assert email_verification.verify_code(db, user=user, code=second)


def test_verify_consumes_code_once(db: Session) -> None:
    user = create_random_user(db)
    code = email_verification.issue_code(db, user=user)

    assert email_verification.verify_code(db, user=user, code=code)
    # 一次性：用过即废
    assert not email_verification.verify_code(db, user=user, code=code)


def test_wrong_code_counts_attempts(db: Session) -> None:
    user = create_random_user(db)
    email_verification.issue_code(db, user=user)

    assert not email_verification.verify_code(db, user=user, code="000000")
    assert _active_row(db, user).attempts == 1


def test_attempts_exhausted_blocks_even_correct_code(db: Session) -> None:
    """超过尝试上限后必须重新发码——这是防爆破的关键。"""
    user = create_random_user(db)
    correct = email_verification.issue_code(db, user=user)

    for _ in range(settings.EMAIL_VERIFICATION_MAX_ATTEMPTS):
        assert not email_verification.verify_code(db, user=user, code="000000")

    assert not email_verification.verify_code(db, user=user, code=correct)

    # 重新发码后可以正常验证
    fresh = email_verification.issue_code(db, user=user)
    assert email_verification.verify_code(db, user=user, code=fresh)


def test_expired_code_rejected(db: Session) -> None:
    user = create_random_user(db)
    code = email_verification.issue_code(db, user=user)

    row = _active_row(db, user)
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.add(row)
    db.commit()

    assert not email_verification.verify_code(db, user=user, code=code)


def test_verify_without_any_code_fails(db: Session) -> None:
    user = create_random_user(db)
    assert not email_verification.verify_code(db, user=user, code="123456")


def test_purge_expired_removes_rows(db: Session) -> None:
    user = create_random_user(db)
    email_verification.issue_code(db, user=user)
    row = _active_row(db, user)
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.add(row)
    db.commit()

    assert email_verification.purge_expired(db) >= 1
    remaining = db.exec(
        select(EmailVerificationCode).where(EmailVerificationCode.user_id == user.id)
    ).all()
    assert remaining == []


def test_generate_verify_email_contains_code_and_ttl() -> None:
    data = email_verification.generate_verify_email(
        email_to="user@example.com", code="135790"
    )
    assert "135790" in data.html_content
    assert str(settings.EMAIL_VERIFICATION_CODE_TTL_MINUTES) in data.html_content


class TestResendTooSoon:
    def test_none_means_never_sent(self) -> None:
        assert email_verification.resend_too_soon(None) is False

    def test_recent_send_is_throttled(self) -> None:
        assert email_verification.resend_too_soon(datetime.now(UTC)) is True

    def test_old_send_is_allowed(self) -> None:
        long_ago = datetime.now(UTC) - timedelta(
            seconds=email_verification.RESEND_INTERVAL_SECONDS + 5
        )
        assert email_verification.resend_too_soon(long_ago) is False

    def test_naive_datetime_is_treated_as_utc(self) -> None:
        naive_recent = datetime.now(UTC).replace(tzinfo=None)
        assert email_verification.resend_too_soon(naive_recent) is True


def test_issue_code_for_unknown_user_id_is_isolated(db: Session) -> None:
    """两个账号的码互不影响（绑定 user_id 而不是邮箱）。"""
    first_user = create_random_user(db)
    second_user = create_random_user(db)
    first_code = email_verification.issue_code(db, user=first_user)
    email_verification.issue_code(db, user=second_user)

    assert email_verification.verify_code(db, user=first_user, code=first_code)
    # 另一个账号不该受影响
    assert _active_row(db, second_user).used_at is None


def test_scope_recorded(db: Session) -> None:
    user = create_random_user(db)
    email_verification.issue_code(
        db, user=user, scope=email_verification.SCOPE_EMAIL_CHANGE
    )
    assert _active_row(db, user).scope == email_verification.SCOPE_EMAIL_CHANGE


def test_random_uuid_has_no_code(db: Session) -> None:
    user = User(
        id=uuid.uuid4(),
        email="ghost@example.com",
        hashed_password="x",
        is_active=True,
    )
    assert not email_verification.verify_code(db, user=user, code="123456")
