"""服务种子文件一致性：defaults/<cfg> 必须等于模板按 schema 默认值的渲染结果。

模板头部把这条不变量写成契约（改字段需同步 schema.json 并重新生成种子文件）；
配置卷首次挂载为空时 entrypoint 播种的就是这些种子文件，一旦与模板漂移，
"独立部署"与"平台渲染"两条路径的行为就会不一致——阶段 3 实机验证时踩过：
6 个服务的种子落后于模板（缺故障注入块、缺 TLS 配置等），独立部署拿到的
是过期配置。
"""

import json
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

from app.core.config import settings

REPO_SERVICES_DIR = Path(settings.SERVICES_DIR).resolve()


def test_seed_files_match_default_render() -> None:
    """逐服务校验：模板与种子文件都存在时，内容必须逐字节一致。"""
    checked = 0
    drifted: list[str] = []
    for service_dir in sorted(p for p in REPO_SERVICES_DIR.iterdir() if p.is_dir()):
        manifest_path = service_dir / "manifest.yaml"
        schema_path = service_dir / "schema.json"
        if not manifest_path.exists() or not schema_path.exists():
            continue
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        defaults = {
            field["name"]: field["default"]
            for field in schema["fields"]
            if "default" in field
        }
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        templates_dir = service_dir / "templates"
        env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            # 与 config_renderer.render_config 保持同一套渲染环境参数
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
            autoescape=False,
        )
        for config_file in manifest["config_files"]:
            template_path = templates_dir / f"{config_file}.j2"
            seed_path = service_dir / "defaults" / config_file
            if not template_path.exists() or not seed_path.exists():
                continue
            checked += 1
            rendered = env.get_template(f"{config_file}.j2").render(**defaults)
            seed = seed_path.read_text(encoding="utf-8").replace("\r\n", "\n")
            if seed != rendered:
                drifted.append(f"{service_dir.name}/{config_file}")

    # 防止服务目录扫不到导致"空跑通过"
    assert checked >= 10, f"只校验到 {checked} 个模板/种子组合，服务目录可能没扫全"
    assert not drifted, f"种子文件与模板默认渲染不一致，需重新生成：{drifted}"
