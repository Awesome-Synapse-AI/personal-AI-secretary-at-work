from app.config import settings
from app import api


def test_health_endpoint(client):
    resp = client.get(f"{settings.api_prefix}/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_chat_endpoint_with_stub(monkeypatch, client):
    async def fake_handle_chat(session_store, message, session_id, user, tenant_id):
        return {
            "session_id": session_id or "s123",
            "message": f"echo: {message}",
            "actions": [],
            "pending_request": None,
            "events": [],
        }

    monkeypatch.setattr(api, "handle_chat", fake_handle_chat)

    resp = client.post(
        f"{settings.api_prefix}/chat",
        json={"message": "hello"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "s123"
    assert body["message"] == "echo: hello"
