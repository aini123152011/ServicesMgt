import random
import string

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import captcha
from app.core.config import settings


def random_lower_string() -> str:
    return "".join(random.choices(string.ascii_lowercase, k=32))


def random_email() -> str:
    return f"{random_lower_string()}@{random_lower_string()}.com"


def captcha_form(db: Session, *, scope: str = "login") -> dict[str, str]:
    """在进程内签发一张图片验证码，返回可直接提交的表单字段。

    为什么不走 HTTP 接口：接口只返回图片，答案在服务端，测试拿不到。直接调
    `captcha.new_challenge` 是唯一既能走真实校验路径、又知道答案的做法，
    也因此**不需要**在生产代码里留任何测试后门——登录验证码是强制校验的。
    """
    issue = captcha.new_challenge(db, scope=scope)
    return {"captcha_id": str(issue.challenge_id), "captcha_answer": issue.answer}


def login_form(db: Session, *, email: str, password: str) -> dict[str, str]:
    """登录接口的完整表单（凭证 + 一次性验证码）。"""
    return {
        "username": email,
        "password": password,
        **captcha_form(db, scope="login"),
    }


def get_superuser_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    login_data = login_form(
        db, email=settings.FIRST_SUPERUSER, password=settings.FIRST_SUPERUSER_PASSWORD
    )
    r = client.post(f"{settings.API_V1_STR}/login/access-token", data=login_data)
    tokens = r.json()
    a_token = tokens["access_token"]
    headers = {"Authorization": f"Bearer {a_token}"}
    return headers
