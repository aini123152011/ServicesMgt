"""审计日志与角色查询路由：均限管理员（admin 角色或超管）。

审计语义（任务契约）：AuditLog.user_id 恒记操作者（用户删除后置 NULL）；
user_email 为冗余追溯字段——用户操作记目标邮箱、服务操作记操作者邮箱。
"""

from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import col, func, select

from app import crud
from app.api.deps import RequireAdmin, SessionDep
from app.models import AuditLog, AuditLogPublic, AuditLogsPublic, RolesPublic

# 列表接口默认单页条数，与 users 列表一致
DEFAULT_LIST_LIMIT = 100

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
    session: SessionDep, offset: int = 0, limit: int = DEFAULT_LIST_LIMIT
) -> Any:
    """分页返回审计日志（按 created_at 倒序，新事件在前）。

    Args:
        session: 数据库会话。
        offset: 分页起始偏移，默认 0。
        limit: 单页条数，默认 100。

    Returns:
        AuditLogsPublic：日志列表（不含 user_id）与总数。

    Raises:
        HTTPException: 403 当前用户无 admin 角色且非超管。
    """
    count_statement = select(func.count()).select_from(AuditLog)
    count = session.exec(count_statement).one()

    statement = (
        select(AuditLog)
        .order_by(col(AuditLog.created_at).desc())
        .offset(offset)
        .limit(limit)
    )
    logs = session.exec(statement).all()

    return AuditLogsPublic(
        data=[AuditLogPublic.model_validate(entry) for entry in logs], count=count
    )


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
