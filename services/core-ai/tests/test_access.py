from app.config import settings


def create_access(client):
    return client.post(
        f"{settings.api_prefix}/domain/access-requests",
        json={"resource": "bi-dashboard", "requested_role": "viewer", "justification": "needs read"},
    )


def test_access_request_duplicate_detection(client):
    first = create_access(client)
    assert first.status_code == 200

    dup = create_access(client)
    assert dup.status_code == 409


def test_approve_and_reject_access_request(client):
    created = create_access(client).json()["access_request"]

    approved = client.post(f"{settings.api_prefix}/domain/access-requests/{created['id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["request"]["status"] == "approved"

    second = client.post(
        f"{settings.api_prefix}/domain/access-requests",
        json={"resource": "data-lake", "requested_role": "editor", "justification": "data updates"},
    ).json()["access_request"]

    rejected = client.post(
        f"{settings.api_prefix}/domain/access-requests/{second['id']}/reject",
        params={"reason": "insufficient training"},
    )
    assert rejected.status_code == 200
    body = rejected.json()["request"]
    assert body["status"] == "rejected"
    assert body["reject_reason"] == "insufficient training"


def test_list_access_requests_filters_by_status(client):
    create_access(client)
    resp = client.get(f"{settings.api_prefix}/domain/access-requests", params={"status": "pending"})
    assert resp.status_code == 200
    assert len(resp.json()["access_requests"]) == 1

    invalid = client.get(f"{settings.api_prefix}/domain/access-requests", params={"status": "unknown"})
    assert invalid.status_code == 400


def test_access_request_missing_role_validation(client):
    resp = client.post(
        f"{settings.api_prefix}/domain/access-requests",
        json={"resource": "db", "requested_role": "invalid", "justification": "need it"},
    )
    assert resp.status_code == 400
