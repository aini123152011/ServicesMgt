"""config_renderer 单元测试：validate_values 校验规则与 render_config 渲染写入。

用仓库内真实的 services/chrony schema + 模板驱动（路径按测试文件位置回溯到仓库根，
不依赖进程工作目录）；写入目标通过 monkeypatch VOLUMES_MOUNT_ROOT 指到临时目录，
单测不碰真实卷。
"""

from pathlib import Path
from typing import Any, cast

import pytest

from app import config_renderer, registry
from app.core.config import settings

# 服务目录统一取 settings.SERVICES_DIR（与 test_services.py 同理，勿按文件层级回溯）
REPO_SERVICES_DIR = Path(settings.SERVICES_DIR).resolve()


@pytest.fixture()
def chrony_plugin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> registry.ServicePlugin:
    """加载真实 chrony 插件，并把配置卷写入点临时目录。"""
    monkeypatch.setattr(settings, "SERVICES_DIR", str(REPO_SERVICES_DIR))
    monkeypatch.setattr(settings, "VOLUMES_MOUNT_ROOT", str(tmp_path))
    plugin = registry.get_service("chrony")
    assert plugin is not None, "chrony 插件应能从仓库 services/ 目录加载"
    return plugin


def test_validate_fills_defaults(chrony_plugin: registry.ServicePlugin) -> None:
    """空提交应回填全部 schema 默认值：required 但带默认值的字段不报错。"""
    values = config_renderer.validate_values(chrony_plugin.schema, {})
    assert values["servers"] == [
        "ntp.aliyun.com",
        "cn.pool.ntp.org",
        "time.windows.com",
    ]
    assert values["makestep"] == "1.0 3"
    assert values["rtcsync"] is True
    assert values["maxdistance"] == 3


def test_validate_rejects_unknown_fields(chrony_plugin: registry.ServicePlugin) -> None:
    """schema 未定义的字段必须整体拒绝，防止任意内容注入模板。"""
    with pytest.raises(
        config_renderer.ConfigValidationError, match="Unknown configuration fields"
    ):
        config_renderer.validate_values(
            chrony_plugin.schema, {"servers": ["ntp.aliyun.com"], "evil_extra": "x"}
        )


def test_validate_rejects_non_object(chrony_plugin: registry.ServicePlugin) -> None:
    """values 不是 JSON 对象时直接拒绝。"""
    # 故意传非对象：类型系统用 cast 放行，运行时由 validate_values 兜住
    bad_values = cast("dict[str, Any]", ["not", "an", "object"])
    with pytest.raises(config_renderer.ConfigValidationError, match="JSON object"):
        config_renderer.validate_values(chrony_plugin.schema, bad_values)


def test_validate_integer_coercion_and_bounds(
    chrony_plugin: registry.ServicePlugin,
) -> None:
    """integer 字段接受数字字符串并做 min/max 边界检查。"""
    values = config_renderer.validate_values(chrony_plugin.schema, {"maxdistance": "5"})
    assert values["maxdistance"] == 5
    with pytest.raises(config_renderer.ConfigValidationError, match=">= 1"):
        config_renderer.validate_values(chrony_plugin.schema, {"maxdistance": 0})
    with pytest.raises(config_renderer.ConfigValidationError, match="<= 15"):
        config_renderer.validate_values(chrony_plugin.schema, {"maxdistance": 99})
    with pytest.raises(
        config_renderer.ConfigValidationError, match="must be an integer"
    ):
        config_renderer.validate_values(chrony_plugin.schema, {"maxdistance": "abc"})


def test_validate_boolean_coercion(chrony_plugin: registry.ServicePlugin) -> None:
    """boolean 字段接受常见布尔字符串并归一化为 bool。"""
    values = config_renderer.validate_values(chrony_plugin.schema, {"rtcsync": "false"})
    assert values["rtcsync"] is False


def test_validate_string_pattern(chrony_plugin: registry.ServicePlugin) -> None:
    """string 字段命中 pattern 才放行（makestep 格式 "<秒> <stratum>"）。"""
    values = config_renderer.validate_values(chrony_plugin.schema, {"makestep": "10 5"})
    assert values["makestep"] == "10 5"
    with pytest.raises(
        config_renderer.ConfigValidationError, match="does not match pattern"
    ):
        config_renderer.validate_values(chrony_plugin.schema, {"makestep": "not time"})


def test_validate_list_item_pattern(chrony_plugin: registry.ServicePlugin) -> None:
    """list 字段逐项做 item_pattern 整串匹配，违规项报错。"""
    values = config_renderer.validate_values(
        chrony_plugin.schema, {"servers": ["ntp.aliyun.com", "10.0.0.1"]}
    )
    assert values["servers"] == ["ntp.aliyun.com", "10.0.0.1"]
    with pytest.raises(
        config_renderer.ConfigValidationError, match="does not match pattern"
    ):
        config_renderer.validate_values(
            chrony_plugin.schema, {"servers": ["bad host!"]}
        )


