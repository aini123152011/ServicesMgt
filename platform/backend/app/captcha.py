"""图片验证码：签发、校验、过期清理。

**为什么票据必须落库，不能放进程内存**：平台以 `fastapi run --workers 2` 启动
（见 `Dockerfile.platform`），出题与校验可能落在不同 worker 上，进程内字典必然错配——
表现是「验证码永远错」。备选的无状态签名方案做不到「一次性」，所以选择落库。

**答案只存哈希**：库被读到也拿不到明文答案。校验用 `hmac.compare_digest` 常量时间比较，
不因比较耗时泄露信息。

**一次性**：校验成功立刻写 `used_at`，同一张验证码第二次使用必然失败；过期与用途不符
（scope 不匹配）同样失败。四种失败原因对外返回同一个错误，不告诉攻击者差在哪一步。
"""

import base64
import hashlib
import hmac
import io
import random
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import delete
from sqlmodel import Session, col

from app.models import CaptchaChallenge

# 去掉易混字符 0/O/1/I/L：出题是为了让人看懂，不是为了难住人
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
LENGTH = 4
TTL_SECONDS = 120

WIDTH, HEIGHT = 160, 48
_BG = (245, 247, 250)
_INK = (30, 41, 59)
_NOISE = (148, 163, 184)


@dataclass(frozen=True)
class ChallengeIssue:
    """一次签发的结果。

    `answer` 是明文答案，**只给测试与调用方内部使用**：路由层只把 `challenge_id` 与
    `png` 返回给客户端，绝不能把 answer 放进响应。
    """

    challenge_id: uuid.UUID
    answer: str
    png: bytes


def new_challenge(
    session: Session, *, scope: str, ip: str | None = None
) -> ChallengeIssue:
    """签发一张验证码并落库；顺带清理已过期的票据。"""
    purge_expired(session)

    answer = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    challenge = CaptchaChallenge(
        scope=scope,
        answer_hash="",
        expires_at=datetime.now(UTC) + timedelta(seconds=TTL_SECONDS),
        created_ip=ip,
    )
    # 哈希里带上票据 id：同一答案在不同票据下的哈希不同，挡住「按已知答案反查库」的做法
    challenge.answer_hash = _hash(answer, challenge.id)

    session.add(challenge)
    session.commit()
    session.refresh(challenge)
    return ChallengeIssue(
        challenge_id=challenge.id, answer=answer, png=render_png(answer)
    )


def verify_challenge(
    session: Session, *, challenge_id: uuid.UUID, answer: str, scope: str
) -> bool:
    """校验并消费一张验证码。任何一种不匹配都返回 False（不区分原因）。"""
    challenge = session.get(CaptchaChallenge, challenge_id)
    if challenge is None:
        return False
    if challenge.scope != scope:
        return False
    if challenge.used_at is not None:
        return False
    if _is_expired(challenge.expires_at):
        return False
    if not hmac.compare_digest(challenge.answer_hash, _hash(answer, challenge.id)):
        return False

    challenge.used_at = datetime.now(UTC)
    session.add(challenge)
    session.commit()
    return True


def purge_expired(session: Session) -> int:
    """删除已过期的票据，返回删除行数。

    每次签发都顺带清理：验证码接口是匿名可调的，没有这一步表会被刷大。
    """
    result = session.exec(
        delete(CaptchaChallenge).where(
            col(CaptchaChallenge.expires_at) < datetime.now(UTC)
        )
    )
    session.commit()
    return int(result.rowcount or 0)


def to_data_url(png: bytes) -> str:
    """转成 data URL，省掉再开一个图片接口与缓存头处理。"""
    return "data:image/png;base64," + base64.b64encode(png).decode()


def render_png(answer: str) -> bytes:
    """把答案渲染成带扭曲与干扰的 PNG。"""
    image = Image.new("RGB", (WIDTH, HEIGHT), _BG)
    font = ImageFont.load_default(size=28)

    x = 14
    for char in answer:
        # 逐字符渲染后旋转再贴回：整体旋转做不出这种错位感，OCR 也更容易被干扰
        glyph = Image.new("RGBA", (34, 42), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((6, 6), char, font=font, fill=_INK)
        glyph = glyph.rotate(
            random.uniform(-26, 26),
            # Pillow 10 起 BICUBIC 迁到 Resampling 枚举；旧的 Image.BICUBIC 只有运行时别名，
            # 类型标注里没有，用新写法
            resample=Image.Resampling.BICUBIC,
            expand=False,
        )
        image.paste(glyph, (x, random.randint(0, 7)), glyph)
        x += 33

    draw = ImageDraw.Draw(image)
    for _ in range(4):
        draw.line(
            [
                (random.randint(0, WIDTH), random.randint(0, HEIGHT)),
                (random.randint(0, WIDTH), random.randint(0, HEIGHT)),
            ],
            fill=_NOISE,
            width=1,
        )
    for _ in range(140):
        draw.point((random.randint(0, WIDTH), random.randint(0, HEIGHT)), fill=_NOISE)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _hash(answer: str, challenge_id: uuid.UUID) -> str:
    payload = f"{challenge_id}:{answer.strip().lower()}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _is_expired(expires_at: datetime) -> bool:
    # 库里的列是 TIMESTAMP WITH TIME ZONE，取出来是 aware datetime；naive 值（理论上不该出现）
    # 按 UTC 解释，避免直接比较抛 TypeError
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at < datetime.now(UTC)
