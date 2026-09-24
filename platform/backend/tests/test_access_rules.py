"""准入规则引擎的纯函数测试（不连库）。

判定语义是 R7/R8 的核心，必须穷举：deny 优先、allow 非空即白名单、空规则即放行，
以及「后缀匹配必须按点边界」这类容易写错的地方。
"""

import pytest

from app.access_rules import (
    RuleSet,
    RuleValueError,
    match_email_suffix,
    match_ip,
    normalize_email_suffix,
    normalize_ip,
)


class TestNormalizeEmailSuffix:
    def test_lowercases_and_strips_leading_at(self) -> None:
        assert normalize_email_suffix("@Example.COM") == "example.com"
        assert normalize_email_suffix("  Schkzy.CN  ") == "schkzy.cn"

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "com",  # 只写 TLD 会把整个顶级域放进白名单
            ".example.com",
            "example.com.",
            "a..b.com",
            "a b.com",
            "a@b.com",
            "a,b.com",
        ],
    )
    def test_rejects_invalid(self, raw: str) -> None:
        with pytest.raises(RuleValueError):
            normalize_email_suffix(raw)


class TestNormalizeIp:
    def test_single_ip_becomes_host_network(self) -> None:
        assert normalize_ip("10.0.0.5") == "10.0.0.5/32"

    def test_host_bits_are_cleared(self) -> None:
        assert normalize_ip("10.0.0.5/24") == "10.0.0.0/24"

    def test_ipv6_supported(self) -> None:
        assert normalize_ip("fd00:90::1") == "fd00:90::1/128"
        assert normalize_ip("FD00:0090::/64") == "fd00:90::/64"

    @pytest.mark.parametrize("raw", ["", "not-an-ip", "10.0.0.256", "10.0.0.0/33"])
    def test_rejects_invalid(self, raw: str) -> None:
        with pytest.raises(RuleValueError):
            normalize_ip(raw)


class TestMatchEmailSuffix:
    def test_empty_rules_allow_everything(self) -> None:
        # 空规则 = 不启用，这是「默认不限制」的实现方式
        assert match_email_suffix("a@whatever.com", RuleSet()).allowed

    def test_deny_hit_blocks(self) -> None:
        decision = match_email_suffix("a@evil.com", RuleSet(deny=("evil.com",)))
        assert decision.allowed is False
        assert decision.rule == "evil.com"
        assert "evil.com" in decision.reason

    def test_deny_wins_over_allow(self) -> None:
        rules = RuleSet(allow=("evil.com",), deny=("evil.com",))
        assert match_email_suffix("a@evil.com", rules).allowed is False

    def test_allowlist_blocks_unlisted(self) -> None:
        rules = RuleSet(allow=("schkzy.cn",))
        assert match_email_suffix("a@other.com", rules).allowed is False

    def test_subdomain_hits_parent_suffix(self) -> None:
        rules = RuleSet(allow=("schkzy.cn",))
        assert match_email_suffix("a@mail.schkzy.cn", rules).allowed is True

    def test_lookalike_domain_does_not_hit(self) -> None:
        # notschkzy.cn 不能命中 schkzy.cn：后缀匹配必须按点边界，否则白名单可被绕过
        rules = RuleSet(allow=("schkzy.cn",))
        assert match_email_suffix("a@notschkzy.cn", rules).allowed is False

    def test_case_insensitive(self) -> None:
        rules = RuleSet(allow=("schkzy.cn",))
        assert match_email_suffix("A@SCHKZY.CN", rules).allowed is True

    def test_malformed_email_rejected(self) -> None:
        assert match_email_suffix("no-at-sign", RuleSet()).allowed is False


class TestMatchIp:
    def test_empty_rules_allow_everything(self) -> None:
        assert match_ip("203.0.113.9", RuleSet()).allowed

    def test_deny_network_hit_blocks(self) -> None:
        decision = match_ip("10.1.2.3", RuleSet(deny=("10.0.0.0/8",)))
        assert decision.allowed is False
        assert decision.rule == "10.0.0.0/8"

    def test_deny_wins_over_allow(self) -> None:
        rules = RuleSet(allow=("10.0.0.0/8",), deny=("10.1.2.3",))
        assert match_ip("10.1.2.3", rules).allowed is False

    def test_allowlist_blocks_other(self) -> None:
        rules = RuleSet(allow=("10.0.0.0/8",))
        assert match_ip("203.0.113.9", rules).allowed is False

    def test_allowlist_permits_listed(self) -> None:
        rules = RuleSet(allow=("10.0.0.0/8",))
        assert match_ip("10.1.2.3", rules).allowed is True

    def test_ipv6_network(self) -> None:
        rules = RuleSet(allow=("fd00:90::/64",))
        assert match_ip("fd00:90::10d", rules).allowed is True

    def test_unparseable_source_is_denied_when_rules_exist(self) -> None:
        # 判不出地址时按拒绝处理：规则配了却放行「来源不明」，白名单等于形同虚设
        assert match_ip("testclient", RuleSet(allow=("10.0.0.0/8",))).allowed is False

    def test_unparseable_source_is_allowed_without_rules(self) -> None:
        # 没有规则时不该因为「判不出地址」而拦住任何人（默认不启用的含义）
        assert match_ip("testclient", RuleSet()).allowed is True
