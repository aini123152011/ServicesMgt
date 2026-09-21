"""版本信息、更新检查与更新任务编排。

更新来源两种（见任务 design.md §4.2）：
1. **离线包**（主路径，适配当前实验室）：上传镜像 tar → `docker load` → 同名 tag 指向
   新镜像 → 「检查更新」发现容器所用镜像 ID 与 tag 当前 ID 不一致 → 一键重建容器。
2. **镜像仓库**（可选）：配置 `UPDATE_REGISTRY` 后，检查时先 `docker pull` 再比较。

平台自更新不能删除自己（进程会被杀）：把参数写成文件，派生一次性 helper 容器
（同镜像 + docker.sock + 状态目录）由它执行重建，见 `scripts/self_update.py`。
任务状态写文件而不是内存：平台自更新会重启进程，内存态会丢，写文件后重启仍可查。
"""

import calendar
import contextlib
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

from app import container_rebuild, update_status
from app.core.config import settings

logger = logging.getLogger(__name__)

# 平台容器名取自配置（部署名可能不同；spec 禁止硬编码）
PLATFORM_CONTAINER_NAME = settings.PLATFORM_CONTAINER_NAME
SELF_UPDATE_CONTAINER_NAME = "bmc-platform-selfupdate"
UPLOADS_DIRNAME = "uploads"
STATUS_FILENAME = "system-update.json"
# 平台自更新的 helper 等待时间：先让触发它的 API 把响应返回给前端
SELF_UPDATE_DELAY_SECONDS = 3
# 更新任务的宽限期：动手前不判失败；超过它仍未切换镜像就判定失败。
# 没有这条，一次进程重启或线程异常逃逸就会让任务永远停在 running，并挡住后续所有更新（复核 P0）
UPDATE_GRACE_SECONDS = 120

_worker_lock = threading.Lock()
_worker_running = False


class UpdateError(Exception):
    """更新操作失败（消息含原始原因，路由层转 502）。"""


class UpdateBusyError(Exception):
    """已有更新任务在执行（路由层转 409）。"""


def _volumes_root() -> Path:
    return Path(settings.VOLUMES_MOUNT_ROOT)


def status_file_path() -> Path:
    """更新任务状态文件路径（平台自更新重启后仍可读）。"""
    return _volumes_root() / STATUS_FILENAME


def uploads_dir() -> Path:
    """离线包落地目录。"""
    return _volumes_root() / UPLOADS_DIRNAME


def read_status() -> dict[str, Any] | None:
    """读取最近一次更新任务状态（平台自更新重启后仍可查）。"""
    return update_status.read(status_file_path())


def write_status(**fields: Any) -> dict[str, Any]:
    """原子写状态文件。

    Raises:
        UpdateError: 写文件失败（路由层转 502）。
    """
    try:
        return update_status.write(status_file_path(), **fields)
    except OSError as e:
        raise UpdateError(f"Failed to write update status file: {e}") from e


def _client() -> docker.DockerClient:
    try:
        return docker.from_env()
    except DockerException as e:  # pragma: no cover - 环境相关
        raise UpdateError(f"Failed to connect to Docker daemon: {e}") from e


def platform_info() -> dict[str, Any]:
    """平台版本/构建信息（镜像构建时经 ARG/ENV 注入，缺省 dev）。"""
    return {
        "version": settings.PLATFORM_VERSION,
        "build": settings.PLATFORM_BUILD,
        "update_registry": settings.UPDATE_REGISTRY,
        "status": read_status(),
    }


def _image_ids(
    client: docker.DockerClient, image_ref: str
) -> tuple[str | None, str | None]:
    """返回 (tag 当前解析到的镜像 ID, 镜像创建时间)；取不到则为 None。"""
    if not image_ref:
        return None, None
    try:
        image = client.images.get(image_ref)
    except NotFound, ImageNotFound, APIError, OSError:
        return None, None
    created = (image.attrs or {}).get("Created")
    return image.id, created


