import pytest

from app.agents import domain as domain_agent
from app.agents.domain import _handle_doc_qa


@pytest.mark.asyncio
async def test_doc_qa_success(monkeypatch):
    called = {}

    async def fake_call_tool(state, service, path, payload, action_type):
        called["payload"] = payload
        return {
            "type": action_type,
            "status": "ok",
            "result": {
                "matches": [
                    {"title": "Policy", "score": 0.91, "path": "/docs/policy.pdf"},
                    {"title": "Handbook", "score": 0.75, "path": "/docs/handbook.pdf"},
                ]
            },
        }

    monkeypatch.setattr(domain_agent, "_call_tool", fake_call_tool)

    response, pending, actions = await _handle_doc_qa("What is the travel policy?", None, {})

    assert "Policy" in response
    assert "Handbook" in response
    assert pending is None
    assert actions and actions[0]["status"] == "ok"
    assert called["payload"]["query"] == "What is the travel policy?"


@pytest.mark.asyncio
async def test_doc_qa_no_matches(monkeypatch):
    async def fake_call_tool(state, service, path, payload, action_type):
        return {"type": action_type, "status": "ok", "result": {"matches": []}}

    monkeypatch.setattr(domain_agent, "_call_tool", fake_call_tool)

    response, pending, actions = await _handle_doc_qa("Any results?", None, {})

    assert "found no relevant matches" in response.lower()
    assert actions and actions[0]["status"] == "ok"


@pytest.mark.asyncio
async def test_doc_qa_failure(monkeypatch):
    async def fake_call_tool(state, service, path, payload, action_type):
        return {"type": action_type, "status": "failed", "error": "service down"}

    monkeypatch.setattr(domain_agent, "_call_tool", fake_call_tool)

    response, pending, actions = await _handle_doc_qa("Hello?", None, {})

    assert "couldn't search" in response.lower()
    assert actions and actions[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_doc_qa_empty_message():
    response, pending, actions = await _handle_doc_qa("   ", None, {})
    assert "upload a document" in response.lower()
    assert actions == []
