"""自助注册与邮箱验证（匿名入口）。

四个端点：

- `POST /auth/register`             提交注册（创建待验证账号并发出 6 位验证码）
- `POST /auth/verify-email`         用邮箱收到的验证码完成验证
- `POST /auth/resend-verification`  重新发码
- `GET  /auth/registration-available` 注册是否可用（登录页据此显隐入口）

校验顺序是有意设计的，不能随意调换：

    IP 规则 → 注册开关 → 邮件是否已配置 → 验证码 → 域名后缀 → 重复注册分流

前三条回答「这件事现在能不能做」；第四条挡住脚本（匿名入口每次尝试都要过验证码）；
第五条是准入规则；最后才碰库。邮件未配置排在验证码之前：连信都发不出去时不该让用户白填一次验证码。

**验证码方式而不是链接**：平台在内网（`http://192.168.235.153:18080`），而邮箱很可能在手机的
企业微信里读——邮件里的内网链接在手机上点不开。发码则只要求「看得到码」，不要求读信设备
能访问平台。代价是 6 位码有爆破面，所以码落库并限制尝试次数（见 `app/email_verification.py`）。

`detail` 文案遵循项目约定用英文（见 .trellis/spec/backend/error-handling.md），
中文只出现在响应体的 message 字段。
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from app import access_rules, captcha, crud, email_verification
from app.api.deps import ClientIp, SessionDep
from app.core.config import settings
from app.models import (
    Message,
    RegisterRequest,
    RegistrationAvailability,
    ResendVerificationRequest,
    User,
    UserCreate,
    VerifyEmailRequest,
)
from app.utils import EmailNotConfiguredError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

# 验证码校验失败统一措辞：不区分「没这张码/已过期/次数用尽/填错了」，不给攻击者区分依据
INVALID_CODE_DETAIL = "Verification code is incorrect or has expired"
# 重发接口的固定响应：无论邮箱是否存在都返回它（防账号枚举）
RESEND_ACCEPTED_MESSAGE = (
    "If that address exists and is not verified yet, a new code has been sent"
)


def _guard_source_ip(session: SessionDep, client_ip: str, *, endpoint: str) -> None:
    """IP 规则拦截。规则集为空即放行，因此「默认不启用」不需要额外的开关位。"""
    rules = access_rules.load_rules(session, access_rules.IP_KIND)
    decision = access_rules.match_ip(client_ip, rules)
    if decision.allowed:
        return
    crud.record_audit_log(
        session=session,
        user_id=None,
        user_email=None,
        action="auth.ip_blocked",
        detail=f"{endpoint}: {decision.reason}",
        ip=client_ip,
    )
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=decision.reason)


def _verify_captcha(
    session: SessionDep, *, captcha_id: uuid.UUID, answer: str, scope: str
) -> None:
    """校验并消费图片验证码；失败即 400。"""
    if not captcha.verify_challenge(
        session,
        challenge_id=captcha_id,
        answer=answer,
        scope=scope,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Captcha is incorrect or has expired",
        )


def _require_email_service() -> None:
    if not settings.emails_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email service is not configured",
        )


def send_verification_code_or_error(session: SessionDep, *, user: User) -> None:
    """发验证码邮件；失败转 502/503。

    发信失败时**账号保留**：这样用户可以用「重新发送」再试，管理员也能在用户列表里手动放行，
    比回滚账号更可恢复。
    """
    try:
        email_verification.send_verification_email(session, user=user)
    except EmailNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email service is not configured",
        ) from exc
    except Exception as exc:  # SMTP 交互失败：转 502 并记原始异常
        logger.error("Failed to send verification email: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to send verification email",
        ) from exc
    user.verification_sent_at = datetime.now(UTC)
    session.add(user)
    session.commit()


@router.post("/auth/register", response_model=Message)
def register(
    *, session: SessionDep, client_ip: ClientIp, payload: RegisterRequest
) -> Message:
    """提交注册：创建待验证账号并把验证码发到该邮箱。

    注册成功不代表能登录——邮箱验证通过前账号 `is_active=False`，登录会被拒并提示未验证。
    """
    _guard_source_ip(session, client_ip, endpoint="register")
    if not access_rules.registration_enabled(session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Self-service registration is disabled",
        )
    _require_email_service()
    _verify_captcha(
        session,
        captcha_id=payload.captcha_id,
        answer=payload.captcha_answer,
        scope="register",
    )

    suffix_decision = access_rules.match_email_suffix(
        payload.email, access_rules.load_rules(session, access_rules.EMAIL_SUFFIX_KIND)
    )
    if not suffix_decision.allowed:
        crud.record_audit_log(
            session=session,
            user_id=None,
            user_email=payload.email,
            action="auth.register",
            detail=f"rejected: {suffix_decision.reason}",
            ip=client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=suffix_decision.reason
        )

    existing = crud.get_user_by_email(session=session, email=payload.email)
    if existing is not None and existing.email_verified_at is not None:
        # 已注册且已验证：绝不覆盖密码，引导走登录/找回密码。
        # 这里会泄露「该邮箱已存在」——注册流程无法避免（否则无法解释为何不建号），
        # 且不泄露更多信息，属有意接受的取舍（见 prd.md R4）
        crud.record_audit_log(
            session=session,
            user_id=None,
            user_email=payload.email,
            action="auth.register",
            detail="rejected: already registered",
            ip=client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this email already exists",
        )

    if existing is not None:
        # 已提交但未验证：重发验证码，**不覆盖已设置的密码**——否则任何人都能用注册接口
        # 改掉别人未验证账号的密码
        if not email_verification.resend_too_soon(existing.verification_sent_at):
            send_verification_code_or_error(session, user=existing)
        crud.record_audit_log(
            session=session,
            user_id=None,
            user_email=existing.email,
            action="auth.register",
            detail="resent verification code",
            ip=client_ip,
        )
        return Message(
            message="Verification code has been sent, please check your inbox"
        )

    user = crud.create_user(
        session=session,
        user_create=UserCreate(
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            is_active=False,
        ),
        email_verified=False,
    )
    send_verification_code_or_error(session, user=user)
    crud.record_audit_log(
        session=session,
        user_id=None,
        user_email=user.email,
        action="auth.register",
        detail="created pending account and sent verification code",
        ip=client_ip,
    )
    return Message(
        message="Registration submitted, please enter the code sent to your email"
    )


@router.post("/auth/verify-email", response_model=Message)
def verify_email(
    *, session: SessionDep, client_ip: ClientIp, payload: VerifyEmailRequest
) -> Message:
    """用邮箱收到的验证码完成验证。重复提交同一张已用过的码会被拒（一次性）。"""
    user = crud.get_user_by_email(session=session, email=payload.email)
    if user is None or user.email_verified_at is not None:
        # 邮箱不存在与已验证都走这里：前者不泄露账号是否存在，后者要求用户去登录
        if user is not None:
            return Message(message="Email is already verified")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_CODE_DETAIL
        )

    if not email_verification.verify_code(session, user=user, code=payload.code):
        crud.record_audit_log(
            session=session,
            user_id=None,
            user_email=user.email,
            action="auth.verify_email_failed",
            detail="invalid verification code",
            ip=client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_CODE_DETAIL
        )

    crud.mark_email_verified(session=session, user=user)
    crud.record_audit_log(
        session=session,
        user_id=user.id,
        user_email=user.email,
        action="auth.verify_email",
        detail="email verified",
        ip=client_ip,
    )
    return Message(message="Email verified, you can sign in now")


@router.post("/auth/resend-verification", response_model=Message)
def resend_verification(
    *, session: SessionDep, client_ip: ClientIp, payload: ResendVerificationRequest
) -> Message:
    """重新发送验证码。

    无论邮箱是否存在都返回同一句话（防账号枚举）。节流也是**静默**的：返回 429 会让
    「这个邮箱存在且刚发过」变成可探测的信号，而 60 秒内的重复请求本来也不该重发。
    """
    _guard_source_ip(session, client_ip, endpoint="resend-verification")
    _require_email_service()
    _verify_captcha(
        session,
        captcha_id=payload.captcha_id,
        answer=payload.captcha_answer,
        scope="resend",
    )

    user = crud.get_user_by_email(session=session, email=payload.email)
    if (
        user is not None
        and user.email_verified_at is None
        and not email_verification.resend_too_soon(user.verification_sent_at)
    ):
        send_verification_code_or_error(session, user=user)
        crud.record_audit_log(
            session=session,
            user_id=None,
            user_email=user.email,
            action="auth.resend_verification",
            detail="resent verification code",
            ip=client_ip,
        )
    return Message(message=RESEND_ACCEPTED_MESSAGE)


@router.get("/auth/registration-available", response_model=RegistrationAvailability)
def registration_available(*, session: SessionDep) -> RegistrationAvailability:
    """注册是否可用。登录页据此决定是否显示注册入口，并区分「被关闭」与「邮件没配好」。"""
    return RegistrationAvailability(
        enabled=access_rules.registration_enabled(session),
        email_configured=settings.emails_enabled,
    )
