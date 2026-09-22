# ruff: noqa: T201  —— CLI 脚本按设计输出到 stdout
"""重新生成服务种子文件：`services/<服务>/defaults/<配置>` = 模板按 schema 默认值的渲染结果。

为什么需要它：种子是「配置卷首次挂载为空时 entrypoint 播种的内容」，必须与平台渲染结果逐字节一致
（`tests/test_service_seeds.py` 守着这条不变量）。改了模板或 schema 默认值后，用它重新生成种子。

用法（在 platform/backend 下）：
    uv run python scripts/regen_seeds.py            # 只重写有差异的种子
    uv run python scripts/regen_seeds.py --check    # 只检查不写，列出漂移项
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402

SERVICES_DIR = Path(settings.SERVICES_DIR).resolve()


def render_all() -> list[tuple[Path, str]]:
    """返回 [(种子文件路径, 期望内容)]，渲染参数与 config_renderer/test_service_seeds 一致。"""
    out: list[tuple[Path, str]] = []
    for service_dir in sorted(p for p in SERVICES_DIR.iterdir() if p.is_dir()):
        schema_path = service_dir / "schema.json"
        manifest_path = service_dir / "manifest.yaml"
        if not schema_path.is_file() or not manifest_path.is_file():
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
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
            autoescape=False,
        )
        for config_file in manifest["config_files"]:
            template_path = templates_dir / f"{config_file}.j2"
            seed_path = service_dir / "defaults" / config_file
            if not template_path.is_file() or not seed_path.is_file():
                continue
            out.append(
                (seed_path, env.get_template(f"{config_file}.j2").render(**defaults))
            )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="重新生成服务种子文件")
    parser.add_argument("--check", action="store_true", help="只检查不写，列出漂移项")
    args = parser.parse_args()

    drifted = []
    for seed_path, rendered in render_all():
        current = seed_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        if current == rendered:
            continue
        drifted.append(seed_path)
        if not args.check:
            seed_path.write_text(rendered, encoding="utf-8", newline="\n")
            print(f"已更新 {seed_path.relative_to(SERVICES_DIR.parent.parent)}")
    if args.check and drifted:
        print("种子漂移：")
        for path in drifted:
            print(f"  {path}")
        return 1
    if not drifted:
        print("所有种子与模板默认渲染一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
