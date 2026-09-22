"""审计日志查询接口测试：过滤、分页、动作字典与权限。

审计表只增不减，过滤必须落在数据库侧——用例断言 count 反映的是「过滤后总数」
而不是当前页条数，这是前端分页器能否正确翻页的前提。
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app import crud
from app.core.config import settings
from app.models import AuditLog

AUDIT_URL = f"{settings.API_V1_STR}/audit-logs"


@pytest.fixture(autouse=True)
def _clear_audit_logs(db: Session) -> None:
    """每个用例前清审计，断言只看本用例写入的记录。"""
    db.exec(delete(AuditLog))
    db.commit()


def _seed(db: Session, *, action: str, service_name: str | None, detail: str) -> None:
    """直接写一条审计（不走路由），让用例专注于查询语义。"""
    crud.record_audit_log(
        session=db,
        user_id=None,
        user_email="ops@bmcplatform.cn",
        action=action,
        service_name=service_name,
        detail=detail,
    )


def test_read_audit_logs_filters_by_action(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """action 精确匹配，且 count 是被过滤后的总数。"""
    _seed(db, action="service.start", service_name="chrony", detail="start")
    _seed(db, action="service.stop", service_name="chrony", detail="stop")
    _seed(db, action="service.stop", service_name="nginx", detail="stop")

    response = client.get(
        AUDIT_URL,
        params={"action": "service.stop"},
        headers=superuser_token_headers,
    )

    assert response.status_code == 200
    content = response.json()
    assert content["count"] == 2
    assert {entry["service_name"] for entry in content["data"]} == {"chrony", "nginx"}


def test_read_audit_logs_filters_by_service_name(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """service_name 精确匹配，用户类动作（service_name 为空）不会被命中。"""
    _seed(db, action="service.start", service_name="nginx", detail="start")
    _seed(db, action="user.create", service_name=None, detail="target=ops@x.cn")

    response = client.get(
        AUDIT_URL, params={"service_name": "nginx"}, headers=superuser_token_headers
    )

    content = response.json()
    assert content["count"] == 1
    assert content["data"][0]["action"] == "service.start"


def test_read_audit_logs_filters_by_keyword(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """q 模糊匹配表格里所有可见文本列（动作/服务/操作者/详情），大小写不敏感。"""
    _seed(db, action="service.start", service_name="chrony", detail="maxdistance=2")
    _seed(db, action="service.stop", service_name="chrony", detail="fault_mode=none")

    by_detail = client.get(
        AUDIT_URL, params={"q": "MAXDISTANCE"}, headers=superuser_token_headers
    ).json()
    assert by_detail["count"] == 1
    assert by_detail["data"][0]["action"] == "service.start"

    by_email = client.get(
        AUDIT_URL, params={"q": "OPS@BMC"}, headers=superuser_token_headers
    ).json()
    assert by_email["count"] == 2

    # 关键字框是「不知道用哪个筛选器」时的入口：搜动作名也要能命中
    by_action = client.get(
        AUDIT_URL, params={"q": "SERVICE.STOP"}, headers=superuser_token_headers
    ).json()
    assert by_action["count"] == 1
    assert by_action["data"][0]["action"] == "service.stop"

    # 服务名同理，且与动作关键字叠加时是「与」关系
    by_service = client.get(
        AUDIT_URL,
        params={"q": "CHRONY", "action": "service.start"},
        headers=superuser_token_headers,
    ).json()
    assert by_service["count"] == 1
    assert by_service["data"][0]["action"] == "service.start"


def test_read_audit_logs_paginates_with_filtered_count(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """分页只影响 data，count 始终是过滤后的全量数（前端据此算总页数）。"""
    for index in range(3):
        _seed(db, action="service.reload", service_name="nginx", detail=f"n={index}")
    _seed(db, action="user.create", service_name=None, detail="other")

    page = client.get(
        AUDIT_URL,
        params={"action": "service.reload", "offset": 1, "limit": 1},
        headers=superuser_token_headers,
    ).json()

    assert page["count"] == 3
    assert len(page["data"]) == 1


def test_read_audit_logs_rejects_limit_over_cap(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    """limit 超过上限返回 422：审计表只增，放开上限等于允许一次拖走整表。"""
    response = client.get(
        AUDIT_URL, params={"limit": 10_000}, headers=superuser_token_headers
    )
    assert response.status_code == 422


def test_read_audit_actions_returns_sorted_distinct_names(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    """动作字典来自实际数据、去重且升序，供筛选下拉直接渲染。"""
    _seed(db, action="user.create", service_name=None, detail="a")
    _seed(db, action="service.start", service_name="chrony", detail="b")
    _seed(db, action="service.start", service_name="nginx", detail="c")

    response = client.get(f"{AUDIT_URL}/actions", headers=superuser_token_headers)

    assert response.status_code == 200
    assert response.json()["data"] == ["service.start", "user.create"]


def test_read_audit_actions_requires_admin(
    client: TestClient, readonly_token_headers: dict[str, str]
) -> None:
    """动作字典同属审计面，readonly 角色读也要 403。"""
    response = client.get(f"{AUDIT_URL}/actions", headers=readonly_token_headers)
    assert response.status_code == 403
