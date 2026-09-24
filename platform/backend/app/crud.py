import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, col, delete, select

from app.core.security import get_password_hash, verify_password
from app.models import (
    AuditLog,
    Role,
    ServiceConfig,
    User,
    UserCreate,
    UserPublic,
    UserRole,
    UsersPublic,
    UserUpdate,
    get_datetime_utc,
)


def create_user(
    *,
    session: Session,
    user_create: UserCreate,
    email_verified: bool = True,
) -> User:
    """建号。

    Args:
        session: 数据库会话。
        user_create: 建号载荷；`is_active` 由调用方决定（自助注册传 False）。
        email_verified: 是否直接标记邮箱已验证。默认 True —— 这个原语的调用方
            （引导首个超管、管理员建号、测试）都是可信来源；**自助注册必须显式传 False**，
            否则未验证的账号能直接登录。默认取 True 是为了不改动既有调用方的行为，
            把安全敏感的例外留在唯一那个调用点上明说。
    """
    db_obj = User.model_validate(
        user_create,
        update={
            "hashed_password": get_password_hash(user_create.password),
            "email_verified_at": datetime.now(UTC) if email_verified else None,
        },
    )
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    if user_create.roles:
        # 指定了角色才挂关联；None 保持"无角色"（首超管的 admin 角色由 initial_data 单独补）
        set_user_roles(session=session, user=db_obj, role_names=user_create.roles)
    return db_obj


def update_user(*, session: Session, db_user: User, user_in: UserUpdate) -> Any:
    user_data = user_in.model_dump(exclude_unset=True)
    # roles 不是 User 列，先取出单独同步；None=未传即保持不变
    role_names: list[str] | None = user_data.pop("roles", None)
    extra_data = {}
    if "password" in user_data:
        password = user_data["password"]
        hashed_password = get_password_hash(password)
        extra_data["hashed_password"] = hashed_password
    db_user.sqlmodel_update(user_data, update=extra_data)
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
    if role_names is not None:
        # 显式传入才整体替换（空列表=清空全部角色）
        set_user_roles(session=session, user=db_user, role_names=role_names)
    return db_user


def get_user_by_email(*, session: Session, email: str) -> User | None:
    statement = select(User).where(User.email == email)
    session_user = session.exec(statement).first()
    return session_user


# Dummy hash to use for timing attack prevention when user is not found
# This is an Argon2 hash of a random password, used to ensure constant-time comparison
DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$MjQyZWE1MzBjYjJlZTI0Yw$YTU4NGM5ZTZmYjE2NzZlZjY0ZWY3ZGRkY2U2OWFjNjk"


def authenticate(*, session: Session, email: str, password: str) -> User | None:
    db_user = get_user_by_email(session=session, email=email)
    if not db_user:
        # Prevent timing attacks by running password verification even when user doesn't exist
        # This ensures the response time is similar whether or not the email exists
        verify_password(password, DUMMY_HASH)
        return None
    verified, updated_password_hash = verify_password(password, db_user.hashed_password)
    if not verified:
        return None
    if updated_password_hash:
        db_user.hashed_password = updated_password_hash
        session.add(db_user)
        session.commit()
        session.refresh(db_user)
    return db_user


def get_service_config(*, session: Session, service_name: str) -> ServiceConfig | None:
    """按服务名取当前配置行，服务从未保存过配置时返回 None。"""
    statement = select(ServiceConfig).where(ServiceConfig.service_name == service_name)
    return session.exec(statement).first()


def upsert_service_config(
    *,
    session: Session,
    service_name: str,
    values: dict[str, Any],
    rendered_at: datetime,
    applied: bool,
) -> ServiceConfig:
    """整行覆盖式保存服务配置，存在则更新、不存在则新建，返回保存后的行。

    每服务仅保留当前配置（历史版本属阶段 4），applied 先以 False 落库、
    reload 成功后由调用方再次调用本函数置 True。

    Args:
        session: 数据库会话，由路由层注入。
        service_name: 服务名，与 services/ 目录名一致。
        values: 已经 config_renderer.validate_values 归一化的配置值。
        rendered_at: 本次渲染完成时间（UTC 带时区）。
        applied: 渲染产物是否已在容器内生效。

    Returns:
        保存后的 ServiceConfig 行（已刷新数据库生成字段）。
    """
    db_config = get_service_config(session=session, service_name=service_name)
    if db_config is None:
        db_config = ServiceConfig(
            service_name=service_name,
            values=values,
            rendered_at=rendered_at,
            applied=applied,
        )
    else:
        db_config.sqlmodel_update(
            {
                "values": values,
                "rendered_at": rendered_at,
                "applied": applied,
                "updated_at": get_datetime_utc(),
            }
        )
    session.add(db_config)
    session.commit()
    session.refresh(db_config)
    return db_config


