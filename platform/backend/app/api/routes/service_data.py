"""服务数据文件查询路由：浏览数据卷目录树、按窗口读取文本文件内容。

面向 rsyslog BMC 日志（<日期>/<IP>.log 归档结构）的通用化：任何声明了
data_dir 的服务插件，其 <name>-data 数据卷根即可浏览根，按子路径逐级下钻。
错误语义沿用 services.py 约定：404 服务/路径不存在、400 无数据卷/参数非法/
类型不符、502 文件系统读取失败（detail 带原始原因）。只读接口，router 级
认证即可访问，不做角色限制（readonly 角色也需要查看日志）。
"""

import logging
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.models import ServiceDataContent, ServiceDataEntry, ServiceDataTree
from app.registry import ServicePlugin, get_service

router = APIRouter(
    prefix="/services", tags=["services"], dependencies=[Depends(get_current_user)]
)

logger = logging.getLogger(__name__)

# content 单次读取的字节窗口上限（从文件尾部截取），防止大日志一次性读入拖垮后端
MAX_READ_BYTES = 2 * 1024 * 1024
# content 返回的最大行数，超出部分裁掉
MAX_TAIL_LINES = 2000
DEFAULT_TAIL_LINES = 500


def _get_data_plugin_or_error(name: str) -> ServicePlugin:
    """取服务插件并要求其声明了数据卷，统一 404/400 语义。

    Args:
        name: 服务名，需与 services/ 目录名一致。

    Returns:
        通过校验且声明了 data_dir 的服务插件。

    Raises:
        HTTPException: 404 服务不存在；400 服务未声明数据卷（detail 固定
            "Service has no data volume"，前端据此隐藏入口而非报错弹窗）。
    """
    plugin = get_service(name)
    if plugin is None:
        raise HTTPException(status_code=404, detail="Service not found")
    if plugin.data_dir is None:
        raise HTTPException(status_code=400, detail="Service has no data volume")
    return plugin


def _resolve_subpath(root: Path, subpath: str) -> tuple[Path, str]:
    """把用户提交的相对子路径解析为数据卷内的绝对路径，越界企图一律拒绝。

    先做字符级校验（反斜杠/绝对路径/.. 段），再 resolve 归一化并确认仍位于
    卷根内——resolve 会跟随符号链接，指向卷外的链接同样在此拦截。

    Args:
        root: 数据卷宿主机根目录（可浏览根）。
        subpath: posix 风格相对子路径，空串表示卷根本身。

    Returns:
        (解析后的绝对路径, 归一化后的相对路径字符串，卷根为空串)。

    Raises:
        HTTPException: 400 subpath 含反斜杠、为绝对路径、含 .. 段，或
            解析后越出卷根（detail 固定 "Invalid subpath"，不区分具体原因，
            避免向客户端泄露路径校验细节）。
    """
    # 反斜杠在 Windows 语义里是分隔符，直接拒绝以免绕过 posix 逐段校验
    if "\\" in subpath:
        raise HTTPException(status_code=400, detail="Invalid subpath")
    relative = PurePosixPath(subpath) if subpath else PurePosixPath()
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(status_code=400, detail="Invalid subpath")
    root_resolved = root.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root_resolved):
        raise HTTPException(status_code=400, detail="Invalid subpath")
    return target, "/".join(relative.parts)


def _describe_entry(entry: Path) -> ServiceDataEntry:
    """把单个文件系统条目转成响应模型，类型判定跟随符号链接指向。

    Args:
        entry: 目录列举得到的条目路径。

    Returns:
        条目的名称/类型/字节大小/修改时间；条目在遍历间隙被轮转删除等
        stat 失败场景降级为 size=0 的文件占位，避免整次列举失败。
    """
    try:
        stat_result = entry.stat()
        modified = datetime.fromtimestamp(stat_result.st_mtime, tz=UTC)
        is_dir = entry.is_dir()
    except OSError:
        return ServiceDataEntry(name=entry.name, type="file", size=0, modified=None)
    return ServiceDataEntry(
        name=entry.name,
        type="dir" if is_dir else "file",
        size=stat_result.st_size,
        modified=modified,
    )


