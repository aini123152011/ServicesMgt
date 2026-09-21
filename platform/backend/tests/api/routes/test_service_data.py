"""/services/{name}/data 数据浏览路由测试：目录树与文件内容各分支。

不依赖仓库 services/ 真实插件：每个用例通过 data_env fixture 在临时目录
现造两个通过 registry 校验的插件（rsyslog 带 data_dir、chrony 不带）和一棵
rsyslog 风格的数据目录树（<日期>/<IP>.log），并把 SERVICES_DIR /
VOLUMES_MOUNT_ROOT monkeypatch 到临时目录。数据库分支依赖 conftest 的
PostgreSQL fixture（认证需要）。
"""

import json
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.core.config import settings

DATA_URL = f"{settings.API_V1_STR}/services"

# 预置的 BMC 日志行：大小写混用，用于验证关键字过滤区分大小写
LOG_LINES = [
    "2026-09-21 10.0.0.1 kernel INFO system boot",
    "2026-09-21 10.0.0.1 sensor ERROR temp 75C",
    "2026-09-21 10.0.0.1 auth INFO login root",
    "2026-09-21 10.0.0.1 kernel ERROR fan stall",
]
# 统一 LF 字节写入：Windows 上 write_text 默认会把 \n 转成 \r\n，污染 size 断言
LOG_BYTES = ("\n".join(LOG_LINES) + "\n").encode("utf-8")


def _make_plugin(directory: Path, *, data_dir: str | None) -> None:
    """在临时目录生成一个能通过 registry 校验的最小服务插件。

    Args:
        directory: 插件目录（目录名即服务名，需与 manifest.name 一致）。
        data_dir: 写入 manifest 的容器内数据目录；None 表示不声明该字段。
    """
    (directory / "templates").mkdir(parents=True)
    manifest: dict[str, object] = {
        "name": directory.name,
        "display_name": f"{directory.name} 测试插件",
        "category": "log-monitor",
        "description": "service_data 接口测试用插件",
        "container_name": f"bmc-{directory.name}",
        "config_dir": "/etc/test",
        "config_files": ["test.conf"],
        "ports": [],
        "reload_mode": "hot",
    }
    if data_dir is not None:
        manifest["data_dir"] = data_dir
    (directory / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8"
    )
    (directory / "schema.json").write_text(
        json.dumps(
            {
                "fields": [
                    {"name": "keep_days", "type": "integer", "default": 7, "min": 1}
                ]
            }
        ),
        encoding="utf-8",
    )
    (directory / "templates" / "test.conf.j2").write_text(
        "keep_days {{ keep_days }}\n", encoding="utf-8"
    )


@pytest.fixture()
def data_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """造出带/不带数据卷的两个插件与一棵 rsyslog 风格数据树，并把平台配置指过去。

    Returns:
        rsyslog 插件的数据卷宿主机根目录，用例可直接在其中追加文件。
    """
    services_dir = tmp_path / "services"
    volumes_root = tmp_path / "volumes"
    _make_plugin(services_dir / "rsyslog", data_dir="/var/log/bmc")
    _make_plugin(services_dir / "chrony", data_dir=None)
    data_root = volumes_root / "rsyslog-data"
    day20 = data_root / "2026-09-20"
    day21 = data_root / "2026-09-21"
    day20.mkdir(parents=True)
    day21.mkdir()
    (data_root / "README").write_text("data volume root\n", encoding="utf-8")
    (day20 / "10.0.0.1.log").write_bytes(b"2026-09-20 10.0.0.1 INFO old entry\n")
    (day21 / "10.0.0.1.log").write_bytes(LOG_BYTES)
    (day21 / "10.0.0.2.log").write_bytes(b"")
    monkeypatch.setattr(settings, "SERVICES_DIR", str(services_dir))
    monkeypatch.setattr(settings, "VOLUMES_MOUNT_ROOT", str(volumes_root))
    return data_root


