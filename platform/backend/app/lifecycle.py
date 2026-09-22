"""生命周期管理：封装 Docker SDK，实现状态查询、启停重启、exec reload 与日志拉取。

平台通过挂载的 docker.sock 管理同宿主机上的服务容器；所有 Docker API 异常
统一转为带原始异常信息的 LifecycleError，由路由层转 502。容器不存在视为
"未部署"（running=False / 空日志）而非错误，因为容器的创建由各服务自己的
docker compose 负责，平台只管理已存在的容器。
"""

import logging
import time
from typing import Any

import docker
from docker.errors import APIError, DockerException, NotFound
from docker.models.containers import Container

logger = logging.getLogger(__name__)

# 容器重启窗口的容忍参数，见 exec_reload 的说明：10 次 × 3s ≈ 30s 的等待预算，
# 覆盖实测到的重启窗口（Docker 重启退避随 RestartCount 增长，上限 1 分钟；
# 实测反复切换故障模式后单次窗口约 30s）。真正的等待只有前 9 次，最后一次直接报错。
RELOAD_RETRY_ATTEMPTS = 10
RELOAD_RETRY_INTERVAL_SECONDS = 3.0


class LifecycleError(Exception):
    """Docker 操作失败（消息包含原始异常信息，路由层转 502）。"""


_client: docker.DockerClient | None = None


def _get_client() -> docker.DockerClient:
    """惰性初始化并复用 Docker 客户端；连接失败转为 LifecycleError。"""
    global _client
    if _client is None:
        try:
            _client = docker.from_env()
        except DockerException as e:
            raise LifecycleError(f"Failed to connect to Docker daemon: {e}") from e
    return _client


def _get_container(container_name: str, action: str) -> Container:
    """按名取容器，不存在或 API 失败时转为带上下文的 LifecycleError。

    Args:
        container_name: 容器名（manifest.container_name）。
        action: 操作描述词，仅用于错误消息（如 "inspect" / "start"）。
    """
    try:
        return _get_client().containers.get(container_name)
    except NotFound as e:
        raise LifecycleError(
            f"Container '{container_name}' not found; deploy the service with docker compose first"
        ) from e
    except APIError as e:
        raise LifecycleError(
            f"Failed to {action} container '{container_name}': {e}"
        ) from e
    except OSError as e:
        # docker SDK 底层连接错误（requests.ConnectionError 等）派生自 OSError
        raise LifecycleError(
            f"Failed to {action} container '{container_name}': {e}"
        ) from e


def get_status(container_name: str) -> dict[str, Any]:
    """查询容器运行状态与健康检查结果。

    Args:
        container_name: 容器名，来自 manifest.container_name。

    Returns:
        {"running": 是否运行中, "health": 健康状态("healthy"/"unhealthy"/...)或 None,
        "status": Docker 原生状态字符串或 None}；容器不存在时 running=False。

    Raises:
        LifecycleError: Docker daemon 不可达或 API 调用失败。
    """
    container = _get_container(container_name, "inspect")
    attrs = container.attrs or {}
    state = attrs.get("State") or {}
    health = (state.get("Health") or {}).get("Status")
    return {
        "running": bool(state.get("Running")),
        "health": health,
        "status": state.get("Status"),
    }


def _run_container_action(container_name: str, action: str) -> None:
    """对容器执行 start/stop/restart 同名方法，失败转为 LifecycleError。"""
    container = _get_container(container_name, action)
    logger.info(f"{action.capitalize()}ing container '{container_name}'")
    try:
        getattr(container, action)()
    except APIError as e:
        raise LifecycleError(
            f"Failed to {action} container '{container_name}': {e}"
        ) from e
    except OSError as e:
        raise LifecycleError(
            f"Failed to {action} container '{container_name}': {e}"
        ) from e


def start(container_name: str) -> None:
    """启动已存在的服务容器。

    Raises:
        LifecycleError: 容器不存在或启动失败。
    """
    _run_container_action(container_name, "start")


def stop(container_name: str) -> None:
    """停止运行中的服务容器。

    Raises:
        LifecycleError: 容器不存在或停止失败。
    """
    _run_container_action(container_name, "stop")


def restart(container_name: str) -> None:
    """重启服务容器。

    Raises:
        LifecycleError: 容器不存在或重启失败。
    """
    _run_container_action(container_name, "restart")


