import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import col, func, select

from app import crud
from app.api.deps import (
    CurrentUser,
    RequireAdmin,
    SessionDep,
)
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

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/", dependencies=[Depends(RequireAdmin)], response_model=UsersPublic)
def read_users(session: SessionDep, skip: int = 0, limit: int = 100) -> Any:
    """分页列出用户（按创建时间倒序），响应逐个带角色名。

    Args:
        session: 数据库会话，用于查询与响应组装。
        skip: 分页起始偏移，默认 0。
        limit: 单页条数上限，默认 100。

    Returns:
        UsersPublic：用户列表（含 roles）与总数。

    Raises:
        HTTPException: 403 当前用户无 admin 角色且非超管。
    """
    count_statement = select(func.count()).select_from(User)
    count = session.exec(count_statement).one()

    statement = (
        select(User).order_by(col(User.created_at).desc()).offset(skip).limit(limit)
    )
    users = session.exec(statement).all()

    return crud.build_users_public(session=session, users=list(users), count=count)


@router.post("/", dependencies=[Depends(RequireAdmin)], response_model=UserPublic)
def create_user(
    *, session: SessionDep, user_in: UserCreate, current_user: CurrentUser
) -> Any:
    """创建新用户并同步角色（缺省授予 readonly），写审计。

    Args:
        session: 数据库会话，用于建用户、挂角色与写审计。
        user_in: 请求体；roles 未传时缺省 ["readonly"]。
        current_user: 当前登录用户（操作者），用于审计归属。

    Returns:
        UserPublic：新用户（含 roles）。

    Raises:
        HTTPException: 403 无 admin 角色；400 邮箱已存在。
    """
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
    *, session: SessionDep, user_in: UserUpdateMe, current_user: CurrentUser
) -> Any:
    """更新当前登录用户自身资料（姓名/邮箱），不改角色。

    Args:
        session: 数据库会话。
        user_in: 请求体，仅 full_name/email。
        current_user: 当前登录用户。

    Returns:
        UserPublic：更新后的自身信息（含 roles）。

    Raises:
        HTTPException: 409 新邮箱已被他人占用。
    """
    if user_in.email:
        existing_user = crud.get_user_by_email(session=session, email=user_in.email)
        if existing_user and existing_user.id != current_user.id:
            raise HTTPException(
                status_code=409, detail="User with this email already exists"
            )
    user_data = user_in.model_dump(exclude_unset=True)
    current_user.sqlmodel_update(user_data)
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
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
) -> Any:
    """更新任意用户（资料/密码/角色整体替换），写审计。

    Args:
        session: 数据库会话，用于更新、同步角色与写审计。
        user_id: 目标用户 id。
        user_in: 请求体；roles 未传保持不变，显式列表整体替换。
        current_user: 当前登录用户（操作者），用于审计归属。

    Returns:
        UserPublic：更新后的用户（含 roles）。

    Raises:
        HTTPException: 403 无 admin 角色；404 用户不存在；409 邮箱冲突。
    """
    db_user = session.get(User, user_id)
    if not db_user:
        raise HTTPException(
            status_code=404,
            detail="The user with this id does not exist in the system",
        )
    if user_in.email:
        existing_user = crud.get_user_by_email(session=session, email=user_in.email)
        if existing_user and existing_user.id != user_id:
            raise HTTPException(
                status_code=409, detail="User with this email already exists"
            )

    db_user = crud.update_user(session=session, db_user=db_user, user_in=user_in)
    # 审计只记被改字段名（含 password 字段名），绝不记字段值
    changed_fields = ",".join(sorted(user_in.model_dump(exclude_unset=True)))
    crud.record_audit_log(
        session=session,
        user_id=current_user.id,
        user_email=db_user.email,
        action="user.update",
        detail=f"fields={changed_fields}",
    )
    return crud.build_user_public(session=session, user=db_user)


@router.delete("/{user_id}", dependencies=[Depends(RequireAdmin)])
def delete_user(
    session: SessionDep, current_user: CurrentUser, user_id: uuid.UUID
) -> Message:
    """删除任意用户（禁止删除自己），写审计（user_email=被删者邮箱）。

    Args:
        session: 数据库会话，用于删用户与写审计。
        current_user: 当前登录用户（操作者）。
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
    )
    return Message(message="User deleted successfully")
