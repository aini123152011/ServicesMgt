"""exec_reload 对「容器正在重启」的容忍度单测。

背景（实机踩到）：chrony 的 faketime 偏移只能随进程启动注入，偏移变更时 reload 会重启
容器；重启期间 Docker 以 409 Conflict 拒绝 exec。原实现直接把这个 409 当 reload 失败，
接口返回 502、版本不记录，而配置其实已经生效——回滚因此"报错但生效"。
这里锁住行为：409 重启窗口内等待重试，其他错误与非零退出码照旧立即失败。
"""

import types
from typing import Any

import pytest
from docker.errors import APIError
from requests.models import Response

from app import lifecycle


class _FakeContainer:
    """exec_run 替身：按脚本依次返回结果或抛异常。"""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls = 0

    def exec_run(self, _cmd: str) -> Any:
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeResult:
    def __init__(self, output: bytes, exit_code: int | None = 0) -> None:
        self.output = output
        self.exit_code = exit_code


def _restarting_conflict() -> APIError:
    """复刻 Docker 在容器重启期间返回的 409（url/explanation 与实机一致）。"""
    response = Response()
    response.status_code = 409
    response.url = "http+docker://localhost/v1.55/containers/abc/exec"
    return APIError(
        "409 Client Error for http+docker://localhost/v1.55/containers/abc/exec",
        response=response,
        explanation='Conflict ("Container abc is restarting, wait until the '
        'container is running")',
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """重试间隔在单测里不真等（time 由 lifecycle 显式导入，patch 它的属性即可）。"""
    monkeypatch.setattr(lifecycle, "time", types.SimpleNamespace(sleep=lambda _s: None))


def test_exec_reload_retries_while_container_is_restarting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重启窗口内的 409 之后重试成功，不把「正在生效」报成失败。"""
    container = _FakeContainer([_restarting_conflict(), _FakeResult(b"reload ok")])
    monkeypatch.setattr(lifecycle, "_get_container", lambda *_a, **_k: container)

    assert lifecycle.exec_reload({"container_name": "fx-chrony"}) == "reload ok"
    assert container.calls == 2


def test_exec_reload_gives_up_after_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一直重启也不能无限等：用尽重试次数后报 LifecycleError。"""
    container = _FakeContainer(
        [_restarting_conflict() for _ in range(lifecycle.RELOAD_RETRY_ATTEMPTS)]
    )
    monkeypatch.setattr(lifecycle, "_get_container", lambda *_a, **_k: container)

    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.exec_reload({"container_name": "fx-chrony"})
    assert container.calls == lifecycle.RELOAD_RETRY_ATTEMPTS


def test_exec_reload_does_not_retry_other_api_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非 409 的 Docker 错误立即失败，不浪费重试预算。"""
    response = Response()
    response.status_code = 500
    container = _FakeContainer(
        [
            APIError(
                "500 Server Error for http+docker://localhost/exec", response=response
            )
        ]
    )
    monkeypatch.setattr(lifecycle, "_get_container", lambda *_a, **_k: container)

    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.exec_reload({"container_name": "fx-chrony"})
    assert container.calls == 1


def test_exec_reload_reports_nonzero_exit_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """脚本自己非零退出是真失败（如未找到守护进程），不重试。"""
    container = _FakeContainer([_FakeResult(b"no daemon", exit_code=1)])
    monkeypatch.setattr(lifecycle, "_get_container", lambda *_a, **_k: container)

    with pytest.raises(lifecycle.LifecycleError) as excinfo:
        lifecycle.exec_reload({"container_name": "fx-chrony"})
    assert "no daemon" in str(excinfo.value)
    assert container.calls == 1
