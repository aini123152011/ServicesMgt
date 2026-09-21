"""系统信息与更新路由：版本可见、检查更新、离线包导入、一键更新。

权限：全部端点要求 admin（含超管）——更新会重建容器、影响在线服务，属高危操作。
错误语义（见 .trellis/spec/backend/error-handling.md）：403 无 admin 角色、
400 参数/包非法、409 已有更新任务在执行、502 Docker/写文件失败（detail 带原因）。
每次「应用更新」成功后写审计（action=system.update，detail 只记目标与镜像，不含敏感值）。
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app import container_rebuild, registry, system_update
from app.api.deps import CurrentUser, RequireAdmin, SessionDep, get_current_user
from app.crud import record_audit_log
from app.models import Message, SystemInfo, UpdateApplyRequest, UpdateCheckResult
from app.system_update import UpdateBusyError, UpdateError

router = APIRouter(
    prefix="/system", tags=["system"], dependencies=[Depends(get_current_user)]
)

logger = logging.getLogger(__name__)

# 离线包大小上限：平台镜像约百 MB 量级，留足余量但挡住误传大文件
MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024


@router.get("/info", response_model=SystemInfo)
def read_system_info() -> Any:
    """平台版本/构建信息 + 平台与各服务的镜像现状。

    Returns:
        SystemInfo：version/build/update_registry、最近一次更新任务状态，
        以及每个更新目标（platform 与 11 个服务）的镜像与容器状态。
    """
    info = system_update.platform_info()
    try:
        targets = system_update.collect_targets(registry.list_services())
        docker_available = True
    except UpdateError as e:
        # Docker 不可达不该让「看版本」一起失败：降级返回空目标列表
        logger.warning(f"Docker unavailable while collecting system info: {e}")
        targets = []
        docker_available = False
    return SystemInfo(**info, docker_available=docker_available, targets=targets)


@router.post(
    "/updates/check",
    dependencies=[Depends(RequireAdmin)],
    response_model=UpdateCheckResult,
)
def check_updates() -> Any:
    """检查更新：配置了镜像仓库则先拉取，再逐个比较容器镜像 ID 与 tag 当前 ID。"""
    try:
        return UpdateCheckResult(
            **system_update.check_updates(registry.list_services())
        )
    except UpdateError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post(
    "/updates/package",
    dependencies=[Depends(RequireAdmin)],
    response_model=UpdateCheckResult,
)
async def upload_package(file: UploadFile = File(...)) -> Any:
    """上传离线镜像包（tar）并 docker load，随后返回最新的检查结果。

    Raises:
        HTTPException: 400 包为空/超限；502 docker load 失败（包损坏等）。
    """
    # 先按声明大小挡掉超大包，避免为了判断大小把整个文件读进内存
    declared = getattr(file, "size", None)
    if declared is not None and declared > MAX_PACKAGE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Uploaded package exceeds {MAX_PACKAGE_BYTES} bytes",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded package is empty")
    if len(data) > MAX_PACKAGE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Uploaded package exceeds {MAX_PACKAGE_BYTES} bytes",
        )
    try:
        system_update.import_package(file.filename or "package.tar", data)
    except UpdateError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    try:
        return UpdateCheckResult(
            **system_update.check_updates(registry.list_services())
        )
    except UpdateError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post(
    "/updates/apply",
    dependencies=[Depends(RequireAdmin)],
    response_model=Message,
)
def apply_update(
    session: SessionDep,
    current_user: CurrentUser,
    payload: UpdateApplyRequest,
) -> Any:
    """应用更新：服务容器后台重建；平台自身由一次性 helper 容器重建。

    Raises:
        HTTPException: 409 已有任务在执行；502 目标未知或镜像不存在/启动 helper 失败。
    """
    try:
        result = system_update.start_update(
            registry.list_services(), payload.target, payload.image
        )
    except UpdateBusyError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except (UpdateError, container_rebuild.RebuildError) as e:
        # RebuildError 也要转 502：自更新派生 helper 前要取平台容器，
        # 容器名不符或平台正被重建时它抛的是 RebuildError（复核 P1）
        raise HTTPException(status_code=502, detail=str(e)) from e

    record_audit_log(
        session=session,
        user_id=current_user.id,
        user_email=current_user.email,
        action="system.update",
        service_name=None if payload.target == "platform" else payload.target,
        detail=f"target={payload.target} image={payload.image}",
    )
    return Message(message=result["message"])


@router.get("/updates/status")
def read_update_status() -> Any:
    """当前/最近一次更新任务状态（平台自更新重启后仍可查，前端据此轮询）。"""
    status = system_update.current_status()
    if status is None:
        return {"status": "idle", "message": "No update task has run yet"}
    return status
