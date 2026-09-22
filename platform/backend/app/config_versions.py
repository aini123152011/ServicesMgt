"""配置版本与回滚：模型、版本记录与清理策略。

设计要点（为什么这样做）：
- **追加式历史**：每次成功下发写一条版本，回滚也写一条新版本（而不是删掉后面的版本）——
  历史只增不改，才能回答「当时到底是什么配置」。
- **secret 语义沿用现状**：版本里存的是**提交后的真实值**（渲染要用它），但对外暴露的
  详情接口一律走 `config_renderer.mask_secret_values` 脱敏——与「读取脱敏、提交掩码保留原值」
  同一套语义，避免版本列表成为明文泄漏口。
- **版本号每服务独立自增**（1,2,3…），并带唯一约束 (service_name, version)：并发下发时由数据库兜底，
  不会出现两个「第 3 版」。
- **保留上限**：超过 `settings.CONFIG_VERSION_LIMIT` 的最旧版本在下发时顺手删除，避免无限增长。
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlmodel import Session, col, func, select

from app.core.config import settings
from app.models import ServiceConfig, ServiceConfigVersion


# --------------------------------------------------------------------------- #
# 版本记录与清理
# --------------------------------------------------------------------------- #
def rendered_digest(paths: list[Any]) -> str:
    """按渲染产物内容算一个稳定摘要（sha256 前 16 位）。

    只用于「两次下发的产物是否一致」这种判断，不用于安全用途，故取前 16 位足够。
    """
    hasher = hashlib.sha256()
    for path in sorted(str(p) for p in paths):
        hasher.update(path.encode())
        try:
            hasher.update(open(path, "rb").read())
        except OSError:
            # 文件读不到（权限/已删除）不该阻断下发：摘要里记个标记即可
            hasher.update(b"<unreadable>")
    return hasher.hexdigest()[:16]


def record_config_version(
    *,
    session: Session,
    service_name: str,
    values: dict[str, Any],
    applied: bool,
    user_email: str | None,
    digest: str = "",
    rolled_back_from: int | None = None,
) -> ServiceConfigVersion:
    """写一条配置版本，并按保留上限清理最旧版本。

    版本号取「当前最大值 + 1」；并发下若撞唯一约束，调用方的事务会失败（宁可报错也不要
    两个相同版本号）。清理只删最旧的、且在同一事务内完成，避免历史无限增长。

    Args:
        session: 数据库会话。
        service_name: 服务名。
        values: 提交后的真实配置值。
        applied: 渲染产物是否已在容器内生效。
        user_email: 操作者邮箱。
        digest: 渲染产物摘要（`rendered_digest()` 的结果）。
        rolled_back_from: 回滚来源版本号；普通下发为 None。

    Returns:
        新建的版本行。
    """
    latest = session.exec(
        select(func.max(col(ServiceConfigVersion.version))).where(
            col(ServiceConfigVersion.service_name) == service_name
        )
    ).one()
    next_version = (latest or 0) + 1

    entry = ServiceConfigVersion(
        service_name=service_name,
        version=next_version,
        values=values,
        applied=applied,
        rendered_digest=digest,
        user_email=user_email,
        rolled_back_from=rolled_back_from,
    )
    session.add(entry)
    session.flush()

    _prune_old_versions(session=session, service_name=service_name)
    session.commit()
    session.refresh(entry)
    return entry


def _prune_old_versions(*, session: Session, service_name: str) -> int:
    """删除超出保留上限的最旧版本，返回删除条数。"""
    limit = max(1, settings.CONFIG_VERSION_LIMIT)
    total = session.exec(
        select(func.count())
        .select_from(ServiceConfigVersion)
        .where(col(ServiceConfigVersion.service_name) == service_name)
    ).one()
    overflow = int(total) - limit
    if overflow <= 0:
        return 0

    stale = session.exec(
        select(ServiceConfigVersion)
        .where(col(ServiceConfigVersion.service_name) == service_name)
        .order_by(col(ServiceConfigVersion.version))
        .limit(overflow)
    ).all()
    for row in stale:
        session.delete(row)
    return len(stale)


def list_config_versions(
    *, session: Session, service_name: str, offset: int = 0, limit: int = 50
) -> tuple[list[ServiceConfigVersion], int]:
    """按版本号倒序返回版本列表与总数（新版本在前）。"""
    count = session.exec(
        select(func.count())
        .select_from(ServiceConfigVersion)
        .where(col(ServiceConfigVersion.service_name) == service_name)
    ).one()
    rows = session.exec(
        select(ServiceConfigVersion)
        .where(col(ServiceConfigVersion.service_name) == service_name)
        .order_by(col(ServiceConfigVersion.version).desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return list(rows), int(count)


def get_config_version(
    *, session: Session, service_name: str, version: int
) -> ServiceConfigVersion | None:
    """按版本号取单个版本；不存在返回 None。"""
    return session.exec(
        select(ServiceConfigVersion).where(
            col(ServiceConfigVersion.service_name) == service_name,
            col(ServiceConfigVersion.version) == version,
        )
    ).first()


def current_values(*, session: Session, service_name: str) -> dict[str, Any] | None:
    """取服务当前配置值（回滚时用于「与当前值相同则不必新建版本」之外的判断）。"""
    config = session.exec(
        select(ServiceConfig).where(col(ServiceConfig.service_name) == service_name)
    ).first()
    return dict(config.values) if config and config.values else None
