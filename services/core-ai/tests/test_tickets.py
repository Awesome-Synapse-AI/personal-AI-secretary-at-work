from app.config import settings
from app.models import TicketStatus, TicketType


def create_ticket(client, **overrides):
    payload = {
        "type": TicketType.IT.value,
        "description": "Laptop not booting",
        "priority": "high",
        "category": "hardware",
    }
    payload.update(overrides)
    return client.post(f"{settings.api_prefix}/domain/tickets", json=payload)


def test_create_and_get_ticket(client):
    created = create_ticket(client)
    assert created.status_code == 200
    ticket_id = created.json()["ticket"]["id"]

    fetched = client.get(f"{settings.api_prefix}/domain/tickets/{ticket_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == ticket_id


def test_list_my_tickets_returns_created(client):
    create_ticket(client)
    listing = client.get(f"{settings.api_prefix}/domain/tickets/me")
    assert listing.status_code == 200
    assert len(listing.json()["tickets"]) == 1


def test_update_ticket_status_and_assignee(client):
    created = create_ticket(client).json()["ticket"]
    update = client.patch(
        f"{settings.api_prefix}/domain/tickets/{created['id']}",
        json={"status": TicketStatus.IN_PROGRESS.value, "assignee": "tech-ops"},
    )
    assert update.status_code == 200
    body = update.json()
    assert body["status"] == TicketStatus.IN_PROGRESS.value
    assert body["assignee"] == "tech-ops"


def test_update_ticket_invalid_status_rejected(client):
    created = create_ticket(client).json()["ticket"]
    bad = client.patch(
        f"{settings.api_prefix}/domain/tickets/{created['id']}",
        json={"status": "not_a_status"},
    )
    assert bad.status_code == 400


def test_ticket_type_validation(client):
    resp = client.post(
        f"{settings.api_prefix}/domain/tickets",
        json={"type": "invalid", "description": "broken mouse"},
    )
    assert resp.status_code == 400
