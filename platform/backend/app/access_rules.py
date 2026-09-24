"""账号准入规则：邮箱后缀与来源 IP 的 allow / deny 判定。

为什么单独一个模块（而不是塞进 crud.py）：判定逻辑是本任务的核心，需要能**穷举单测**，
所以写成不碰数据库的纯函数，DB 读写只是薄薄一层包装（与 `l2_config.py` 同一套组织方式）。

判定语义（R7 / R8，三档固定顺序）：

1. `deny` 命中 → 拒绝（deny 优先于 allow）
2. `deny` 未命中、`allow` 非空且未命中 → 拒绝（白名单模式）
3. `deny` 未命中、`allow` 为空 → 放行

第 3 条就是「规则未配置 = 不启用」的实现方式——IP 规则默认不生效不需要额外的开关位，
也不会出现「开关说开着、规则是空的」这种自相矛盾的状态。
"""

import ipaddress
import uuid
from dataclasses import dataclass

from sqlmodel import Session, select

from app.models import AccessRule, PlatformSetting

EMAIL_SUFFIX_KIND = "email_suffix"
IP_KIND = "ip"
ALLOW = "allow"
DENY = "deny"
RULE_KINDS = (EMAIL_SUFFIX_KIND, IP_KIND)

# 自助注册开关的平台设置键；未落库时按「开启」处理（本功能的目的就是开放注册）
REGISTRATION_ENABLED_KEY = "registration.enabled"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


class RuleValueError(ValueError):
    """规则值不合法。crud 层不抛 HTTP 异常，由路由层转成 400。"""


@dataclass(frozen=True)
class RuleSet:
    """一类规则的归一化集合（值都已 normalize 过）。"""

    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.allow and not self.deny


@dataclass(frozen=True)
class Decision:
    """判定结论。allowed=True 时 reason 为空串。"""

    allowed: bool
    reason: str
    rule: str | None = None


def normalize_email_suffix(raw: str) -> str:
    """归一化邮箱后缀：去空白、去前导 @、转小写。

    要求含点：`example.com` 合法，`com` 不合法——后者会把整个 TLD 放进白名单，
    这是最容易误伤也最容易被滥用的写法，直接在入口挡住。
    """
    value = raw.strip().lstrip("@").lower()
    if not value:
        raise RuleValueError("Email suffix cannot be empty")
    if any(ch in value for ch in " \t@,;"):
        raise RuleValueError(
            "Email suffix cannot contain spaces, @, comma or semicolon"
        )
    if "." not in value:
        raise RuleValueError(
            "Email suffix must look like example.com (a dot is required)"
        )
    if value.startswith(".") or value.endswith("."):
        raise RuleValueError("Email suffix cannot start or end with a dot")
    if ".." in value:
        raise RuleValueError("Email suffix cannot contain consecutive dots")
    return value


def normalize_ip(raw: str) -> str:
    """归一化 IP / 网段：`10.0.0.5` → `10.0.0.5/32`，`10.0.0.0/8` 原样。

    库里只存归一化后的形式，判定时不必再猜用户当初写的是单 IP 还是网段。
    """
    value = raw.strip()
    if not value:
        raise RuleValueError("IP address cannot be empty")
    try:
        # strict=False：允许 10.0.0.5/24 这种「主机位不为零」的写法，自动归一成网段
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise RuleValueError(f"Invalid IP address or network: {value}") from exc
    return str(network)


def email_suffix_of(email: str) -> str:
    """取邮箱域名部分（小写）；没有 @ 时返回空串。"""
    _, _, domain = email.strip().partition("@")
    return domain.lower()


def match_email_suffix(email: str, rules: RuleSet) -> Decision:
    """邮箱后缀判定。"""
    domain = email_suffix_of(email)
    if not domain:
        return Decision(False, "Email address is malformed")

    for rule in rules.deny:
        if _suffix_hit(domain, rule):
            return Decision(
                False, f"Email domain {domain} is blocked by rule {rule}", rule
            )

    if not rules.allow:
        return Decision(True, "")

    for rule in rules.allow:
        if _suffix_hit(domain, rule):
            return Decision(True, "", rule)
    return Decision(False, f"Email domain {domain} is not in the allowed list")


