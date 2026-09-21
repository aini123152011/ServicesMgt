"""容器重建：用新镜像按旧容器的 run 参数重建同名容器，失败自动回滚。

为什么单独成模块：平台自更新的 helper 容器也要复用这套逻辑，而 helper 只挂
docker.sock 与状态目录、不注入平台环境变量，所以这里**不能**依赖 settings。

重建为什么必须复现 run 参数：阶段 3 实机验证时逐个踩过——bind 挂载、被动端口段、
`privileged`、`cap_add` 少任何一个，服务要么起不来要么功能残缺（例如 nfs-ganesha
少了 privileged 就加载不了导出）。因此这里从旧容器 inspect 结果反推参数，而不是
按 compose 文件重建。
"""

import logging
import time
from typing import Any, Protocol

import docker
from docker.errors import APIError, DockerException, NotFound
from docker.models.containers import Container

logger = logging.getLogger(__name__)

# 重建后等待容器健康的秒数：够慢启动服务（ganesha/rsyslog 这类）完成初始化
HEALTH_TIMEOUT_SECONDS = 60

# 由镜像本身携带的环境变量，重建时不从旧容器继承。
# PLATFORM_VERSION/PLATFORM_BUILD 是构建期注入的版本元数据：若照抄旧容器的值，
# 更新后新容器会继续上报旧版本号，更新等于白做（实机演练踩到）。
IMAGE_OWNED_ENV_KEYS = frozenset({"PLATFORM_VERSION", "PLATFORM_BUILD"})


class RebuildError(Exception):
    """重建失败（消息含原始原因，路由层转 502）。"""


class _HasAttrs(Protocol):
    """只要求 attrs 的容器形状：extract_run_params 只用 inspect 结果，
    收敛成 Protocol 后单测可以用替身喂 attrs，不必真的连 docker。"""

    # 只读属性：可变属性在 Protocol 里是不变的，写成 property 才能同时接受
    # docker 的 Container.attrs 与单测替身
    @property
    def attrs(self) -> dict[str, Any] | None: ...


def get_client() -> docker.DockerClient:
    """惰性连接 Docker daemon，失败转 RebuildError。"""
    try:
        return docker.from_env()
    except DockerException as e:  # pragma: no cover - 环境相关
        raise RebuildError(f"Failed to connect to Docker daemon: {e}") from e


def get_container(client: docker.DockerClient, name: str) -> Container:
    """按名取容器，不存在转 RebuildError。"""
    try:
        return client.containers.get(name)
    except NotFound as e:
        raise RebuildError(f"Container '{name}' not found") from e
    except (APIError, OSError) as e:
        raise RebuildError(f"Failed to inspect container '{name}': {e}") from e


def _split_bind(bind: str) -> tuple[str, str, str]:
    """拆 `host:container[:mode]`；容器路径含冒号的极端情况不在本平台出现。"""
    parts = bind.split(":")
    host, container_path = parts[0], parts[1]
    mode = parts[2] if len(parts) > 2 else "rw"
    return host, container_path, mode


def extract_run_params(container: _HasAttrs) -> dict[str, Any]:
    """从旧容器提取重建所需的 run 参数（docker-py 的 containers.run kwargs）。

    Returns:
        含 volumes/ports/environment/network/restart_policy/cap_add/privileged/
        entrypoint/command 的子集；未设置的项不出现在返回值里。
    """
    attrs = container.attrs or {}
    host_config = attrs.get("HostConfig") or {}
    config = attrs.get("Config") or {}
    params: dict[str, Any] = {}

    binds = host_config.get("Binds") or []
    if binds:
        volumes: dict[str, dict[str, str]] = {}
        for bind in binds:
            host, container_path, mode = _split_bind(bind)
            volumes[host] = {"bind": container_path, "mode": mode}
        params["volumes"] = volumes

    port_bindings = host_config.get("PortBindings") or {}
    if port_bindings:
        ports: dict[str, Any] = {}
        for container_port, bindings in port_bindings.items():
            mapped = [
                (binding.get("HostIp") or "", int(binding["HostPort"]))
                for binding in (bindings or [])
                if binding.get("HostPort")
            ]
            if mapped:
                ports[container_port] = mapped
        if ports:
            params["ports"] = ports

    env_list = config.get("Env") or []
    if env_list:
        params["environment"] = {
            key: value
            for item in env_list
            if "=" in item
            for key, value in [item.split("=", 1)]
            # 版本元数据跟随新镜像，不从旧容器继承（见 IMAGE_OWNED_ENV_KEYS）
            if key not in IMAGE_OWNED_ENV_KEYS
        }

    networks = list((attrs.get("NetworkSettings") or {}).get("Networks") or {})
    # 只指定非默认网络：默认 bridge 无需显式传，传了反而可能因网络不存在失败
    if networks and networks != ["bridge"]:
        params["network"] = networks[0]

    restart = (host_config.get("RestartPolicy") or {}).get("Name")
    if restart:
        params["restart_policy"] = {"Name": restart}

    cap_add = host_config.get("CapAdd") or []
    if cap_add:
        params["cap_add"] = cap_add

    if host_config.get("Privileged"):
        params["privileged"] = True

    if config.get("Entrypoint"):
        params["entrypoint"] = config["Entrypoint"]
    if config.get("Cmd"):
        params["command"] = config["Cmd"]

    return params


