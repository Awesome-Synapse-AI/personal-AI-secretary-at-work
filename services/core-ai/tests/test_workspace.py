from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.config import settings
from app.models import Booking, Desk, Equipment, ParkingSpot, ResourceType, Room


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def seed_workspace(session: Session):
    session.add_all(
        [
            Room(name="Ocean", capacity=6),
            Room(name="Sky", capacity=4),
            Desk(name="D-101"),
            Equipment(name="Projector A", type="projector"),
            ParkingSpot(name="P1"),
        ]
    )
    session.commit()


def test_list_rooms(client, session):
    seed_workspace(session)
    resp = client.get(f"{settings.api_prefix}/domain/rooms")
    assert resp.status_code == 200
    names = {room["name"] for room in resp.json()["rooms"]}
    assert {"Ocean", "Sky"} <= names


def test_room_booking_conflict_returns_alternatives(client, session):
    seed_workspace(session)
    start = datetime(2026, 1, 5, 10, 0)
    end = start + timedelta(hours=1)

    room_id = session.exec(select(Room.id).where(Room.name == "Ocean")).first()
    other_id = session.exec(select(Room.id).where(Room.name == "Sky")).first()

    first = client.post(
        f"{settings.api_prefix}/domain/rooms/{room_id}/book",
        json={"start_time": _iso(start), "end_time": _iso(end)},
    )
    assert first.status_code == 200

    conflict = client.post(
        f"{settings.api_prefix}/domain/rooms/{room_id}/book",
        json={"start_time": _iso(start), "end_time": _iso(end)},
    )
    assert conflict.status_code == 409
    available = {res["id"] for res in conflict.json()["detail"]["available"]}
    assert other_id in available


def test_room_booking_end_before_start_returns_400(client, session):
    seed_workspace(session)
    start = datetime(2026, 1, 5, 11, 0)
    end = datetime(2026, 1, 5, 10, 0)
    room_id = session.exec(select(Room.id)).first()
    resp = client.post(
        f"{settings.api_prefix}/domain/rooms/{room_id}/book",
        json={"start_time": _iso(start), "end_time": _iso(end)},
    )
    assert resp.status_code == 400
    assert "End time must be after start time." in resp.json()["detail"]


def test_desk_booking_by_name(client, session):
    seed_workspace(session)
    start = datetime(2026, 1, 6, 9, 0)
    end = start + timedelta(hours=2)
    resp = client.post(
        f"{settings.api_prefix}/domain/desks/book",
        json={"resource_name": "D-101", "start_time": _iso(start), "end_time": _iso(end)},
    )
    assert resp.status_code == 200
    with Session(session.get_bind()) as check:
        booking = check.exec(select(Booking)).first()
        assert booking is not None
        assert booking.resource_type == ResourceType.DESK
        assert booking.resource_id == check.exec(select(Desk.id)).first()


def test_desk_booking_requires_name_or_id(client):
    resp = client.post(
        f"{settings.api_prefix}/domain/desks/book",
        json={"start_time": "2026-01-06T09:00:00", "end_time": "2026-01-06T10:00:00"},
    )
    assert resp.status_code == 400
    assert "resource_name is required" in resp.json()["detail"]


def test_equipment_reservation_not_found_returns_404(client, session):
    seed_workspace(session)
    start = datetime(2026, 1, 6, 9, 0)
    end = start + timedelta(hours=1)
    resp = client.post(
        f"{settings.api_prefix}/domain/equipment/reserve",
        json={"resource_name": "Nonexistent", "start_time": _iso(start), "end_time": _iso(end)},
    )
    assert resp.status_code == 404


def test_equipment_conflict_blocks_second_booking(client, session):
    seed_workspace(session)
    start = datetime(2026, 1, 8, 13, 0)
    end = start + timedelta(hours=1)
    resp1 = client.post(
        f"{settings.api_prefix}/domain/equipment/reserve",
        json={"resource_name": "Projector A", "start_time": _iso(start), "end_time": _iso(end)},
    )
    assert resp1.status_code == 200
    resp2 = client.post(
        f"{settings.api_prefix}/domain/equipment/reserve",
        json={"resource_name": "Projector A", "start_time": _iso(start), "end_time": _iso(end)},
    )
    assert resp2.status_code == 409


def test_parking_booking_creates_event_and_records(client, session):
    seed_workspace(session)
    start = datetime(2026, 1, 7, 8, 0)
    end = start + timedelta(hours=1)
    resp = client.post(
        f"{settings.api_prefix}/domain/parking/book",
        json={"resource_name": "P1", "start_time": _iso(start), "end_time": _iso(end)},
    )
    assert resp.status_code == 200
    with Session(session.get_bind()) as check:
        persisted = check.exec(select(Booking).where(Booking.resource_type == ResourceType.PARKING)).first()
        assert persisted is not None
        booking_id = persisted.id

    avail = client.get(
        f"{settings.api_prefix}/domain/availability",
        params={"start": _iso(start), "end": _iso(end)},
    )
    assert avail.status_code == 200
    titles = [ev["title"] for ev in avail.json()["events"]]
    assert any("Parking booking" in t for t in titles)


def test_availability_no_events_returns_empty(client):
    resp = client.get(
        f"{settings.api_prefix}/domain/availability",
        params={"start": "2026-01-01T00:00:00", "end": "2026-01-01T23:59:00"},
    )
    assert resp.status_code == 200
    assert resp.json()["events"] == []