# 系统固定三角色；顺序即 GET /roles 之外的种子创建顺序
ROLE_NAMES: tuple[str, str, str] = ("admin", "operator", "readonly")

ROLE_DESCRIPTIONS: dict[str, str] = {
    "admin": "管理员：全部权限（用户管理、审计查询、服务配置与生命周期）",
    "operator": "操作员：读 + 配置修改 + 生命周期操作",
    "readonly": "只读：仅查看服务与自身信息",
}

# detail 为"简短说明"，超长截断防止 JSON 说明撑爆 VARCHAR(1024)
MAX_AUDIT_DETAIL_LENGTH = 1024


def get_role_names_by_user_ids(
    *, session: Session, user_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """批量取多个用户的角色名映射，供用户列表一次查询组装响应。

    Args:
        session: 数据库会话，由路由层注入。
        user_ids: 用户 id 集合；空集合直接返回空映射避免无效查询。

    Returns:
        {user_id: [角色名, ...]}；无角色的用户不出现在映射中。
    """
    mapping: dict[uuid.UUID, list[str]] = {}
    if not user_ids:
        return mapping
    statement = (
        select(UserRole.user_id, Role.name)
        .join(Role, col(Role.id) == col(UserRole.role_id))
        .where(col(UserRole.user_id).in_(user_ids))
    )
    for user_id, role_name in session.exec(statement).all():
        mapping.setdefault(user_id, []).append(role_name)
    return mapping


def get_user_role_names(*, session: Session, user_id: uuid.UUID) -> list[str]:
    """取单个用户的角色名列表（每次请求只此一次查询，供鉴权与响应组装复用）。

    Args:
        session: 数据库会话，由路由层注入。
        user_id: 目标用户 id。

    Returns:
        角色名列表；用户无任何角色时为空列表。
    """
    return get_role_names_by_user_ids(session=session, user_ids=[user_id]).get(
        user_id, []
    )


def user_has_role(*, session: Session, user: User, role_name: str) -> bool:
    """判断用户是否拥有指定角色名。

    Args:
        session: 数据库会话，由路由层注入。
        user: 目标用户（通常是当前登录用户）。
        role_name: 角色名，须为固定三角色之一。

    Returns:
        拥有该角色为 True，否则 False。
    """
    return role_name in get_user_role_names(session=session, user_id=user.id)


def get_roles(*, session: Session) -> list[Role]:
    """返回全部角色行，按 name 升序保证响应顺序稳定。

    Args:
        session: 数据库会话，由路由层注入。

    Returns:
        角色行列表；角色种子未执行时为空列表。
    """
    statement = select(Role).order_by(col(Role.name).asc())
    return list(session.exec(statement).all())


def ensure_roles(*, session: Session) -> None:
    """幂等创建三个固定角色，已存在的名字跳过（可重复执行）。

    Args:
        session: 数据库会话，由 initial_data 或测试夹具注入。
    """
    existing = set(session.exec(select(Role.name)).all())
    for name in ROLE_NAMES:
        if name not in existing:
            session.add(Role(name=name, description=ROLE_DESCRIPTIONS.get(name)))
    session.commit()


def ensure_user_role(*, session: Session, user: User, role_name: str) -> None:
    """给用户补挂一个角色；已拥有则跳过（幂等）。

    Args:
        session: 数据库会话，由 initial_data 或测试夹具注入。
        user: 目标用户（须已落库，id 非空）。
        role_name: 角色名，须已由 ensure_roles 创建。

    Raises:
        ValueError: 角色名不存在于 role 表时（种子顺序错误）。
    """
    role = session.exec(select(Role).where(Role.name == role_name)).first()
    if role is None:
        raise ValueError(f"Unknown role: {role_name}")
    link = session.exec(
        select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id)
    ).first()
    if link is not None:
        return
    session.add(UserRole(user_id=user.id, role_id=role.id))
    session.commit()