@pytest.mark.usefixtures("data_env")
def test_read_data_tree_root(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """卷根列举：目录优先、同级按名称排序，条目四字段齐全且 modified 为 ISO8601。"""
    response = client.get(
        f"{DATA_URL}/rsyslog/data/tree", headers=superuser_token_headers
    )
    assert response.status_code == 200
    content = response.json()
    assert content["path"] == ""
    assert [(entry["name"], entry["type"]) for entry in content["entries"]] == [
        ("2026-09-20", "dir"),
        ("2026-09-21", "dir"),
        ("README", "file"),
    ]
    first = content["entries"][0]
    assert set(first) == {"name", "type", "size", "modified"}
    assert isinstance(first["size"], int)
    # ISO8601 可直接被 fromisoformat 解析（带时区）
    datetime.fromisoformat(first["modified"])


@pytest.mark.usefixtures("data_env")
def test_read_data_tree_subdir(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """子目录列举返回归一化子路径，文件条目 size 与磁盘字节数一致。"""
    response = client.get(
        f"{DATA_URL}/rsyslog/data/tree",
        headers=superuser_token_headers,
        params={"subpath": "2026-09-21"},
    )
    assert response.status_code == 200
    content = response.json()
    assert content["path"] == "2026-09-21"
    assert [(entry["name"], entry["type"]) for entry in content["entries"]] == [
        ("10.0.0.1.log", "file"),
        ("10.0.0.2.log", "file"),
    ]
    sizes = {entry["name"]: entry["size"] for entry in content["entries"]}
    assert sizes["10.0.0.1.log"] == len(LOG_BYTES)
    assert sizes["10.0.0.2.log"] == 0


def test_read_data_tree_empty_dir(
    client: TestClient, superuser_token_headers: dict[str, str], data_env: Path
) -> None:
    """空目录列举返回 200 与空 entries，不视为错误。"""
    (data_env / "2026-09-22").mkdir()
    response = client.get(
        f"{DATA_URL}/rsyslog/data/tree",
        headers=superuser_token_headers,
        params={"subpath": "2026-09-22"},
    )
    assert response.status_code == 200
    assert response.json() == {"path": "2026-09-22", "entries": []}


@pytest.mark.usefixtures("data_env")
def test_read_data_tree_subpath_is_file(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """对文件做目录列举返回 400，文案与路由实现精确对应。"""
    response = client.get(
        f"{DATA_URL}/rsyslog/data/tree",
        headers=superuser_token_headers,
        params={"subpath": "2026-09-21/10.0.0.1.log"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Path is not a directory"


@pytest.mark.usefixtures("data_env")
def test_read_data_tree_no_data_volume(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """未声明 data_dir 的服务返回 400，文案与路由实现精确对应。"""
    response = client.get(
        f"{DATA_URL}/chrony/data/tree", headers=superuser_token_headers
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Service has no data volume"


@pytest.mark.usefixtures("data_env")
def test_read_data_tree_service_not_found(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """未知服务名返回 404，文案与 services 路由保持一致。"""
    response = client.get(f"{DATA_URL}/nope/data/tree", headers=superuser_token_headers)
    assert response.status_code == 404
    assert response.json()["detail"] == "Service not found"


@pytest.mark.usefixtures("data_env")
def test_read_data_tree_subpath_not_found(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """子路径不存在返回 404（如日期目录已被清理）。"""
    response = client.get(
        f"{DATA_URL}/rsyslog/data/tree",
        headers=superuser_token_headers,
        params={"subpath": "1999-01-01"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Data path not found"


@pytest.mark.parametrize(
    "subpath", ["../secrets", "/etc/passwd", "2026-09-21/../../escape", "back\\slash"]
)
@pytest.mark.usefixtures("data_env")
def test_read_data_tree_rejects_invalid_subpath(
    client: TestClient, superuser_token_headers: dict[str, str], subpath: str
) -> None:
    """越界/绝对路径/反斜杠等非法 subpath 统一 400，文案不区分具体原因。"""
    response = client.get(
        f"{DATA_URL}/rsyslog/data/tree",
        headers=superuser_token_headers,
        params={"subpath": subpath},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid subpath"


def test_read_data_tree_rejects_symlink_escape(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    data_env: Path,
    tmp_path: Path,
) -> None:
    """数据卷内指向卷外的符号链接被 resolve 校验拦截，返回 400。"""
    link = data_env / "2026-09-21" / "escape"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        # Windows 无开发者模式时禁止建链：该平台跳过，linux CI 仍覆盖
        pytest.skip("当前平台无权限创建符号链接")
    response = client.get(
        f"{DATA_URL}/rsyslog/data/tree",
        headers=superuser_token_headers,
        params={"subpath": "2026-09-21/escape"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid subpath"


@pytest.mark.usefixtures("data_env")
def test_read_data_content(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """小文件整读：响应四字段逐字校验，行序与原文一致且不带行尾换行。"""
    response = client.get(
        f"{DATA_URL}/rsyslog/data/content",
        headers=superuser_token_headers,
        params={"subpath": "2026-09-21/10.0.0.1.log"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "path": "2026-09-21/10.0.0.1.log",
        "size": len(LOG_BYTES),
        "truncated": False,
        "lines": LOG_LINES,
    }


@pytest.mark.usefixtures("data_env")
def test_read_data_content_tail_clamped(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """tail 取末尾 N 行；0 与负数抬到 1，超过文件行数时返回全量。"""
    response = client.get(
        f"{DATA_URL}/rsyslog/data/content",
        headers=superuser_token_headers,
        params={"subpath": "2026-09-21/10.0.0.1.log", "tail": 2},
    )
    assert response.status_code == 200
    assert response.json()["lines"] == LOG_LINES[-2:]
    response = client.get(
        f"{DATA_URL}/rsyslog/data/content",
        headers=superuser_token_headers,
        params={"subpath": "2026-09-21/10.0.0.1.log", "tail": 0},
    )
    assert response.status_code == 200
    assert response.json()["lines"] == [LOG_LINES[-1]]
    response = client.get(
        f"{DATA_URL}/rsyslog/data/content",
        headers=superuser_token_headers,
        params={"subpath": "2026-09-21/10.0.0.1.log", "tail": 9999},
    )
    assert response.status_code == 200
    assert response.json()["lines"] == LOG_LINES


def test_read_data_content_tail_upper_limit(
    client: TestClient, superuser_token_headers: dict[str, str], data_env: Path
) -> None:
    """tail 请求超过 2000 上限时裁剪到 2000 行。"""
    total = 2100
    lines = [f"line-{index:06d}" for index in range(total)]
    (data_env / "2026-09-21" / "big.log").write_bytes(
        ("\n".join(lines) + "\n").encode("utf-8")
    )
    response = client.get(
        f"{DATA_URL}/rsyslog/data/content",
        headers=superuser_token_headers,
        params={"subpath": "2026-09-21/big.log", "tail": 9999},
    )
    assert response.status_code == 200
    content = response.json()
    assert len(content["lines"]) == 2000
    assert content["lines"][0] == f"line-{total - 2000:06d}"
    assert content["truncated"] is False


@pytest.mark.usefixtures("data_env")
def test_read_data_content_keyword(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """关键字区分大小写；tail 在过滤后的结果上再裁剪。"""
    response = client.get(
        f"{DATA_URL}/rsyslog/data/content",
        headers=superuser_token_headers,
        params={"subpath": "2026-09-21/10.0.0.1.log", "keyword": "ERROR"},
    )
    assert response.status_code == 200
    assert response.json()["lines"] == [LOG_LINES[1], LOG_LINES[3]]
    # 小写不命中：确认区分大小写
    response = client.get(
        f"{DATA_URL}/rsyslog/data/content",
        headers=superuser_token_headers,
        params={"subpath": "2026-09-21/10.0.0.1.log", "keyword": "error"},
    )
    assert response.status_code == 200
    assert response.json()["lines"] == []
    # 过滤后再取末尾 1 行
    response = client.get(
        f"{DATA_URL}/rsyslog/data/content",
        headers=superuser_token_headers,
        params={
            "subpath": "2026-09-21/10.0.0.1.log",
            "keyword": "ERROR",
            "tail": 1,
        },
    )
    assert response.status_code == 200
    assert response.json()["lines"] == [LOG_LINES[3]]


def test_read_data_content_truncated_over_size_limit(
    client: TestClient, superuser_token_headers: dict[str, str], data_env: Path
) -> None:
    """超过 2MB 的文件从尾部截窗：truncated=True，行内容与全文件末尾一致。"""
    total = 200_000
    lines = [f"line-{index:06d}" for index in range(total)]
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    assert len(payload) > 2 * 1024 * 1024  # 前置确认确实触发截窗
    (data_env / "2026-09-21" / "huge.log").write_bytes(payload)
    response = client.get(
        f"{DATA_URL}/rsyslog/data/content",
        headers=superuser_token_headers,
        params={"subpath": "2026-09-21/huge.log"},
    )
    assert response.status_code == 200
    content = response.json()
    assert content["truncated"] is True
    assert content["size"] == len(payload)
    # 默认 tail=500：与全文件末尾 500 行逐行一致（首行残缺已被丢弃）
    assert content["lines"] == lines[-500:]


@pytest.mark.usefixtures("data_env")
def test_read_data_content_empty_file(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """空文件返回空 lines，size 为 0 且不视为截断。"""
    response = client.get(
        f"{DATA_URL}/rsyslog/data/content",
        headers=superuser_token_headers,
        params={"subpath": "2026-09-21/10.0.0.2.log"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "path": "2026-09-21/10.0.0.2.log",
        "size": 0,
        "truncated": False,
        "lines": [],
    }


@pytest.mark.usefixtures("data_env")
def test_read_data_content_directory_rejected(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """content 只允许文件：子目录与卷根均返回 400。"""
    for subpath in ("2026-09-21", ""):
        response = client.get(
            f"{DATA_URL}/rsyslog/data/content",
            headers=superuser_token_headers,
            params={"subpath": subpath},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Path is not a file"


@pytest.mark.usefixtures("data_env")
def test_read_data_content_not_found(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """文件不存在返回 404（如日志已被轮转删除）。"""
    response = client.get(
        f"{DATA_URL}/rsyslog/data/content",
        headers=superuser_token_headers,
        params={"subpath": "2026-09-21/9.9.9.9.log"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Data path not found"


def test_read_data_unauthenticated(client: TestClient) -> None:
    """未登录访问数据浏览接口保持模板现状：401。"""
    tree = client.get(f"{DATA_URL}/rsyslog/data/tree")
    content = client.get(f"{DATA_URL}/rsyslog/data/content")
    assert tree.status_code == 401
    assert content.status_code == 401
