"""更新编排的单元测试：状态对账、异常兜底、坏包处理、状态文件合并写。

这些是复核发现的 P0/P1 修复点，必须固化——此前 Docker 侧逻辑零测试，
「任务卡在 running 挡住后续更新」「坏包返回 500」都是靠实机才暴露的。
Docker 交互全部用替身，不碰真容器。
"""

import json
from pathlib import Path
from typing import Any

import pytest
from docker.errors import ImageLoadError

from app import container_rebuild, system_update, update_status
from app.core.config import settings


class _FakeContainer:
    """容器替身：只需要 attrs 与 image.id。"""

    def __init__(
        self, image_id: str, *, running: bool = True, exit_code: int = 0
    ) -> None:
        self.attrs = {
            "Image": image_id,
            "State": {"Running": running, "ExitCode": exit_code},
            "Config": {"Image": "bmc/nginx:latest"},
        }
        self.image = type("_Image", (), {"id": image_id})()

    def logs(self, tail: int = 20) -> bytes:  # noqa: ARG002
        return b"fake logs"


class _FakeImage:
    def __init__(self, image_id: str) -> None:
        self.id = image_id
        self.attrs: dict[str, Any] = {"Created": "2026-09-21T00:00:00Z"}


class _FakeContainers:
    def __init__(self, mapping: dict[str, _FakeContainer]) -> None:
        self._mapping = mapping

    def get(self, name: str) -> _FakeContainer:
        from docker.errors import NotFound

        if name not in self._mapping:
            raise NotFound(f"no such container {name}")
        return self._mapping[name]


class _FakeImages:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def get(self, ref: str) -> _FakeImage:
        from docker.errors import NotFound

        if ref not in self._mapping:
            raise NotFound(f"no such image {ref}")
        return _FakeImage(self._mapping[ref])


class _FakeClient:
    """docker 客户端替身；images_obj 用于注入自定义的 images.load 行为。"""

    def __init__(
        self,
        containers: dict[str, _FakeContainer] | None = None,
        images: dict[str, str] | None = None,
        images_obj: Any = None,
    ) -> None:
        self.containers = _FakeContainers(containers or {})
        self.images: Any = (
            images_obj if images_obj is not None else _FakeImages(images or {})
        )


@pytest.fixture()
def status_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把状态文件指到临时目录，用例之间互不影响。"""
    path = tmp_path / "system-update.json"
    monkeypatch.setattr(system_update, "status_file_path", lambda: path)
    return path


# --------------------------------------------------------------------------- #
# 状态文件：合并写不能丢字段
# --------------------------------------------------------------------------- #
def test_write_status_merges_and_keeps_identity(status_file: Path) -> None:
    """更新过程中重写状态只应改变化字段，id/started_at 不能被覆盖掉。"""
    system_update.write_status(
        id="job-1",
        target="nginx",
        image="bmc/nginx:latest",
        status="running",
        phase="running",
        started_at="2026-09-21T00:00:00Z",
    )
    system_update.write_status(status="succeeded", phase="succeeded")

    payload = json.loads(status_file.read_text(encoding="utf-8"))
    assert payload["status"] == "succeeded"
    assert payload["id"] == "job-1"
    assert payload["started_at"] == "2026-09-21T00:00:00Z"
    assert payload["target"] == "nginx"


# --------------------------------------------------------------------------- #
# 服务目标对账：线程消失/异常逃逸不能让任务永远卡在 running（复核 P0）
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("status_file")
def test_service_status_within_grace_stays_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """宽限期内不动：更新可能正在进行，不能误判失败。"""
    monkeypatch.setattr(system_update, "_worker_running", False)
    system_update.write_status(
        target="nginx", image="bmc/nginx:latest", status="running", phase="running"
    )

    status = system_update.current_status()

    assert status is not None
    assert status["status"] == "running"


@pytest.mark.usefixtures("status_file")
def test_service_status_stale_and_not_switched_becomes_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超过宽限期、没有 worker、容器仍是旧镜像 → 判失败，解除对后续更新的阻塞。"""
    monkeypatch.setattr(system_update, "_worker_running", False)
    monkeypatch.setattr(
        system_update, "_service_container", lambda _plugins, _t: "fx-nginx"
    )
    monkeypatch.setattr(
        system_update,
        "_client",
        lambda: _FakeClient(
            containers={"fx-nginx": _FakeContainer("sha256:old")},
            images={"bmc/nginx:latest": "sha256:new"},
        ),
    )
    system_update.write_status(
        target="nginx",
        image="bmc/nginx:latest",
        status="running",
        phase="running",
        updated_at="2020-01-01T00:00:00Z",
    )

    status = system_update.current_status()

    assert status is not None
    assert status["status"] == "failed"
    assert "did not complete" in (status["message"] or "")


