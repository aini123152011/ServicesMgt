"""/services 路由测试：registry 响应形状与配置下发/生命周期各分支。

Docker 交互全部用 FakeLifecycle 替身（monkeypatch 到 lifecycle 模块函数上），
服务目录 monkeypatch 指到仓库真实 services/（路径按测试文件位置回溯），
渲染产物写到 pytest 临时卷；数据库分支依赖 conftest 的 PostgreSQL fixture。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import config_renderer, lifecycle
from app.core.config import settings
from app.crud import get_service_config, upsert_service_config
from app.models import ServiceConfig, get_datetime_utc

# 服务目录统一取 settings.SERVICES_DIR：本地默认按 config.py 锚定解析到仓库 services/，
# 容器内由环境变量指向挂载目录——不要按测试文件层级回溯（容器内层级不同会失效）
REPO_SERVICES_DIR = Path(settings.SERVICES_DIR).resolve()


@pytest.fixture(autouse=True)
def _services_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """每个用例都把服务目录与配置卷指到受控位置，不依赖进程工作目录。"""
    monkeypatch.setattr(settings, "SERVICES_DIR", str(REPO_SERVICES_DIR))
    monkeypatch.setattr(settings, "VOLUMES_MOUNT_ROOT", str(tmp_path))


class FakeLifecycle:
    """lifecycle 模块的替身：记录调用、可配置运行状态与按操作名注入失败。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.running = True
        self.fail_on: set[str] = set()
        self.logs_text = "log line 1\nlog line 2\n"
        self.last_tail: int | None = None

    def _record(self, name: str, target: str) -> None:
        self.calls.append((name, target))
        if name in self.fail_on:
            raise lifecycle.LifecycleError(f"fake docker failure on {name}")

    def get_status(self, container_name: str) -> dict[str, object]:
        self._record("get_status", container_name)
        if self.running:
            return {"running": True, "health": "healthy", "status": "running"}
        return {"running": False, "health": None, "status": "exited"}

    def exec_reload(self, manifest: dict[str, object]) -> str:
        # manifest 键取值与真实容器名对齐，便于断言
        self._record("exec_reload", str(manifest["container_name"]))
        return "reload ok"

    def start(self, container_name: str) -> None:
        self._record("start", container_name)

    def stop(self, container_name: str) -> None:
        self._record("stop", container_name)

    def restart(self, container_name: str) -> None:
        self._record("restart", container_name)

    def get_logs(self, container_name: str, tail: int) -> str:
        self.last_tail = tail
        self._record("get_logs", container_name)
        return self.logs_text


@pytest.fixture()
def fake_lifecycle(monkeypatch: pytest.MonkeyPatch) -> FakeLifecycle:
    """把 lifecycle 模块函数整体替换为替身实例。"""
    fake = FakeLifecycle()
    monkeypatch.setattr(lifecycle, "get_status", fake.get_status)
    monkeypatch.setattr(lifecycle, "exec_reload", fake.exec_reload)
    monkeypatch.setattr(lifecycle, "start", fake.start)
    monkeypatch.setattr(lifecycle, "stop", fake.stop)
    monkeypatch.setattr(lifecycle, "restart", fake.restart)
    monkeypatch.setattr(lifecycle, "get_logs", fake.get_logs)
    return fake


def _clear_service_configs(db: Session) -> None:
    """清空配置表，保证用例之间互不残留状态。"""
    for row in db.exec(select(ServiceConfig)).all():
        db.delete(row)
    db.commit()