def test_validate_collects_all_errors(chrony_plugin: registry.ServicePlugin) -> None:
    """多个字段同时非法时一次性收集报出，便于表单整体提示。"""
    with pytest.raises(config_renderer.ConfigValidationError) as exc_info:
        config_renderer.validate_values(
            chrony_plugin.schema, {"makestep": "bad", "maxdistance": 0}
        )
    message = str(exc_info.value)
    assert "makestep" in message and "maxdistance" in message


def test_render_with_defaults_contains_server_lines(
    chrony_plugin: registry.ServicePlugin, tmp_path: Path
) -> None:
    """默认值渲染出合法 chrony.conf：包含 server 行、无残留模板标签。"""
    values = config_renderer.validate_values(chrony_plugin.schema, {})
    written = config_renderer.render_config(chrony_plugin, values)
    # chrony 声明两个渲染产物：主配置 + 故障注入偏移传递文件
    assert [p.name for p in written] == ["chrony.conf", "faketime.conf"]
    target = written[0]
    assert target.parent == tmp_path / "chrony-config"
    content = target.read_text(encoding="utf-8")
    # 断言放宽为"包含 server 行"，与实现说明保持一致
    assert "server ntp.aliyun.com iburst" in content
    assert "allow 10.0.0.0/8" in content
    assert "rtcsync" in content
    assert "maxdistance 3" in content
    # trim_blocks/lstrip_blocks 生效：块标签与其产生的空行不应残留
    assert "{%" not in content and "{{" not in content
    assert "\n\n\n" not in content


def test_render_overwrites_existing_file(
    chrony_plugin: registry.ServicePlugin,
) -> None:
    """重复渲染覆盖旧文件，且值同步更新（原子替换路径）。"""
    first = config_renderer.render_config(
        chrony_plugin, config_renderer.validate_values(chrony_plugin.schema, {})
    )
    second = config_renderer.render_config(
        chrony_plugin,
        config_renderer.validate_values(chrony_plugin.schema, {"maxdistance": 7}),
    )
    assert first == second
    assert "maxdistance 7" in second[0].read_text(encoding="utf-8")
    # 临时文件不留残留：目录内只剩 manifest 声明的渲染产物
    assert sorted(p.name for p in second[0].parent.iterdir()) == [
        "chrony.conf",
        "faketime.conf",
    ]


def test_render_rejects_path_traversal(
    chrony_plugin: registry.ServicePlugin,
) -> None:
    """manifest 里出现相对逃逸路径时拒绝渲染，不写出配置卷。"""
    plugin = chrony_plugin
    plugin.manifest["config_files"] = ["../escape.conf"]
    with pytest.raises(
        config_renderer.TemplateRenderError, match="Invalid config file path"
    ):
        config_renderer.render_config(plugin, {})
    assert not plugin.config_volume_dir.exists()


def test_render_missing_template_raises(tmp_path: Path) -> None:
    """config_files 缺少同名模板时抛 TemplateRenderError。"""
    directory = tmp_path / "broken"
    (directory / "templates").mkdir(parents=True)
    manifest: dict[str, Any] = {"name": "broken", "config_files": ["app.conf"]}
    plugin = registry.ServicePlugin(
        name="broken", directory=directory, manifest=manifest, schema={"fields": []}
    )
    with pytest.raises(
        config_renderer.TemplateRenderError, match="Failed to render template"
    ):
        config_renderer.render_config(plugin, {})


def test_validate_text_and_pem() -> None:
    """text 字段多行支持，以及 pem 证书校验。"""
    schema = {
        "fields": [
            {"name": "description", "type": "text"},
            {"name": "cert", "type": "text", "pem": True},
        ]
    }
    # 正常值
    values = config_renderer.validate_values(
        schema,
        {
            "description": "line 1\nline 2",
            "cert": "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----",
        },
    )
    assert values["description"] == "line 1\nline 2"
    assert "BEGIN CERTIFICATE" in values["cert"]

    # 非法 PEM
    with pytest.raises(config_renderer.ConfigValidationError, match="valid PEM format"):
        config_renderer.validate_values(
            schema,
            {"description": "ok", "cert": "invalid content without pem markers"},
        )


def test_validate_secret_placeholder_and_masking() -> None:
    """secret 字段脱敏与占位符合并回填。"""
    schema = {
        "fields": [
            {"name": "username", "type": "string"},
            {"name": "password", "type": "string", "secret": True},
        ]
    }
    # 首次提交明文
    first_values = config_renderer.validate_values(
        schema, {"username": "admin", "password": "supersecretpassword"}
    )
    assert first_values["password"] == "supersecretpassword"

    # API 脱敏输出
    masked = config_renderer.mask_secret_values(schema, first_values)
    assert masked is not None
    assert masked["password"] == "********"
    assert masked["username"] == "admin"

    # 二次提交带掩码占位符：保留 existing_values 里的原值
    second_values = config_renderer.validate_values(
        schema,
        {"username": "admin2", "password": "********"},
        existing_values=first_values,
    )
    assert second_values["username"] == "admin2"
    assert second_values["password"] == "supersecretpassword"

    # 无历史原值但提交掩码应拒绝
    with pytest.raises(
        config_renderer.ConfigValidationError, match="requires a non-masked"
    ):
        config_renderer.validate_values(
            schema, {"username": "admin", "password": "********"}
        )