@pytest.mark.usefixtures("status_file")
def test_service_status_stale_but_already_switched_becomes_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """容器已切到目标镜像 → 补记成功（线程没来得及写状态也算成功）。"""
    monkeypatch.setattr(system_update, "_worker_running", False)
    monkeypatch.setattr(
        system_update, "_service_container", lambda _plugins, _t: "fx-nginx"
    )
    monkeypatch.setattr(
        system_update,
        "_client",
        lambda: _FakeClient(
            containers={"fx-nginx": _FakeContainer("sha256:new")},
            images={"bmc/nginx:latest": "sha256:new"},
        ),
    )
    system_update.write_status(
        target="nginx",
        image="bmc/nginx:latest",
        status="running",
        phase="running",
        updated_at="2020-01-01T00:00:00Z",
    )

    status = system_update.current_status()

    assert status is not None
    assert status["status"] == "succeeded"


@pytest.mark.usefixtures("status_file")
def test_service_status_worker_in_flight_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """线程还在跑：即使超过宽限期也不判失败（大镜像重建可能很慢）。"""
    monkeypatch.setattr(system_update, "_worker_running", True)
    system_update.write_status(
        target="nginx",
        image="bmc/nginx:latest",
        status="running",
        phase="running",
        updated_at="2020-01-01T00:00:00Z",
    )

    status = system_update.current_status()

    assert status is not None
    assert status["status"] == "running"