def _container_state(
    client: docker.DockerClient, container_name: str
) -> dict[str, Any]:
    """容器当前所用镜像 ID 与运行状态。"""
    try:
        container = client.containers.get(container_name)
    except NotFound, APIError, OSError:
        return {"running": False, "image_id": None, "image": None}
    attrs = container.attrs or {}
    return {
        "running": bool((attrs.get("State") or {}).get("Running")),
        "image_id": (attrs.get("Image") or None),
        "image": (attrs.get("Config") or {}).get("Image"),
    }


def collect_targets(plugins: list[Any]) -> list[dict[str, Any]]:
    """汇总「平台 + 各服务」的镜像现状，供检查更新与界面展示复用。"""
    client = _client()
    targets: list[dict[str, Any]] = [
        {
            "target": "platform",
            "display_name": settings.PROJECT_NAME,
            "container_name": PLATFORM_CONTAINER_NAME,
        }
    ]
    for plugin in plugins:
        targets.append(
            {
                "target": plugin.name,
                "display_name": plugin.manifest.get("display_name", plugin.name),
                "container_name": plugin.manifest["container_name"],
            }
        )

    for item in targets:
        state = _container_state(client, item["container_name"])
        image_ref = state["image"] or ""
        tag_id, tag_created = _image_ids(client, image_ref)
        item.update(
            {
                "image": image_ref,
                "running_image_id": state["image_id"],
                "available_image_id": tag_id,
                "image_created": tag_created,
                "container_running": state["running"],
                # 判定口径：容器所用镜像 ID 与同名 tag 当前 ID 不一致即「有新版本」。
                # 离线包 docker load 会覆盖同名 tag，所以这条判断在离线场景同样成立
                "update_available": bool(
                    tag_id and state["image_id"] and tag_id != state["image_id"]
                ),
            }
        )
    return targets


def check_updates(plugins: list[Any]) -> dict[str, Any]:
    """检查更新：可选先拉取镜像仓库，再逐个比较镜像 ID。"""
    if settings.UPDATE_REGISTRY:
        _pull_from_registry(plugins)
    targets = collect_targets(plugins)
    return {
        "registry": settings.UPDATE_REGISTRY,
        "targets": targets,
        "update_available": [t["target"] for t in targets if t["update_available"]],
    }


def _pull_from_registry(plugins: list[Any]) -> None:
    """配置了镜像仓库时逐个 pull；单个失败只记日志，不阻断其余检查。"""
    client = _client()
    refs = [
        f"{settings.UPDATE_REGISTRY}/{t['image']}" for t in collect_targets(plugins)
    ]
    for ref in refs:
        if not ref or ref.endswith("/"):
            continue
        try:
            client.images.pull(ref)
            logger.info(f"Pulled image '{ref}'")
        except (APIError, OSError) as e:
            logger.warning(f"Failed to pull image '{ref}': {e}")


def import_package(filename: str, data: bytes) -> dict[str, Any]:
    """保存上传的镜像包并 `docker load`，返回加载出的镜像引用。

    Raises:
        UpdateError: 目录/写文件失败，或 docker load 失败（包损坏等）。
    """
    safe_name = Path(filename).name
    if not safe_name:
        raise UpdateError("Uploaded package has no file name")
    directory = uploads_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / safe_name
    try:
        target.write_bytes(data)
    except OSError as e:
        raise UpdateError(f"Failed to store uploaded package: {e}") from e

    client = _client()
    try:
        with target.open("rb") as handle:
            loaded = client.images.load(handle)
    except (DockerException, OSError) as e:
        # NOTE: 包损坏时 docker SDK 抛的是 ImageLoadError，它的 MRO 是
        # ImageLoadError → DockerException（不是 APIError/OSError），只接后两者会漏成 500
        with contextlib.suppress(OSError):
            target.unlink()  # 坏包不留盘，避免污染 uploads 目录
        raise UpdateError(f"Failed to load image package '{safe_name}': {e}") from e

    tags = sorted(
        tag
        for image in loaded or []
        for tag in ((image.attrs or {}).get("RepoTags") or [])
        if tag and tag != "<none>:<none>"
    )
    logger.info(f"Loaded image package '{safe_name}', tags: {tags}")
    return {"file": safe_name, "size": len(data), "tags": tags}


