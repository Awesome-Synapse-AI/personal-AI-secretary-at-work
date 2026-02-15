import pytest

from app.agents import domain as domain_agent
from app.agents.clarification import RequestType, classify_request, extract_fields
from app.state import ChatState


@pytest.mark.asyncio
async def test_classify_request_ops_falls_back_to_travel_when_llm_empty(monkeypatch):
    async def fake_call_llm_json(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.clarification.call_llm_json", fake_call_llm_json)

    request_type, fields = await classify_request(
        "ops", "I want to reserve a car on 8/Mar/2024 to travel to Bangkok"
    )

    assert request_type == RequestType.TRAVEL
    assert fields == {}


@pytest.mark.asyncio
async def test_domain_ops_asks_follow_up_for_travel_when_classifier_llm_empty(monkeypatch):
    async def fake_call_llm_json(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.clarification.call_llm_json", fake_call_llm_json)

    state = ChatState(
        message="I want to reserve a car on 8/Mar/2024 to travel to Bangkok",
        domain="ops",
        pending_request=None,
        actions=[],
        events=[],
    )

    result = await domain_agent.domain_node(state)

    assert result["pending_request"] is not None
    assert result["pending_request"]["type"] == RequestType.TRAVEL
    assert "departing from" in result["response"].lower()


@pytest.mark.asyncio
async def test_classify_request_hr_fallback_when_llm_empty(monkeypatch):
    async def fake_call_llm_json(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.clarification.call_llm_json", fake_call_llm_json)
    request_type, fields = await classify_request("hr", "I need sick leave next Monday")
    assert request_type == RequestType.LEAVE
    assert fields == {}


@pytest.mark.asyncio
async def test_classify_request_it_fallback_when_llm_empty(monkeypatch):
    async def fake_call_llm_json(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.clarification.call_llm_json", fake_call_llm_json)
    request_type, fields = await classify_request("it", "Please grant me access to the data lake")
    assert request_type == RequestType.ACCESS
    assert fields == {}


@pytest.mark.asyncio
async def test_classify_request_workspace_fallback_when_llm_empty(monkeypatch):
    async def fake_call_llm_json(*args, **kwargs):
        return None

    monkeypatch.setattr("app.agents.clarification.call_llm_json", fake_call_llm_json)
    request_type, fields = await classify_request("workspace", "Book a room tomorrow 2pm")
    assert request_type == RequestType.WORKSPACE_BOOKING
    assert fields == {}


@pytest.mark.asyncio
async def test_domain_pending_uses_missing_field_fallback_when_extract_empty(monkeypatch):
    async def fake_extract_fields(*args, **kwargs):
        return {}

    monkeypatch.setattr(domain_agent, "extract_fields", fake_extract_fields)

    state = ChatState(
        message="Dubai",
        domain="ops",
        pending_request={
            "domain": "ops",
            "type": RequestType.TRAVEL,
            "filled": {"destination": "Bangkok", "departure_date": None, "return_date": None},
            "missing": ["origin", "departure_date", "return_date"],
            "step": "collecting_details",
        },
        actions=[],
        events=[],
    )

    result = await domain_agent.domain_node(state)

    assert result["pending_request"] is not None
    assert result["pending_request"]["filled"]["origin"] == "Dubai"
    assert "departure date" in result["response"].lower()


@pytest.mark.asyncio
async def test_submit_access_request_normalizes_write_role(monkeypatch):
    captured = {}

    async def fake_call_tool(state, service, path, payload, action_type):
        captured["payload"] = payload
        return {"type": action_type, "status": "submitted", "payload": payload}

    monkeypatch.setattr(domain_agent, "_call_tool", fake_call_tool)

    action = await domain_agent._submit_access_request(
        {"filled": {"resource": "repo-x", "requested_role": "write", "justification": "need to commit"}},
        {},
    )

    assert action["status"] == "submitted"
    assert captured["payload"]["requested_role"] == "editor"


@pytest.mark.asyncio
async def test_extract_fields_accepts_string_request_type(monkeypatch):
    async def fake_call_llm_json(*args, **kwargs):
        return {"request_type": "travel", "fields": {"origin": "BKK"}}

    monkeypatch.setattr("app.agents.clarification.call_llm_json", fake_call_llm_json)
    updates = await extract_fields("travel", "from bangkok")
    assert updates["origin"] == "BKK"


@pytest.mark.asyncio
async def test_domain_pending_string_type_does_not_crash(monkeypatch):
    async def fake_extract_fields(*args, **kwargs):
        return {}

    monkeypatch.setattr(domain_agent, "extract_fields", fake_extract_fields)

    state = ChatState(
        message="Dubai",
        domain="ops",
        pending_request={
            "domain": "ops",
            "type": "travel",
            "filled": {"destination": "Bangkok", "departure_date": None, "return_date": None},
            "missing": ["origin", "departure_date", "return_date"],
            "step": "collecting_details",
        },
        actions=[],
        events=[],
    )

    result = await domain_agent.domain_node(state)

    assert result["pending_request"] is not None
    assert result["pending_request"]["filled"]["origin"] == "Dubai"
    assert "departure date" in result["response"].lower()