def test_read_services(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """服务列表返回 data+count，chrony 概要字段与 manifest 一致，并带上当前故障注入模式。"""
    _clear_service_configs(db)
    response = client.get(
        f"{settings.API_V1_STR}/services/", headers=superuser_token_headers
    )
    assert response.status_code == 200
    content = response.json()
    assert content["count"] == len(content["data"])
    assert content["count"] >= 1
    chrony = next(item for item in content["data"] if item["name"] == "chrony")
    assert chrony == {
        "name": "chrony",
        "display_name": "NTP 时间服务器",
        "display_name_en": "NTP Time Server",
        "category": "time",
        "description": "基于 chrony 的 NTP 服务，为 BMC/内网设备提供时间同步",
        "container_name": "fx-chrony",
        "ports": [{"port": 123, "protocol": "udp", "description": "NTP 服务端口"}],
        "reload_mode": "hot",
        # 未保存过配置时没有故障模式（首页据此判断是否处于非正常模式）
        "fault_mode": None,
    }


def test_read_services_reports_saved_fault_mode(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """已保存配置的服务在列表里带上 fault_mode，供首页「当前故障注入」面板直接使用。"""
    upsert_service_config(
        session=db,
        service_name="chrony",
        values={
            "servers": ["ntp.aliyun.com"],
            "allow_networks": ["0.0.0.0/0"],
            "makestep": "1.0 3",
            "rtcsync": True,
            "driftfile": "/var/lib/chrony/drift",
            "maxdistance": 6,
            "fault_mode": "stratum_16",
        },
        rendered_at=get_datetime_utc(),
        applied=True,
    )
    try:
        response = client.get(
            f"{settings.API_V1_STR}/services/", headers=superuser_token_headers
        )
        assert response.status_code == 200
        chrony = next(
            item for item in response.json()["data"] if item["name"] == "chrony"
        )
        assert chrony["fault_mode"] == "stratum_16"
    finally:
        _clear_service_configs(db)


def test_read_service_detail(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """服务详情返回 manifest+schema+config 三段结构，未保存过配置时 config 全空。"""
    _clear_service_configs(db)
    response = client.get(
        f"{settings.API_V1_STR}/services/chrony", headers=superuser_token_headers
    )
    assert response.status_code == 200
    content = response.json()
    assert content["manifest"]["name"] == "chrony"
    assert content["manifest"]["config_dir"] == "/etc/chrony"
    assert content["manifest"]["config_files"] == ["chrony.conf", "faketime.conf"]
    field_names = [field["name"] for field in content["schema"]["fields"]]
    assert field_names == [
        "servers",
        "allow_networks",
        "makestep",
        "rtcsync",
        "driftfile",
        "maxdistance",
        "fault_mode",
        "fake_time_offset",
    ]
    assert content["config"] == {"values": None, "applied": None, "rendered_at": None}


def test_read_service_not_found(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """未知服务名返回 404，文案与路由实现精确对应。"""
    response = client.get(
        f"{settings.API_V1_STR}/services/nope", headers=superuser_token_headers
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Service not found"


def test_update_service_config_applied(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    fake_lifecycle: FakeLifecycle,
    tmp_path: Path,
) -> None:
    """容器运行中：写卷 → 落库 → reload 成功 → applied=True。"""
    _clear_service_configs(db)
    response = client.put(
        f"{settings.API_V1_STR}/services/chrony/config",
        headers=superuser_token_headers,
        json={"values": {"maxdistance": 5}},
    )
    assert response.status_code == 200
    content = response.json()
    assert content["applied"] is True
    assert content["message"] == "Configuration applied successfully"
    # 渲染产物写入临时卷且包含提交值与回填默认值
    rendered = tmp_path / "chrony-config" / "chrony.conf"
    assert rendered.is_file()
    text = rendered.read_text(encoding="utf-8")
    assert "maxdistance 5" in text
    assert "server ntp.aliyun.com iburst" in text
    # reload 以 manifest 容器名执行
    assert ("get_status", "fx-chrony") in fake_lifecycle.calls
    assert ("exec_reload", "fx-chrony") in fake_lifecycle.calls
    # 数据库记录新值与已生效标记
    config = get_service_config(session=db, service_name="chrony")
    assert config is not None
    assert config.values["maxdistance"] == 5
    assert config.applied is True
    assert config.rendered_at is not None


def test_update_service_config_not_running(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    fake_lifecycle: FakeLifecycle,
) -> None:
    """容器未运行：跳过 reload，applied=False，返回提示信息。"""
    _clear_service_configs(db)
    fake_lifecycle.running = False
    response = client.put(
        f"{settings.API_V1_STR}/services/chrony/config",
        headers=superuser_token_headers,
        json={"values": {"maxdistance": 2}},
    )
    assert response.status_code == 200
    content = response.json()
    assert content["applied"] is False
    assert content["message"] == (
        "Configuration saved; service is not running, it will be applied on next start"
    )
    assert ("exec_reload", "fx-chrony") not in fake_lifecycle.calls
    config = get_service_config(session=db, service_name="chrony")
    assert config is not None
    assert config.applied is False


def test_update_service_config_invalid_values(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """越界值返回 400，detail 前缀与 renderer 错误消息一致。"""
    response = client.put(
        f"{settings.API_V1_STR}/services/chrony/config",
        headers=superuser_token_headers,
        json={"values": {"maxdistance": 999}},
    )
    assert response.status_code == 400
    assert response.json()["detail"].startswith("Invalid configuration values")


def test_update_service_config_unknown_field(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """未知字段返回 400，防止任意键注入模板。"""
    response = client.put(
        f"{settings.API_V1_STR}/services/chrony/config",
        headers=superuser_token_headers,
        json={"values": {"evil_field": "x"}},
    )
    assert response.status_code == 400
    assert "Unknown configuration fields: evil_field" in response.json()["detail"]


def test_update_service_config_not_found(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """未知服务的配置提交返回 404。"""
    response = client.put(
        f"{settings.API_V1_STR}/services/nope/config",
        headers=superuser_token_headers,
        json={"values": {}},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Service not found"


def test_update_service_config_render_failure(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """渲染失败返回 502，且不产生任何配置记录。"""
    _clear_service_configs(db)

    def _boom(_plugin: object, _values: dict[str, object]) -> list[Path]:
        raise config_renderer.TemplateRenderError("template exploded")

    monkeypatch.setattr(config_renderer, "render_config", _boom)
    response = client.put(
        f"{settings.API_V1_STR}/services/chrony/config",
        headers=superuser_token_headers,
        json={"values": {}},
    )
    assert response.status_code == 502
    assert "template exploded" in response.json()["detail"]
    assert get_service_config(session=db, service_name="chrony") is None


def test_update_service_config_reload_failure(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    fake_lifecycle: FakeLifecycle,
) -> None:
    """reload 失败返回 502：配置值已落库但 applied 保持 False。"""
    _clear_service_configs(db)
    fake_lifecycle.fail_on.add("exec_reload")
    response = client.put(
        f"{settings.API_V1_STR}/services/chrony/config",
        headers=superuser_token_headers,
        json={"values": {"maxdistance": 4}},
    )
    assert response.status_code == 502
    assert "fake docker failure" in response.json()["detail"]
    config = get_service_config(session=db, service_name="chrony")
    assert config is not None
    assert config.values["maxdistance"] == 4
    assert config.applied is False


@pytest.mark.parametrize("action", ["start", "stop", "restart"])
def test_run_service_action(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_lifecycle: FakeLifecycle,
    action: str,
) -> None:
    """生命周期动作逐一透传到 lifecycle，响应对应的成功文案。"""
    response = client.post(
        f"{settings.API_V1_STR}/services/chrony/{action}",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    assert response.json() == {
        "message": f"Service 'chrony' {action}ed successfully"
        if action != "stop"
        else "Service 'chrony' stopped successfully"
    }
    assert (action, "fx-chrony") in fake_lifecycle.calls


def test_run_service_action_invalid(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """枚举外的动作由路径参数校验拦截，返回 422。"""
    response = client.post(
        f"{settings.API_V1_STR}/services/chrony/pause",
        headers=superuser_token_headers,
    )
    assert response.status_code == 422


def test_run_service_action_not_found(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """未知服务的生命周期动作返回 404。"""
    response = client.post(
        f"{settings.API_V1_STR}/services/nope/start",
        headers=superuser_token_headers,
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Service not found"


def test_run_service_action_docker_failure(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_lifecycle: FakeLifecycle,
) -> None:
    """Docker 操作失败返回 502，detail 带原始异常信息。"""
    fake_lifecycle.fail_on.add("start")
    response = client.post(
        f"{settings.API_V1_STR}/services/chrony/start",
        headers=superuser_token_headers,
    )
    assert response.status_code == 502
    assert "fake docker failure on start" in response.json()["detail"]


@pytest.mark.usefixtures("fake_lifecycle")
def test_read_service_status(
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    """状态查询返回 name+running+health+status 四字段结构。"""
    response = client.get(
        f"{settings.API_V1_STR}/services/chrony/status",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    assert response.json() == {
        "name": "chrony",
        "running": True,
        "health": "healthy",
        "status": "running",
    }


def test_read_service_status_docker_failure(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_lifecycle: FakeLifecycle,
) -> None:
    """状态查询的 Docker 失败返回 502。"""
    fake_lifecycle.fail_on.add("get_status")
    response = client.get(
        f"{settings.API_V1_STR}/services/chrony/status",
        headers=superuser_token_headers,
    )
    assert response.status_code == 502


def test_read_service_status_not_found(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """未知服务的状态查询返回 404。"""
    response = client.get(
        f"{settings.API_V1_STR}/services/nope/status",
        headers=superuser_token_headers,
    )
    assert response.status_code == 404


def test_read_service_logs(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_lifecycle: FakeLifecycle,
) -> None:
    """日志查询返回 {"logs": ...}，默认 tail=200 透传给 lifecycle。"""
    response = client.get(
        f"{settings.API_V1_STR}/services/chrony/logs",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    assert response.json() == {"logs": "log line 1\nlog line 2\n"}
    assert fake_lifecycle.last_tail == 200


def test_read_service_logs_tail_clamped(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    fake_lifecycle: FakeLifecycle,
) -> None:
    """tail 超过上限时裁剪到 1000，负数则抬到 1。"""
    response = client.get(
        f"{settings.API_V1_STR}/services/chrony/logs",
        headers=superuser_token_headers,
        params={"tail": 5000},
    )
    assert response.status_code == 200
    assert fake_lifecycle.last_tail == 1000
    client.get(
        f"{settings.API_V1_STR}/services/chrony/logs",
        headers=superuser_token_headers,
        params={"tail": -5},
    )
    assert fake_lifecycle.last_tail == 1


def test_read_service_logs_not_found(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """未知服务的日志查询返回 404。"""
    response = client.get(
        f"{settings.API_V1_STR}/services/nope/logs",
        headers=superuser_token_headers,
    )
    assert response.status_code == 404
