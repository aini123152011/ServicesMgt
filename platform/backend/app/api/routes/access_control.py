"""访问控制管理端：准入规则 CRUD、注册开关、IP 规则保存前自检。

**防自锁（R9）**：IP 规则的写路径（增 / 改 / 删）在落库前先用**新规则**评估当前请求的来源 IP，
不通过就拒绝保存并说明原因。这是本任务里唯一能把运维者锁在平台外的功能，所以宁可拒绝保存，
也不允许写入「一写进去自己就进不来」的规则。

刻意**不做**「自动把当前 IP 加进白名单」的隐式豁免：那会让界面显示的规则与实际生效范围
不一致，后续排查（「为什么这个 IP 能进」）会更困难。

`detail` 文案遵循项目约定用英文（见 .trellis/spec/backend/error-handling.md）。
"""

import uuid

from fastapi import APIRouter, HTTPException, status

from app import access_rules, crud
from app.api.deps import AdminUser, ClientIp, SessionDep
from app.core.config import settings
from app.models import (
    AccessControlSettings,
    AccessControlSettingsUpdate,
    AccessRule,
    AccessRuleCreate,
    AccessRulePublic,
    AccessRulesPublic,
    IpPreflightResult,
    IpRuleSet,
    Message,
)

router = APIRouter(tags=["access-control"])


def _ip_rule_values(session: SessionDep) -> tuple[list[str], list[str]]:
    """当前库里 IP 规则的 (allow, deny) 值列表。"""
    rows = access_rules.list_rules(session, access_rules.IP_KIND)
    allow = [row.value for row in rows if row.list_type == access_rules.ALLOW]
    deny = [row.value for row in rows if row.list_type == access_rules.DENY]
    return allow, deny


def _normalize(kind: str, value: str) -> str:
    """按规则类型归一化；不合法即 400。"""
    try:
        if kind == access_rules.IP_KIND:
            return access_rules.normalize_ip(value)
        return access_rules.normalize_email_suffix(value)
    except access_rules.RuleValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


def _ensure_not_self_locked(
    *, allow: list[str], deny: list[str], client_ip: str
) -> None:
    """用待保存的规则集评估当前来源 IP；不通过即拒绝保存。"""
    try:
        rules = access_rules.ip_ruleset_from_raw(allow, deny)
    except access_rules.RuleValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    decision = access_rules.match_ip(client_ip, rules)
    if decision.allowed:
        return
    address = client_ip or "unknown"
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"Saving these rules would block your current address ({address}): "
            f"{decision.reason}"
        ),
    )


@router.get("/access-control/rules", response_model=AccessRulesPublic)
def list_rules(
    *, session: SessionDep, current_user: AdminUser, kind: str | None = None
) -> AccessRulesPublic:
    """规则列表；`kind` 可选（email_suffix / ip），缺省返回全部。"""
    del current_user  # 仅做权限校验
    if kind is not None and kind not in access_rules.RULE_KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown rule kind"
        )
    rows = access_rules.list_rules(session, kind)
    return AccessRulesPublic(
        data=[AccessRulePublic.model_validate(row) for row in rows],
        count=len(rows),
    )


@router.post(
    "/access-control/rules",
    response_model=AccessRulePublic,
    status_code=status.HTTP_201_CREATED,
)
def create_rule(
    *,
    session: SessionDep,
    current_user: AdminUser,
    client_ip: ClientIp,
    payload: AccessRuleCreate,
) -> AccessRulePublic:
    """新建规则。IP 规则会先做防自锁自检。"""
    value = _normalize(payload.kind, payload.value)

    if payload.kind == access_rules.IP_KIND:
        allow, deny = _ip_rule_values(session)
        (allow if payload.list_type == access_rules.ALLOW else deny).append(value)
        _ensure_not_self_locked(allow=allow, deny=deny, client_ip=client_ip)

    if access_rules.find_rule(
        session, kind=payload.kind, list_type=payload.list_type, value=value
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Rule already exists"
        )

    rule = access_rules.create_rule(
        session,
        kind=payload.kind,
        list_type=payload.list_type,
        value=value,
        note=payload.note,
        user_id=current_user.id,
    )
    crud.record_audit_log(
        session=session,
        user_id=current_user.id,
        user_email=current_user.email,
        action="access_rule.create",
        detail=f"kind={rule.kind} list={rule.list_type} value={rule.value}",
        ip=client_ip,
    )
    return AccessRulePublic.model_validate(rule)


