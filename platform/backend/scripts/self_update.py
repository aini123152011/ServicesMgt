"""平台自更新 helper：由平台派生的一次性容器执行（见 app/system_update.py）。

为什么需要独立容器：平台进程不能重建自己——执行到一半进程会被杀掉，接口与状态都
不可控。本脚本跑在独立容器里（与平台同镜像，只挂 docker.sock 与状态目录，不注入
平台环境变量），读参数文件后延迟数秒再动手，那时触发接口的响应已经返回前端。

重建走 app.container_rebuild（从旧容器 inspect 反推 run 参数，失败自动回滚），
状态写 app.update_status（与平台共享同一个卷，平台重启后即可读到结果）。
"""

import json
import logging
import sys
import time
from pathlib import Path

# 镜像里应用代码位于 /app/backend；显式加进 sys.path，保证可 import app.*
sys.path.insert(0, "/app/backend")

from app import container_rebuild, update_status  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("self_update")


def main() -> int:
    """执行平台自更新，成功返回 0；失败返回 1（状态文件里已写明原因）。"""
    if len(sys.argv) < 2:
        logger.error("usage: self_update.py <params.json>")
        return 1

    params = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    container_name = str(params["container_name"])
    image = str(params["image"])
    status_file = Path(params["status_file"])
    delay = int(params.get("delay_seconds", 3))

    logger.info(
        f"Self-update scheduled: container='{container_name}' image='{image}', "
        f"starting in {delay}s"
    )
    time.sleep(delay)

    update_status.write(
        status_file,
        target="platform",
        image=image,
        phase="running",
        status="running",
        message=f"Rebuilding platform container '{container_name}' with image '{image}'",
    )

    try:
        result = container_rebuild.rebuild_container(container_name, image)
    except container_rebuild.RebuildError as e:
        logger.error(f"Self-update failed: {e}")
        update_status.write(
            status_file,
            target="platform",
            image=image,
            phase="failed",
            status="failed",
            message=str(e),
            finished_at=update_status.now_iso(),
        )
        return 1

    logger.info(
        f"Self-update succeeded: '{result['old_image']}' -> '{result['new_image']}'"
    )
    update_status.write(
        status_file,
        target="platform",
        image=image,
        phase="succeeded",
        status="succeeded",
        message=(
            f"Platform updated from '{result['old_image']}' to '{result['new_image']}'"
        ),
        old_image=result["old_image"],
        new_image=result["new_image"],
        finished_at=update_status.now_iso(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