def _run_rebuild(target: str, container_name: str, image: str) -> None:
    """后台线程里执行重建并落状态文件（服务更新与 helper 之外的自更新兜底）。"""
    global _worker_running
    write_status(
        id=str(uuid.uuid4()),
        target=target,
        image=image,
        phase="running",
        status="running",
        message=f"Rebuilding container '{container_name}' with image '{image}'",
        started_at=update_status.now_iso(),
    )
    try:
        result = container_rebuild.rebuild_container(container_name, image)
    except Exception as e:  # noqa: BLE001 - 任何异常都必须落状态
        # 只接 RebuildError 是不够的：docker SDK 的 NotFound/APIError 等都可能逃逸，
        # 线程一旦死亡状态就永远停在 running，后续更新全部被 409 挡住（复核 P0）
        logger.exception(f"Update failed for '{target}'")
        write_status(
            target=target,
            image=image,
            phase="failed",
            status="failed",
            message=f"{type(e).__name__}: {e}",
            finished_at=update_status.now_iso(),
        )
    else:
        write_status(
            target=target,
            image=image,
            phase="succeeded",
            status="succeeded",
            message=f"Updated '{target}' from '{result['old_image']}' to '{result['new_image']}'",
            old_image=result["old_image"],
            new_image=result["new_image"],
            finished_at=update_status.now_iso(),
        )
    finally:
        with _worker_lock:
            _worker_running = False


def start_update(plugins: list[Any], target: str, image: str) -> dict[str, Any]:
    """创建更新任务：服务直接后台重建；平台走一次性 helper 容器。

    Raises:
        UpdateBusyError: 已有任务在执行（含平台自更新期间的状态文件）。
        UpdateError: 目标未知或镜像不存在。
    """
    global _worker_running
    current = current_status()
    if current and current.get("status") == "running":
        raise UpdateBusyError("Another update task is still running")

    container_name = (
        PLATFORM_CONTAINER_NAME
        if target == "platform"
        else _service_container(plugins, target)
    )
    if container_name is None:
        raise UpdateError(f"Unknown update target '{target}'")

    client = _client()
    tag_id, _ = _image_ids(client, image)
    if tag_id is None:
        raise UpdateError(f"Image '{image}' not found locally; import a package first")

    if target == "platform":
        return _spawn_self_update(image)

    with _worker_lock:
        if _worker_running:
            raise UpdateBusyError("Another update task is still running")
        _worker_running = True
    thread = threading.Thread(
        target=_run_rebuild,
        args=(target, container_name, image),
        name=f"update-{target}",
        daemon=True,
    )
    thread.start()
    return {
        "target": target,
        "image": image,
        "status": "running",
        "message": "Update started; poll /system/updates/status for progress",
    }


def _service_container(plugins: list[Any], target: str) -> str | None:
    for plugin in plugins:
        if plugin.name == target:
            return str(plugin.manifest["container_name"])
    return None


def _host_path_for(
    client: docker.DockerClient, container_name: str, container_path: str
) -> str:
    """反查某个容器内路径在宿主上的真实来源路径。

    helper 由平台派生，它的 -v 源必须是**宿主路径**：直接传容器内路径（如
    /var/lib/platform）会被 Docker 当成宿主上另一个目录，helper 因此读不到参数文件
    （实机演练踩到）。这里从平台自身容器的 Mounts 反查 Source，查不到就退回原路径。
    """
    try:
        container = client.containers.get(container_name)
    except NotFound, APIError, OSError:
        return container_path
    for mount in (container.attrs or {}).get("Mounts") or []:
        if mount.get("Destination") == container_path and mount.get("Source"):
            return str(mount["Source"])
    logger.warning(
        f"No host source found for '{container_path}' in container "
        f"'{container_name}'; using the path as-is"
    )
    return container_path


