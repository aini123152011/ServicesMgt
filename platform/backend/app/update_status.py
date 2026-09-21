"""更新任务状态文件的读写（原子写）。

单独成模块的原因：平台自更新的 helper 容器也要读写同一个状态文件，而 helper
只挂 docker.sock 与状态目录、不注入平台环境变量——所以这里不依赖 settings，
路径由调用方传入。
"""

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def now_iso() -> str:
    """UTC 秒级时间戳（状态文件里的时间字段统一用它）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read(path: Path) -> dict[str, Any] | None:
    """读取状态文件；不存在或损坏返回 None（损坏时记 warning，不抛异常）。"""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to read update status file '{path}': {e}")
        return None
    if not isinstance(data, dict):
        logger.warning(f"Update status file '{path}' is not a JSON object")
        return None
    return data


def write(path: Path, **fields: Any) -> dict[str, Any]:
    """原子写状态文件（同目录临时文件 + os.replace），返回写入内容。

    Raises:
        OSError: 目录创建或写入失败（调用方决定如何呈现）。
    """
    payload = {"updated_at": now_iso(), **fields}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as tmp_file:
            json.dump(payload, tmp_file, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return payload
