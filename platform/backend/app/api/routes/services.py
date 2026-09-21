"""服务管理路由：注册表查询、配置读写与渲染下发、生命周期操作、状态与日志。

错误语义（见 .trellis/spec/backend/error-handling.md）：404 服务不存在、
400 配置校验失败、502 Docker/渲染/写卷失败（detail 带原始原因）；
所有端点都要求已认证用户，写操作（配置下发/生命周期）额外要求
operator 及以上角色（admin/超管放行），操作成功后写审计日志。
"""

import logging
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app import config_renderer, lifecycle, registry
from app.api.deps import CurrentUser, RequireOperator, SessionDep, get_current_user
from app.crud import get_service_config, record_audit_log, upsert_service_config
from app.models import (
    Message,
    ServiceConfigApplyResult,
    ServiceConfigState,
    ServiceConfigUpdate,
    ServiceLogs,
    ServiceManifest,
    ServicesPublic,
    ServiceStatus,
    ServiceSummary,
    get_datetime_utc,
)
from app.registry import ServicePlugin

router = APIRouter(
    prefix="/services", tags=["services"], dependencies=[Depends(get_current_user)]
)

logger = logging.getLogger(__name__)

MAX_LOG_TAIL = 1000

_ACTION_PAST_TENSE = {"start": "started", "stop": "stopped", "restart": "restarted"}


class ServiceAction(StrEnum):
    """生命周期动作，用作 URL 路径参数（枚举外的动作由 FastAPI 返回 422）。"""

    start = "start"
    stop = "stop"
    restart = "restart"


def _get_plugin_or_404(name: str) -> ServicePlugin:
    """按名取服务插件，统一抛 404（detail 文案与测试精确对应）。"""
    plugin = registry.get_service(name)
    if plugin is None:
        raise HTTPException(status_code=404, detail="Service not found")
    return plugin


@router.get("/", response_model=ServicesPublic)
def read_services() -> Any:
    """列出全部服务概要。

    每次调用现扫 SERVICES_DIR；manifest 不合规的目录被跳过而非报错。

    Returns:
        ServicesPublic：概要列表与数量；目录为空时 data 为空列表。
    """
    plugins = registry.list_services()
    summaries = [ServiceSummary.model_validate(plugin.manifest) for plugin in plugins]
    return ServicesPublic(data=summaries, count=len(summaries))


@router.get("/{name}")
def read_service(session: SessionDep, name: str) -> Any:
    """返回服务详情：完整 manifest、schema 字段定义与当前已保存配置。

    Args:
        session: 数据库会话（读取已保存的配置状态）。
        name: 服务名，需与 services/ 目录名一致。

    Returns:
        手工组装的 dict：manifest、schema（顶层键与 pydantic 保留名冲突，
        不走 response_model）、config（从未保存过时三字段均为 null）。

    Raises:
        HTTPException: 404 服务不存在。
    """
    plugin = _get_plugin_or_404(name)
    config = get_service_config(session=session, service_name=name)
    masked_values = (
        config_renderer.mask_secret_values(plugin.schema, config.values)
        if config and config.values
        else None
    )
    # 手工组响应而非 response_model：顶层键 "schema" 与 pydantic 保留名冲突，直传 dict 保证形状逐字一致
    return {
        "manifest": ServiceManifest.model_validate(plugin.manifest),
        "schema": plugin.schema,
        "config": ServiceConfigState(
            values=masked_values,
            applied=config.applied if config else None,
            rendered_at=config.rendered_at if config else None,
        ),
    }


