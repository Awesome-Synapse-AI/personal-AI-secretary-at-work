from datetime import date

from sqlmodel import Session, select

from app.config import settings
from app.models import LeaveEntitlement, LeaveRequest


def seed_entitlement(session: Session, days=10.0):
    session.add(
        LeaveEntitlement(
            user_id="local-user",
            year=2026,
            leave_type="annual",
            days_available=days,
        )
    )
    session.commit()


def test_create_leave_request_reduces_entitlement(client, session):
    seed_entitlement(session, days=5)
    payload = {"leave_type": "annual", "start_date": "2026-02-10", "end_date": "2026-02-12"}
    resp = client.post(f"{settings.api_prefix}/domain/requests", json=payload)
    assert resp.status_code == 200

    ent = client.get(
        f"{settings.api_prefix}/domain/entitlements/me",
        params={"year": 2026, "leave_type": "annual"},
    ).json()
    assert ent["available_days"] == 2.0

    events = client.get(
        f"{settings.api_prefix}/domain/availability",
        params={"start": "2026-02-10", "end": "2026-02-12"},
    ).json()["events"]
    assert any("Leave: annual" in ev["title"] for ev in events)


def test_leave_request_insufficient_balance_400(client, session):
    seed_entitlement(session, days=1)
    payload = {"leave_type": "annual", "start_date": "2026-02-01", "end_date": "2026-02-03"}
    resp = client.post(f"{settings.api_prefix}/domain/requests", json=payload)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Not enough leave balance"

    # entitlement should remain unchanged
    with Session(session.get_bind()) as check:
        ent = check.exec(select(LeaveEntitlement)).first()
        assert ent.days_available == 1


def test_approve_and_reject_leave_request(client, session):
    seed_entitlement(session, days=5)
    payload = {"leave_type": "annual", "start_date": "2026-03-01", "end_date": "2026-03-01"}
    resp = client.post(f"{settings.api_prefix}/domain/requests", json=payload)
    assert resp.status_code == 200
    from sqlmodel import select

    with Session(session.get_bind()) as check:
        req_id = check.exec(select(LeaveRequest)).first().id

    approve = client.post(f"{settings.api_prefix}/domain/requests/{req_id}/approve")
    assert approve.status_code == 200
    assert approve.json()["request"]["status"] == "approved"

    # create another request for rejection path
    payload["start_date"] = "2026-03-05"
    payload["end_date"] = "2026-03-05"
    second_resp = client.post(f"{settings.api_prefix}/domain/requests", json=payload)
    assert second_resp.status_code == 200
    with Session(session.get_bind()) as check:
        second_id = (
            check.exec(select(LeaveRequest).where(LeaveRequest.start_date == date(2026, 3, 5))).first().id
        )

    reject = client.post(
        f"{settings.api_prefix}/domain/requests/{second_id}/reject",
        params={"reason": "not eligible"},
    )
    assert reject.status_code == 200
    body = reject.json()["request"]
    assert body["status"] == "rejected"
    assert body["reject_reason"] == "not eligible"


def test_create_leave_without_entitlement_returns_400(client):
    resp = client.post(
        f"{settings.api_prefix}/domain/requests",
        json={"leave_type": "annual", "start_date": "2026-02-10", "end_date": "2026-02-10"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "No entitlement configured for this leave type/year"
