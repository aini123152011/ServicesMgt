import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import EmailStr
from sqlalchemy import JSON, Column, DateTime
from sqlmodel import Field, Relationship, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(UTC)


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on update, all are optional
class UserUpdate(SQLModel):
    email: EmailStr | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    is_superuser: bool | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    items: list[Item] = Relationship(back_populates="owner", cascade_delete=True)


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Shared properties
class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Properties to receive on item creation
class ItemCreate(ItemBase):
    pass


# Properties to receive on item update
class ItemUpdate(SQLModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Database model, database table inferred from class name
class Item(ItemBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    owner: User | None = Relationship(back_populates="items")


# Properties to return via API, id is always required
class ItemPublic(ItemBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime | None = None


class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int


# 服务概要（列表页展示），字段与 manifest.yaml 对应
class ServicePort(SQLModel):
    port: int
    protocol: str
    description: str | None = None


class ServiceSummary(SQLModel):
    name: str
    display_name: str
    category: str
    description: str | None = None
    container_name: str
    ports: list[ServicePort] = Field(default_factory=list)
    reload_mode: str


# 服务详情中的 manifest：在概要之上补充配置目录与配置文件清单
class ServiceManifest(ServiceSummary):
    config_dir: str
    config_files: list[str]


# 服务当前配置状态：从未保存过配置时三个字段均为 None
class ServiceConfigState(SQLModel):
    values: dict[str, Any] | None = None
    applied: bool | None = None
    rendered_at: datetime | None = None


# PUT /services/{name}/config 的请求体
class ServiceConfigUpdate(SQLModel):
    values: dict[str, Any]


# 配置提交结果：applied=False 表示已写卷但容器未运行、reload 被跳过
class ServiceConfigApplyResult(SQLModel):
    message: str
    applied: bool


# 服务运行状态查询结果
class ServiceStatus(SQLModel):
    name: str
    running: bool
    health: str | None = None
    status: str | None = None


# 服务日志查询结果
class ServiceLogs(SQLModel):
    logs: str


class ServicesPublic(SQLModel):
    data: list[ServiceSummary]
    count: int


# 每服务当前生效配置：service_name 唯一定位一行，整行覆盖式更新（不存历史，历史版本属阶段 4）
class ServiceConfigBase(SQLModel):
    service_name: str = Field(unique=True, index=True, max_length=64)
    # 配置值按 schema 归一化后原样存 JSON，键与服务插件 schema 字段名对应
    values: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    applied: bool | None = None


# Database model, database table inferred from class name
class ServiceConfig(ServiceConfigBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    # 最近一次渲染时间（非配置提交时间）；applied 记录渲染产物是否已在容器内生效
    rendered_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)
