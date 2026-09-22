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

from app import config_renderer, config_versions, lifecycle, registry
from app.api.deps import CurrentUser, RequireOperator, SessionDep, get_current_user
from app.crud import (
    get_fault_modes,
    get_service_config,
    record_audit_log,
    upsert_service_config,
)
from app.models import (
    Message,
    ServiceConfigApplyResult,
    ServiceConfigState,
    ServiceConfigUpdate,
    ServiceConfigVersionDetail,
    ServiceConfigVersionPublic,
    ServiceConfigVersionsPublic,
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
def read_services(session: SessionDep) -> Any:
    """列出全部服务概要（含当前生效的故障注入模式）。

    每次调用现扫 SERVICES_DIR；manifest 不合规的目录被跳过而非报错。
    fault_mode 取自已保存配置（一次查询），供首页「当前故障注入」面板直接使用。

    Args:
        session: 数据库会话，用于一次性读取各服务的 fault_mode。

    Returns:
        ServicesPublic：概要列表与数量；目录为空时 data 为空列表。
    """
    plugins = registry.list_services()
    fault_modes = get_fault_modes(session=session)
    summaries = [
        ServiceSummary.model_validate(plugin.manifest).model_copy(
            update={"fault_mode": fault_modes.get(plugin.name)}
        )
        for plugin in plugins
    ]
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


def _apply_config_values(
    *,
    session: SessionDep,
    plugin: ServicePlugin,
    name: str,
    values: dict[str, Any],
    user_email: str | None,
    rolled_back_from: int | None = None,
) -> bool:
    """渲染 → 写卷 → 落库 → reload → 记一条配置版本，返回 applied。

    PUT /config 与回滚共用这一段：两条路径对「生效」的定义必须完全一致，否则回滚会出现
    「界面说成功、服务其实没生效」。版本记录放在最后、用最终 applied 值——一次下发只产生
    一条版本，中途 applied=False 的中间态不入历史。

    Args:
        session: 数据库会话。
        plugin: 服务插件（提供 schema/manifest）。
        name: 服务名。
        values: 已校验的配置值。
        user_email: 操作者邮箱，记入版本。
        rolled_back_from: 回滚来源版本号；普通下发为 None。

    Returns:
        applied：渲染产物是否已在容器内生效。

    Raises:
        HTTPException: 502 渲染失败或 Docker 调用失败。
    """
    rendered_at = get_datetime_utc()
    try:
        rendered_paths = config_renderer.render_config(plugin, values)
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
    config_versions.record_config_version(
        session=session,
        service_name=name,
        values=values,
        applied=applied,
        user_email=user_email,
        digest=config_versions.rendered_digest(list(rendered_paths)),
        rolled_back_from=rolled_back_from,
    )
    return applied


@router.get(
    "/{name}/config/versions",
    response_model=ServiceConfigVersionsPublic,
)
def read_config_versions(
    session: SessionDep,
    name: str,
    offset: int = 0,
    limit: int = 50,
) -> Any:
    """列出该服务的配置版本（版本号倒序，新版本在前）。

    列表不含配置值：列表不需要内容，也避免一页带出大量 JSON；看内容用版本详情接口（已脱敏）。

    Args:
        session: 数据库会话。
        name: 服务名。
        offset: 分页起始偏移。
        limit: 单页条数。

    Returns:
        ServiceConfigVersionsPublic：版本列表与总数。

    Raises:
        HTTPException: 403 未登录；404 服务不存在。
    """
    _get_plugin_or_404(name)
    rows, count = config_versions.list_config_versions(
        session=session, service_name=name, offset=offset, limit=limit
    )
    return ServiceConfigVersionsPublic(
        data=[ServiceConfigVersionPublic.model_validate(row) for row in rows],
        count=count,
    )


@router.get(
    "/{name}/config/versions/{version}",
    response_model=ServiceConfigVersionDetail,
)
def read_config_version(session: SessionDep, name: str, version: int) -> Any:
    """查看某个版本的配置内容（secret 字段脱敏）。

    与「服务详情」同一套脱敏语义：库里版本存的是真实值（渲染需要），对外只给掩码。

    Args:
        session: 数据库会话。
        name: 服务名。
        version: 版本号。

    Returns:
        ServiceConfigVersionDetail：该版本的脱敏配置与元信息。

    Raises:
        HTTPException: 403 未登录；404 服务或版本不存在。
    """
    plugin = _get_plugin_or_404(name)
    row = config_versions.get_config_version(
        session=session, service_name=name, version=version
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Version {version} not found")
    # 历史行的 values 理论上不会为空（写入时必填），但类型上是可空的，兜底成空字典
    raw_values: dict[str, Any] = row.values if row.values is not None else {}
    masked = config_renderer.mask_secret_values(plugin.schema, raw_values)
    return ServiceConfigVersionDetail(
        version=row.version,
        values=masked if masked is not None else {},
        applied=row.applied,
        rendered_digest=row.rendered_digest,
        user_email=row.user_email,
        rolled_back_from=row.rolled_back_from,
        created_at=row.created_at,
    )


@router.post(
    "/{name}/config/versions/{version}/rollback",
    dependencies=[Depends(RequireOperator)],
    response_model=ServiceConfigApplyResult,
)
def rollback_config_version(
    session: SessionDep,
    current_user: CurrentUser,
    name: str,
    version: int,
) -> Any:
    """把服务配置回滚到指定版本（回滚本身也是一次新下发）。

    为什么回滚写新版本而不是删掉后面的版本：历史只增不改才能回答「当时到底是什么配置」，
    且回滚后「最新版本 = 当前生效配置」这条不变量仍然成立。

    Args:
        session: 数据库会话。
        current_user: 当前登录用户（操作者）。
        name: 服务名。
        version: 要回滚到的版本号。

    Returns:
        ServiceConfigApplyResult：applied 状态与提示文案。

    Raises:
        HTTPException: 403 无 operator 及以上角色；404 服务或版本不存在；
            400 历史值已不符合当前 schema；502 渲染或 Docker 调用失败。
    """
    plugin = _get_plugin_or_404(name)
    row = config_versions.get_config_version(
        session=session, service_name=name, version=version
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Version {version} not found")
    try:
        # 历史值当初合法，但 schema 可能已演进（字段增删/范围收紧），回滚前重新校验
        values = config_renderer.validate_values(plugin.schema, row.values)
    except config_renderer.ConfigValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    applied = _apply_config_values(
        session=session,
        plugin=plugin,
        name=name,
        values=values,
        user_email=current_user.email,
        rolled_back_from=version,
    )
    record_audit_log(
        session=session,
        user_id=current_user.id,
        user_email=current_user.email,
        action="config.rollback",
        service_name=name,
        detail=f"from_version={version} applied={'true' if applied else 'false'}",
    )
    return ServiceConfigApplyResult(
        message=(
            f"Rolled back to version {version}"
            if applied
            else f"Rolled back to version {version}; service is not running, "
            "it will be applied on next start"
        ),
        applied=applied,
    )


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
    applied = _apply_config_values(
        session=session,
        plugin=plugin,
        name=name,
        values=values,
        user_email=current_user.email,
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
