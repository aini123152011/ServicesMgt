"""系统信息与更新路由测试：权限矩阵、响应结构、状态文件、错误语义与审计。

Docker 交互全部打桩（不真动容器）：路由层用例 monkeypatch `app.system_update` 里的
函数；真正跑容器的那段由实机更新演练覆盖。审计表在相关用例前清空，断言只看本用例。
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app import system_update
from app.core.config import settings
from app.models import AuditLog
from app.system_update import UpdateBusyError, UpdateError


@pytest.fixture(autouse=True)
def _clear_audit_logs(db: Session) -> None:
    """每个用例前清审计，断言只看本次操作写入的记录。"""
    db.exec(delete(AuditLog))
    db.commit()


@pytest.fixture(autouse=True)
def fake_targets(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """替身更新目标：一条「有新版本」的服务 + 平台自身。"""
    targets = [
        {
            "target": "platform",
            "display_name": settings.PROJECT_NAME,
            "container_name": "fx-platform",
            "image": "fx-platform:latest",
            "running_image_id": "sha256:aaa",
            "available_image_id": "sha256:aaa",
            "image_created": "2026-09-21T00:00:00Z",
            "container_running": True,
            "update_available": False,
        },
        {
            "target": "nginx",
            "display_name": "HTTP / HTTPS 文件服务",
            "container_name": "fx-nginx",
            "image": "bmc/nginx:latest",
            "running_image_id": "sha256:old",
            "available_image_id": "sha256:new",
            "image_created": "2026-09-21T01:00:00Z",
            "container_running": True,
            "update_available": True,
        },
    ]
    monkeypatch.setattr(system_update, "collect_targets", lambda plugins: targets)
    return targets


def test_read_system_info_returns_version_and_targets(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """版本信息与各更新目标镜像现状都返回，update_available 反映检查结论。"""
    response = client.get(
        f"{settings.API_V1_STR}/system/info", headers=superuser_token_headers
    )

    assert response.status_code == 200
    content = response.json()
    assert content["version"] == settings.PLATFORM_VERSION
    assert content["docker_available"] is True
    targets = {item["target"]: item for item in content["targets"]}
    assert set(targets) == {"platform", "nginx"}
    assert targets["nginx"]["update_available"] is True
    assert targets["platform"]["update_available"] is False


def test_read_system_info_degrades_when_docker_unavailable(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker 不可达时仍返回版本信息（targets 为空、docker_available=False），不整页 502。"""

    def _raise(_plugins: list[Any]) -> list[dict[str, Any]]:
        raise UpdateError("Failed to connect to Docker daemon")

    monkeypatch.setattr(system_update, "collect_targets", _raise)

    response = client.get(
        f"{settings.API_V1_STR}/system/info", headers=superuser_token_headers
    )

    assert response.status_code == 200
    content = response.json()
    assert content["docker_available"] is False
    assert content["targets"] == []


def test_check_updates_requires_admin(
    client: TestClient, readonly_token_headers: dict[str, str]
) -> None:
    """readonly 用户不能触发检查更新（403）。"""
    response = client.post(
        f"{settings.API_V1_STR}/system/updates/check", headers=readonly_token_headers
    )

    assert response.status_code == 403