def _spawn_self_update(image: str) -> dict[str, Any]:
    """派生一次性 helper 容器来重建平台自身。

    helper 与平台同镜像，只挂 docker.sock 与状态目录（不注入平台环境变量），
    由它 sleep 数秒后执行重建——那时触发接口的响应已经返回前端。
    """
    client = _client()
    platform_container = container_rebuild.get_container(
        client, PLATFORM_CONTAINER_NAME
    )
    platform_image = (platform_container.attrs or {}).get("Config", {}).get(
        "Image"
    ) or image

    try:
        client.containers.get(SELF_UPDATE_CONTAINER_NAME).remove(force=True)
    except NotFound, APIError, OSError:
        pass

    params_file = _volumes_root() / "self-update-params.json"
    params_file.parent.mkdir(parents=True, exist_ok=True)
    params_file.write_text(
        json.dumps(
            {
                "container_name": PLATFORM_CONTAINER_NAME,
                "image": image,
                "status_file": str(status_file_path()),
                "delay_seconds": SELF_UPDATE_DELAY_SECONDS,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    write_status(
        id=str(uuid.uuid4()),
        target="platform",
        image=image,
        phase="pending",
        status="running",
        message="Platform self-update scheduled; the platform will restart shortly",
        started_at=update_status.now_iso(),
    )

    try:
        client.containers.run(
            image=platform_image,
            name=SELF_UPDATE_CONTAINER_NAME,
            detach=True,
            remove=False,
            entrypoint=["python", "/app/backend/scripts/self_update.py"],
            command=[str(params_file)],
            volumes={
                _host_path_for(
                    client, PLATFORM_CONTAINER_NAME, "/var/run/docker.sock"
                ): {
                    "bind": "/var/run/docker.sock",
                    "mode": "rw",
                },
                _host_path_for(client, PLATFORM_CONTAINER_NAME, str(_volumes_root())): {
                    "bind": str(_volumes_root()),
                    "mode": "rw",
                },
            },
            environment={"VOLUMES_MOUNT_ROOT": str(_volumes_root())},
        )
    except (APIError, OSError) as e:
        write_status(
            target="platform",
            image=image,
            phase="failed",
            status="failed",
            message=f"Failed to start self-update helper: {e}",
            finished_at=update_status.now_iso(),
        )
        raise UpdateError(f"Failed to start self-update helper: {e}") from e

    return {
        "target": "platform",
        "image": image,
        "status": "running",
        "message": "Self-update scheduled; the platform will restart in a few seconds",
    }


def _status_age_seconds(status: dict[str, Any]) -> float:
    """状态文件里 updated_at 距现在的秒数；解析失败按「刚更新」处理（不误判失败）。"""
    stamp = status.get("updated_at")
    if not isinstance(stamp, str):
        return 0.0
    try:
        parsed = time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return 0.0
    return max(0.0, time.time() - calendar.timegm(parsed))


def _container_matches_image(
    client: docker.DockerClient, container_name: str, image: str
) -> bool:
    """容器当前所用镜像是否已经是目标 tag 指向的镜像。"""
    if not image:
        return False
    tag_id, _ = _image_ids(client, image)
    if tag_id is None:
        return False
    return _container_state(client, container_name).get("image_id") == tag_id


def _platform_matches_target(
    client: docker.DockerClient, status: dict[str, Any]
) -> bool:
    """平台容器当前所用镜像是否已经是目标 tag 指向的镜像。"""
    return _container_matches_image(
        client, PLATFORM_CONTAINER_NAME, str(status.get("image") or "")
    )


def _helper_failure(status: dict[str, Any]) -> str | None:
    """平台自更新任务是否需要改判失败；需要则返回原因，否则 None。

    helper 起不来（镜像里缺脚本、权限不对等）时它自己没法写状态文件，任务会永远
    停在 running 并挡住后续更新——所以读状态时做一次对账，分三种情形：
    1. helper 仍在运行 → 任务确实在进行；
    2. helper 非零退出 → 用退出码与日志尾部说明原因；
    3. helper 已不在（被清理/正常退出）但平台仍未切到目标镜像 → 超过宽限期判失败。
    """
    client = _client()
    running = False
    exit_code: int | None = None
    logs = ""
    try:
        helper = client.containers.get(SELF_UPDATE_CONTAINER_NAME)
    except NotFound, APIError, OSError:
        helper = None
    if helper is not None:
        state = (helper.attrs or {}).get("State") or {}
        running = bool(state.get("Running"))
        exit_code = state.get("ExitCode")
        try:
            logs = helper.logs(tail=20).decode("utf-8", "replace").strip()
        except APIError, OSError:
            logs = ""
    if running:
        return None
    if exit_code not in (None, 0):
        detail = f" (exit code {exit_code})"
        return (
            f"Self-update helper failed{detail}: {logs}"
            if logs
            else f"Self-update helper failed{detail}"
        )

    # helper 不在了或正常退出：看平台是否真的切到了目标镜像
    if _platform_matches_target(client, status):
        return None  # 已经切过去了，属于成功（只是没记录）
    if _status_age_seconds(status) < UPDATE_GRACE_SECONDS:
        return None  # 宽限期内，等 helper 动手
    return (
        "Self-update helper did not complete and the platform is still running "
        "the previous image"
    )


def _reconcile_service_status(status: dict[str, Any], target: str) -> dict[str, Any]:
    """服务更新的状态对账：线程在跑就继续，否则按镜像是否已切换给出结论。

    服务更新跑在线程里，两种情形会让状态永远停在 running——平台进程重启（线程随之消失）、
    线程内抛出未捕获异常。两者都表现为「没有 worker 在跑」，所以这里用
    「宽限期 + 容器镜像是否已是目标镜像」判定，避免更新能力被静默砖化。
    """
    with _worker_lock:
        in_flight = _worker_running
    if in_flight or _status_age_seconds(status) < UPDATE_GRACE_SECONDS:
        return status

    container_name = _service_container(registry_plugins(), target) or ""
    image = str(status.get("image") or "")
    try:
        client = _client()
        matched = bool(container_name) and _container_matches_image(
            client, container_name, image
        )
    except UpdateError:
        return status  # Docker 不可达时不对账，保持原状态

    if matched:
        return write_status(
            target=target,
            image=image,
            phase="succeeded",
            status="succeeded",
            message=f"Container '{container_name}' already runs the target image",
            finished_at=update_status.now_iso(),
        )
    logger.error(f"Service update for '{target}' did not complete")
    return write_status(
        target=target,
        image=image,
        phase="failed",
        status="failed",
        message=(
            f"Update for '{target}' did not complete (no worker running and the "
            "container still runs the previous image)"
        ),
        finished_at=update_status.now_iso(),
    )


def registry_plugins() -> list[Any]:
    """取当前注册的服务插件（延迟 import，避免模块级依赖 registry/settings）。"""
    from app import registry

    return list(registry.list_services())


def current_status() -> dict[str, Any] | None:
    """读取状态并在必要时对账：helper 已失败的任务改判为 failed 并落盘。"""
    status = read_status()
    if not status or status.get("status") != "running":
        return status
    target = str(status.get("target") or "")
    if target != "platform":
        return _reconcile_service_status(status, target)
    reason = _helper_failure(status)
    if reason is None:
        # helper 正常退出但没来得及写成功状态时，用「平台镜像已是目标镜像」补记，
        # 否则任务会一直显示 running（并挡住后续更新）
        if _platform_matches_target(_client(), status):
            return write_status(
                target="platform",
                image=status.get("image"),
                phase="succeeded",
                status="succeeded",
                message="Platform already runs the target image",
                finished_at=update_status.now_iso(),
            )
        return status
    logger.error(f"Self-update helper failed: {reason}")
    return write_status(
        target="platform",
        image=status.get("image"),
        phase="failed",
        status="failed",
        message=reason,
        finished_at=update_status.now_iso(),
    )
