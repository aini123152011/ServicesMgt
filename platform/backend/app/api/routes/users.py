import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import col, func, select

from app import access_rules, crud
from app.api.deps import (
    AdminUser,
    ClientIp,
    CurrentUser,
    RequireAdmin,
    SessionDep,
)
from app.api.routes.registration import send_verification_code_or_error
from app.core.config import settings
from app.core.security import get_password_hash, verify_password
from app.models import (
    Message,
    UpdatePassword,
    User,
    UserCreate,
    UserPublic,
    UsersPublic,
    UserUpdate,
    UserUpdateMe,
)
from app.utils import generate_new_account_email, send_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


def _guard_email_suffix(session: SessionDep, email: str) -> None:
    """建号 / 改邮箱统一走准入规则。

    必须与自助注册共用同一套规则：否则「管理员建号」就是绕过域名白名单的后门，
    规则配了等于没配。
    """
    decision = access_rules.match_email_suffix(
        email, access_rules.load_rules(session, access_rules.EMAIL_SUFFIX_KIND)
    )
    if not decision.allowed:
        raise HTTPException(status_code=400, detail=decision.reason)


def _restart_email_verification(session: SessionDep, *, user: User) -> None:
    """改邮箱后重新走一遍验证：清验证状态（账号随之下线）并把验证码发到新邮箱。

    为什么改邮箱必须重新验证：否则可以「先用合规域名注册并通过验证，再把邮箱改成任意地址」，
    域名白名单一步就被绕过。发不出信时不静默继续——那会把账号锁在「未验证且收不到码」的状态。
    """
    crud.reset_email_verification(session=session, user=user)
    send_verification_code_or_error(session, user=user)
    user.verification_sent_at = datetime.now(UTC)
    session.add(user)
    session.commit()


@router.get("/", dependencies=[Depends(RequireAdmin)], response_model=UsersPublic)
def read_users(
    session: SessionDep,
    skip: int = 0,
    limit: int = 100,
    email_verified: bool | None = None,
) -> Any:
    """分页列出用户（按创建时间倒序），响应逐个带角色名。

    Args:
        session: 数据库会话，用于查询与响应组装。
        skip: 分页起始偏移，默认 0。
        limit: 单页条数上限，默认 100。
        email_verified: 只看已验证 / 未验证邮箱的账号，缺省不过滤。用户列表的
            「待验证」页签靠它；**过滤条件与计数必须同一组**，否则总页数会算错。

    Returns:
        UsersPublic：用户列表（含 roles）与总数。

    Raises:
        HTTPException: 403 当前用户无 admin 角色且非超管。
    """
    count_statement = select(func.count()).select_from(User)
    statement = select(User).order_by(col(User.created_at).desc())
    if email_verified is True:
        count_statement = count_statement.where(
            col(User.email_verified_at).is_not(None)
        )
        statement = statement.where(col(User.email_verified_at).is_not(None))
    elif email_verified is False:
        count_statement = count_statement.where(col(User.email_verified_at).is_(None))
        statement = statement.where(col(User.email_verified_at).is_(None))

    count = session.exec(count_statement).one()
    users = session.exec(statement.offset(skip).limit(limit)).all()

    return crud.build_users_public(session=session, users=list(users), count=count)


@router.post("/", dependencies=[Depends(RequireAdmin)], response_model=UserPublic)
def create_user(
    *,
    session: SessionDep,
    user_in: UserCreate,
    current_user: CurrentUser,
    client_ip: ClientIp,
) -> Any:
    """创建新用户并同步角色（缺省授予 readonly），写审计。

    管理员建号视为可信来源：邮箱后缀规则照样校验（与自助注册同一套），但邮箱验证直接置位——
    管理员在现实中已经确认过这个人，不必再走一遍邮件验证。

    Args:
        session: 数据库会话，用于建用户、挂角色与写审计。
        user_in: 请求体；roles 未传时缺省 ["readonly"]。
        current_user: 当前登录用户（操作者），用于审计归属。
        client_ip: 来源 IP，写入审计。

    Returns:
        UserPublic：新用户（含 roles）。

    Raises:
        HTTPException: 403 无 admin 角色；400 邮箱命中准入规则或已存在。
    """
    _guard_email_suffix(session, user_in.email)

    user = crud.get_user_by_email(session=session, email=user_in.email)
    if user:
        raise HTTPException(
            status_code=400,
            detail="The user with this email already exists in the system.",
        )

    # 创建缺省授予 readonly，保证新用户至少可读
    roles = user_in.roles if user_in.roles is not None else ["readonly"]
    user = crud.create_user(
        session=session,
        user_create=user_in.model_copy(update={"roles": roles}),
    )
    crud.record_audit_log(
        session=session,
        user_id=current_user.id,
        user_email=user.email,
        action="user.create",
        detail=f"roles={','.join(roles)}",
        ip=client_ip,
    )
    if settings.emails_enabled and user_in.email:
        email_data = generate_new_account_email(
            email_to=user_in.email, username=user_in.email, password=user_in.password
        )
        send_email(
            email_to=user_in.email,
            subject=email_data.subject,
            html_content=email_data.html_content,
        )
    return crud.build_user_public(session=session, user=user)


