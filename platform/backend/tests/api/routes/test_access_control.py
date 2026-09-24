"""访问控制管理端：规则 CRUD、注册开关、IP 规则防自锁自检。

覆盖 AC6（IP 规则默认不生效 / deny 生效）、AC7（规则变更入审计）、AC8（防自锁拒绝保存）。

**防自锁测试的注意点**：默认 TestClient 的 peer 是 "testclient"（不是 IP），而
`match_ip` 对「判不出地址」按拒绝处理。因此**通过默认客户端根本无法建立 IP 白名单**——
这本身就是防自锁在起作用；要测「能建立白名单」必须自建一个带真实 peer 的客户端。
"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.main import app
from app.models import AuditLog
from tests.utils.access import add_rule, wipe_access_state

RULES = f"{settings.API_V1_STR}/access-control/rules"
SETTINGS_URL = f"{settings.API_V1_STR}/access-control/settings"
PREFLIGHT = f"{settings.API_V1_STR}/access-control/preflight"


@pytest.fixture(autouse=True)
def clean_state(db: Session) -> Generator[None]:
    wipe_access_state(db)
    yield
    wipe_access_state(db)


def test_requires_admin(
    client: TestClient, readonly_token_headers: dict[str, str]
) -> None:
    assert client.get(RULES, headers=readonly_token_headers).status_code == 403
    assert (
        client.post(
            RULES,
            headers=readonly_token_headers,
            json={"kind": "ip", "list_type": "deny", "value": "203.0.113.1"},
        ).status_code
        == 403
    )


def test_create_and_list_email_suffix_rule(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    created = client.post(
        RULES,
        headers=superuser_token_headers,
        json={
            "kind": "email_suffix",
            "list_type": "allow",
            "value": "@Schkzy.CN",
            "note": "只允许公司域名",
        },
    )
    assert created.status_code == 201, created.text
    # 入库前归一化：去 @、转小写
    assert created.json()["value"] == "schkzy.cn"

    listed = client.get(RULES, headers=superuser_token_headers)
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    filtered = client.get(
        RULES, headers=superuser_token_headers, params={"kind": "ip"}
    ).json()
    assert filtered["count"] == 0

    actions = [row.action for row in db.exec(select(AuditLog)).all()]
    assert "access_rule.create" in actions


def test_create_duplicate_rule_conflicts(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    add_rule(db, kind="email_suffix", list_type="allow", value="schkzy.cn")
    response = client.post(
        RULES,
        headers=superuser_token_headers,
        json={"kind": "email_suffix", "list_type": "allow", "value": "schkzy.cn"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Rule already exists"


def test_invalid_rule_value_rejected(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    response = client.post(
        RULES,
        headers=superuser_token_headers,
        json={"kind": "ip", "list_type": "deny", "value": "not-an-ip"},
    )
    assert response.status_code == 400
    assert "Invalid IP address" in response.json()["detail"]


def test_unknown_kind_filter_rejected(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    assert (
        client.get(
            RULES, headers=superuser_token_headers, params={"kind": "nope"}
        ).status_code
        == 400
    )


def test_update_rule(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    rule = add_rule(db, kind="email_suffix", list_type="deny", value="evil.com")
    response = client.patch(
        f"{RULES}/{rule.id}",
        headers=superuser_token_headers,
        json={
            "kind": "email_suffix",
            "list_type": "deny",
            "value": "bad.example.com",
            "note": "改名",
        },
    )
    assert response.status_code == 200
    assert response.json()["value"] == "bad.example.com"
    assert "access_rule.update" in [
        row.action for row in db.exec(select(AuditLog)).all()
    ]


def test_delete_rule(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    rule = add_rule(db, kind="email_suffix", list_type="deny", value="evil.com")
    assert (
        client.delete(f"{RULES}/{rule.id}", headers=superuser_token_headers).status_code
        == 200
    )
    assert client.get(RULES, headers=superuser_token_headers).json()["count"] == 0
    assert "access_rule.delete" in [
        row.action for row in db.exec(select(AuditLog)).all()
    ]


def test_delete_missing_rule_404(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    import uuid

    response = client.delete(f"{RULES}/{uuid.uuid4()}", headers=superuser_token_headers)
    assert response.status_code == 404


def test_ip_deny_rule_can_be_created(db: Session) -> None:
    """deny 别的网段不会锁住自己，因此可以建。

    必须用带真实 peer 的客户端：默认客户端的 peer 是 "testclient"（非 IP），
    配了任何规则后它都会被判为「来源不明」而拒绝——那测的就不是这条规则了。
    """
    from tests.utils.utils import get_superuser_token_headers

    with TestClient(app, client=("198.51.100.7", 51002)) as local_client:
        headers = get_superuser_token_headers(local_client, db)
        response = local_client.post(
            RULES,
            headers=headers,
            json={"kind": "ip", "list_type": "deny", "value": "203.0.113.0/24"},
        )
    assert response.status_code == 201, response.text
    assert response.json()["value"] == "203.0.113.0/24"


def test_ip_allowlist_blocked_when_source_unresolvable(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """防自锁：判不出当前来源地址时，建立白名单会被拒绝（而不是写进去把自己关在门外）。"""
    response = client.post(
        RULES,
        headers=superuser_token_headers,
        json={"kind": "ip", "list_type": "allow", "value": "10.0.0.0/8"},
    )
    assert response.status_code == 400
    assert "would block your current address" in response.json()["detail"]


def test_ip_allowlist_creatable_from_allowed_source(db: Session) -> None:
    """从允许范围内的来源建白名单可以成功（自检按当前请求 IP 判定）。"""
    from tests.utils.utils import get_superuser_token_headers

    with TestClient(app, client=("10.0.0.5", 51000)) as local_client:
        headers = get_superuser_token_headers(local_client, db)
        response = local_client.post(
            RULES,
            headers=headers,
            json={"kind": "ip", "list_type": "allow", "value": "10.0.0.0/8"},
        )
        assert response.status_code == 201, response.text
        second = local_client.post(
            RULES,
            headers=headers,
            json={"kind": "ip", "list_type": "allow", "value": "198.51.100.0/24"},
        )
        assert second.status_code == 201, second.text
        second_id = second.json()["id"]

    # 从 198.51.100.7 删掉覆盖自己的那条：剩下的 10.0.0.0/8 不含它 → 必须拒绝
    with TestClient(app, client=("198.51.100.7", 51001)) as outsider:
        outside_headers = get_superuser_token_headers(outsider, db)
        blocked = outsider.delete(f"{RULES}/{second_id}", headers=outside_headers)
        assert blocked.status_code == 400
        assert "would block your current address" in blocked.json()["detail"]


def test_deleting_last_allowlist_rule_is_allowed(db: Session) -> None:
    """删掉最后一条白名单等于「没有规则 = 不限制」，不会锁住任何人，因此放行。"""
    from tests.utils.utils import get_superuser_token_headers

    with TestClient(app, client=("10.0.0.5", 51003)) as local_client:
        headers = get_superuser_token_headers(local_client, db)
        created = local_client.post(
            RULES,
            headers=headers,
            json={"kind": "ip", "list_type": "allow", "value": "10.0.0.0/8"},
        )
        assert created.status_code == 201
        deleted = local_client.delete(
            f"{RULES}/{created.json()['id']}", headers=headers
        )
    assert deleted.status_code == 200


def test_preflight_reports_current_ip_and_reason(db: Session) -> None:
    from tests.utils.utils import get_superuser_token_headers

    with TestClient(app, client=("198.51.100.7", 51004)) as local_client:
        headers = get_superuser_token_headers(local_client, db)
        allowed = local_client.post(
            PREFLIGHT,
            headers=headers,
            json={"allow": [], "deny": ["203.0.113.0/24"]},
        )
        assert allowed.status_code == 200
        body = allowed.json()
        assert body["allowed"] is True
        assert body["current_ip"] == "198.51.100.7"

        blocked = local_client.post(
            PREFLIGHT,
            headers=headers,
            json={"allow": ["10.0.0.0/8"], "deny": []},
        ).json()
        assert blocked["allowed"] is False
        assert blocked["reason"]


def test_settings_default_enabled_and_toggle(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    initial = client.get(SETTINGS_URL, headers=superuser_token_headers)
    assert initial.status_code == 200
    assert initial.json() == {"registration_enabled": True, "email_configured": False}

    updated = client.patch(
        SETTINGS_URL,
        headers=superuser_token_headers,
        json={"registration_enabled": False},
    )
    assert updated.status_code == 200
    assert updated.json()["registration_enabled"] is False

    assert "platform_setting.update" in [
        row.action for row in db.exec(select(AuditLog)).all()
    ]