def test_check_updates_returns_conclusion(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """检查更新返回「已是最新/有新版本」的明确结论。"""
    monkeypatch.setattr(
        system_update,
        "check_updates",
        lambda plugins: {
            "registry": "",
            "targets": [
                {
                    "target": "nginx",
                    "display_name": "HTTP / HTTPS 文件服务",
                    "container_name": "fx-nginx",
                    "image": "bmc/nginx:latest",
                    "running_image_id": "sha256:old",
                    "available_image_id": "sha256:new",
                    "image_created": None,
                    "container_running": True,
                    "update_available": True,
                }
            ],
            "update_available": ["nginx"],
        },
    )

    response = client.post(
        f"{settings.API_V1_STR}/system/updates/check", headers=superuser_token_headers
    )

    assert response.status_code == 200
    assert response.json()["update_available"] == ["nginx"]


def test_upload_package_rejects_empty_file(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """空包直接 400，不去碰 docker。"""
    response = client.post(
        f"{settings.API_V1_STR}/system/updates/package",
        headers=superuser_token_headers,
        files={"file": ("empty.tar", b"", "application/x-tar")},
    )

    assert response.status_code == 400
    assert "empty" in response.json()["detail"]


def test_apply_update_requires_admin(
    client: TestClient, readonly_token_headers: dict[str, str]
) -> None:
    """readonly 用户不能应用更新（403）。"""
    response = client.post(
        f"{settings.API_V1_STR}/system/updates/apply",
        headers=readonly_token_headers,
        json={"target": "nginx", "image": "bmc/nginx:latest"},
    )

    assert response.status_code == 403


def test_apply_update_rejects_unknown_target(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未知目标转 502（含原始原因），不写审计。"""

    def _raise(_plugins: list[Any], _target: str, _image: str) -> dict[str, Any]:
        raise UpdateError("Unknown update target 'nope'")

    monkeypatch.setattr(system_update, "start_update", _raise)

    response = client.post(
        f"{settings.API_V1_STR}/system/updates/apply",
        headers=superuser_token_headers,
        json={"target": "nope", "image": "bmc/nope:latest"},
    )

    assert response.status_code == 502
    assert "Unknown update target" in response.json()["detail"]


def test_apply_update_conflicts_when_task_running(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已有更新任务在执行时再次触发返回 409。"""

    def _busy(_plugins: list[Any], _target: str, _image: str) -> dict[str, Any]:
        raise UpdateBusyError("Another update task is still running")

    monkeypatch.setattr(system_update, "start_update", _busy)

    response = client.post(
        f"{settings.API_V1_STR}/system/updates/apply",
        headers=superuser_token_headers,
        json={"target": "nginx", "image": "bmc/nginx:latest"},
    )

    assert response.status_code == 409


def test_apply_update_writes_audit(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    db: Session,
) -> None:
    """应用更新成功后写审计：action=system.update，detail 只记目标与镜像。"""
    monkeypatch.setattr(
        system_update,
        "start_update",
        lambda plugins, target, image: {
            "target": target,
            "image": image,
            "status": "running",
            "message": "Update started",
        },
    )

    response = client.post(
        f"{settings.API_V1_STR}/system/updates/apply",
        headers=superuser_token_headers,
        json={"target": "nginx", "image": "bmc/nginx:latest"},
    )

    assert response.status_code == 200
    logs = db.exec(select(AuditLog).where(AuditLog.action == "system.update")).all()
    assert len(logs) == 1
    assert logs[0].service_name == "nginx"
    assert "bmc/nginx:latest" in (logs[0].detail or "")


def test_read_update_status_idle_then_reports_file(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """没有任务文件时返回 idle；有文件时原样返回其中的状态。"""
    status_file = tmp_path / "system-update.json"
    monkeypatch.setattr(system_update, "status_file_path", lambda: status_file)

    idle = client.get(
        f"{settings.API_V1_STR}/system/updates/status", headers=superuser_token_headers
    )
    assert idle.status_code == 200
    assert idle.json()["status"] == "idle"

    system_update.write_status(
        target="nginx", image="bmc/nginx:latest", status="succeeded", phase="succeeded"
    )
    done = client.get(
        f"{settings.API_V1_STR}/system/updates/status", headers=superuser_token_headers
    )
    assert done.status_code == 200
    body = done.json()
    assert body["status"] == "succeeded"
    assert body["target"] == "nginx"


def test_current_status_marks_failed_when_helper_died(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """helper 起不来时任务不能永远停在 running：读状态时对账改判为 failed 并落盘。"""
    status_file = tmp_path / "system-update.json"
    monkeypatch.setattr(system_update, "status_file_path", lambda: status_file)
    monkeypatch.setattr(
        system_update,
        "_helper_failure",
        lambda _status: "Self-update helper failed (exit code 2)",
    )
    system_update.write_status(
        target="platform",
        image="fx-platform:latest",
        status="running",
        phase="pending",
    )

    status = system_update.current_status()

    assert status is not None
    assert status["status"] == "failed"
    assert "exit code 2" in (status["message"] or "")
    # 对账结果要落盘，前端刷新后仍能看到失败原因
    persisted = system_update.read_status()
    assert persisted is not None
    assert persisted["status"] == "failed"


def test_current_status_leaves_running_service_task_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """服务更新任务不经过 helper，对账逻辑不应误判。"""
    status_file = tmp_path / "system-update.json"
    monkeypatch.setattr(system_update, "status_file_path", lambda: status_file)
    monkeypatch.setattr(
        system_update, "_helper_failure", lambda _status: "should not be called"
    )
    system_update.write_status(
        target="nginx", image="bmc/nginx:latest", status="running", phase="running"
    )

    status = system_update.current_status()

    assert status is not None
    assert status["status"] == "running"
