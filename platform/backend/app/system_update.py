"""版本信息、更新检查与更新任务编排。

更新来源两种（见任务 design.md §4.2）：
1. **离线包**（主路径，适配当前实验室）：上传镜像 tar → `docker load` → 同名 tag 指向
   新镜像 → 「检查更新」发现容器所用镜像 ID 与 tag 当前 ID 不一致 → 一键重建容器。
2. **镜像仓库**（可选）：配置 `UPDATE_REGISTRY` 后，检查时先 `docker pull` 再比较。

平台自更新不能删除自己（进程会被杀）：把参数写成文件，派生一次性 helper 容器
（同镜像 + docker.sock + 状态目录）由它执行重建，见 `scripts/self_update.py`。
任务状态写文件而不是内存：平台自更新会重启进程，内存态会丢，写文件后重启仍可查。
"""

import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

from app import container_rebuild, update_status
from app.core.config import settings

logger = logging.getLogger(__name__)

PLATFORM_CONTAINER_NAME = "bmc-platform-backend"
SELF_UPDATE_CONTAINER_NAME = "bmc-platform-selfupdate"
UPLOADS_DIRNAME = "uploads"
STATUS_FILENAME = "system-update.json"
# 平台自更新的 helper 等待时间：先让触发它的 API 把响应返回给前端
SELF_UPDATE_DELAY_SECONDS = 3

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
    except (APIError, OSError) as e:
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
    except container_rebuild.RebuildError as e:
        logger.error(f"Update failed for '{target}': {e}")
        write_status(
            target=target,
            image=image,
            phase="failed",
            status="failed",
            message=str(e),
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
    current = read_status()
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
                "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
                str(_volumes_root()): {"bind": str(_volumes_root()), "mode": "rw"},
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
