from sqlmodel import Session, select

from app.config import settings
from app.models import TravelRequest


def test_create_travel_and_conflict_on_submit(client):
    payload = {
        "origin": "NYC",
        "destination": "LAX",
        "departure_date": "2026-04-01",
        "return_date": "2026-04-05",
    }
    ok = client.post(f"{settings.api_prefix}/domain/travel-requests", json=payload)
    assert ok.status_code == 200

    conflict = client.post(
        f"{settings.api_prefix}/domain/travel-requests",
        json={
            "origin": "NYC",
            "destination": "SFO",
            "departure_date": "2026-04-03",
            "return_date": "2026-04-07",
        },
    )
    assert conflict.status_code == 409


def test_travel_validation_errors(client):
    # return date before departure
    bad_return = client.post(
        f"{settings.api_prefix}/domain/travel-requests",
        json={
            "origin": "NYC",
            "destination": "LAX",
            "departure_date": "2026-02-10",
            "return_date": "2026-02-09",
        },
    )
    assert bad_return.status_code == 400

    # bad date format
    bad_date = client.post(
        f"{settings.api_prefix}/domain/travel-requests",
        json={
            "origin": "NYC",
            "destination": "LAX",
            "departure_date": "10-02-2026",
        },
    )
    assert bad_date.status_code == 400


def test_approve_travel_and_creation_conflict(client, session):
    first = client.post(
        f"{settings.api_prefix}/domain/travel-requests",
        json={
            "origin": "NYC",
            "destination": "SEA",
            "departure_date": "2026-05-01",
            "return_date": "2026-05-05",
        },
    )
    assert first.status_code == 200
    with Session(session.get_bind()) as check:
        first_id = check.exec(select(TravelRequest)).first().id

    approve_first = client.post(f"{settings.api_prefix}/domain/travel-requests/{first_id}/approve")
    assert approve_first.status_code == 200
    assert approve_first.json()["travel"]["status"] == "approved"

    conflict = client.post(
        f"{settings.api_prefix}/domain/travel-requests",
        json={
            "origin": "NYC",
            "destination": "CHI",
            "departure_date": "2026-05-03",
            "return_date": "2026-05-04",
        },
    )
    assert conflict.status_code == 409

    # non-overlapping request can be approved
    second = client.post(
        f"{settings.api_prefix}/domain/travel-requests",
        json={
            "origin": "NYC",
            "destination": "BOS",
            "departure_date": "2026-05-10",
            "return_date": "2026-05-12",
        },
    )
    assert second.status_code == 200
    with Session(session.get_bind()) as check:
        second_id = check.exec(select(TravelRequest).where(TravelRequest.destination == "BOS")).first().id
    approve_second = client.post(f"{settings.api_prefix}/domain/travel-requests/{second_id}/approve")
    assert approve_second.status_code == 200


def test_reject_travel_request(client, session):
    created_resp = client.post(
        f"{settings.api_prefix}/domain/travel-requests",
        json={
            "origin": "BOS",
            "destination": "MIA",
            "departure_date": "2026-06-10",
        },
    )
    assert created_resp.status_code == 200
    with Session(session.get_bind()) as check:
        created_id = check.exec(select(TravelRequest).where(TravelRequest.destination == "MIA")).first().id

    reject = client.post(
        f"{settings.api_prefix}/domain/travel-requests/{created_id}/reject",
        json={"reason": "budget"},
    )
    assert reject.status_code == 200
    body = reject.json()["travel"]
    assert body["status"] == "rejected"
