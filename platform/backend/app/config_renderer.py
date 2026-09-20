"""配置校验与渲染：按服务 schema 校验提交值，渲染 Jinja2 模板并原子写入配置卷。

数据流：路由层拿到 PUT body 的 values → validate_values 归一化校验（整体拒绝，
不通过不落任何东西）→ render_config 逐文件渲染并写入服务配置卷。渲染环境必须
trim_blocks=True + lstrip_blocks=True，这是 services/*/templates 头部注释的硬性要求。
"""

import contextlib
import logging
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from jinja2 import Environment, FileSystemLoader
from jinja2.exceptions import TemplateError

from app.registry import ServicePlugin

logger = logging.getLogger(__name__)

# boolean 字段接受的可转换字符串（不区分大小写）
TRUE_STRINGS = {"true", "1", "yes", "on"}
FALSE_STRINGS = {"false", "0", "no", "off"}


class ConfigValidationError(ValueError):
    """提交的配置值与 schema 不匹配（用户输入问题，路由层转 400）。"""


class TemplateRenderError(Exception):
    """模板渲染或配置文件写入失败（插件/环境问题，路由层转 502）。"""


def _validate_string(field_def: dict[str, Any], raw: Any, name: str) -> str:
    """校验 string 字段：必须是字符串，声明了 pattern 时整串匹配。"""
    if not isinstance(raw, str):
        raise ConfigValidationError(f"Field '{name}' must be a string")
    pattern = field_def.get("pattern")
    if pattern is not None:
        try:
            matched = re.fullmatch(pattern, raw)
        except re.error as e:
            raise ConfigValidationError(
                f"Field '{name}' has an invalid pattern: {e}"
            ) from e
        if matched is None:
            raise ConfigValidationError(
                f"Field '{name}' does not match pattern '{pattern}'"
            )
    return raw


