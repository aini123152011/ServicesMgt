"""图片验证码签发（匿名）。

为什么单独一个模块：登录、注册、重发三个入口都要用同一张票据机制，按用途（scope）区分，
放一起比塞进 login.py 更清楚。

`detail` 文案遵循项目约定用英文（见 .trellis/spec/backend/error-handling.md），
中文只出现在响应体的 message 字段。
"""

from fastapi import APIRouter

from app import captcha
from app.api.deps import ClientIp, SessionDep
from app.models import CaptchaRequest, CaptchaResponse

router = APIRouter(tags=["auth"])


@router.post("/auth/captcha")
def issue_captcha(
    *, session: SessionDep, client_ip: ClientIp, payload: CaptchaRequest
) -> CaptchaResponse:
    """签发一张一次性图片验证码。

    匿名可调，所以每次签发都顺带清理过期票据（见 `captcha.purge_expired`）——
    否则这张表会被刷大。

    Args:
        session: 数据库会话。
        client_ip: 来源 IP，记入票据便于排查刷接口的来源。
        payload: 指定 scope（login / register / resend），跨入口复用同一张票据会校验失败。

    Returns:
        CaptchaResponse：captcha_id 用于校验，image 为 data URL 形式的 PNG。
    """
    issue = captcha.new_challenge(session, scope=payload.scope, ip=client_ip)
    return CaptchaResponse(
        captcha_id=issue.challenge_id, image=captcha.to_data_url(issue.png)
    )
