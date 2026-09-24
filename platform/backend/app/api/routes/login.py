"""登录、密码找回与令牌校验。

登录校验顺序是有意设计的，**不能随意调换**（见 prd.md R10/R11）：

    IP 规则 → 图片验证码 → 凭证 → 邮箱未验证 → 账号停用 → 签发 token

- 前两步在「碰凭证」之前：IP 规则拦住不允许的来源；验证码让每次尝试都要过一道人机校验，
  否则脚本可以无限撞密码。
- 第三步之后才区分「未验证 / 已停用」：在密码校验通过之前透露账号状态，等于把登录接口
  变成账号枚举接口——`crud.authenticate` 在用户不存在时也跑一次 DUMMY_HASH 校验，
  正是为了不让响应耗时泄露存在性，这个努力不能被一句「邮箱未验证」抵消掉。
"""

import logging
import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm

from app import access_rules, captcha, crud
from app.api.deps import ClientIp, CurrentUser, SessionDep, get_current_active_superuser
from app.core import security
from app.core.config import settings
from app.models import Message, NewPassword, Token, UserPublic, UserUpdate
from app.utils import (
    generate_password_reset_token,
    generate_reset_password_email,
    send_email,
    verify_password_reset_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["login"])

# 邮箱找回的固定响应：无论邮箱是否存在、邮件是否配置好，都返回这一句（防账号枚举）
_RECOVERY_MESSAGE = "If that email is registered, we sent a password recovery link"


@router.post("/login/access-token")
def login_access_token(
    session: SessionDep,
    client_ip: ClientIp,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    captcha_id: Annotated[uuid.UUID | None, Form()] = None,
    captcha_answer: Annotated[str, Form()] = "",
) -> Token:
    """OAuth2 compatible token login, get an access token for future requests.

    校验顺序见模块说明；失败原因分档给出，但「凭证错」始终是同一句。

    验证码字段作为**独立的 Form 参数**而不是继承 OAuth2PasswordRequestForm：
    该类的 `__init__` 是 keyword-only 且字段带 Doc 元数据，继承要复刻一长串签名；
    并列 Form 参数更简单，且与 OAuth2 的 username/password 从同一个表单体里取值。
    """
    ip_decision = access_rules.match_ip(
        client_ip, access_rules.load_rules(session, access_rules.IP_KIND)
    )
    if not ip_decision.allowed:
        crud.record_audit_log(
            session=session,
            user_id=None,
            user_email=form_data.username,
            action="auth.ip_blocked",
            detail=f"login: {ip_decision.reason}",
            ip=client_ip,
        )
        raise HTTPException(status_code=403, detail=ip_decision.reason)

    # 缺 captcha_id 与填错走同一句错误：不告诉调用方「是没带字段还是填错了」
    if captcha_id is None or not captcha.verify_challenge(
        session,
        challenge_id=captcha_id,
        answer=captcha_answer,
        scope="login",
    ):
        raise HTTPException(
            status_code=400, detail="Captcha is incorrect or has expired"
        )

    user = crud.authenticate(
        session=session, email=form_data.username, password=form_data.password
    )
    if not user:
        # 认证失败只记 debug：登录接口会被脚本高频试探，审计表不该被刷满
        logger.debug("Login failed for an unknown or mismatched credential")
        raise HTTPException(status_code=400, detail="Incorrect email or password")

    if user.email_verified_at is None:
        # 到这一步说明密码是对的，可以安全告知状态
        raise HTTPException(status_code=400, detail="Email is not verified")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return Token(
        access_token=security.create_access_token(
            user.id, expires_delta=access_token_expires
        )
    )


@router.post("/login/test-token", response_model=UserPublic)
def test_token(session: SessionDep, current_user: CurrentUser) -> Any:
    """
    Test access token
    """
    # 与 /users/me 对齐：响应回填真实角色，前端据此显隐操作按钮
    return crud.build_user_public(session=session, user=current_user)


@router.post("/password-recovery/{email}")
def recover_password(email: str, session: SessionDep) -> Message:
    """Password Recovery.

    修复的既有缺陷：原实现直接调 `send_email`，而它在邮件未配置时抛断言（→ 500）。
    于是「已注册邮箱 → 500」「未注册邮箱 → 200」形成账号枚举差异。现在无论哪种情况都返回
    同一句，邮件没配时只记日志（运维看得到，调用方看不出）。
    """
    user = crud.get_user_by_email(session=session, email=email)
    if user is None:
        return Message(message=_RECOVERY_MESSAGE)

    if not settings.emails_enabled:
        logger.warning(
            "Password recovery requested but email service is not configured"
        )
        return Message(message=_RECOVERY_MESSAGE)

    try:
        password_reset_token = generate_password_reset_token(email=email)
        email_data = generate_reset_password_email(
            email_to=user.email, email=email, token=password_reset_token
        )
        send_email(
            email_to=user.email,
            subject=email_data.subject,
            html_content=email_data.html_content,
        )
    except Exception as exc:  # SMTP 交互失败：记日志，对外仍是同一句
        logger.error("Failed to send password recovery email: %s", exc)
    return Message(message=_RECOVERY_MESSAGE)


@router.post("/reset-password/")
def reset_password(session: SessionDep, body: NewPassword) -> Message:
    """
    Reset password
    """
    email = verify_password_reset_token(token=body.token)
    if not email:
        raise HTTPException(status_code=400, detail="Invalid token")
    user = crud.get_user_by_email(session=session, email=email)
    if not user:
        # Don't reveal that the user doesn't exist - use same error as invalid token
        raise HTTPException(status_code=400, detail="Invalid token")
    elif not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    user_in_update = UserUpdate(password=body.new_password)
    crud.update_user(
        session=session,
        db_user=user,
        user_in=user_in_update,
    )
    return Message(message="Password updated successfully")


@router.post(
    "/password-recovery-html-content/{email}",
    dependencies=[Depends(get_current_active_superuser)],
    response_class=HTMLResponse,
)
def recover_password_html_content(email: str, session: SessionDep) -> Any:
    """
    HTML Content for Password Recovery
    """
    user = crud.get_user_by_email(session=session, email=email)

    if not user:
        raise HTTPException(
            status_code=404,
            detail="The user with this username does not exist in the system.",
        )
    password_reset_token = generate_password_reset_token(email=email)
    email_data = generate_reset_password_email(
        email_to=user.email, email=email, token=password_reset_token
    )

    return HTMLResponse(
        content=email_data.html_content, headers={"subject:": email_data.subject}
    )