def _read_tail_window(path: Path) -> tuple[bytes, bool, int]:
    """从文件尾部读取最多 MAX_READ_BYTES 字节的窗口。

    Args:
        path: 目标文本文件的宿主机路径。

    Returns:
        (字节窗口, 头部是否被截断, 文件总字节数)；截断指文件超过窗口上限、
        头部字节被丢弃。

    Raises:
        OSError: 文件 stat/打开/读取失败时原样上抛，由路由层转 502。
    """
    file_size = path.stat().st_size
    with path.open("rb") as file:
        if file_size <= MAX_READ_BYTES:
            return file.read(), False, file_size
        file.seek(file_size - MAX_READ_BYTES)
        return file.read(MAX_READ_BYTES), True, file_size


@router.get("/{name}/data/tree", response_model=ServiceDataTree)
def read_service_data_tree(name: str, subpath: str = "") -> Any:
    """列出数据卷内指定子目录的条目（文件与目录混排，目录优先，按名称排序）。

    Args:
        name: 服务名，需与 services/ 目录名一致。
        subpath: 相对数据卷根的子目录路径，空串表示卷根；用于 rsyslog 场景
            即先列日期目录、再列某日期下的 IP 日志文件。

    Returns:
        ServiceDataTree：归一化子路径与条目列表，空目录返回空 entries。

    Raises:
        HTTPException: 404 服务不存在或子路径不存在；400 未声明数据卷/
            subpath 非法/目标不是目录；502 目录读取失败。
    """
    plugin = _get_data_plugin_or_error(name)
    target, normalized = _resolve_subpath(plugin.data_volume_dir, subpath)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Data path not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")
    try:
        children = list(target.iterdir())
    except OSError as e:
        logger.error(f"Data tree query failed for service '{name}': {e}")
        raise HTTPException(
            status_code=502, detail=f"Failed to list data directory: {e}"
        ) from e
    entries = [_describe_entry(child) for child in children]
    # 目录优先、同级内按名称升序：前端按层级下钻时目录排前面更直观
    entries.sort(key=lambda entry: (entry.type != "dir", entry.name))
    return ServiceDataTree(path=normalized, entries=entries)


@router.get("/{name}/data/content", response_model=ServiceDataContent)
def read_service_data_content(
    name: str, subpath: str = "", tail: int = DEFAULT_TAIL_LINES, keyword: str = ""
) -> Any:
    """读取数据卷内指定文本文件的尾部内容，支持关键字过滤。

    读取策略：先从文件尾部按字节截取最多 2MB 窗口（truncated 标识头部被
    丢弃），再在窗口内切行、按关键字（区分大小写子串）过滤、最后裁到 tail
    行。NOTE: 关键字过滤只作用于窗口内的行，不回读更早的历史内容——保证
    接口耗时与文件总量无关，代价是命中统计不保证全量。

    Args:
        name: 服务名，需与 services/ 目录名一致。
        subpath: 相对数据卷根的文件路径；空串（卷根）或目录一律 400。
        tail: 返回的末尾行数上限，默认 500，裁剪到 [1, 2000]。
        keyword: 区分大小写的子串过滤关键字，空串表示不过滤。

    Returns:
        ServiceDataContent：文件总字节数、是否截断与按原文顺序的行列表；
        空文件返回空 lines。

    Raises:
        HTTPException: 404 服务不存在或文件不存在；400 未声明数据卷/
            subpath 非法/目标不是文件；502 文件读取失败。
    """
    plugin = _get_data_plugin_or_error(name)
    target, normalized = _resolve_subpath(plugin.data_volume_dir, subpath)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Data path not found")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    # 上限裁剪与 services.py 容器日志接口同一思路，防止过量请求拖垮后端
    clamped_tail = max(1, min(tail, MAX_TAIL_LINES))
    try:
        window, truncated, file_size = _read_tail_window(target)
    except OSError as e:
        logger.error(f"Data content query failed for service '{name}': {e}")
        raise HTTPException(
            status_code=502, detail=f"Failed to read data file: {e}"
        ) from e
    # 日志文件按行消费，个别非法字节以替换符呈现即可，不因编码问题整体失败
    lines = window.decode("utf-8", errors="replace").splitlines()
    if truncated and lines:
        # 窗口起点可能落在某行中间：丢弃首行残缺半行，保证返回的都是完整行
        lines = lines[1:]
    if keyword:
        lines = [line for line in lines if keyword in line]
    return ServiceDataContent(
        path=normalized,
        size=file_size,
        truncated=truncated,
        lines=lines[-clamped_tail:],
    )