def _validate_integer(field_def: dict[str, Any], raw: Any, name: str) -> int:
    """校验 integer 字段：接受 int 或可转换的数字字符串，并检查 min/max 边界。"""
    if isinstance(raw, bool):
        # bool 是 int 的子类，必须显式拒绝，避免 True 被当成 1
        raise ConfigValidationError(f"Field '{name}' must be an integer")
    value: int | None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        try:
            value = int(raw.strip())
        except ValueError:
            value = None
    else:
        value = None
    if value is None:
        raise ConfigValidationError(f"Field '{name}' must be an integer")
    minimum = field_def.get("min")
    maximum = field_def.get("max")
    if minimum is not None and value < minimum:
        raise ConfigValidationError(f"Field '{name}' must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigValidationError(f"Field '{name}' must be <= {maximum}")
    return value


def _validate_boolean(_field_def: dict[str, Any], raw: Any, name: str) -> bool:
    """校验 boolean 字段：接受 bool、0/1 数字及常见布尔字符串，归一化为 bool。"""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.lower() in TRUE_STRINGS:
        return True
    if isinstance(raw, str) and raw.lower() in FALSE_STRINGS:
        return False
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    raise ConfigValidationError(f"Field '{name}' must be a boolean")


def _validate_enum(field_def: dict[str, Any], raw: Any, name: str) -> Any:
    """校验 enum 字段：值必须出现在 options 列表中。"""
    options = field_def.get("options")
    if not isinstance(options, list):
        # schema 在 registry 已校验，此处兜底插件缺陷
        raise ConfigValidationError(f"Field '{name}' has no 'options' list in schema")
    if raw not in options:
        raise ConfigValidationError(f"Field '{name}' must be one of {options}")
    return raw


def _validate_list(field_def: dict[str, Any], raw: Any, name: str) -> list[str]:
    """校验 list 字段：必须是标量列表，声明了 item_pattern 时逐项整串匹配。

    Returns:
        归一化后的字符串列表；非字符串标量按其字符串形式参与匹配。
    """
    if not isinstance(raw, list):
        raise ConfigValidationError(f"Field '{name}' must be a list")
    item_pattern = field_def.get("item_pattern")
    items: list[str] = []
    for index, item in enumerate(raw):
        if isinstance(item, (dict, list)):
            raise ConfigValidationError(
                f"Field '{name}' item {index} must be a scalar value"
            )
        text = item if isinstance(item, str) else str(item)
        if item_pattern is not None:
            try:
                matched = re.fullmatch(item_pattern, text)
            except re.error as e:
                raise ConfigValidationError(
                    f"Field '{name}' has an invalid item_pattern: {e}"
                ) from e
            if matched is None:
                raise ConfigValidationError(
                    f"Field '{name}' item '{text}' does not match pattern '{item_pattern}'"
                )
        items.append(text)
    return items


_FIELD_TYPE_VALIDATORS = {
    "string": _validate_string,
    "integer": _validate_integer,
    "boolean": _validate_boolean,
    "enum": _validate_enum,
    "list": _validate_list,
}


def validate_values(schema: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """按服务 schema 逐项校验提交值并返回归一化后的值。

    只接受 schema 定义过的字段（未知键整体拒绝）；缺省字段先回填 schema 默认值，
    必填且无默认值才报错；所有字段的错误一次性收集抛出，不返回半通过的结果。

    Args:
        schema: 服务 schema.json 解析后的字典，含 fields 字段定义列表。
        values: 用户提交的配置值，键与 schema 字段名对应。

    Returns:
        归一化后的值：仅含 schema 字段，类型按字段定义转换（如 "5" → 5）。

    Raises:
        ConfigValidationError: values 不是对象、包含未知字段、或任一字段校验失败。
    """
    if not isinstance(values, dict):
        raise ConfigValidationError("Configuration values must be a JSON object")
    fields = schema.get("fields")
    if not isinstance(fields, list):
        raise ConfigValidationError("Service schema is invalid: missing 'fields' list")

    known_names = {
        field_def["name"]
        for field_def in fields
        if isinstance(field_def, dict) and "name" in field_def
    }
    unknown_names = sorted(str(key) for key in values if key not in known_names)
    if unknown_names:
        raise ConfigValidationError(
            f"Unknown configuration fields: {', '.join(unknown_names)}"
        )

    errors: list[str] = []
    normalized: dict[str, Any] = {}
    for field_def in fields:
        if not isinstance(field_def, dict):
            # schema 结构在 registry 已校验，此处兜底
            continue
        name = field_def["name"]
        if name in values:
            raw = values[name]
        elif "default" in field_def:
            raw = field_def["default"]
        elif field_def.get("required"):
            errors.append(f"Field '{name}' is required")
            continue
        else:
            # 可选且无默认值：留给模板渲染时的 undefined 变量暴露插件缺陷
            continue
        field_type = field_def.get("type", "")
        validator = _FIELD_TYPE_VALIDATORS.get(field_type)
        if validator is None:
            errors.append(f"Field '{name}' has invalid type '{field_type}'")
            continue
        try:
            normalized[name] = validator(field_def, raw, name)
        except ConfigValidationError as e:
            errors.append(str(e))
    if errors:
        raise ConfigValidationError(
            "Invalid configuration values: " + "; ".join(errors)
        )
    return normalized


def _atomic_write(target: Path, content: str) -> None:
    """原子写入单个配置文件：同目录写临时文件后 os.replace 覆盖。

    同目录临时文件保证与目标同一文件系统，os.replace 才是原子操作；
    读取方要么看到旧文件、要么看到完整新文件，不会读到半成品。

    Raises:
        TemplateRenderError: 目录创建或文件写入失败时。
    """
    try:
        # 配置文件路径允许子目录（如 conf.d/x.conf），先逐级建目录
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as tmp_file:
                tmp_file.write(content)
            os.replace(tmp_name, target)
        except BaseException:
            # 清理残留临时文件后原样上抛，卷内不留垃圾
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
    except OSError as e:
        raise TemplateRenderError(f"Failed to write config file '{target}': {e}") from e


def render_config(plugin: ServicePlugin, values: dict[str, Any]) -> list[Path]:
    """按插件 config_files 逐个渲染模板，并原子写入该服务的配置卷。

    渲染环境开启 trim_blocks 与 lstrip_blocks（模板头部注释的硬性要求）；
    任一文件渲染失败则整批中断，已写入的文件不回滚（下次成功渲染会覆盖），
    由调用方决定是否向客户端报错。

    Args:
        plugin: registry 加载的服务插件对象。
        values: 已经 validate_values 归一化的配置值，键与模板变量对应。

    Returns:
        成功写入的配置文件绝对路径列表，顺序与 manifest.config_files 一致。

    Raises:
        TemplateRenderError: 模板缺失、渲染出错或目标路径非法/写入失败。
    """
    config_files = plugin.manifest["config_files"]
    environment = Environment(
        loader=FileSystemLoader(plugin.templates_dir),
        # 模板契约：去掉块标签产生的空行，与 Ansible 默认行为一致
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        # 配置文件是纯文本，开启转义反而会破坏配置语法
        autoescape=False,
    )
    volume_dir = plugin.config_volume_dir
    written: list[Path] = []
    for config_file in config_files:
        relative = PurePosixPath(config_file)
        if relative.is_absolute() or ".." in relative.parts:
            # 纵深防御：manifest 虽可信，也要防止路径逃出配置卷
            raise TemplateRenderError(
                f"Invalid config file path in manifest: '{config_file}'"
            )
        template_name = f"{config_file}.j2"
        try:
            template = environment.get_template(template_name)
            content = template.render(**values)
        except TemplateError as e:
            raise TemplateRenderError(
                f"Failed to render template '{template_name}' for service '{plugin.name}': {e}"
            ) from e
        target = volume_dir / relative
        _atomic_write(target, content)
        written.append(target)
        logger.info(f"Rendered config file for service '{plugin.name}': {target}")
    return written
