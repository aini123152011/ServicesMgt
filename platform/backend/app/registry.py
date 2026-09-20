"""服务插件注册表：扫描 SERVICES_DIR，加载并校验各服务的 manifest 与 schema。

每个服务是一个自描述目录（services/<name>/）：manifest.yaml 提供元数据，
schema.json 提供配置字段定义，templates/ 存放配置模板。本模块是唯一的加载入口，
每次调用都现读磁盘（目录规模小，免去缓存失效的复杂度）；格式有问题的目录
跳过并记 warning，绝不影响其他服务的识别与展示。
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.config import settings

logger = logging.getLogger(__name__)

# 插件契约约定的取值枚举，与 services/*/manifest.yaml、schema.json 的注释保持一致
ALLOWED_CATEGORIES = {"time", "file-share", "log-monitor"}
ALLOWED_RELOAD_MODES = {"hot", "restart"}
ALLOWED_FIELD_TYPES = {"string", "integer", "boolean", "enum", "list"}


@dataclass
class ServicePlugin:
    """一个已通过校验的服务插件。

    Attributes:
        name: 服务名，与插件目录名一致。
        directory: 插件目录绝对路径。
        manifest: 解析后的 manifest.yaml 字典。
        schema: 解析后的 schema.json 字典（含 fields 字段定义列表）。
    """

    name: str
    directory: Path
    manifest: dict[str, Any]
    schema: dict[str, Any]

    @property
    def templates_dir(self) -> Path:
        """配置模板目录，内含与 config_files 同名的 .j2 文件。"""
        return self.directory / "templates"

    @property
    def config_volume_dir(self) -> Path:
        """渲染产物的写入目录（宿主机路径）。

        根 compose 把名为 <name>-config 的卷挂载到 VOLUMES_MOUNT_ROOT/<name>-config，
        因此平台直接写宿主机该前缀，容器内即映射到 manifest.config_dir。
        """
        return Path(settings.VOLUMES_MOUNT_ROOT) / f"{self.name}-config"


def _check_manifest(manifest: dict[str, Any], dir_name: str) -> list[str]:
    """校验 manifest 字段完整性与类型，返回问题描述列表（空列表即通过）。"""
    problems: list[str] = []
    for key in (
        "name",
        "display_name",
        "category",
        "description",
        "container_name",
        "config_dir",
        "reload_mode",
    ):
        value = manifest.get(key)
        if not isinstance(value, str) or not value:
            problems.append(f"field '{key}' must be a non-empty string")
    name = manifest.get("name")
    if isinstance(name, str) and name != dir_name:
        # 目录名是对外路由标识，manifest.name 必须一致，避免两套名字
        problems.append(
            f"manifest name '{name}' does not match directory name '{dir_name}'"
        )
    if manifest.get("category") not in ALLOWED_CATEGORIES:
        problems.append(
            f"category '{manifest.get('category')}' not in {sorted(ALLOWED_CATEGORIES)}"
        )
    if manifest.get("reload_mode") not in ALLOWED_RELOAD_MODES:
        problems.append(
            f"reload_mode '{manifest.get('reload_mode')}' not in {sorted(ALLOWED_RELOAD_MODES)}"
        )
    config_files = manifest.get("config_files")
    if (
        not isinstance(config_files, list)
        or not config_files
        or not all(isinstance(item, str) and item for item in config_files)
    ):
        problems.append("config_files must be a non-empty list of strings")
    ports = manifest.get("ports")
    if not isinstance(ports, list):
        problems.append("ports must be a list")
    else:
        for index, port in enumerate(ports):
            port_number = port.get("port") if isinstance(port, dict) else None
            # bool 是 int 的子类，需显式排除
            if (
                not isinstance(port, dict)
                or not isinstance(port_number, int)
                or isinstance(port_number, bool)
                or not isinstance(port.get("protocol"), str)
            ):
                problems.append(
                    f"ports[{index}] must contain an integer 'port' and a string 'protocol'"
                )
    return problems


def _check_schema(schema: dict[str, Any]) -> list[str]:
    """校验 schema 字段定义的结构，返回问题描述列表（空列表即通过）。"""
    fields = schema.get("fields")
    if not isinstance(fields, list) or not fields:
        return ["schema must contain a non-empty 'fields' list"]
    problems: list[str] = []
    seen_names: set[str] = set()
    for index, field_def in enumerate(fields):
        if not isinstance(field_def, dict):
            problems.append(f"fields[{index}] must be an object")
            continue
        field_name = field_def.get("name")
        field_type = field_def.get("type")
        if not isinstance(field_name, str) or not field_name:
            problems.append(f"fields[{index}] must contain a non-empty string 'name'")
            continue
        if field_name in seen_names:
            problems.append(f"duplicate field name '{field_name}'")
        seen_names.add(field_name)
        if field_type not in ALLOWED_FIELD_TYPES:
            problems.append(f"field '{field_name}' has invalid type '{field_type}'")
        if field_type == "enum" and not isinstance(field_def.get("options"), list):
            problems.append(f"enum field '{field_name}' requires an 'options' list")
    return problems


def _load_plugin(directory: Path) -> ServicePlugin | None:
    """加载并校验单个插件目录，任何一步失败都返回 None 并记 warning。"""
    problems: list[str] = []
    manifest: dict[str, Any] | None = None
    schema: dict[str, Any] | None = None

    manifest_path = directory / "manifest.yaml"
    if manifest_path.is_file():
        try:
            loaded_manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError) as e:
            problems.append(f"failed to load manifest.yaml: {e}")
        else:
            if isinstance(loaded_manifest, dict):
                manifest = loaded_manifest
            else:
                problems.append("manifest.yaml must be a YAML mapping")
    else:
        problems.append("manifest.yaml not found")

    schema_path = directory / "schema.json"
    if schema_path.is_file():
        try:
            loaded_schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            problems.append(f"failed to load schema.json: {e}")
        else:
            if isinstance(loaded_schema, dict):
                schema = loaded_schema
            else:
                problems.append("schema.json must be a JSON object")
    else:
        problems.append("schema.json not found")

    if manifest is not None:
        problems.extend(_check_manifest(manifest, directory.name))
    if schema is not None:
        problems.extend(_check_schema(schema))
    # 模板完整性：每个 config_files 项都要有同名 .j2 模板，否则渲染必然失败
    if isinstance(manifest, dict) and isinstance(manifest.get("config_files"), list):
        for config_file in manifest["config_files"]:
            if (
                isinstance(config_file, str)
                and not (directory / "templates" / f"{config_file}.j2").is_file()
            ):
                problems.append(f"missing template for config file '{config_file}'")

    if problems:
        logger.warning(
            f"Skipping service plugin directory '{directory}': {'; '.join(problems)}"
        )
        return None
    if manifest is None or schema is None:
        # 加载失败必然已记入 problems，此分支只为类型收窄
        logger.warning(
            f"Skipping service plugin directory '{directory}': manifest or schema failed to load"
        )
        return None
    return ServicePlugin(
        name=directory.name,
        directory=directory,
        manifest=manifest,
        schema=schema,
    )


def list_services() -> list[ServicePlugin]:
    """扫描服务目录，返回全部通过校验的插件（按目录名排序）。

    Returns:
        插件列表；服务目录不存在时返回空列表（记 warning，不抛异常）。
    """
    services_dir = Path(settings.SERVICES_DIR)
    if not services_dir.is_dir():
        logger.warning(f"Services directory not found: {services_dir}")
        return []
    plugins: list[ServicePlugin] = []
    for entry in sorted(services_dir.iterdir()):
        if not entry.is_dir():
            continue
        plugin = _load_plugin(entry)
        if plugin is not None:
            plugins.append(plugin)
    return plugins


def get_service(name: str) -> ServicePlugin | None:
    """按名取单个服务插件，目录不存在或格式有问题时返回 None。

    Args:
        name: 服务名，必须等于 services/ 下的目录名。

    Returns:
        插件对象；未找到返回 None（由路由层转 404）。
    """
    # 路径安全：name 必须是纯目录名，拦截 ../ 与嵌套路径
    if not name or Path(name).name != name or name in {".", ".."}:
        return None
    directory = Path(settings.SERVICES_DIR) / name
    if not directory.is_dir():
        return None
    return _load_plugin(directory)
