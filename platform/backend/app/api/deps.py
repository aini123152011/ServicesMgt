from collections.abc import Callable, Generator
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError
from sqlmodel import Session

from app import crud
from app.client_ip import TRUSTED_HEADER, resolve_client_ip
from app.core import security
from app.core.config import settings
from app.core.db import engine
from app.models import TokenPayload, User

reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login/access-token"
)


def get_db() -> Generator[Session]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_db)]
TokenDep = Annotated[str, Depends(reusable_oauth2)]


def get_client_ip(request: Request) -> str:
    """当前请求的来源 IP，供准入规则与审计使用。

    判定前提与「为什么不默认信任 X-Forwarded-For」见 `app/client_ip.py` 的模块说明。
    """
    peer = request.client.host if request.client else None
    return resolve_client_ip(
        peer_host=peer,
        forwarded_for=request.headers.get(TRUSTED_HEADER),
        trust_forwarded_for=settings.TRUST_FORWARDED_FOR,
    )


ClientIp = Annotated[str, Depends(get_client_ip)]


def get_current_user(session: SessionDep, token: TokenDep) -> User:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
    except InvalidTokenError, ValidationError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate credentials",
        )
    user = session.get(User, token_data.sub)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_active_superuser(current_user: CurrentUser) -> User:
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=403, detail="The user doesn't have enough privileges"
        )
    return current_user


def require_role(*allowed_roles: str) -> Callable[..., User]:
    """工厂：生成"拥有任一指定角色（或超管）才放行"的路由级依赖。

    Args:
        *allowed_roles: 放行所需的角色名集合（任一命中即可），如 ("admin", "operator")。

    Returns:
        可用于 Depends 的依赖函数；放行时返回当前用户。

    Raises:
        HTTPException: 403 当前用户既非超管也不具备任一指定角色（detail 固定
            "Not enough permissions"，与模板权限文案一致）。
    """

    def dependency(session: SessionDep, current_user: CurrentUser) -> User:
        # 超管视同 admin：兼容首个超管尚未挂角色行的情况
        if current_user.is_superuser:
            return current_user
        user_roles = crud.get_user_role_names(session=session, user_id=current_user.id)
        if not any(role in user_roles for role in allowed_roles):
            raise HTTPException(status_code=403, detail="Not enough permissions")
        return current_user

    return dependency


# 常用便捷别名：管理员全权；操作员=读 + 配置修改 + 生命周期
RequireAdmin = require_role("admin")
RequireOperator = require_role("admin", "operator")

# 需要拿到管理员本人的场景（审计要记操作者，不能只做路由级守卫）
AdminUser = Annotated[User, Depends(RequireAdmin)]