@router.patch("/access-control/rules/{rule_id}", response_model=AccessRulePublic)
def update_rule(
    *,
    session: SessionDep,
    current_user: AdminUser,
    client_ip: ClientIp,
    rule_id: uuid.UUID,
    payload: AccessRuleCreate,
) -> AccessRulePublic:
    """修改规则（整体替换语义：kind / list_type / value / note 以请求体为准）。"""
    rule = session.get(AccessRule, rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found"
        )
    value = _normalize(payload.kind, payload.value)

    if payload.kind == access_rules.IP_KIND:
        allow, deny = _ip_rule_values(session)
        # 先摘掉旧值再加新值：自检必须针对「保存后的最终集合」
        target = allow if rule.list_type == access_rules.ALLOW else deny
        if rule.value in target:
            target.remove(rule.value)
        (allow if payload.list_type == access_rules.ALLOW else deny).append(value)
        _ensure_not_self_locked(allow=allow, deny=deny, client_ip=client_ip)

    duplicate = access_rules.find_rule(
        session, kind=payload.kind, list_type=payload.list_type, value=value
    )
    if duplicate is not None and duplicate.id != rule.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Rule already exists"
        )

    before = f"{rule.kind}/{rule.list_type}={rule.value}"
    rule.kind = payload.kind
    rule.list_type = payload.list_type
    rule.value = value
    rule.note = payload.note
    session.add(rule)
    session.commit()
    session.refresh(rule)

    crud.record_audit_log(
        session=session,
        user_id=current_user.id,
        user_email=current_user.email,
        action="access_rule.update",
        detail=f"{before} -> {rule.kind}/{rule.list_type}={rule.value}",
        ip=client_ip,
    )
    return AccessRulePublic.model_validate(rule)


@router.delete("/access-control/rules/{rule_id}", response_model=Message)
def delete_rule(
    *,
    session: SessionDep,
    current_user: AdminUser,
    client_ip: ClientIp,
    rule_id: uuid.UUID,
) -> Message:
    """删除规则。删 IP 规则同样做自检——删掉白名单里的一条也可能把自己挡在外面。"""
    rule = session.get(AccessRule, rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found"
        )

    if rule.kind == access_rules.IP_KIND:
        allow, deny = _ip_rule_values(session)
        target = allow if rule.list_type == access_rules.ALLOW else deny
        if rule.value in target:
            target.remove(rule.value)
        _ensure_not_self_locked(allow=allow, deny=deny, client_ip=client_ip)

    detail = f"kind={rule.kind} list={rule.list_type} value={rule.value}"
    access_rules.delete_rule(session, rule)
    crud.record_audit_log(
        session=session,
        user_id=current_user.id,
        user_email=current_user.email,
        action="access_rule.delete",
        detail=detail,
        ip=client_ip,
    )
    return Message(message="Rule deleted successfully")


@router.post("/access-control/preflight", response_model=IpPreflightResult)
def preflight(
    *,
    current_user: AdminUser,
    client_ip: ClientIp,
    payload: IpRuleSet,
) -> IpPreflightResult:
    """保存前自检：用给定的 IP 规则集评估当前来源 IP。

    前端在保存前调用它，把结论以警告条展示；不通过则禁用保存按钮并说明原因。
    """
    del current_user  # 仅做权限校验
    try:
        rules = access_rules.ip_ruleset_from_raw(payload.allow, payload.deny)
    except access_rules.RuleValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    decision = access_rules.match_ip(client_ip, rules)
    return IpPreflightResult(
        allowed=decision.allowed,
        reason=decision.reason,
        current_ip=client_ip,
        matched_rule=decision.rule,
    )


@router.get("/access-control/settings", response_model=AccessControlSettings)
def read_settings(
    *, session: SessionDep, current_user: AdminUser
) -> AccessControlSettings:
    """读注册开关（`email_configured` 由进程配置推导，不落库）。"""
    del current_user  # 仅做权限校验
    return AccessControlSettings(
        registration_enabled=access_rules.registration_enabled(session),
        email_configured=settings.emails_enabled,
    )


@router.patch("/access-control/settings", response_model=AccessControlSettings)
def update_settings(
    *,
    session: SessionDep,
    current_user: AdminUser,
    client_ip: ClientIp,
    payload: AccessControlSettingsUpdate,
) -> AccessControlSettings:
    """改注册开关。"""
    row = access_rules.set_registration_enabled(
        session, enabled=payload.registration_enabled, user_id=current_user.id
    )
    crud.record_audit_log(
        session=session,
        user_id=current_user.id,
        user_email=current_user.email,
        action="platform_setting.update",
        detail=f"{row.key}={row.value}",
        ip=client_ip,
    )
    return AccessControlSettings(
        registration_enabled=access_rules.registration_enabled(session),
        email_configured=settings.emails_enabled,
    )
