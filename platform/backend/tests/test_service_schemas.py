"""服务 schema 契约测试：字段元数据的形状必须一致，否则前端渲染会静默缺内容。

背景：nginx / nfs-ganesha / samba 三个服务的 schema 曾用 `description` 存字段名，
而平台渲染的是 `label`（前端 `ServiceField.label` 为必填）。多余的键被静默忽略，
结果这 30 个字段在详情页上只剩一个必填星号、没有字段名——页面上没有任何报错，
只有逐字段核对才发现。这里把契约固化成测试。
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.core.config import settings

# 与 tests/test_service_seeds.py 用同一份配置解析，避免两处各写一套相对路径
SERVICES_DIR = Path(settings.SERVICES_DIR).resolve()

# schema 字段允许出现的键（与 platform/frontend/src/api/services.ts 的 ServiceField 对齐）
ALLOWED_FIELD_KEYS = {
    "name",
    "type",
    "label",
    "help",
    "default",
    "required",
    "options",
    "pattern",
    "min",
    "max",
    "item_pattern",
    "secret",
    "group",
    "pem",
}

ALLOWED_TYPES = {"string", "integer", "boolean", "enum", "list", "text"}
ALLOWED_GROUPS = {"base", "fault"}


def _schemas() -> list[tuple[str, dict[str, Any]]]:
    out = []
    for schema_path in sorted(SERVICES_DIR.glob("*/schema.json")):
        out.append(
            (
                schema_path.parent.name,
                json.loads(schema_path.read_text(encoding="utf-8")),
            )
        )
    return out


SCHEMAS = _schemas()


def test_schemas_discovered() -> None:
    """至少要能发现 11 个服务的 schema，避免路径写错导致下面全部空跑。"""
    assert len(SCHEMAS) == 11, [name for name, _ in SCHEMAS]


@pytest.mark.parametrize("service,schema", SCHEMAS, ids=[name for name, _ in SCHEMAS])
def test_field_keys_are_known(service: str, schema: dict[str, Any]) -> None:
    """字段键必须在允许集合内：多余的键会被静默忽略，等于把内容写进了没人读的地方。"""
    unknown: dict[str, list[str]] = {}
    for field in schema["fields"]:
        extra = set(field) - ALLOWED_FIELD_KEYS
        if extra:
            unknown[field["name"]] = sorted(extra)
    assert not unknown, f"{service} 出现未知字段键（前端不会渲染）: {unknown}"


@pytest.mark.parametrize("service,schema", SCHEMAS, ids=[name for name, _ in SCHEMAS])
def test_every_field_has_label(service: str, schema: dict[str, Any]) -> None:
    """每个字段都必须有非空 label —— 否则详情页上只剩一个控件与必填星号。"""
    missing = [f["name"] for f in schema["fields"] if not f.get("label")]
    assert not missing, f"{service} 缺少 label 的字段: {missing}"


@pytest.mark.parametrize("service,schema", SCHEMAS, ids=[name for name, _ in SCHEMAS])
def test_field_types_and_groups_are_valid(service: str, schema: dict[str, Any]) -> None:
    """类型与分组取值受前端控件分支约束；enum 必须有 options。"""
    for field in schema["fields"]:
        assert field["type"] in ALLOWED_TYPES, (
            f"{service}.{field['name']} 类型非法: {field['type']}"
        )
        assert field.get("group", "base") in ALLOWED_GROUPS, (
            f"{service}.{field['name']} 分组非法: {field.get('group')}"
        )
        if field["type"] == "enum":
            assert field.get("options"), (
                f"{service}.{field['name']} 是 enum 但没有 options"
            )


@pytest.mark.parametrize("service,schema", SCHEMAS, ids=[name for name, _ in SCHEMAS])
def test_fault_mode_leads_fault_group_and_starts_with_none(
    service: str, schema: dict[str, Any]
) -> None:
    """故障注入模式必须是 fault 组的**第一个**字段，且首项为 none。

    前端按「故障组首字段是模式、其余是该模式的参数」组织页签，平台与套件也按
    fault_mode 取值判断当前模式；位置或首项变了会让故障页签渲染错位。
    """
    fault_fields = [f for f in schema["fields"] if f.get("group") == "fault"]
    assert fault_fields, f"{service} 没有故障注入字段"
    assert fault_fields[0]["name"] == "fault_mode", (
        f"{service} 的 fault 组首字段应为 fault_mode，实际: {fault_fields[0]['name']}"
    )
    fault_mode = fault_fields[0]
    assert fault_mode.get("options", [None])[0] == "none", (
        f"{service} 的 fault_mode 首项必须是 none，实际: {fault_mode.get('options')}"
    )
