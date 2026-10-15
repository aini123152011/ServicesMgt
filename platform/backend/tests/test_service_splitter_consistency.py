"""分流器副本一致性：多个服务共享同一份 splitter.py，必须逐字节相同。

为什么需要这条：snmptrapd 与 freeradius 都用「stdout + entrypoint 分流」落地
`<根>/<日期段>/<来源标识>.log`（见 .trellis/spec/services/index.md「日志落盘约定」）。
Docker 的构建上下文是各服务自己的目录（`services/<name>/`，见各 Dockerfile 的 COPY），
所以同一份 splitter.py 只能各放一份。两份一旦漂移，就会出现「改了一个服务、另一个
服务行为不同」这种只在实机才暴露的问题——这条测试把它挡在提交前。
"""

from pathlib import Path

import pytest

from app.core.config import settings

SERVICES_DIR = Path(settings.SERVICES_DIR).resolve()


def _splitter_copies() -> list[Path]:
    return sorted(p for p in SERVICES_DIR.glob("*/splitter.py") if p.is_file())


COPIES = _splitter_copies()


def test_splitter_copies_exist() -> None:
    """至少要扫到两份（snmptrapd / freeradius），避免路径写错导致下面空跑。"""
    assert len(COPIES) >= 2, [p.as_posix() for p in COPIES]


@pytest.mark.parametrize("copy", COPIES, ids=[p.parent.name for p in COPIES])
def test_splitter_copies_are_byte_identical(copy: Path) -> None:
    raw = copy.read_bytes()
    # 容器内直接执行，必须是 LF 行尾且没有 CR
    assert b"\r" not in raw, f"{copy} 含 CR，容器内执行会出问题"
    assert raw.endswith(b"\n"), f"{copy} 末尾缺少换行"
    assert raw == COPIES[0].read_bytes(), (
        f"{copy} 与 {COPIES[0]} 不一致：多服务共用的分流器必须逐字节相同"
    )


@pytest.mark.parametrize("copy", COPIES, ids=[p.parent.name for p in COPIES])
def test_splitter_compiles(copy: Path) -> None:
    """语法必须能编译（容器里没有 lint，只有运行期的 SyntaxError）。"""
    compile(copy.read_text(encoding="utf-8"), str(copy), "exec")
