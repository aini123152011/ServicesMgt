"""二层夹具（macvlan 绑定）的启用 / 停用 / 切换。

与 `/system/host-network` 的分工：那边只读呈现与校验，这里做**会改数据面**的写操作。
写入面有三处，顺序固定（见 `apply_l2_config`）：部署目录 `.env`（权威）→ Docker 网络（它的投影）
→ 可选的 dhcp 服务配置联动；任一步失败都按**逆序**回滚，回滚结果与步骤日志一并回给页面。

失败语义：
- 参数非法、预检阻断 → 400（**不做任何变更**，detail 里给稳定的 check code）；
- 执行中途失败 → 200 + `applied=false`，带上步骤与 `rolled_back`——页面需要「改到哪一步、
  有没有退回去」这两项才能正确提示，单给一个 502 反而丢信息。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app import host_network, l2_config, l2_network, lifecycle, registry
from app.api.deps import CurrentUser, RequireAdmin, SessionDep, get_current_user
from app.api.routes.services import apply_config_values
from app.core.config import settings
from app.crud import get_service_config, record_audit_log
from app.models import (
    HostNetworkCheck,
    L2ApplyResult,
    L2CandidateInterface,
    L2ConfigUpdate,
    L2PreflightResult,
    L2ServiceConfigChange,
    L2State,
)

router = APIRouter(prefix="/l2", tags=["l2"], dependencies=[Depends(get_current_user)])

logger = logging.getLogger(__name__)

DHCP_SERVICE = "dhcp"
DHCP_CONTAINER = "bmc-dhcp"

# 执行路径上会遇到的异常：Docker 网络操作、.env 读写、容器重启、服务配置下发（502）
_EXEC_ERRORS = (
    l2_network.L2NetworkError,
    l2_config.L2EnvError,
    lifecycle.LifecycleError,
    HTTPException,
)

# 变更路径上额外阻断的 warn 级结论：状态展示里它们只是提示（配置已存在，先别打断用户），
# 但「现在要把这个口选作父口」时它们意味着一定会出事，必须拦住。
# - parent_has_default_route：父口承载宿主默认路由，测试网段波动会波及管理通道与平台访问
# - parent_no_carrier：父口没有链路，BMC 收不到任何广播、取不到地址
BLOCKING_WARN_CODES = frozenset({"parent_has_default_route", "parent_no_carrier"})


@dataclass
class _Snapshot:
    """变更前状态快照：回滚按逆序恢复这三样。

    Attributes:
        env_values: 改前 `.env` 里的 L2 键值（只含白名单键，不含密钥）。
        network: 改前的网络与容器连接状态。
        dhcp_values: 改前 dhcp 服务配置值；None 表示当时没有配置记录。
    """

    env_values: dict[str, str]
    network: l2_network.L2NetworkState
    dhcp_values: dict[str, Any] | None


def _env_values_or_502() -> tuple[dict[str, str], list[str]]:
    """读 `.env` 的 L2 值；读不到直接 502（写操作不能在没有权威源时瞎猜）。"""
    try:
        parsed = l2_config.read_l2_env(settings.env_file_path)
    except l2_config.L2EnvError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return parsed.values, parsed.duplicates


def _env_values_degraded() -> tuple[dict[str, str], list[str], str]:
    """只读场景用的宽松读法：读不到就用容器环境变量兜底，并把原因带出去给页面。"""
    try:
        parsed = l2_config.read_l2_env(settings.env_file_path)
    except l2_config.L2EnvError as e:
        logger.warning(f"L2 env file unavailable, falling back to container env: {e}")
        return (
            {
                "DHCP_PARENT_IFACE": settings.DHCP_PARENT_IFACE,
                "L2_SUBNET": settings.L2_SUBNET,
                "L2_SERVICES": settings.L2_SERVICES,
            },
            [],
            str(e),
        )
    return parsed.values, parsed.duplicates, ""


def _dhcp_values(session: Session) -> dict[str, Any] | None:
    """读 dhcp 服务当前配置值（校验与联动推导都要用）。"""
    config = get_service_config(session=session, service_name=DHCP_SERVICE)
    return dict(config.values) if config and config.values else None


def _network_state() -> l2_network.L2NetworkState | None:
    """读容器当前的 macvlan 绑定；Docker 不可用时返回 None（只读场景不报错）。"""
    try:
        return l2_network.read_state(l2_network.get_client())
    except l2_network.L2NetworkError as e:
        logger.warning(f"Docker unavailable while reading L2 state: {e}")
        return None


def _network_state_or_502() -> l2_network.L2NetworkState:
    """写操作前的状态快照；Docker 不可用时 502。

    写路径不能拿「读不到状态」当「没有网络」——那会让回滚走错分支（把本该恢复的网络删掉）。
    """
    state = _network_state()
    if state is None:
        raise HTTPException(
            status_code=502,
            detail="Docker unavailable: cannot read current L2 network state",
        )
    return state


def _candidates(
    interfaces: list[dict[str, Any]], default_iface: str, parent: str
) -> list[L2CandidateInterface]:
    """列出可作为父口的候选网口。

    可选判据：有链路，且不承载宿主默认路由（后者一旦选错会连带影响管理通道与平台访问）。
    当前父口即使暂时不可选也会列出来并标注原因——否则用户看不到自己配的是哪个口。
    """
    out: list[L2CandidateInterface] = []
    for iface in interfaces:
        addresses = [
            str(item.get("address"))
            for item in (iface.get("ipv4") or []) + (iface.get("ipv6") or [])
            if item.get("address")
        ]
        carrier = iface.get("carrier")
        selectable = True
        reason = ""
        if carrier == 0:
            selectable, reason = (
                False,
                "该网口没有链路（carrier=0）：没插线或对端未上电。",
            )
        elif carrier is None:
            selectable, reason = (
                False,
                "读不到该网口的链路状态（/sys 未挂载或网口不支持）。",
            )
        elif default_iface and iface["name"] == default_iface:
            selectable = False
            reason = "该网口承载宿主默认路由：测试网段波动会影响管理通道与平台访问。"
        out.append(
            L2CandidateInterface(
                name=str(iface["name"]),
                carrier=carrier if isinstance(carrier, int) else None,
                speed_mbps=iface.get("speed_mbps"),
                mac=iface.get("mac"),
                addresses=addresses,
                is_parent=iface["name"] == parent,
                selectable=selectable,
                reason=reason,
            )
        )
    return out


def _drift(
    env_values: dict[str, str], state: l2_network.L2NetworkState | None
) -> list[str]:
    """列出 `.env`（权威）与 Docker 实际值的不一致项。"""
    if state is None:
        return []
    drift: list[str] = []
    parent = env_values.get("DHCP_PARENT_IFACE", "").strip()
    if not state.exists:
        if parent:
            drift.append(
                f".env 里配了父口 {parent}，但 macvlan 网络 {state.network} 不存在"
                "（执行一次「应用」即可创建）。"
            )
        return drift
    if state.parent != parent:
        drift.append(
            f".env 的父口是 {parent or '（空）'}，实际网络挂在 {state.parent}（以 .env 为准）。"
        )
    if (
        env_values.get("L2_SUBNET", "").strip()
        and state.subnet != env_values.get("L2_SUBNET", "").strip()
    ):
        drift.append(
            f".env 的网段是 {env_values.get('L2_SUBNET')}，实际网络是 {state.subnet}。"
        )
    if (
        env_values.get("L2_SUBNET_V6", "").strip()
        and state.subnet_v6 != env_values.get("L2_SUBNET_V6", "").strip()
    ):
        drift.append(
            f".env 的 IPv6 网段是 {env_values.get('L2_SUBNET_V6')}，实际网络是 {state.subnet_v6}。"
        )
    return drift


def _evaluate(
    *,
    env_values: dict[str, str],
    dhcp_values: dict[str, Any] | None,
    facts: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """用给定参数跑一遍校验（纯函数），供状态展示与预检共用。"""
    resolved = facts if facts is not None else host_network.host_facts()
    services = {
        item.strip()
        for item in env_values.get("L2_SERVICES", "").split(",")
        if item.strip()
    }
    return host_network.evaluate_checks(
        interfaces=resolved["interfaces"],
        bindings=host_network.service_bindings([DHCP_SERVICE]),
        dhcp_values=dhcp_values,
        parent=env_values.get("DHCP_PARENT_IFACE", "").strip(),
        l2_subnet=env_values.get("L2_SUBNET", "").strip(),
        l2_services=services,
        l2_subnet_v6=env_values.get("L2_SUBNET_V6", "").strip(),
        default_iface=resolved["default_iface"],
    )


def _validated(config_in: L2ConfigUpdate) -> dict[str, str]:
    """校验请求体并归一化；非法时 400。"""
    try:
        return l2_config.validate_params(
            {
                "DHCP_PARENT_IFACE": config_in.parent_iface,
                "L2_SUBNET": config_in.l2_subnet,
                "L2_GATEWAY": config_in.l2_gateway,
                "L2_SUBNET_V6": config_in.l2_subnet_v6,
                "L2_GATEWAY_V6": config_in.l2_gateway_v6,
            }
        )
    except l2_config.L2ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _blocking(
    params: dict[str, str],
    dhcp_values: dict[str, Any] | None,
    *,
    sync_service_config: bool,
    l2_services: str,
) -> list[dict[str, Any]]:
    """用目标参数预检，返回阻断级结论。

    预检要拿「改完之后」的 dhcp 配置去校验：池子/RA 前缀是否落在新网段内取决于联动后的值，
    否则每次都会误报 pool_outside_parent_subnet。**用户关掉联动时不能按「已同步」算**——
    那种情况下池子确实会落在网段外，必须如实报出来。

    Args:
        params: 已归一化的目标 L2 参数。
        dhcp_values: dhcp 当前配置值；None 表示取不到。
        sync_service_config: 用户是否勾选了同步 dhcp 服务配置。
        l2_services: 当前的 L2 服务清单，原样带过去（否则相关校验会被跳过）。

    Returns:
        阻断级结论列表；空列表表示可以执行。
    """
    merged = dict(dhcp_values or {})
    if sync_service_config:
        merged.update(l2_config.derive_dhcp_values(dhcp_values or {}, params))
    checks = _evaluate(
        env_values={**params, "L2_SERVICES": l2_services}, dhcp_values=merged
    )
    return [
        item
        for item in checks
        if item["level"] == "error" or item["code"] in BLOCKING_WARN_CODES
    ]


def _network_matches(state: l2_network.L2NetworkState, params: dict[str, str]) -> bool:
    """当前网络与容器连接是否已等于目标参数（幂等判定的 Docker 侧）。"""
    return (
        state.exists
        and state.attached
        and state.parent == params["DHCP_PARENT_IFACE"]
        and state.subnet == params["L2_SUBNET"]
        and state.gateway == params["L2_GATEWAY"]
        and state.subnet_v6 == params["L2_SUBNET_V6"]
        and state.gateway_v6 == params["L2_GATEWAY_V6"]
    )


def _planned_steps(
    params: dict[str, str],
    service_changes: dict[str, Any],
    subnet_changed: bool = False,
) -> list[str]:
    """将要执行的步骤（供确认弹窗展示，与实际执行顺序一致）。

    Args:
        params: 已归一化的目标 L2 参数。
        service_changes: 要联动的 dhcp 服务配置差异。
        subnet_changed: 网段是否变化（变化时会把旧租约归档，BMC 需重新取址）。

    Returns:
        人可读的步骤列表。
    """
    steps = [
        f"改写部署目录 .env：parent={params['DHCP_PARENT_IFACE']}、"
        f"{params['L2_SUBNET']}（网关 {params['L2_GATEWAY']}）、{params['L2_SUBNET_V6']}",
        f"重建 macvlan 网络并重连 {DHCP_CONTAINER} 容器（网络参数创建后不可改，只能重建）",
        f"重启 {DHCP_CONTAINER}，让 dnsmasq 重新绑定新接口",
    ]
    if service_changes:
        steps.append(f"同步 dhcp 服务配置：{'、'.join(sorted(service_changes))}")
    if subnet_changed:
        steps.append("归档 dnsmasq 旧租约（BMC 需重新取址：等续租失败或拔插一次网线）")
    steps.append("重新校验，要求无 error 级结论")
    return steps


@router.get("/status", response_model=L2State)
def read_l2_status(session: SessionDep) -> Any:
    """当前二层夹具状态：`.env` 权威值、Docker 实际值、候选网口、校验结论与命令。

    Args:
        session: 数据库会话，用于读 dhcp 当前配置值参与校验。

    Returns:
        L2State：只读快照，不产生任何变更；`.env` 读不到时降级用容器环境变量并带出原因。
    """
    env_values, duplicates, env_error = _env_values_degraded()
    facts = host_network.host_facts()
    dhcp_values = _dhcp_values(session)
    state = _network_state()
    parent = env_values.get("DHCP_PARENT_IFACE", "").strip()
    gateway = env_values.get("L2_GATEWAY", "").strip()
    subnet = env_values.get("L2_SUBNET", "").strip()
    return L2State(
        enabled=bool(parent),
        env_path=str(settings.env_file_path),
        env_available=not env_error,
        env_error=env_error,
        parent_iface=parent,
        l2_subnet=subnet,
        l2_gateway=gateway,
        l2_subnet_v6=env_values.get("L2_SUBNET_V6", "").strip(),
        l2_gateway_v6=env_values.get("L2_GATEWAY_V6", "").strip(),
        l2_services=sorted(
            item.strip()
            for item in env_values.get("L2_SERVICES", "").split(",")
            if item.strip()
        ),
        duplicate_keys=duplicates,
        network=state.network if state else None,
        network_exists=bool(state and state.exists),
        network_parent=state.parent if state else None,
        attached=bool(state and state.attached),
        address=state.address if state else None,
        address_v6=state.address_v6 if state else None,
        drift=_drift(env_values, state),
        checks=[
            HostNetworkCheck(**item)
            for item in _evaluate(
                env_values=env_values, dhcp_values=dhcp_values, facts=facts
            )
        ],
        candidates=_candidates(facts["interfaces"], facts["default_iface"], parent),
        nmcli_commands=(
            l2_config.nmcli_commands(
                parent,
                gateway,
                subnet,
                env_values.get("L2_GATEWAY_V6", "").strip(),
                env_values.get("L2_SUBNET_V6", "").strip(),
            )
            if parent and gateway and subnet
            else []
        ),
        default_iface=facts["default_iface"],
    )


@router.post(
    "/preflight",
    dependencies=[Depends(RequireAdmin)],
    response_model=L2PreflightResult,
)
def preflight_l2_config(session: SessionDep, config_in: L2ConfigUpdate) -> Any:
    """干跑一次变更：返回将执行的步骤、阻断项与要联动的 dhcp 配置差异（不产生任何变更）。

    Args:
        session: 数据库会话。
        config_in: 目标 L2 参数。

    Returns:
        L2PreflightResult：`ok=false` 表示存在阻断项，页面据此禁用执行按钮。

    Raises:
        HTTPException: 400 参数非法（detail 为英文短句）。
    """
    params = _validated(config_in)
    dhcp_values = _dhcp_values(session)
    service_changes = (
        l2_config.derive_dhcp_values(dhcp_values or {}, params)
        if config_in.sync_service_config
        else {}
    )
    env_values, _ = _env_values_or_502()
    l2_services = env_values.get("L2_SERVICES", "")
    subnet_changed = (
        env_values.get("L2_SUBNET", "").strip() != params["L2_SUBNET"]
        or env_values.get("L2_SUBNET_V6", "").strip() != params["L2_SUBNET_V6"]
    )
    blocking = _blocking(
        params,
        dhcp_values,
        sync_service_config=config_in.sync_service_config,
        l2_services=l2_services,
    )
    merged = dict(dhcp_values or {})
    merged.update(service_changes)
    checks = _evaluate(
        env_values={**params, "L2_SERVICES": l2_services}, dhcp_values=merged
    )
    return L2PreflightResult(
        ok=not blocking,
        blocking=[HostNetworkCheck(**item) for item in blocking],
        checks=[HostNetworkCheck(**item) for item in checks],
        steps=_planned_steps(params, service_changes, subnet_changed),
        service_config={key: str(value) for key, value in service_changes.items()},
        nmcli_commands=l2_config.nmcli_commands(
            params["DHCP_PARENT_IFACE"],
            params["L2_GATEWAY"],
            params["L2_SUBNET"],
            params["L2_GATEWAY_V6"],
            params["L2_SUBNET_V6"],
        ),
    )


@router.put(
    "/config",
    dependencies=[Depends(RequireAdmin)],
    response_model=L2ApplyResult,
)
def apply_l2_config(
    session: SessionDep, current_user: CurrentUser, config_in: L2ConfigUpdate
) -> Any:
    """启用或切换二层夹具：写 `.env` → 重建网络 → 重启 dhcp → 可选联动服务配置。

    任一步失败都按逆序回滚（服务配置 → 网络 → `.env`），并把步骤与是否已回滚回给页面。

    Args:
        session: 数据库会话（联动要写配置版本与审计）。
        current_user: 操作者，用于审计归属。
        config_in: 目标 L2 参数与是否联动 dhcp 服务配置。

    Returns:
        L2ApplyResult：`applied=false` 时 `rolled_back` 说明是否已退回变更前状态。

    Raises:
        HTTPException: 400 参数非法或被预检阻断（此时零变更）；502 `.env` 读不到。
    """
    params = _validated(config_in)
    env_values, _ = _env_values_or_502()
    previous_parent = env_values.get("DHCP_PARENT_IFACE", "").strip()
    action = "l2.update" if previous_parent else "l2.enable"

    l2_services = env_values.get("L2_SERVICES", "")
    dhcp_values = _dhcp_values(session)
    blocking = _blocking(
        params,
        dhcp_values,
        sync_service_config=config_in.sync_service_config,
        l2_services=l2_services,
    )
    if blocking:
        codes = ", ".join(str(item["code"]) for item in blocking)
        raise HTTPException(
            status_code=400, detail=f"Blocked by preflight checks: {codes}"
        )

    service_changes: dict[str, Any] = {}
    if config_in.sync_service_config and dhcp_values:
        service_changes = l2_config.derive_dhcp_values(dhcp_values, params)

    # 幂等：.env、网络与要联动的服务配置都已一致时不碰任何东西——提交相同参数不该
    # 白白重启一次 dnsmasq（那会让 BMC 侧短暂取不到地址）
    if not service_changes and l2_config.matches_env(env_values, params):
        if _network_matches(_network_state_or_502(), params):
            return L2ApplyResult(
                applied=True,
                rolled_back=False,
                steps=["无需变更：.env、macvlan 网络与容器连接都已与目标一致"],
                message="二层夹具已是目标状态。",
            )

    subnet_changed = (
        env_values.get("L2_SUBNET", "").strip() != params["L2_SUBNET"]
        or env_values.get("L2_SUBNET_V6", "").strip() != params["L2_SUBNET_V6"]
    )
    snapshot = _Snapshot(
        env_values=dict(env_values),
        network=_network_state_or_502(),
        dhcp_values=dict(dhcp_values) if dhcp_values else None,
    )

    steps: list[str] = []
    service_change: L2ServiceConfigChange | None = None
    try:
        l2_config.write_l2_env(settings.env_file_path, params)
        steps.append(f"已改写 .env：parent={params['DHCP_PARENT_IFACE']}")

        _, net_steps = l2_network.apply_network(
            l2_network.get_client(),
            parent=params["DHCP_PARENT_IFACE"],
            subnet=params["L2_SUBNET"],
            gateway=params["L2_GATEWAY"],
            subnet_v6=params["L2_SUBNET_V6"],
            gateway_v6=params["L2_GATEWAY_V6"],
        )
        steps.extend(net_steps)

        if subnet_changed:
            # 网段变了：旧租约属于旧网段，BMC 拿着它不会主动放弃（默认 12h），
            # 归档掉让 dnsmasq 重启后从空租约开始，BMC 续租时会拿到 NAK 并重新取址。
            # 归档失败不致命（网络切换本身是对的），如实提示即可——不因为一个 mv 失败就把变更回滚掉
            try:
                archived = l2_network.archive_leases(l2_network.get_client())
                steps.append(
                    f"已归档 dnsmasq 旧租约（{archived}），BMC 需重新取址"
                    if archived
                    else "旧租约文件不存在，无需归档"
                )
            except l2_network.L2NetworkError as e:
                logger.warning(f"Failed to archive dnsmasq leases: {e}")
                steps.append(
                    f"旧租约归档失败（{e}）：BMC 可能继续用旧网段地址直到续租失败"
                )

        lifecycle.restart(DHCP_CONTAINER)
        steps.append(f"已重启 {DHCP_CONTAINER}，dnsmasq 会重新绑定新接口")

        if service_changes:
            plugin = registry.get_service(DHCP_SERVICE)
            if plugin is None:
                raise HTTPException(status_code=404, detail="Service not found")
            applied = apply_config_values(
                session=session,
                plugin=plugin,
                name=DHCP_SERVICE,
                values={**(dhcp_values or {}), **service_changes},
                user_email=current_user.email,
                user_id=current_user.id,
            )
            service_change = L2ServiceConfigChange(
                changed={key: str(value) for key, value in service_changes.items()},
                applied=applied,
            )
            steps.append(
                f"已同步 dhcp 服务配置：{'、'.join(sorted(service_changes))}"
                f"（applied={applied}）"
            )

        # 落地校验必须用**生效后**的 dhcp 配置去比：传 None 会跳过池子/RA 前缀检查，
        # 于是「改了网段但池子没跟上」这种坏状态会被判成「校验通过」（代码评审发现）
        post_values = _dhcp_values(session) or {}
        post_checks = _evaluate(
            env_values={**params, "L2_SERVICES": l2_services}, dhcp_values=post_values
        )
        errors = [item for item in post_checks if item["level"] == "error"]
        if errors:
            raise l2_network.L2NetworkError(
                "Post-apply checks failed: "
                + ", ".join(str(item["code"]) for item in errors)
            )
        warns = [item for item in post_checks if item["level"] == "warn"]
        steps.append(
            f"校验完成：无 error 级结论（warn {len(warns)} 条）"
            if warns
            else "校验通过：无 error / warn 级结论"
        )
    except _EXEC_ERRORS as e:
        detail = f"{type(e).__name__}: {e}"
        logger.error(f"L2 apply failed: {detail}")
        rolled_back, rollback_steps = _rollback(
            session=session,
            snapshot=snapshot,
            current_user=current_user,
            service_changed=service_change is not None,
        )
        steps.extend(rollback_steps)
        record_audit_log(
            session=session,
            user_id=current_user.id,
            user_email=current_user.email,
            action=action,
            service_name=None,
            detail=(
                f"parent={previous_parent or '-'}->{params['DHCP_PARENT_IFACE']} "
                f"applied=false rolled_back={'true' if rolled_back else 'false'}"
            ),
        )
        return L2ApplyResult(
            applied=False,
            rolled_back=rolled_back,
            steps=steps,
            service_config=service_change,
            message=detail,
        )

    record_audit_log(
        session=session,
        user_id=current_user.id,
        user_email=current_user.email,
        action=action,
        service_name=None,
        detail=(
            f"parent={previous_parent or '-'}->{params['DHCP_PARENT_IFACE']} "
            f"{params['L2_SUBNET']} applied=true"
        ),
    )
    return L2ApplyResult(
        applied=True,
        rolled_back=False,
        steps=steps,
        service_config=service_change,
        message=f"二层夹具已{'启用' if action == 'l2.enable' else '更新'}。",
    )


@router.post(
    "/disable",
    dependencies=[Depends(RequireAdmin)],
    response_model=L2ApplyResult,
)
def disable_l2(session: SessionDep, current_user: CurrentUser) -> Any:
    """停用二层夹具：断开容器、清空 `.env` 的 `DHCP_PARENT_IFACE`。

    网络对象**保留**（见 `l2_network.detach_l2` 的说明：删了它容器就重启不了）；
    其余 L2 参数保留在 `.env` 里，下次启用可直接预填。

    Args:
        session: 数据库会话（审计）。
        current_user: 操作者，用于审计归属。

    Returns:
        L2ApplyResult：失败时按逆序回滚（网络 → `.env`）。

    Raises:
        HTTPException: 502 `.env` 读不到。
    """
    env_values, _ = _env_values_or_502()
    previous_parent = env_values.get("DHCP_PARENT_IFACE", "").strip()
    if not previous_parent:
        return L2ApplyResult(
            applied=True,
            steps=["无需变更：二层夹具未启用"],
            message="二层夹具未启用。",
        )

    snapshot = _Snapshot(
        env_values=dict(env_values),
        network=_network_state_or_502(),
        dhcp_values=None,
    )
    steps: list[str] = []
    try:
        steps.extend(l2_network.detach_l2(l2_network.get_client()))
        l2_config.write_l2_env(settings.env_file_path, {"DHCP_PARENT_IFACE": ""})
        steps.append(
            "已清空 .env 的 DHCP_PARENT_IFACE（其余 L2 参数保留，便于下次预填）"
        )
        lifecycle.restart(DHCP_CONTAINER)
        steps.append(f"已重启 {DHCP_CONTAINER}")
    except _EXEC_ERRORS as e:
        detail = f"{type(e).__name__}: {e}"
        logger.error(f"L2 disable failed: {detail}")
        rolled_back, rollback_steps = _rollback(
            session=session,
            snapshot=snapshot,
            current_user=current_user,
            service_changed=False,
        )
        steps.extend(rollback_steps)
        record_audit_log(
            session=session,
            user_id=current_user.id,
            user_email=current_user.email,
            action="l2.disable",
            service_name=None,
            detail=(
                f"parent={previous_parent}->- applied=false "
                f"rolled_back={'true' if rolled_back else 'false'}"
            ),
        )
        return L2ApplyResult(
            applied=False, rolled_back=rolled_back, steps=steps, message=detail
        )

    record_audit_log(
        session=session,
        user_id=current_user.id,
        user_email=current_user.email,
        action="l2.disable",
        service_name=None,
        detail=f"parent={previous_parent}->- applied=true",
    )
    return L2ApplyResult(applied=True, steps=steps, message="二层夹具已停用。")


def _rollback(
    *,
    session: Session,
    snapshot: _Snapshot,
    current_user: CurrentUser,
    service_changed: bool,
) -> tuple[bool, list[str]]:
    """按逆序回滚：服务配置 → 网络 → `.env`。

    任一步失败都记录下来并继续尝试后面的步骤（网络与 `.env` 是数据面的根，优先恢复），
    最后返回整体是否全部成功——只要有一项没恢复就必须如实报 false。

    Args:
        session: 数据库会话。
        snapshot: 变更前快照。
        current_user: 操作者，联动回滚也要记审计。
        service_changed: 本次是否已经改过 dhcp 服务配置。

    Returns:
        (是否全部回滚成功, 步骤日志)。
    """
    steps: list[str] = []
    ok = True

    if service_changed and snapshot.dhcp_values is not None:
        plugin = registry.get_service(DHCP_SERVICE)
        if plugin is None:
            ok = False
            steps.append("回滚失败：dhcp 插件不存在")
        else:
            try:
                applied = apply_config_values(
                    session=session,
                    plugin=plugin,
                    name=DHCP_SERVICE,
                    values=snapshot.dhcp_values,
                    user_email=current_user.email,
                    user_id=current_user.id,
                )
                steps.append(f"已回滚 dhcp 服务配置（applied={applied}）")
            except _EXEC_ERRORS as e:
                ok = False
                steps.append(f"回滚 dhcp 服务配置失败：{e}")

    try:
        if snapshot.network.exists or snapshot.network.attached:
            l2_network.apply_network(
                l2_network.get_client(),
                parent=snapshot.network.parent or "",
                subnet=snapshot.network.subnet or "",
                gateway=snapshot.network.gateway or "",
                subnet_v6=snapshot.network.subnet_v6 or "",
                gateway_v6=snapshot.network.gateway_v6 or "",
            )
            steps.append(f"已回滚网络到 parent={snapshot.network.parent}")
        else:
            l2_network.detach_l2(l2_network.get_client())
            steps.append("已回滚网络：恢复为未连接状态")
    except _EXEC_ERRORS as e:
        ok = False
        steps.append(f"回滚网络失败：{e}")

    try:
        if snapshot.env_values:
            l2_config.write_l2_env(settings.env_file_path, snapshot.env_values)
            steps.append("已回滚 .env 的 L2 参数")
    except _EXEC_ERRORS as e:
        ok = False
        steps.append(f"回滚 .env 失败：{e}")

    try:
        lifecycle.restart(DHCP_CONTAINER)
        steps.append(f"已重启 {DHCP_CONTAINER} 让 dnsmasq 绑定回原接口")
    except _EXEC_ERRORS as e:
        ok = False
        steps.append(f"重启 {DHCP_CONTAINER} 失败：{e}")

    return ok, steps
