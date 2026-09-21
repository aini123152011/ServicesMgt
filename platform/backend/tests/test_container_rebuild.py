"""容器重建的纯逻辑单测：run 参数提取。

这是「重建不丢运行条件」的核心——阶段 3 实机验证时逐个踩过 bind 挂载、被动端口段、
privileged、cap_add 漏一个就出问题，所以用旧容器 inspect 结果反推参数的行为必须锁住。
（真正跑容器的那部分由实机更新演练覆盖，不在单测里动 docker。）
"""

from typing import Any

from app import container_rebuild


class _FakeContainer:
    """只带 attrs 的容器替身，够 extract_run_params 使用。"""

    def __init__(self, attrs: dict[str, Any]) -> None:
        self.attrs = attrs


def test_extract_run_params_covers_all_runtime_conditions() -> None:
    """bind 挂载/端口段/环境变量/网络/重启策略/cap_add/privileged/entrypoint 全都要还原。"""
    attrs = {
        "Config": {
            "Image": "bmc/nginx:latest",
            "Env": ["TZ=Asia/Shanghai", "SERVICES_DIR=/app/services"],
            "Entrypoint": ["/usr/local/bin/entrypoint.sh"],
            "Cmd": ["nginx", "-g", "daemon off;"],
        },
        "HostConfig": {
            "Binds": [
                "/opt/deploy/volumes/nginx-config:/etc/nginx-bmc:rw",
                "/opt/deploy/volumes/nginx-data:/data/nginx:ro",
            ],
            "PortBindings": {
                "80/tcp": [{"HostIp": "", "HostPort": "18088"}],
                "443/tcp": [{"HostIp": "", "HostPort": "18443"}],
                "123/udp": [{"HostIp": "", "HostPort": "123"}],
            },
            "RestartPolicy": {"Name": "unless-stopped"},
            "CapAdd": ["SYS_ADMIN"],
            "Privileged": True,
        },
        "NetworkSettings": {"Networks": {"bmc-net": {}}},
    }

    params = container_rebuild.extract_run_params(_FakeContainer(attrs))

    assert params["volumes"] == {
        "/opt/deploy/volumes/nginx-config": {"bind": "/etc/nginx-bmc", "mode": "rw"},
        "/opt/deploy/volumes/nginx-data": {"bind": "/data/nginx", "mode": "ro"},
    }
    assert params["ports"]["80/tcp"] == [("", 18088)]
    assert params["ports"]["443/tcp"] == [("", 18443)]
    assert params["ports"]["123/udp"] == [("", 123)]
    assert params["environment"] == {
        "TZ": "Asia/Shanghai",
        "SERVICES_DIR": "/app/services",
    }
    assert params["network"] == "bmc-net"
    assert params["restart_policy"] == {"Name": "unless-stopped"}
    assert params["cap_add"] == ["SYS_ADMIN"]
    assert params["privileged"] is True
    assert params["entrypoint"] == ["/usr/local/bin/entrypoint.sh"]
    assert params["command"] == ["nginx", "-g", "daemon off;"]


def test_extract_run_params_omits_defaults() -> None:
    """默认网络与未设置项不出现在参数里（传了反而可能因网络不存在而启动失败）。"""
    attrs = {
        "Config": {"Image": "bmc/chrony:latest"},
        "HostConfig": {"RestartPolicy": {"Name": "no"}},
        "NetworkSettings": {"Networks": {"bridge": {}}},
    }

    params = container_rebuild.extract_run_params(_FakeContainer(attrs))

    assert "network" not in params
    assert "volumes" not in params
    assert "ports" not in params
    assert "privileged" not in params
    # restart 策略照原样还原（含 "no"）：重建的准则是忠实复现旧容器，而不是省参数
    assert params["restart_policy"] == {"Name": "no"}


def test_extract_run_params_handles_bind_without_mode() -> None:
    """bind 写法省略 mode 时按 rw 处理。"""
    attrs = {
        "Config": {"Image": "bmc/sftp:latest"},
        "HostConfig": {"Binds": ["/host/data:/data"]},
        "NetworkSettings": {"Networks": {}},
    }

    params = container_rebuild.extract_run_params(_FakeContainer(attrs))

    assert params["volumes"] == {"/host/data": {"bind": "/data", "mode": "rw"}}


def test_extract_run_params_drops_image_owned_version_env() -> None:
    """版本元数据必须跟随新镜像：照抄旧容器的值会让更新后仍上报旧版本。"""
    attrs = {
        "Config": {
            "Image": "bmc-platform:latest",
            "Env": [
                "DATABASE_URL=postgresql://bmc:pwd@pg:5432/bmc_platform",
                "PLATFORM_VERSION=0.4.0",
                "PLATFORM_BUILD=202609212046",
            ],
        },
        "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
        "NetworkSettings": {"Networks": {"bmc-platform-isolated-net": {}}},
    }

    params = container_rebuild.extract_run_params(_FakeContainer(attrs))

    assert params["environment"] == {
        "DATABASE_URL": "postgresql://bmc:pwd@pg:5432/bmc_platform"
    }
