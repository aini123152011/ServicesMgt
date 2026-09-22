"""配置版本与回滚：模型、版本记录与清理策略。

设计要点（为什么这样做）：
- **追加式历史**：每次成功下发写一条版本，回滚也写一条新版本（而不是删掉后面的版本）——
  历史只增不改，才能回答「当时到底是什么配置」。
- **secret 语义沿用现状**：版本里存的是**提交后的真实值**（渲染要用它），但对外暴露的
  详情接口一律走 `config_renderer.mask_secret_values` 脱敏——与「读取脱敏、提交掩码保留原值」
  同一套语义，避免版本列表成为明文泄漏口。
- **版本号每服务独立自增**（1,2,3…），并带唯一约束 (service_name, version)：并发下发时由数据库兜底，
  撞约束就重算重试，不会出现两个「第 3 版」。
- **脱敏按写入时的 secret 字段名单**：版本行存下当时的 secret 字段名（`secret_fields`），
  详情接口据此打码——只按「当前 schema」反推的话，schema 演进（字段改名/去掉 secret）会让
  历史版本里的密文明文返回。
- **保留上限**：超过 `settings.CONFIG_VERSION_LIMIT` 的最旧版本在下发时顺手删除，避免无限增长。
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select

from app.core.config import settings
from app.models import ServiceConfigVersion

# 版本号分配的重试次数：版本号取「当前最大值 + 1」，两个并发下发会读到同一个最大值，
# 后提交的那个撞唯一约束。重算重试即可，次数足够覆盖「两个操作员同时点保存」这种量级。
VERSION_ALLOC_ATTEMPTS = 3


class ConfigVersionConflictError(Exception):
    """并发下发导致版本号分配失败（重试用尽）。路由层转 409。"""


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
            with open(path, "rb") as handle:
                hasher.update(handle.read())
        except OSError:
            # 文件读不到（权限/已删除）不该阻断下发：摘要里记个标记即可
            hasher.update(b"<unreadable>")
    return hasher.hexdigest()[:16]


def _next_version(session: Session, service_name: str) -> int:
    """取该服务的下一个版本号（当前最大值 + 1）。

    单独成函数是为了让并发场景可测：并发下两个下发会读到同一个最大值，
    测试需要能构造出「第一次读到旧值」的情形。
    """
    latest = session.exec(
        select(func.max(col(ServiceConfigVersion.version))).where(
            col(ServiceConfigVersion.service_name) == service_name
        )
    ).one()
    return int(latest or 0) + 1


def record_config_version(
    *,
    session: Session,
    service_name: str,
    values: dict[str, Any],
    applied: bool,
    user_email: str | None,
    digest: str = "",
    rolled_back_from: int | None = None,
    secret_fields: list[str] | None = None,
) -> ServiceConfigVersion:
    """写一条配置版本，并按保留上限清理最旧版本。

    版本号取「当前最大值 + 1」；并发下两个下发会读到同一个最大值，后提交的那个撞
    `uq_config_version` 唯一约束——此时回滚本次插入、重算版本号再试，预算用尽则抛
    `ConfigVersionConflictError`（宁可报错也不要两个相同版本号）。清理只删最旧的、
    且在同一事务内完成，避免历史无限增长。

    Args:
        session: 数据库会话。
        service_name: 服务名。
        values: 提交后的真实配置值。
        applied: 渲染产物是否已在容器内生效。
        user_email: 操作者邮箱。
        digest: 渲染产物摘要（`rendered_digest()` 的结果）。
        rolled_back_from: 回滚来源版本号；普通下发为 None。
        secret_fields: 写入时 schema 里的 secret 字段名，供详情接口按「当时的名单」脱敏。

    Returns:
        新建的版本行。

    Raises:
        ConfigVersionConflictError: 重试用尽仍撞唯一约束。
    """
    for attempt in range(1, VERSION_ALLOC_ATTEMPTS + 1):
        entry = ServiceConfigVersion(
            service_name=service_name,
            version=_next_version(session, service_name),
            values=values,
            applied=applied,
            rendered_digest=digest,
            user_email=user_email,
            rolled_back_from=rolled_back_from,
            secret_fields=secret_fields or [],
        )
        session.add(entry)
        try:
            session.flush()
        except IntegrityError as e:
            # 只回滚本次插入：配置值已经在别的事务里提交过，重算版本号再来一次
            session.rollback()
            if attempt == VERSION_ALLOC_ATTEMPTS:
                raise ConfigVersionConflictError(
                    f"Could not allocate a config version for '{service_name}' "
                    f"after {VERSION_ALLOC_ATTEMPTS} attempts"
                ) from e
            continue

        _prune_old_versions(session=session, service_name=service_name)
        session.commit()
        session.refresh(entry)
        return entry
    # 循环内要么 return 要么 raise，走到这里说明重试预算用尽（与上面的 raise 同义）
    raise ConfigVersionConflictError(
        f"Could not allocate a config version for '{service_name}'"
    )


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
