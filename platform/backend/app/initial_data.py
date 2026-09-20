import logging

from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.core.db import engine, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def seed(session: Session) -> None:
    """幂等创建固定三角色并为首个超管补 admin 角色（可重复执行）。

    Args:
        session: 数据库会话；角色缺失时补建，超管已有 admin 角色则跳过。
    """
    crud.ensure_roles(session=session)
    user = crud.get_user_by_email(session=session, email=settings.FIRST_SUPERUSER)
    if user:
        crud.ensure_user_role(session=session, user=user, role_name="admin")


def init() -> None:
    with Session(engine) as session:
        init_db(session)
        seed(session)


def main() -> None:
    logger.info("Creating initial data")
    init()
    logger.info("Initial data created")


if __name__ == "__main__":
    main()