@router.put(
    "/{name}/config",
    dependencies=[Depends(RequireOperator)],
    response_model=ServiceConfigApplyResult,
)
def update_service_config(
    session: SessionDep,
    current_user: CurrentUser,
    name: str,
    config_in: ServiceConfigUpdate,
) -> Any:
    """校验 → 渲染 → 写卷 → 落库，容器在运行则执行 /reload.sh 使配置生效。

    applied 语义：reload 成功为 True；容器未运行（跳过 reload）或 reload/写卷
    失败一律为 False，绝不把失败标记成已生效。操作成功后写审计
    （action=config.update，user_id 记操作者）。

    Args:
        session: 数据库会话，用于配置落库与审计写入。
        current_user: 当前登录用户（操作者），用于审计归属。
        name: 服务名，需与 services/ 目录名一致。
        config_in: 请求体，values 键与该服务 schema 字段对应。

    Returns:
        ServiceConfigApplyResult：applied 状态与提示文案。

    Raises:
        HTTPException: 403 无 operator 及以上角色；404 服务不存在；
            400 values 与 schema 不匹配；502 渲染/写卷/Docker 调用失败。
    """
    plugin = _get_plugin_or_404(name)
    existing_config = get_service_config(session=session, service_name=name)
    existing_values = existing_config.values if existing_config else None
    try:
        values = config_renderer.validate_values(
            plugin.schema, config_in.values, existing_values=existing_values
        )
    except config_renderer.ConfigValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    rendered_at = get_datetime_utc()
    try:
        config_renderer.render_config(plugin, values)
    except config_renderer.TemplateRenderError as e:
        logger.error(f"Config apply failed for service '{name}': {e}")
        raise HTTPException(status_code=502, detail=str(e)) from e
    upsert_service_config(
        session=session,
        service_name=name,
        values=values,
        rendered_at=rendered_at,
        applied=False,
    )
    applied = False
    try:
        status = lifecycle.get_status(plugin.manifest["container_name"])
        if status["running"]:
            lifecycle.exec_reload(plugin.manifest)
            applied = True
    except lifecycle.LifecycleError as e:
        logger.error(f"Config apply failed for service '{name}': {e}")
        raise HTTPException(status_code=502, detail=str(e)) from e
    if applied:
        # reload 成功后才把 applied 置 True；失败路径保持 False 落库
        upsert_service_config(
            session=session,
            service_name=name,
            values=values,
            rendered_at=rendered_at,
            applied=True,
        )
    # 审计只记结果状态，不记配置值（值里可能含密码类字段）
    record_audit_log(
        session=session,
        user_id=current_user.id,
        user_email=current_user.email,
        action="config.update",
        service_name=name,
        detail=f"applied={'true' if applied else 'false'}",
    )
    message = (
        "Configuration applied successfully"
        if applied
        else "Configuration saved; service is not running, it will be applied on next start"
    )
    return ServiceConfigApplyResult(message=message, applied=applied)


@router.post(
    "/{name}/{action}",
    dependencies=[Depends(RequireOperator)],
    response_model=Message,
)
def run_service_action(
    session: SessionDep, current_user: CurrentUser, name: str, action: ServiceAction
) -> Any:
    """对服务容器执行 start/stop/restart，操作成功后写审计。

    Args:
        session: 数据库会话，用于审计写入。
        current_user: 当前登录用户（操作者），用于审计归属。
        name: 服务名，用于定位插件与容器名。
        action: 生命周期动作，枚举外的路径参数由 FastAPI 返回 422。

    Returns:
        Message：操作结果文案。

    Raises:
        HTTPException: 403 无 operator 及以上角色；404 服务不存在；
            502 Docker 调用失败。
    """
    plugin = _get_plugin_or_404(name)
    container_name = plugin.manifest["container_name"]
    try:
        if action == ServiceAction.start:
            lifecycle.start(container_name)
        elif action == ServiceAction.stop:
            lifecycle.stop(container_name)
        else:
            lifecycle.restart(container_name)
    except lifecycle.LifecycleError as e:
        logger.error(f"Service action '{action.value}' failed for '{name}': {e}")
        raise HTTPException(status_code=502, detail=str(e)) from e
    record_audit_log(
        session=session,
        user_id=current_user.id,
        user_email=current_user.email,
        action=f"service.{action.value}",
        service_name=name,
        detail=f"container={container_name}",
    )
    return Message(
        message=f"Service '{name}' {_ACTION_PAST_TENSE[action.value]} successfully"
    )


@router.get("/{name}/status", response_model=ServiceStatus)
def read_service_status(name: str) -> Any:
    """返回服务容器实时状态与健康检查结果。

    Args:
        name: 服务名，用于定位插件与容器名。

    Returns:
        ServiceStatus：容器不存在时 running=False，health/status 相应为空。

    Raises:
        HTTPException: 404 服务不存在；502 Docker 调用失败。
    """
    plugin = _get_plugin_or_404(name)
    try:
        state = lifecycle.get_status(plugin.manifest["container_name"])
    except lifecycle.LifecycleError as e:
        logger.error(f"Status query failed for service '{name}': {e}")
        raise HTTPException(status_code=502, detail=str(e)) from e
    return ServiceStatus(
        name=name,
        running=state["running"],
        health=state["health"],
        status=state["status"],
    )


@router.get("/{name}/logs", response_model=ServiceLogs)
def read_service_logs(name: str, tail: int = 200) -> Any:
    """返回服务容器最近日志文本。

    Args:
        name: 服务名，用于定位插件与容器名。
        tail: 拉取的末尾行数，默认 200，裁剪到 [1, 1000]。

    Returns:
        ServiceLogs：按行拼接的日志文本。

    Raises:
        HTTPException: 404 服务不存在；502 Docker 调用失败。
    """
    plugin = _get_plugin_or_404(name)
    # 上限裁剪防止一次拉取过量日志拖垮后端
    clamped_tail = max(1, min(tail, MAX_LOG_TAIL))
    try:
        logs = lifecycle.get_logs(plugin.manifest["container_name"], tail=clamped_tail)
    except lifecycle.LifecycleError as e:
        logger.error(f"Log query failed for service '{name}': {e}")
        raise HTTPException(status_code=502, detail=str(e)) from e
    return ServiceLogs(logs=logs)