def _is_restarting_conflict(error: APIError) -> bool:
    """判断 Docker 的 409 是否只是「容器正在重启 / 尚未回到运行态」。

    重启期间 docker exec 会被拒绝（409 Conflict / "is restarting, wait until the
    container is running"；退避等待期间则是 "is not running"）。这类拒绝不代表 reload
    失败——容器重启本身就是某些服务让配置生效的方式（如 chrony 的 faketime 偏移只能随
    进程启动注入），此时配置已落盘并正在生效，等容器回来重试即可。

    文案来源不止一处：docker SDK 的 APIError.__str__ 依赖 response 细节，某些路径下
    只剩 args，因此把 explanation 与 args 一起纳入判断。
    """
    response = getattr(error, "response", None)
    if getattr(response, "status_code", None) != 409:
        return False
    text = " ".join(
        [
            str(error),
            str(getattr(error, "explanation", "") or ""),
            *(str(arg) for arg in error.args),
        ]
    ).lower()
    return "restarting" in text or "is not running" in text


def exec_reload(manifest: dict[str, Any]) -> str:
    """在容器内执行 /reload.sh 触发新配置生效（平台统一契约）。

    容器重启窗口内会等待并重试：reload 期间容器可能因配置生效而重启（进程无法热加载
    的配置项只能重启进程），Docker 此时以 409 拒绝 exec。若不重试，平台会把「正在生效」
    报成下发失败，调用方看到 502 却其实已经生效（实测：回滚 chrony 的 faketime 偏移时
    接口报 502，版本未记录，而 NTP 应答已经变了）。

    Args:
        manifest: 服务 manifest 字典，用 container_name 定位容器。

    Returns:
        reload 脚本的成功输出文本（记日志/排障用）。

    Raises:
        LifecycleError: 容器不存在、exec 失败或脚本非零退出码（附脚本输出）。
    """
    container_name = manifest["container_name"]
    container = _get_container(container_name, "exec /reload.sh in")
    for attempt in range(1, RELOAD_RETRY_ATTEMPTS + 1):
        try:
            result = container.exec_run("/reload.sh")
        except (APIError, OSError) as e:
            if isinstance(e, APIError) and _is_restarting_conflict(e):
                if attempt < RELOAD_RETRY_ATTEMPTS:
                    logger.info(
                        f"Container '{container_name}' is restarting (reload attempt "
                        f"{attempt}/{RELOAD_RETRY_ATTEMPTS}); waiting "
                        f"{RELOAD_RETRY_INTERVAL_SECONDS}s and retrying"
                    )
                    time.sleep(RELOAD_RETRY_INTERVAL_SECONDS)
                    continue
                # 预算用尽：落到循环外的统一报错，不再区分「还在重启」与「其他失败」
                logger.error(
                    f"Reload failed for container '{container_name}': still restarting "
                    f"after {RELOAD_RETRY_ATTEMPTS} attempts: {e}"
                )
                break
            logger.error(f"Reload failed for container '{container_name}': {e}")
            raise LifecycleError(
                f"Failed to exec '/reload.sh' in container '{container_name}': {e}"
            ) from e
        output = result.output
        text = (
            output.decode("utf-8", errors="replace")
            if isinstance(output, bytes)
            else str(output or "")
        )
        exit_code = result.exit_code
        if exit_code is None or exit_code != 0:
            # None 表示 exec 建立阶段就失败；非零码是脚本自身的失败信号
            logger.error(
                f"Reload failed for container '{container_name}': exit code {exit_code}, output: {text.strip()}"
            )
            raise LifecycleError(
                f"/reload.sh in container '{container_name}' exited with code {exit_code}: {text.strip()}"
            )
        logger.info(f"Reloaded container '{container_name}' via /reload.sh")
        return text
    # 只有「预算用尽仍在重启」会走到这里（其他失败都在循环内 raise）
    raise LifecycleError(
        f"Container '{container_name}' kept restarting; gave up after "
        f"{RELOAD_RETRY_ATTEMPTS} reload attempts"
    )


def get_logs(container_name: str, tail: int) -> str:
    """拉取容器最近的日志文本。

    Args:
        container_name: 容器名，来自 manifest.container_name。
        tail: 拉取的末尾行数，路由层已做上限裁剪。

    Returns:
        日志文本（UTF-8 解码，非法字节替换处理）；容器不存在时返回空字符串。

    Raises:
        LifecycleError: Docker daemon 不可达或 API 调用失败。
    """
    try:
        container = _get_client().containers.get(container_name)
    except NotFound:
        # 与 get_status 口径一致：未部署的服务没有日志，返回空串而非报错
        return ""
    except (APIError, OSError) as e:
        raise LifecycleError(
            f"Failed to read logs of container '{container_name}': {e}"
        ) from e
    try:
        raw = container.logs(tail=tail)
    except (APIError, OSError) as e:
        raise LifecycleError(
            f"Failed to read logs of container '{container_name}': {e}"
        ) from e
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