def _wait_healthy(client: docker.DockerClient, name: str, timeout: int) -> bool:
    """轮询容器状态：有健康检查则等 healthy，无则等 running 稳定几秒。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            container = client.containers.get(name)
            state = (container.attrs or {}).get("State") or {}
            health = (state.get("Health") or {}).get("Status")
            if health == "healthy":
                return True
            if health == "unhealthy":
                return False
            if state.get("Running") and health is None:
                # 没有健康检查的服务：容器起来后留一点时间，避免刚起就判定成功
                time.sleep(3)
                return bool(
                    ((client.containers.get(name).attrs or {}).get("State") or {}).get(
                        "Running"
                    )
                )
        except NotFound, APIError, OSError:
            pass
        time.sleep(2)
    return False


def rebuild_container(
    container_name: str,
    new_image: str,
    *,
    health_timeout: int = HEALTH_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """用新镜像重建容器；健康检查失败则回滚到旧容器。

    流程：inspect 旧容器 → 旧容器改名（不删，留作回滚）→ 用原参数跑新容器 →
    健康检查 → 成功删除旧容器（保留旧镜像）/ 失败删新容器并把旧容器改回原名启动。

    Args:
        container_name: 目标容器名（服务容器或平台自身容器）。
        new_image: 新镜像引用（tag 或 ID）。
        health_timeout: 等待健康的秒数。

    Returns:
        {"old_image": 旧镜像引用, "new_image": 新镜像引用, "backup_name": 旧容器改名后的名字}

    Raises:
        RebuildError: 取容器/启动新容器失败，或健康检查失败并已回滚。
    """
    client = get_client()
    old = get_container(client, container_name)
    old_image = (old.attrs or {}).get("Config", {}).get("Image", "")
    params = extract_run_params(old)
    backup_name = f"{container_name}.old-{int(time.time())}"

    try:
        old.rename(backup_name)
        old.stop(timeout=20)
    except (APIError, OSError) as e:
        # 改名/停止失败：尽量把名字改回去，保持原状
        with_client_restore = client.containers.get(backup_name)
        with_client_restore.rename(container_name)
        raise RebuildError(f"Failed to stop container '{container_name}': {e}") from e

    try:
        client.containers.run(
            image=new_image, name=container_name, detach=True, **params
        )
    except (APIError, OSError) as e:
        _rollback(client, container_name, backup_name)
        raise RebuildError(
            f"Failed to start container '{container_name}' with image '{new_image}': {e}"
        ) from e

    if _wait_healthy(client, container_name, health_timeout):
        try:
            client.containers.get(backup_name).remove(force=True)
        except (APIError, OSError) as e:  # 旧容器删不掉不影响更新结果，仅记日志
            logger.warning(f"Failed to remove backup container '{backup_name}': {e}")
        return {
            "old_image": old_image,
            "new_image": new_image,
            "backup_name": backup_name,
        }

    _rollback(client, container_name, backup_name)
    raise RebuildError(
        f"Container '{container_name}' did not become healthy with image "
        f"'{new_image}'; rolled back to '{old_image}'"
    )


def _rollback(
    client: docker.DockerClient, container_name: str, backup_name: str
) -> None:
    """删除未通过健康检查的新容器，把旧容器改回原名并启动。"""
    try:
        client.containers.get(container_name).remove(force=True)
    except (NotFound, APIError, OSError) as e:  # pragma: no cover - 极端情况
        logger.error(f"Failed to remove failed container '{container_name}': {e}")
    try:
        old = client.containers.get(backup_name)
        old.rename(container_name)
        old.start()
        logger.info(f"Rolled back container '{container_name}' to previous image")
    except (NotFound, APIError, OSError) as e:  # pragma: no cover - 需人工介入
        logger.error(
            f"Failed to roll back container '{container_name}' from '{backup_name}': {e}"
        )