@router.patch("/me", response_model=UserPublic)
def update_user_me(
    *,
    session: SessionDep,
    user_in: UserUpdateMe,
    current_user: CurrentUser,
    client_ip: ClientIp,
) -> Any:
    """更新当前登录用户自身资料（姓名/邮箱），不改角色。

    改邮箱会清掉验证状态并重新发码（见 `_restart_email_verification`）：调用方拿到成功响应后
    账号即处于未验证状态，需要去新邮箱取码，前端要把这一点提示清楚。

    Args:
        session: 数据库会话。
        user_in: 请求体，仅 full_name/email。
        current_user: 当前登录用户。
        client_ip: 来源 IP，写入审计。

    Returns:
        UserPublic：更新后的自身信息（含 roles）。

    Raises:
        HTTPException: 409 新邮箱已被他人占用；400 新邮箱命中准入规则。
    """
    email_changed = bool(user_in.email) and user_in.email != current_user.email
    if user_in.email:
        existing_user = crud.get_user_by_email(session=session, email=user_in.email)
        if existing_user and existing_user.id != current_user.id:
            raise HTTPException(
                status_code=409, detail="User with this email already exists"
            )
        if email_changed:
            _guard_email_suffix(session, user_in.email)

    user_data = user_in.model_dump(exclude_unset=True)
    current_user.sqlmodel_update(user_data)
    session.add(current_user)
    session.commit()
    session.refresh(current_user)

    if email_changed:
        _restart_email_verification(session, user=current_user)
        crud.record_audit_log(
            session=session,
            user_id=current_user.id,
            user_email=current_user.email,
            action="user.update_me",
            detail="email changed, verification restarted",
            ip=client_ip,
        )
    return crud.build_user_public(session=session, user=current_user)


@router.patch("/me/password", response_model=Message)
def update_password_me(
    *, session: SessionDep, body: UpdatePassword, current_user: CurrentUser
) -> Any:
    """
    Update own password.
    """
    verified, _ = verify_password(body.current_password, current_user.hashed_password)
    if not verified:
        raise HTTPException(status_code=400, detail="Incorrect password")
    if body.current_password == body.new_password:
        raise HTTPException(
            status_code=400, detail="New password cannot be the same as the current one"
        )
    hashed_password = get_password_hash(body.new_password)
    current_user.hashed_password = hashed_password
    session.add(current_user)
    session.commit()
    return Message(message="Password updated successfully")


@router.get("/me", response_model=UserPublic)
def read_user_me(session: SessionDep, current_user: CurrentUser) -> Any:
    """返回当前登录用户信息（含 roles，前端据此显隐操作按钮）。

    Args:
        session: 数据库会话，用于查角色关联。
        current_user: 当前登录用户。

    Returns:
        UserPublic：自身信息（含 roles）。
    """
    return crud.build_user_public(session=session, user=current_user)


@router.delete("/me", response_model=Message)
def delete_user_me(session: SessionDep, current_user: CurrentUser) -> Any:
    """
    Delete own user.
    """
    if current_user.is_superuser:
        raise HTTPException(
            status_code=403, detail="Super users are not allowed to delete themselves"
        )
    session.delete(current_user)
    session.commit()
    return Message(message="User deleted successfully")


@router.get("/{user_id}", response_model=UserPublic)
def read_user_by_id(
    user_id: uuid.UUID, session: SessionDep, current_user: CurrentUser
) -> Any:
    """按 id 查看用户：本人可看，他人需 admin 角色语义（admin 角色或超管）。

    Args:
        user_id: 目标用户 id。
        session: 数据库会话，用于查用户与角色。
        current_user: 当前登录用户。

    Returns:
        UserPublic：目标用户（含 roles）。

    Raises:
        HTTPException: 403 非本人且无 admin 角色；404 用户不存在。
    """
    user = session.get(User, user_id)
    if user == current_user:
        return crud.build_user_public(session=session, user=user)
    # 非本人访问按 admin 语义鉴权（is_superuser 兼容首个超管）
    if not (
        current_user.is_superuser
        or crud.user_has_role(session=session, user=current_user, role_name="admin")
    ):
        raise HTTPException(
            status_code=403,
            detail="Not enough permissions",
        )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return crud.build_user_public(session=session, user=user)