def set_user_roles(*, session: Session, user: User, role_names: Sequence[str]) -> None:
    """整体替换用户的角色为给定名字集合（先清旧关联再建新关联）。

    Args:
        session: 数据库会话，由路由层注入。
        user: 目标用户（须已落库，id 非空）。
        role_names: 角色名列表；空列表表示清空全部角色，重复名字自动去重。

    Raises:
        ValueError: 任一角色名不存在于 role 表时（请求体 Literal 校验的兜底）。
    """
    if not role_names:
        statement = delete(UserRole).where(col(UserRole.user_id) == user.id)
        session.exec(statement)
        session.commit()
        return
    roles = session.exec(select(Role).where(col(Role.name).in_(role_names))).all()
    found = {role.name for role in roles}
    missing = set(role_names) - found
    if missing:
        raise ValueError(f"Unknown roles: {', '.join(sorted(missing))}")
    statement = delete(UserRole).where(col(UserRole.user_id) == user.id)
    session.exec(statement)
    for role in roles:
        session.add(UserRole(user_id=user.id, role_id=role.id))
    session.commit()


def build_user_public(*, session: Session, user: User) -> UserPublic:
    """组装带角色名的 UserPublic（User 表无角色列，需单独查关联表回填）。

    Args:
        session: 数据库会话，由路由层注入。
        user: 目标用户行。

    Returns:
        UserPublic，roles 为该用户角色名列表（无角色为空列表）。
    """
    roles = get_user_role_names(session=session, user_id=user.id)
    return UserPublic.model_validate(user, update={"roles": roles})


def build_users_public(
    *, session: Session, users: list[User], count: int
) -> UsersPublic:
    """批量组装带角色名的用户列表响应（一次关联查询，避免 N+1）。

    Args:
        session: 数据库会话，由路由层注入。
        users: 当前页的用户行列表。
        count: 过滤后的用户总数（非本页条数）。

    Returns:
        UsersPublic，每个用户的 roles 已回填。
    """
    mapping = get_role_names_by_user_ids(
        session=session, user_ids=[user.id for user in users]
    )
    data = [
        UserPublic.model_validate(user, update={"roles": mapping.get(user.id, [])})
        for user in users
    ]
    return UsersPublic(data=data, count=count)


def mark_email_verified(
    *, session: Session, user: User, when: datetime | None = None
) -> User:
    """标记邮箱已验证，并放开登录（is_active=True）。

    两件事一起做是有意的：`email_verified_at` 是「为什么能/不能登录」的语义字段，
    `is_active` 是执行层的拦截开关。未验证账号建号时 is_active=False，验证成功必须同时放开，
    否则用户点完链接仍然登不进去。
    """
    user.email_verified_at = when or datetime.now(UTC)
    user.is_active = True
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def reset_email_verification(*, session: Session, user: User) -> User:
    """清空邮箱验证状态并停用（改邮箱后必须重新验证）。

    为什么改邮箱要清验证状态：否则可以「先用合规域名注册并通过验证，再把邮箱改成任意地址」，
    域名白名单会被一步绕过。
    """
    user.email_verified_at = None
    user.is_active = False
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def record_audit_log(
    *,
    session: Session,
    user_id: uuid.UUID | None,
    user_email: str | None,
    action: str,
    service_name: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
) -> AuditLog:
    """写一条审计日志并提交。

    user_id 恒记操作者（用户被删后由 ON DELETE SET NULL 置空）；user_email 为
    冗余追溯字段：用户操作记目标邮箱，服务操作记操作者邮箱。detail 只记
    字段名/状态，调用方不得传密码等敏感值。

    Args:
        session: 数据库会话，由路由层注入。
        user_id: 操作者用户 id；系统行为无操作者时为 None。
        user_email: 冗余追溯邮箱，语义见摘要。
        action: 动作标识，如 config.update / service.start / user.create。
        service_name: 目标服务名；非服务操作为 None。
        detail: 简短说明，超长自动截断到 1024 字符。
        ip: 来源 IP。准入拒绝、登录被拒这类事件靠它回答「谁在试」；缺省 None
            表示该事件与来源无关（如系统定时任务）。

    Returns:
        已落库并刷新的 AuditLog 行。
    """
    if detail is not None and len(detail) > MAX_AUDIT_DETAIL_LENGTH:
        detail = detail[:MAX_AUDIT_DETAIL_LENGTH]
    entry = AuditLog(
        user_id=user_id,
        user_email=user_email,
        action=action,
        service_name=service_name,
        detail=detail,
        ip=ip,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def get_fault_modes(*, session: Session) -> dict[str, str]:
    """一次查询取出所有服务当前生效的 fault_mode（供服务概要列表复用）。

    Returns:
        {服务名: fault_mode}；未保存过配置或配置里没有该字段的服务不出现在结果里。
    """
    modes: dict[str, str] = {}
    for config in session.exec(select(ServiceConfig)).all():
        values = config.values or {}
        mode = values.get("fault_mode")
        if isinstance(mode, str) and mode:
            modes[config.service_name] = mode
    return modes