# --------------------------------------------------------------------------- #
# 线程兜底：非 RebuildError 异常也要落状态
# --------------------------------------------------------------------------- #
def test_run_rebuild_records_failure_for_unexpected_exception(
    status_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重建抛非 RebuildError（docker SDK 的 NotFound 等）时，状态必须落 failed 而不是卡住。"""
    from docker.errors import NotFound

    def _boom(_name: str, _image: str) -> dict[str, Any]:
        raise NotFound("simulated docker NotFound")

    monkeypatch.setattr(container_rebuild, "rebuild_container", _boom)
    monkeypatch.setattr(system_update, "_worker_running", True)

    system_update._run_rebuild("nginx", "fx-nginx", "bmc/nginx:latest")

    payload = json.loads(status_file.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert "NotFound" in (payload["message"] or "")
    # 线程结束后必须复位，否则后续更新会被误判为「有任务在跑」
    assert system_update._worker_running is False


# --------------------------------------------------------------------------- #
# 坏包：转 UpdateError 并清理落盘文件（复核 P1）
# --------------------------------------------------------------------------- #
def test_import_package_reports_bad_archive_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """损坏包应转成 UpdateError（路由转 502），且不把坏包留在 uploads 目录。"""
    monkeypatch.setattr(system_update, "uploads_dir", lambda: tmp_path)

    class _BadImages:
        def load(self, _handle: Any) -> Any:
            raise ImageLoadError("archive/tar: invalid tar header")

    monkeypatch.setattr(
        system_update, "_client", lambda: _FakeClient(images_obj=_BadImages())
    )

    with pytest.raises(system_update.UpdateError, match="Failed to load image package"):
        system_update.import_package("broken.tar", b"not-a-tar")

    assert not (tmp_path / "broken.tar").exists()


def test_import_package_returns_loaded_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正常包返回加载出的镜像 tag。"""
    monkeypatch.setattr(system_update, "uploads_dir", lambda: tmp_path)

    class _GoodImages:
        def load(self, _handle: Any) -> list[Any]:
            image = type("_I", (), {"attrs": {"RepoTags": ["bmc/nginx:latest"]}})()
            return [image]

    monkeypatch.setattr(
        system_update, "_client", lambda: _FakeClient(images_obj=_GoodImages())
    )

    result = system_update.import_package("good.tar", b"tar-bytes")

    assert result["tags"] == ["bmc/nginx:latest"]
    assert (tmp_path / "good.tar").exists()


# --------------------------------------------------------------------------- #
# 状态文件读写
# --------------------------------------------------------------------------- #
def test_read_status_returns_none_for_corrupt_file(tmp_path: Path) -> None:
    """状态文件损坏时返回 None（接口降级为 idle），不抛异常。"""
    path = tmp_path / "system-update.json"
    path.write_text("{not json", encoding="utf-8")

    assert update_status.read(path) is None


# --------------------------------------------------------------------------- #
# 拉取用的镜像引用构造（UPDATE_REGISTRY）
#
# 实测踩到：容器上的镜像引用可能已经带仓库（甚至镜像站）主机名，把整段拼在前缀后面会得到
# 非法引用（ghcr.io/org/ghcr.nju.edu.cn/org/fx-chrony:latest），pull 必然失败。
# --------------------------------------------------------------------------- #
def _stub_targets(image: str) -> Any:
    """替身 collect_targets：只返回一个带指定镜像引用的目标。"""
    return lambda plugins: [{"target": "chrony", "image": image}]


def test_pull_refs_replaces_existing_registry_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """容器引用带镜像站主机名时，只取「名字:tag」再拼目标仓库前缀。"""
    monkeypatch.setattr(settings, "UPDATE_REGISTRY", "ghcr.io/aini123152011")
    monkeypatch.setattr(
        system_update,
        "collect_targets",
        _stub_targets("ghcr.nju.edu.cn/aini123152011/fx-chrony:latest"),
    )

    assert system_update.pull_refs([]) == ["ghcr.io/aini123152011/fx-chrony:latest"]


def test_pull_refs_handles_bare_local_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """本地构建（引用没有仓库前缀）时同样成立。"""
    monkeypatch.setattr(settings, "UPDATE_REGISTRY", "registry.local:5000")
    monkeypatch.setattr(
        system_update, "collect_targets", _stub_targets("fx-nginx:latest")
    )

    assert system_update.pull_refs([]) == ["registry.local:5000/fx-nginx:latest"]


def test_pull_refs_tolerates_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    """前缀末尾多写一个斜杠不该拼出双斜杠。"""
    monkeypatch.setattr(settings, "UPDATE_REGISTRY", "ghcr.io/org/")
    monkeypatch.setattr(
        system_update, "collect_targets", _stub_targets("fx-dhcp:latest")
    )

    assert system_update.pull_refs([]) == ["ghcr.io/org/fx-dhcp:latest"]


def test_pull_refs_empty_without_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置仓库时不拉取（界面也不显示在线更新入口）。"""
    monkeypatch.setattr(settings, "UPDATE_REGISTRY", "")
    monkeypatch.setattr(
        system_update, "collect_targets", _stub_targets("fx-dhcp:latest")
    )

    assert system_update.pull_refs([]) == []


def test_pull_refs_skips_targets_without_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """容器还没起来（取不到镜像引用）的目标跳过，不拼出「前缀/」这种半截引用。"""
    monkeypatch.setattr(settings, "UPDATE_REGISTRY", "ghcr.io/org")
    monkeypatch.setattr(
        system_update,
        "collect_targets",
        lambda plugins: [
            {"target": "chrony", "image": ""},
            {"target": "nginx", "image": "fx-nginx:latest"},
        ],
    )

    assert system_update.pull_refs([]) == ["ghcr.io/org/fx-nginx:latest"]


# --------------------------------------------------------------------------- #
# 一键更新（批次）
# --------------------------------------------------------------------------- #
def _target_rows(entries: list[tuple[str, bool]]) -> Any:
    """替身 collect_targets：按 (目标, 是否有新版本) 造结果。"""
    return lambda plugins: [
        {
            "target": name,
            "container_name": f"fx-{name}",
            "image": f"ghcr.io/org/fx-{name}:latest",
            "update_available": available,
        }
        for name, available in entries
    ]


def test_plan_batch_skips_up_to_date_and_puts_platform_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """批次只含真正有新版本的目标，且平台排最后（它会重启平台进程）。"""
    monkeypatch.setattr(
        system_update,
        "collect_targets",
        _target_rows(
            [("chrony", True), ("nginx", False), ("platform", True), ("dhcp", True)]
        ),
    )

    assert [item["target"] for item in system_update.plan_batch([])] == [
        "chrony",
        "dhcp",
        "platform",
    ]


@pytest.mark.usefixtures("status_file")
def test_run_batch_rebuilds_serially_and_reports_progress(
    status_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """按顺序逐个重建，结束后状态文件里能读到「共几个、成功哪几个」。"""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        container_rebuild,
        "rebuild_container",
        lambda name, image: calls.append((name, image)),
    )
    monkeypatch.setattr(system_update, "_worker_running", True)

    system_update._run_batch(
        [
            {"target": "chrony", "container_name": "fx-chrony", "image": "img-chrony"},
            {"target": "dhcp", "container_name": "fx-dhcp", "image": "img-dhcp"},
        ]
    )

    assert calls == [("fx-chrony", "img-chrony"), ("fx-dhcp", "img-dhcp")]
    payload = json.loads(status_file.read_text(encoding="utf-8"))
    assert payload["status"] == "succeeded"
    assert payload["batch_total"] == 2
    assert payload["updated_targets"] == ["chrony", "dhcp"]
    assert payload["failed_targets"] == []
    assert system_update._worker_running is False


@pytest.mark.usefixtures("status_file")
def test_run_batch_stops_on_failure_and_lists_remaining(
    status_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """中途失败即停：记录失败目标与「未执行」的剩余目标，不再继续重建。"""
    calls: list[str] = []

    def _rebuild(name: str, _image: str) -> None:
        calls.append(name)
        if name == "fx-dhcp":
            raise container_rebuild.RebuildError("boom")

    monkeypatch.setattr(container_rebuild, "rebuild_container", _rebuild)
    monkeypatch.setattr(system_update, "_worker_running", True)

    system_update._run_batch(
        [
            {"target": "chrony", "container_name": "fx-chrony", "image": "img1"},
            {"target": "dhcp", "container_name": "fx-dhcp", "image": "img2"},
            {"target": "nginx", "container_name": "fx-nginx", "image": "img3"},
        ]
    )

    assert calls == ["fx-chrony", "fx-dhcp"]
    payload = json.loads(status_file.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["failed_targets"] == ["dhcp"]
    assert payload["updated_targets"] == ["chrony"]
    assert "nginx" in payload["message"]


@pytest.mark.usefixtures("status_file")
def test_run_batch_schedules_platform_self_update_last(
    status_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """平台自身不在这里重建，而是排程 helper 容器，并如实报告「服务更新了几个」。"""
    calls: list[tuple[str, str]] = []
    spawned: list[str] = []
    monkeypatch.setattr(
        container_rebuild,
        "rebuild_container",
        lambda name, image: calls.append((name, image)),
    )
    monkeypatch.setattr(
        system_update, "_spawn_self_update", lambda image: spawned.append(image)
    )
    monkeypatch.setattr(system_update, "_worker_running", True)

    system_update._run_batch(
        [
            {"target": "chrony", "container_name": "fx-chrony", "image": "img1"},
            {"target": "platform", "container_name": "fx-platform", "image": "img-p"},
        ]
    )

    assert calls == [("fx-chrony", "img1")]
    assert spawned == ["img-p"]
    payload = json.loads(status_file.read_text(encoding="utf-8"))
    assert payload["status"] == "succeeded"
    assert "平台自更新已排程" in payload["message"]