def _suffix_hit(domain: str, suffix: str) -> bool:
    """`example.com` 同时命中 `example.com` 与其子域 `mail.example.com`。"""
    return domain == suffix or domain.endswith("." + suffix)


def match_ip(ip: str, rules: RuleSet) -> Decision:
    """来源 IP 判定。传入的 ip 可以是单地址；规则值可以是单地址或网段。

    没有规则时直接放行，**连地址能不能解析都不看**：这正是「IP 规则默认不启用」的含义。
    反过来，配了规则却判不出来源地址时按拒绝处理（fail-closed）——规则生效期间放行
    「来源不明」的请求，等于白名单形同虚设。
    """
    if rules.empty:
        return Decision(True, "")

    try:
        address = ipaddress.ip_address(ip.strip())
    except ValueError:
        return Decision(
            False, f"Source address cannot be determined: {ip or 'unknown'}"
        )

    for rule in rules.deny:
        if address in ipaddress.ip_network(rule, strict=False):
            return Decision(
                False, f"Source address {address} is blocked by rule {rule}", rule
            )

    if not rules.allow:
        return Decision(True, "")

    for rule in rules.allow:
        if address in ipaddress.ip_network(rule, strict=False):
            return Decision(True, "", rule)
    return Decision(False, f"Source address {address} is not in the allowed list")


def load_rules(session: Session, kind: str) -> RuleSet:
    """读某一类规则（kind = email_suffix | ip）。"""
    rows = session.exec(select(AccessRule).where(AccessRule.kind == kind)).all()
    return RuleSet(
        allow=tuple(sorted(r.value for r in rows if r.list_type == ALLOW)),
        deny=tuple(sorted(r.value for r in rows if r.list_type == DENY)),
    )


def ip_ruleset_from_raw(allow: list[str], deny: list[str]) -> RuleSet:
    """把待保存的原始 IP 规则归一化成 RuleSet（保存前自检用）。

    归一化失败即抛 RuleValueError——保存路径据此在写库前拦下非法值。
    """
    return RuleSet(
        allow=tuple(sorted(normalize_ip(value) for value in allow)),
        deny=tuple(sorted(normalize_ip(value) for value in deny)),
    )


def list_rules(session: Session, kind: str | None = None) -> list[AccessRule]:
    """规则列表（按 kind 过滤，缺省全部），稳定排序便于界面展示。"""
    statement = select(AccessRule)
    if kind is not None:
        statement = statement.where(AccessRule.kind == kind)
    rows = list(session.exec(statement).all())
    return sorted(rows, key=lambda r: (r.kind, r.list_type, r.value))


def find_rule(
    session: Session, *, kind: str, list_type: str, value: str
) -> AccessRule | None:
    """按唯一键找规则，用于重复检测（重复项会让界面出现两条一模一样的规则）。"""
    return session.exec(
        select(AccessRule).where(
            AccessRule.kind == kind,
            AccessRule.list_type == list_type,
            AccessRule.value == value,
        )
    ).first()


def create_rule(
    session: Session,
    *,
    kind: str,
    list_type: str,
    value: str,
    note: str | None,
    user_id: uuid.UUID | None,
) -> AccessRule:
    """写入一条规则（调用方负责先做归一化、重复检测与自检）。"""
    rule = AccessRule(
        kind=kind,
        list_type=list_type,
        value=value,
        note=note,
        created_by=user_id,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def delete_rule(session: Session, rule: AccessRule) -> None:
    session.delete(rule)
    session.commit()


def registration_enabled(session: Session) -> bool:
    """自助注册开关；未落库时默认开启。"""
    row = session.get(PlatformSetting, REGISTRATION_ENABLED_KEY)
    if row is None:
        return True
    return row.value.strip().lower() in _TRUTHY


def set_registration_enabled(
    session: Session, *, enabled: bool, user_id: uuid.UUID | None
) -> PlatformSetting:
    """写自助注册开关（不存在则建行）。"""
    row = session.get(PlatformSetting, REGISTRATION_ENABLED_KEY)
    if row is None:
        row = PlatformSetting(
            key=REGISTRATION_ENABLED_KEY,
            value="true" if enabled else "false",
            updated_by=user_id,
        )
    else:
        row.value = "true" if enabled else "false"
        row.updated_by = user_id
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
