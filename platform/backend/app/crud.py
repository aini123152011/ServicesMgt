import uuid
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from app.core.security import get_password_hash, verify_password
from app.models import (
    Item,
    ItemCreate,
    ServiceConfig,
    User,
    UserCreate,
    UserUpdate,
    get_datetime_utc,
)


def create_user(*, session: Session, user_create: UserCreate) -> User:
    db_obj = User.model_validate(
        user_create, update={"hashed_password": get_password_hash(user_create.password)}
    )
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def update_user(*, session: Session, db_user: User, user_in: UserUpdate) -> Any:
    user_data = user_in.model_dump(exclude_unset=True)
    extra_data = {}
    if "password" in user_data:
        password = user_data["password"]
        hashed_password = get_password_hash(password)
        extra_data["hashed_password"] = hashed_password
    db_user.sqlmodel_update(user_data, update=extra_data)
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
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


def create_item(*, session: Session, item_in: ItemCreate, owner_id: uuid.UUID) -> Item:
    db_item = Item.model_validate(item_in, update={"owner_id": owner_id})
    session.add(db_item)
    session.commit()
    session.refresh(db_item)
    return db_item


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
