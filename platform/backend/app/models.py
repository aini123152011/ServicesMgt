import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import EmailStr
from sqlalchemy import JSON, Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(UTC)


# 系统固定三角色名（role.name 唯一）；请求体角色校验与种子数据都以此为界
RoleName = Literal["admin", "operator", "readonly"]


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)
    # 创建时的缺省授予（readonly）由路由层决定；None 表示不指定角色
    roles: list[RoleName] | None = None


# Properties to receive via API on update, all are optional
class UserUpdate(SQLModel):
    email: EmailStr | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    is_superuser: bool | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    # None=保持既有角色不变；显式列表=整体替换（空列表=清空全部角色）
    roles: list[RoleName] | None = None


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


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None
    # 前端按角色显隐操作按钮依赖此字段；无角色用户为空列表
    roles: list[str] = Field(default_factory=list)


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# 角色共享属性：name 固定三值之一，唯一索引防重复种子
class RoleBase(SQLModel):
    name: str = Field(unique=True, index=True, max_length=32)
    description: str | None = Field(default=None, max_length=255)


class RoleCreate(RoleBase):
    pass


# Database model, database table inferred from class name
class Role(RoleBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


# Properties to return via API, id is always required
class RolePublic(RoleBase):
    id: uuid.UUID
    created_at: datetime | None = None


# 用户-角色多对多关联：复合主键 (user_id, role_id)，任一侧删除即级联清理
class UserRole(SQLModel, table=True):
    user_id: uuid.UUID = Field(
        foreign_key="user.id", primary_key=True, ondelete="CASCADE"
    )
    role_id: uuid.UUID = Field(
        foreign_key="role.id", primary_key=True, ondelete="CASCADE"
    )


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
    # 当前生效的故障注入模式（来自已保存配置；未保存过配置时为 None）。
    # 首页「当前故障注入」面板据此列出处于非 none 模式的服务，无需逐个拉详情
    fault_mode: str | None = None


# manifest.usage 的单条外部用法提示；三字段形状由 registry._check_manifest 校验
class ServiceUsageEntry(SQLModel):
    target: str
    summary: str
    command: str


# 服务详情中的 manifest：在概要之上补充配置目录与配置文件清单
class ServiceManifest(ServiceSummary):
    config_dir: str
    config_files: list[str]
    # 容器内数据目录；None=服务未声明数据卷（前端据此隐藏数据浏览入口）
    data_dir: str | None = None
    # 外部设备/客户端怎么接入本服务；None=未声明（前端不渲染该卡片）
    usage: list[ServiceUsageEntry] | None = None


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


# 服务数据卷内单个条目：modified 为最后修改时间（序列化为 ISO8601，取不到时为 null）
class ServiceDataEntry(SQLModel):
    name: str
    type: Literal["file", "dir"]
    size: int
    modified: datetime | None = None


# GET /services/{name}/data/tree 响应：path 为归一化后的相对子路径（卷根为空串）
class ServiceDataTree(SQLModel):
    path: str
    entries: list[ServiceDataEntry]


# GET /services/{name}/data/content 响应：size 为文件总字节数，truncated 表示头部超出读取窗口被丢弃
class ServiceDataContent(SQLModel):
    path: str
    size: int
    truncated: bool
    lines: list[str]


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


# 审计日志共享属性：action 为动作标识（config.update / service.start / user.create 等）
class AuditLogBase(SQLModel):
    action: str = Field(max_length=64)
    # 操作对象为服务时填服务名；用户操作留空
    service_name: str | None = Field(default=None, max_length=64)
    # 简短说明（仅字段名/状态，不含密码等敏感值），写入侧超长截断
    detail: str | None = Field(default=None, max_length=1024)
    # 冗余邮箱便于删除后追溯：用户操作存目标邮箱，服务操作存操作者邮箱
    user_email: str | None = Field(default=None, max_length=255)


# Database model, database table inferred from class name
class AuditLog(AuditLogBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    # 操作者；用户被删除后由 ON DELETE SET NULL 置空，靠 user_email 追溯
    user_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", nullable=True, ondelete="SET NULL"
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


# Properties to return via API, id is always required（不外露 user_id）
class AuditLogPublic(SQLModel):
    id: uuid.UUID
    user_email: str | None = None
    action: str
    service_name: str | None = None
    detail: str | None = None
    created_at: datetime | None = None


class AuditLogsPublic(SQLModel):
    data: list[AuditLogPublic]
    count: int


# 出现过的动作名（GET /audit-logs/actions），供前端筛选下拉使用
class AuditActionsPublic(SQLModel):
    data: list[str]


# 角色名列表（GET /roles），供前端角色选择器使用
class RolesPublic(SQLModel):
    data: list[str]


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


# 更新目标（平台自身或某个服务）的镜像现状；update_available 由「容器所用镜像 ID
# 与同名 tag 当前 ID 是否一致」判定，离线包 load 会覆盖同名 tag，故离线场景同样成立
class UpdateTarget(SQLModel):
    target: str
    display_name: str
    container_name: str
    image: str
    running_image_id: str | None = None
    available_image_id: str | None = None
    image_created: str | None = None
    container_running: bool = False
    update_available: bool = False


# 最近一次更新任务的状态（状态文件持久化，平台自更新重启后仍可读）
class UpdateTaskState(SQLModel):
    status: str
    phase: str | None = None
    target: str | None = None
    image: str | None = None
    message: str | None = None
    updated_at: str | None = None
    finished_at: str | None = None


# GET /system/info 响应：平台版本/构建 + 最近任务状态 + 各更新目标镜像现状
class SystemInfo(SQLModel):
    version: str
    build: str
    update_registry: str
    # Docker 不可达时为 False（targets 为空）：版本信息仍要能看到，不整页 502
    docker_available: bool = True
    status: UpdateTaskState | None = None
    targets: list[UpdateTarget]


# --------------------------------------------------------------------------- #
# 宿主网口与二层绑定（GET /system/host-network）
#
# 数据全部来自运行态：网口物理事实读宿主 /sys 的只读挂载，IP/掩码与默认路由出口由
# `--network host` 的一次性 helper 容器读出，绑定从 docker network inspect 的 Options.parent 推导。
# 校验结论（checks）只做展示，不阻断任何下发——见 app/host_network.py 的模块说明。
# --------------------------------------------------------------------------- #
class HostIPv4Address(SQLModel):
    address: str
    netmask: str
    # 归一后的网段写法（如 192.168.90.0/24），供前端展示与「地址池是否在网段内」的判断
    cidr: str = ""


class HostIPv6Address(SQLModel):
    address: str
    prefix: int
    # 归一后的网段写法（如 fd00:30:12::/64）
    cidr: str = ""


class HostInterface(SQLModel):
    name: str
    # 1=有链路；0=没插线/对端未上电；None=读不到（/sys 未挂载或该网口不支持）
    carrier: int | None = None
    speed_mbps: int | None = None
    mac: str | None = None
    ipv4: list[HostIPv4Address] = Field(default_factory=list)
    # 非链路本地的 IPv6 地址（链路本地与 ::1 已过滤）：用于 RA 前缀一致性校验
    ipv6: list[HostIPv6Address] = Field(default_factory=list)


class HostServiceBinding(SQLModel):
    service: str
    container: str
    # 挂到的 macvlan 网络名与它的 parent 网口；未绑定时三者分别为 None/None/False
    network: str | None = None
    parent: str | None = None
    attached: bool = False
    # 容器在该 macvlan 网络上的地址（用于「使用方式」卡片与地址冲突校验）
    address: str | None = None


class HostNetworkCheck(SQLModel):
    # ok / info / warn / error：前端按级别配色，warn 与 error 才显著提示
    level: str
    code: str
    service: str | None = None
    message: str


class HostNetworkInfo(SQLModel):
    # ok=已读到宿主各口 IP；unavailable=helper 容器不可用（IP 留空，前端提示即可，不当失败）
    ip_source: str = "ok"
    # 意图侧（来自 .env）：接 BMC 的网口、测试网段、需要在测试网段上被访问的服务
    parent_iface: str = ""
    l2_subnet: str = ""
    l2_services: list[str] = Field(default_factory=list)
    # 宿主默认路由出口网口：若与 parent_iface 相同，说明测试口承载默认路由（危险配置）
    default_iface: str = ""
    interfaces: list[HostInterface] = Field(default_factory=list)
    bindings: list[HostServiceBinding] = Field(default_factory=list)
    checks: list[HostNetworkCheck] = Field(default_factory=list)


# POST /system/updates/check 与 /package 响应
class UpdateCheckResult(SQLModel):
    registry: str
    targets: list[UpdateTarget]
    update_available: list[str]


# POST /system/updates/apply 请求体：target 为 "platform" 或服务名
class UpdateApplyRequest(SQLModel):
    target: str
    image: str


# --------------------------------------------------------------------------- #
# 配置版本（每次下发留存一条，供历史查看与回滚）
#
# 版本里存的是**提交后的真实值**（回滚要拿它重新渲染），对外暴露必须走
# config_renderer.mask_secret_values 脱敏——与「读取脱敏、提交掩码保留原值」同一套语义。
# 版本号每服务独立自增 + (service_name, version) 唯一约束：并发下发时由数据库兜底。
# --------------------------------------------------------------------------- #
class ServiceConfigVersionBase(SQLModel):
    service_name: str = Field(max_length=64, index=True)
    # 每服务独立自增的版本号，从 1 开始
    version: int
    # 提交后的真实配置值（含 secret 明文）；对外接口必须脱敏后再返回。
    # nullable=False 与 serviceconfig 表同口径：下发必写值，空字典也写 {}
    values: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    # 写入时 schema 里的 secret 字段名。详情接口据此脱敏，而不是按「当前 schema」反推——
    # schema 演进（字段改名/去掉 secret）后，历史版本里的密文才不会明文返回
    secret_fields: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    # 本次渲染产物是否已在容器内生效（容器未运行或 reload 失败时为 False）
    applied: bool = False
    # 渲染产物摘要（sha256 前 16 位）：用于回答「这两次下发的产物是否一致」
    rendered_digest: str = Field(default="", max_length=64)
    # 操作者邮箱（冗余存一份，用户删除后仍可追溯）
    user_email: str | None = Field(default=None, max_length=255)
    # 回滚产生的版本会记下来源版本号，便于在历史里区分「改配置」与「回滚」
    rolled_back_from: int | None = Field(default=None)


class ServiceConfigVersion(ServiceConfigVersionBase, table=True):
    __table_args__ = (
        UniqueConstraint("service_name", "version", name="uq_config_version"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


# 列表项：不含 values（列表不需要配置内容，也避免一页带出大量 JSON）
class ServiceConfigVersionPublic(SQLModel):
    id: uuid.UUID
    version: int
    applied: bool
    rendered_digest: str
    user_email: str | None = None
    rolled_back_from: int | None = None
    created_at: datetime | None = None


class ServiceConfigVersionsPublic(SQLModel):
    data: list[ServiceConfigVersionPublic]
    count: int


# 详情：values 已脱敏（由路由层负责脱敏，模型只声明形状）
class ServiceConfigVersionDetail(SQLModel):
    version: int
    values: dict[str, Any]
    applied: bool
    rendered_digest: str
    user_email: str | None = None
    rolled_back_from: int | None = None
    created_at: datetime | None = None
