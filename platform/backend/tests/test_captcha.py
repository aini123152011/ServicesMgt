"""图片验证码：签发、一次性、过期、scope 隔离、过期清理。

这些断言对应 AC9：同一张码第二次必然失败、超时失效、跨入口复用失败。
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from app import captcha
from app.models import CaptchaChallenge


def _expire(db: Session, challenge_id: uuid.UUID) -> None:
    row = db.get(CaptchaChallenge, challenge_id)
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.add(row)
    db.commit()


def test_issue_returns_png_and_stores_hash(db: Session) -> None:
    issue = captcha.new_challenge(db, scope="login", ip="10.0.0.5")
    assert issue.png.startswith(b"\x89PNG")
    assert len(issue.answer) == captcha.LENGTH

    row = db.get(CaptchaChallenge, issue.challenge_id)
    assert row is not None
    # 库里存的是哈希而不是明文答案
    assert row.answer_hash != issue.answer
    assert len(row.answer_hash) == 64
    assert row.scope == "login"
    assert row.created_ip == "10.0.0.5"
    assert row.used_at is None


def test_data_url_prefix(db: Session) -> None:
    issue = captcha.new_challenge(db, scope="login")
    assert captcha.to_data_url(issue.png).startswith("data:image/png;base64,")


def test_verify_success_then_consumed(db: Session) -> None:
    issue = captcha.new_challenge(db, scope="login")
    assert captcha.verify_challenge(
        db, challenge_id=issue.challenge_id, answer=issue.answer, scope="login"
    )
    # 一次性：同一张码第二次使用必然失败
    assert not captcha.verify_challenge(
        db, challenge_id=issue.challenge_id, answer=issue.answer, scope="login"
    )


def test_verify_is_case_insensitive_and_trims(db: Session) -> None:
    issue = captcha.new_challenge(db, scope="login")
    assert captcha.verify_challenge(
        db,
        challenge_id=issue.challenge_id,
        answer=f"  {issue.answer.lower()}  ",
        scope="login",
    )


def test_verify_rejects_wrong_answer(db: Session) -> None:
    issue = captcha.new_challenge(db, scope="login")
    assert not captcha.verify_challenge(
        db, challenge_id=issue.challenge_id, answer="ZZZZ", scope="login"
    )


def test_verify_rejects_other_scope(db: Session) -> None:
    # 跨入口复用同一张码即失败（注册页的码不能拿去登录）
    issue = captcha.new_challenge(db, scope="register")
    assert not captcha.verify_challenge(
        db, challenge_id=issue.challenge_id, answer=issue.answer, scope="login"
    )


def test_verify_rejects_unknown_id(db: Session) -> None:
    assert not captcha.verify_challenge(
        db, challenge_id=uuid.uuid4(), answer="ABCD", scope="login"
    )


def test_verify_rejects_expired(db: Session) -> None:
    issue = captcha.new_challenge(db, scope="login")
    _expire(db, issue.challenge_id)
    assert not captcha.verify_challenge(
        db, challenge_id=issue.challenge_id, answer=issue.answer, scope="login"
    )


def test_purge_removes_expired_rows(db: Session) -> None:
    issue = captcha.new_challenge(db, scope="login")
    _expire(db, issue.challenge_id)
    assert captcha.purge_expired(db) >= 1
    assert db.get(CaptchaChallenge, issue.challenge_id) is None
