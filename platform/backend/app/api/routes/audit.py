"""审计日志与角色查询路由：均限管理员（admin 角色或超管）。

审计语义（任务契约）：AuditLog.user_id 恒记操作者（用户删除后置 NULL）；
user_email 为冗余追溯字段——用户操作记目标邮箱、服务操作记操作者邮箱。
"""

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlmodel import col, func, or_, select

from app import crud
from app.api.deps import RequireAdmin, SessionDep
from app.models import (
    AuditActionsPublic,
    AuditLog,
    AuditLogPublic,
    AuditLogsPublic,
    RolesPublic,
)

# 列表接口默认单页条数，与 users 列表一致
DEFAULT_LIST_LIMIT = 100
# 单页上限：审计表只增不减，放开发分页参数会让一次请求拖走整表
MAX_LIST_LIMIT = 500

router = APIRouter(
    prefix="/audit-logs",
    tags=["audit"],
    dependencies=[Depends(RequireAdmin)],
)

roles_router = APIRouter(
    tags=["roles"],
    dependencies=[Depends(RequireAdmin)],
)


@router.get("", response_model=AuditLogsPublic)
def read_audit_logs(
    session: SessionDep,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    action: str | None = Query(default=None, max_length=64),
    service_name: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=128),
) -> Any:
    """分页返回审计日志（按 created_at 倒序，新事件在前），支持按动作/服务/关键字过滤。

    过滤在数据库侧完成：审计表是只增表，前端本地过滤只能看到当前页，
    会把「筛出的条数」和「实际条数」弄成两个数。

    Args:
        session: 数据库会话。
        offset: 分页起始偏移，默认 0。
        limit: 单页条数，默认 100，上限 500。
        action: 精确匹配的动作名，如 ``config.update``。
        service_name: 精确匹配的服务名，用户类动作留空故不会被命中。
        q: 关键字，模糊匹配表格里可见的文本列（动作名/服务名/操作者邮箱/detail）。

    Returns:
        AuditLogsPublic：当前过滤条件下总数与日志列表（不含 user_id）。

    Raises:
        HTTPException: 403 当前用户无 admin 角色且非超管。
    """
    conditions = []
    if action:
        conditions.append(col(AuditLog.action) == action)
    if service_name:
        conditions.append(col(AuditLog.service_name) == service_name)
    if q:
        pattern = f"%{q}%"
        # 覆盖表格里所有可见文本列：关键字框是「不知道用哪个筛选器时」的入口，
        # 只搜 detail 会让用户搜「动作名/服务名」时得到空结果
        conditions.append(
            or_(
                col(AuditLog.action).ilike(pattern),
                col(AuditLog.service_name).ilike(pattern),
                col(AuditLog.user_email).ilike(pattern),
                col(AuditLog.detail).ilike(pattern),
            )
        )

    count_statement = select(func.count()).select_from(AuditLog)
    statement = select(AuditLog)
    for condition in conditions:
        count_statement = count_statement.where(condition)
        statement = statement.where(condition)

    count = session.exec(count_statement).one()
    logs = session.exec(
        statement.order_by(col(AuditLog.created_at).desc()).offset(offset).limit(limit)
    ).all()

    return AuditLogsPublic(
        data=[AuditLogPublic.model_validate(entry) for entry in logs], count=count
    )


@router.get("/actions", response_model=AuditActionsPublic)
def read_audit_actions(session: SessionDep) -> Any:
    """返回库中出现过的全部动作名（升序），供前端筛选下拉使用。

    取自实际数据而非写死的枚举：动作名随功能演进增减，写死会让新增动作
    在筛选器里查不到。

    Args:
        session: 数据库会话。

    Returns:
        AuditActionsPublic：data 形如 ["service.config.update", "user.create"]。

    Raises:
        HTTPException: 403 当前用户无 admin 角色且非超管。
    """
    statement = select(AuditLog.action).distinct().order_by(col(AuditLog.action))
    return AuditActionsPublic(data=list(session.exec(statement).all()))


@roles_router.get("/roles", response_model=RolesPublic)
def read_roles(session: SessionDep) -> Any:
    """返回全部角色名列表（按 name 升序），供前端角色选择器使用。

    Args:
        session: 数据库会话。

    Returns:
        RolesPublic：data 形如 ["admin", "operator", "readonly"]。

    Raises:
        HTTPException: 403 当前用户无 admin 角色且非超管。
    """
    return RolesPublic(data=[role.name for role in crud.get_roles(session=session)])
