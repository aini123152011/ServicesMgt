"""邮箱验证码：签发、校验、重发节流。

**为什么落库而不是无状态签名**：6 位数字码只有 10^6 种可能。服务端若不记录「这张码试过几次」，
攻击者可以对着一个已知邮箱在有效期内把组合跑完（几分钟的事）。落库才能实现**尝试次数上限**
与**一次性**——这也是它和密码重置令牌（无状态 JWT）最本质的区别。

**为什么绑定 user_id 而不是邮箱**：邮箱可变、账号不变。改邮箱时重发新码并作废旧码，
「先用合规域名注册并通过验证，再把邮箱改成任意地址」这条路因此走不通。

**同一账号同时只有一张有效码**：发新码时先删掉该账号此前的码，既避免多张码并存导致
尝试次数被摊薄，也避免表无限增长。
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.core.config import settings
from app.models import EmailVerificationCode, User
from app.utils import EmailData, render_email_template, send_email

# 同一邮箱两次发码的最小间隔（秒）：注册与重发都是匿名可调的，没有节流会被当成发信机
RESEND_INTERVAL_SECONDS = 60
CODE_LENGTH = 6
SCOPE_REGISTER = "register"
SCOPE_EMAIL_CHANGE = "email_change"


def issue_code(session: Session, *, user: User, scope: str = SCOPE_REGISTER) -> str:
    """签发一张验证码（作废该账号此前的所有码），返回明文码供发信使用。

    明文码只在本次调用内存在，库里存的是哈希。
    """
    purge_expired(session)
    session.exec(
        delete(EmailVerificationCode).where(
            col(EmailVerificationCode.user_id) == user.id
        )
    )

    code = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
    row = EmailVerificationCode(
        user_id=user.id,
        scope=scope,
        code_hash="",
        expires_at=datetime.now(UTC)
        + timedelta(minutes=settings.EMAIL_VERIFICATION_CODE_TTL_MINUTES),
        attempts=0,
    )
    # 哈希里带上票据 id：同一码在不同票据下的哈希不同，挡住「按已知码反查库」的做法
    row.code_hash = _hash(code, row.id)

    session.add(row)
    session.commit()
    return code


def verify_code(session: Session, *, user: User, code: str) -> bool:
    """校验验证码。

    未找到 / 已过期 / 次数用尽 / 不匹配一律返回 False（不区分原因，不给攻击者区分依据）。
    不匹配时累加尝试次数：这是防爆破的关键，6 位码不限次等于没有防护。
    """
    row = session.exec(
        select(EmailVerificationCode)
        .where(col(EmailVerificationCode.user_id) == user.id)
        .where(col(EmailVerificationCode.used_at).is_(None))
        .order_by(col(EmailVerificationCode.created_at).desc())
    ).first()
    if row is None:
        return False
    if _is_expired(row.expires_at):
        return False
    if row.attempts >= settings.EMAIL_VERIFICATION_MAX_ATTEMPTS:
        return False

    row.attempts += 1
    if not hmac.compare_digest(row.code_hash, _hash(code.strip(), row.id)):
        session.add(row)
        session.commit()
        return False

    row.used_at = datetime.now(UTC)
    session.add(row)
    session.commit()
    return True


def purge_expired(session: Session) -> int:
    """删除已过期的验证码，返回删除行数（每次签发顺带清理）。"""
    result = session.exec(
        delete(EmailVerificationCode).where(
            col(EmailVerificationCode.expires_at) < datetime.now(UTC)
        )
    )
    session.commit()
    return int(result.rowcount or 0)


def generate_verify_email(*, email_to: str, code: str) -> EmailData:
    """渲染验证码邮件。"""
    html_content = render_email_template(
        template_name="verify_email.html",
        context={
            "project_name": settings.PROJECT_NAME,
            "username": email_to,
            "email": email_to,
            "code": code,
            "valid_minutes": settings.EMAIL_VERIFICATION_CODE_TTL_MINUTES,
            "max_attempts": settings.EMAIL_VERIFICATION_MAX_ATTEMPTS,
        },
    )
    return EmailData(
        html_content=html_content,
        subject=f"{settings.PROJECT_NAME} - 邮箱验证码",
    )


def send_verification_email(
    session: Session, *, user: User, scope: str = SCOPE_REGISTER
) -> None:
    """签发验证码并发送到该账号邮箱。

    Raises:
        EmailNotConfiguredError: 邮件未配置（调用方应返回 503，而不是建出收不到信的账号）。
        其他异常来自 SMTP 交互，调用方按「发信失败」处理（账号保留，可重发）。
    """
    code = issue_code(session, user=user, scope=scope)
    data = generate_verify_email(email_to=user.email, code=code)
    send_email(
        email_to=user.email, subject=data.subject, html_content=data.html_content
    )


def resend_too_soon(sent_at: datetime | None, *, now: datetime | None = None) -> bool:
    """距上次发送是否不足最小间隔（None 表示从未发过，不算过快）。"""
    if sent_at is None:
        return False
    reference = now or datetime.now(UTC)
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)
    return reference - sent_at < timedelta(seconds=RESEND_INTERVAL_SECONDS)


def _hash(code: str, row_id: object) -> str:
    payload = f"{row_id}:{code.strip()}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _is_expired(expires_at: datetime) -> bool:
    # 列是 TIMESTAMP WITH TIME ZONE，取出来是 aware datetime；naive 值（理论上不该出现）
    # 按 UTC 解释，避免直接比较抛 TypeError
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at < datetime.now(UTC)