@router.patch(
    "/{user_id}", dependencies=[Depends(RequireAdmin)], response_model=UserPublic
)
def update_user(
    *,
    session: SessionDep,
    user_id: uuid.UUID,
    user_in: UserUpdate,
    current_user: CurrentUser,
    client_ip: ClientIp,
) -> Any:
    """更新任意用户（资料/密码/角色整体替换），写审计。

    Args:
        session: 数据库会话，用于更新、同步角色与写审计。
        user_id: 目标用户 id。
        user_in: 请求体；roles 未传保持不变，显式列表整体替换。
        current_user: 当前登录用户（操作者），用于审计归属。
        client_ip: 来源 IP，写入审计。

    Returns:
        UserPublic：更新后的用户（含 roles）。

    Raises:
        HTTPException: 403 无 admin 角色；404 用户不存在；409 邮箱冲突；400 邮箱命中准入规则。
    """
    db_user = session.get(User, user_id)
    if not db_user:
        raise HTTPException(
            status_code=404,
            detail="The user with this id does not exist in the system",
        )
    email_changed = bool(user_in.email) and user_in.email != db_user.email
    if user_in.email:
        existing_user = crud.get_user_by_email(session=session, email=user_in.email)
        if existing_user and existing_user.id != user_id:
            raise HTTPException(
                status_code=409, detail="User with this email already exists"
            )
        if email_changed:
            _guard_email_suffix(session, user_in.email)

    db_user = crud.update_user(session=session, db_user=db_user, user_in=user_in)
    if email_changed:
        _restart_email_verification(session, user=db_user)
    # 审计只记被改字段名（含 password 字段名），绝不记字段值
    changed_fields = ",".join(sorted(user_in.model_dump(exclude_unset=True)))
    crud.record_audit_log(
        session=session,
        user_id=current_user.id,
        user_email=db_user.email,
        action="user.update",
        detail=f"fields={changed_fields}",
        ip=client_ip,
    )
    return crud.build_user_public(session=session, user=db_user)


@router.post("/{user_id}/verify-email", response_model=UserPublic)
def verify_user_email_manually(
    *,
    session: SessionDep,
    current_user: AdminUser,
    client_ip: ClientIp,
    user_id: uuid.UUID,
) -> Any:
    """管理员手动把某账号标记为「邮箱已验证」。

    存在的理由：邮件中继故障或用户就是收不到信时，需要一个**人工放行出口**，否则账号会永久卡在
    未验证状态（自助注册出来的账号尤其如此）。动作入审计，便于回溯「谁放行的」。
    """
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.email_verified_at is not None:
        # 幂等：已验证过就直接返回，不重复写审计
        return crud.build_user_public(session=session, user=user)

    user = crud.mark_email_verified(session=session, user=user)
    crud.record_audit_log(
        session=session,
        user_id=current_user.id,
        user_email=user.email,
        action="user.verify_email_manual",
        detail="email verified manually by admin",
        ip=client_ip,
    )
    return crud.build_user_public(session=session, user=user)


@router.delete("/{user_id}", dependencies=[Depends(RequireAdmin)])
def delete_user(
    session: SessionDep,
    current_user: CurrentUser,
    client_ip: ClientIp,
    user_id: uuid.UUID,
) -> Message:
    """删除任意用户（禁止删除自己），写审计（user_email=被删者邮箱）。

    Args:
        session: 数据库会话，用于删用户与写审计。
        current_user: 当前登录用户（操作者）。
        client_ip: 来源 IP，写入审计。
        user_id: 目标用户 id。

    Returns:
        Message：删除成功文案。

    Raises:
        HTTPException: 403 目标为操作者本人或无 admin 角色；404 用户不存在。
    """
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user == current_user:
        raise HTTPException(
            status_code=403, detail="Super users are not allowed to delete themselves"
        )
    # 审计要在删除前取出目标邮箱；user_id 仍记操作者
    deleted_email = user.email
    session.delete(user)
    session.commit()
    crud.record_audit_log(
        session=session,
        user_id=current_user.id,
        user_email=deleted_email,
        action="user.delete",
        detail=f"actor={current_user.email}",
        ip=client_ip,
    )
    return Message(message="User deleted successfully")
