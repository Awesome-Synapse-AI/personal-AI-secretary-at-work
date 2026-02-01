from app.config import settings


def test_expense_validation_errors(client):
    negative = client.post(
        f"{settings.api_prefix}/domain/expenses",
        json={"amount": -1, "currency": "USD", "date": "2026-02-01", "category": "food"},
    )
    assert negative.status_code == 400

    bad_currency = client.post(
        f"{settings.api_prefix}/domain/expenses",
        json={"amount": 10, "currency": "US", "date": "2026-02-01", "category": "food"},
    )
    assert bad_currency.status_code == 400

    bad_date = client.post(
        f"{settings.api_prefix}/domain/expenses",
        json={"amount": 10, "currency": "USD", "date": "02-01-2026", "category": "food"},
    )
    assert bad_date.status_code == 400

    missing_category = client.post(
        f"{settings.api_prefix}/domain/expenses",
        json={"amount": 10, "currency": "USD", "date": "2026-02-01", "category": ""},
    )
    assert missing_category.status_code == 400


def test_create_expense_and_attach_receipt(client):
    created = client.post(
        f"{settings.api_prefix}/domain/expenses",
        json={"amount": 100.5, "currency": "USD", "date": "2026-02-02", "category": "travel"},
    )
    assert created.status_code == 200
    expense_id = created.json()["expense"]["id"]

    receipt = client.post(
        f"{settings.api_prefix}/domain/expenses/{expense_id}/attach-receipt",
        json={"url": "https://example.com/receipt.pdf", "content_type": "application/pdf"},
    )
    assert receipt.status_code == 200
    assert receipt.json()["expense_id"] == expense_id


def test_approve_and_reject_expense(client):
    created = client.post(
        f"{settings.api_prefix}/domain/expenses",
        json={"amount": 50, "currency": "USD", "date": "2026-02-03", "category": "meals"},
    ).json()["expense"]

    approved = client.post(
        f"{settings.api_prefix}/domain/expenses/{created['id']}/approve",
        json={"reason": "looks good"},
    )
    assert approved.status_code == 200
    assert approved.json()["expense"]["status"] == "approved"

    created2 = client.post(
        f"{settings.api_prefix}/domain/expenses",
        json={"amount": 75, "currency": "USD", "date": "2026-02-04", "category": "hotel"},
    ).json()["expense"]

    rejected = client.post(
        f"{settings.api_prefix}/domain/expenses/{created2['id']}/reject",
        json={"reason": "missing receipt"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["expense"]["status"] == "rejected"
