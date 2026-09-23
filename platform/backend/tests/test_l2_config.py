"""l2_config 单元测试：.env 白名单键的解析、合并与原子写。

全部在 tmp_path 上跑，不碰真实部署目录；重点断言「非白名单内容（含密钥）字节不变」，
因为 .env 里同时装着 SECRET_KEY / POSTGRES_PASSWORD。
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from app import l2_config

# 模拟目标机上的真实 .env：注释行、空行、密钥、以及 6 个 L2 键
SAMPLE_ENV = """# BMC Services Platform 部署变量
PROJECT_NAME=BMC Services Platform
SECRET_KEY=super-secret-value-do-not-touch
POSTGRES_PASSWORD=pg-secret-value-do-not-touch

IMAGE_PREFIX=ghcr.example.com/org/
DHCP_PARENT_IFACE=enp125s0f1
L2_SUBNET=192.168.90.0/24
L2_SERVICES=dhcp,tftpd-hpa,rsyslog,chrony
L2_GATEWAY=192.168.90.1
L2_SUBNET_V6=fd00:90::/64
L2_GATEWAY_V6=fd00:90::1
"""


def _write_env(tmp_path: Path, content: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_reads_whitelisted_keys_only() -> None:
    """只返回白名单键：密钥与其它变量不出现在结果里。"""
    parsed = l2_config.parse_l2_values(SAMPLE_ENV)

    assert parsed.values == {
        "DHCP_PARENT_IFACE": "enp125s0f1",
        "L2_SUBNET": "192.168.90.0/24",
        "L2_SERVICES": "dhcp,tftpd-hpa,rsyslog,chrony",
        "L2_GATEWAY": "192.168.90.1",
        "L2_SUBNET_V6": "fd00:90::/64",
        "L2_GATEWAY_V6": "fd00:90::1",
    }
    assert "SECRET_KEY" not in parsed.values
    assert parsed.duplicates == []


def test_parse_handles_quotes_comments_and_duplicates() -> None:
    """带引号的值去引号、未加引号的值截掉行尾注释、重复键以最后一次出现为准。"""
    content = (
        'L2_SUBNET="10.0.0.0/24"\n'
        "L2_GATEWAY=10.0.0.1 # 宿主测试口地址\n"
        "DHCP_PARENT_IFACE=old0\n"
        "DHCP_PARENT_IFACE=new1\n"
        "not_a_key=ignored\n"
    )

    parsed = l2_config.parse_l2_values(content)

    assert parsed.values["L2_SUBNET"] == "10.0.0.0/24"
    assert parsed.values["L2_GATEWAY"] == "10.0.0.1"
    assert parsed.values["DHCP_PARENT_IFACE"] == "new1"
    assert parsed.duplicates == ["DHCP_PARENT_IFACE"]
    assert "not_a_key" not in parsed.values


def test_render_keeps_other_lines_byte_identical() -> None:
    """改一个 L2 键时，其余行（含密钥与注释）必须逐字节不变。"""
    updated = l2_config.render_l2_updates(
        SAMPLE_ENV, {"DHCP_PARENT_IFACE": "enp125s0f3"}
    )

    assert "DHCP_PARENT_IFACE=enp125s0f3\n" in updated
    assert "DHCP_PARENT_IFACE=enp125s0f1" not in updated
    for untouched in (
        "# BMC Services Platform 部署变量\n",
        "SECRET_KEY=super-secret-value-do-not-touch\n",
        "POSTGRES_PASSWORD=pg-secret-value-do-not-touch\n",
        "L2_GATEWAY_V6=fd00:90::1\n",
    ):
        assert untouched in updated


def test_render_appends_missing_keys_in_defined_order() -> None:
    """缺失键追加到末尾，且顺序按 L2_KEYS 定义（同输入产出稳定字节）。"""
    content = "SECRET_KEY=keep-me\nDHCP_PARENT_IFACE=enp125s0f1\n"

    updated = l2_config.render_l2_updates(
        content, {"L2_GATEWAY": "192.168.90.1", "L2_SUBNET": "192.168.90.0/24"}
    )

    assert updated == (
        "SECRET_KEY=keep-me\n"
        "DHCP_PARENT_IFACE=enp125s0f1\n"
        "L2_SUBNET=192.168.90.0/24\n"
        "L2_GATEWAY=192.168.90.1\n"
    )


def test_render_adds_trailing_newline_before_appending() -> None:
    """原文件末尾没有换行时，先补一个再追加，避免与新键粘连。"""
    updated = l2_config.render_l2_updates(
        "SECRET_KEY=keep-me", {"L2_SUBNET": "10.0.0.0/24"}
    )

    assert updated == "SECRET_KEY=keep-me\nL2_SUBNET=10.0.0.0/24\n"


def test_render_preserves_crlf_line_endings() -> None:
    """CRLF 文件（从 Windows 侧生成过）替换后仍是 CRLF，不混入 LF。"""
    content = "SECRET_KEY=keep-me\r\nL2_SUBNET=10.0.0.0/24\r\n"

    updated = l2_config.render_l2_updates(content, {"L2_SUBNET": "10.0.1.0/24"})

    assert updated == "SECRET_KEY=keep-me\r\nL2_SUBNET=10.0.1.0/24\r\n"


def test_render_replaces_last_duplicate_occurrence() -> None:
    """重复键只改最后一次出现（dotenv 语义：后者生效），前面那行原样留着。"""
    content = "DHCP_PARENT_IFACE=old0\nSECRET_KEY=keep-me\nDHCP_PARENT_IFACE=old1\n"

    updated = l2_config.render_l2_updates(content, {"DHCP_PARENT_IFACE": "enp125s0f3"})

    assert (
        updated
        == "DHCP_PARENT_IFACE=old0\nSECRET_KEY=keep-me\nDHCP_PARENT_IFACE=enp125s0f3\n"
    )


def test_render_allows_clearing_a_key() -> None:
    """停用二层夹具要把 DHCP_PARENT_IFACE 置空：空值是合法输入。"""
    updated = l2_config.render_l2_updates(SAMPLE_ENV, {"DHCP_PARENT_IFACE": ""})

    assert "DHCP_PARENT_IFACE=\n" in updated
    assert l2_config.parse_l2_values(updated).values["DHCP_PARENT_IFACE"] == ""


@pytest.mark.parametrize(
    "value",
    ["bad value", "a\nb", "x#y", '"quoted"', "a=1"],
)
def test_render_rejects_values_with_unsupported_characters(value: str) -> None:
    """含空格/换行/井号/引号/等号的值一律拒绝，防止改掉 .env 的文件语义。"""
    with pytest.raises(l2_config.L2EnvError, match="unsupported characters"):
        l2_config.render_l2_updates(SAMPLE_ENV, {"L2_SUBNET": value})


def test_render_rejects_non_l2_key() -> None:
    """白名单之外的键（如 SECRET_KEY）拒绝改写。"""
    with pytest.raises(l2_config.L2EnvError, match="is not an L2 key"):
        l2_config.render_l2_updates(SAMPLE_ENV, {"SECRET_KEY": "hijacked"})


def test_read_missing_file_raises() -> None:
    """部署目录没挂载时给出明确错误，而不是静默返回空值。"""
    with pytest.raises(l2_config.L2EnvError, match="Failed to read"):
        l2_config.read_l2_env(Path("/nonexistent/host-deploy/.env"))


def test_write_updates_file_and_keeps_secrets_intact(tmp_path: Path) -> None:
    """写盘后：目标键已更新、密钥原样、权限是 0600。"""
    path = _write_env(tmp_path, SAMPLE_ENV)

    l2_config.write_l2_env(
        path,
        {
            "DHCP_PARENT_IFACE": "enp125s0f3",
            "L2_SUBNET": "192.168.91.0/24",
            "L2_GATEWAY": "192.168.91.1",
        },
    )

    content = path.read_text(encoding="utf-8")
    assert "DHCP_PARENT_IFACE=enp125s0f3\n" in content
    assert "L2_SUBNET=192.168.91.0/24\n" in content
    assert "L2_GATEWAY=192.168.91.1\n" in content
    assert "SECRET_KEY=super-secret-value-do-not-touch\n" in content
    assert "POSTGRES_PASSWORD=pg-secret-value-do-not-touch\n" in content
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_clears_key_when_disabling(tmp_path: Path) -> None:
    """停用路径：写入空值后可被解析回空串（而不是把键删掉，便于下次启用预填）。"""
    path = _write_env(tmp_path, SAMPLE_ENV)

    l2_config.write_l2_env(path, {"DHCP_PARENT_IFACE": ""})

    assert l2_config.read_l2_env(path).values["DHCP_PARENT_IFACE"] == ""
    assert "L2_SUBNET=192.168.90.0/24\n" in path.read_text(encoding="utf-8")


def test_write_rejects_illegal_value_without_touching_file(tmp_path: Path) -> None:
    """非法值在写盘前就被拒，文件保持原样（不留下半成品）。"""
    path = _write_env(tmp_path, SAMPLE_ENV)

    with pytest.raises(l2_config.L2EnvError):
        l2_config.write_l2_env(path, {"L2_SUBNET": "1.2.3.4/24\nSECRET_KEY=oops"})

    assert path.read_text(encoding="utf-8") == SAMPLE_ENV
