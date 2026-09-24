"""准入相关的测试辅助：清库与造规则。"""

from sqlmodel import Session, delete

from app.models import (
    AccessRule,
    CaptchaChallenge,
    EmailVerificationCode,
    PlatformSetting,
)


def wipe_access_state(db: Session) -> None:
    """清空准入规则 / 平台开关 / 验证码票据。

    为什么每个用例前后都要清：残留的 allow 规则会让**别的**用例的注册被拒，
    残留的注册开关会让「默认开启」的断言失败——这类跨用例污染排查起来极费时间。
    """
    for model in (AccessRule, PlatformSetting, CaptchaChallenge, EmailVerificationCode):
        db.exec(delete(model))
    db.commit()


def add_rule(db: Session, *, kind: str, list_type: str, value: str) -> AccessRule:
    """直接落库一条规则（绕过接口，用于构造测试前置状态）。"""
    rule = AccessRule(kind=kind, list_type=list_type, value=value)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def set_registration_enabled(db: Session, *, enabled: bool) -> None:
    db.add(
        PlatformSetting(
            key="registration.enabled", value="true" if enabled else "false"
        )
    )
    db.commit()
