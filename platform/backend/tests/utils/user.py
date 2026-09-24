from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import RoleName, User, UserCreate, UserUpdate
from tests.utils.utils import login_form, random_email, random_lower_string


def user_authentication_headers(
    *, client: TestClient, db: Session, email: str, password: str
) -> dict[str, str]:
    """走真实登录接口换取 Bearer 头（含一次性图片验证码）。"""
    data = login_form(db, email=email, password=password)
    r = client.post(f"{settings.API_V1_STR}/login/access-token", data=data)
    response = r.json()
    auth_token = response["access_token"]
    headers = {"Authorization": f"Bearer {auth_token}"}
    return headers


def create_user_token_headers(
    *, client: TestClient, db: Session, roles: list[RoleName]
) -> dict[str, str]:
    """创建带指定角色的随机用户并返回其 Bearer 认证头。

    测试里直接建的用户一律视为**邮箱已验证**（等价于管理员建号）：未验证账号按设计登不进去，
    这里若留空，所有依赖登录的用例都会挂在「Email is not verified」上——而那并不是它们要测的东西。

    Args:
        client: TestClient，用于走真实登录接口换取 token。
        db: 数据库会话，用于建用户并挂角色。
        roles: 角色名列表（须为系统固定三角色）。

    Returns:
        {"Authorization": "Bearer <token>"} 形式的请求头。
    """
    email = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=email, password=password, roles=roles)
    crud.create_user(session=db, user_create=user_in)
    return user_authentication_headers(
        client=client, db=db, email=email, password=password
    )


def create_random_user(db: Session) -> User:
    email = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=email, password=password)
    return crud.create_user(session=db, user_create=user_in)


def authentication_token_from_email(
    *, client: TestClient, email: str, db: Session
) -> dict[str, str]:
    """
    Return a valid token for the user with given email.

    If the user doesn't exist it is created first.
    """
    password = random_lower_string()
    user = crud.get_user_by_email(session=db, email=email)
    if not user:
        user_in_create = UserCreate(email=email, password=password)
        user = crud.create_user(session=db, user_create=user_in_create)
    else:
        user_in_update = UserUpdate(password=password)
        if not user.id:
            raise Exception("User id not set")
        user = crud.update_user(session=db, db_user=user, user_in=user_in_update)

    return user_authentication_headers(
        client=client, db=db, email=email, password=password
    )
