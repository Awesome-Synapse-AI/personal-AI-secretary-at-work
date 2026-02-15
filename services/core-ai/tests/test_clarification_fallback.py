import pytest

from app.agents import domain as domain_agent
from app.agents.clarification import RequestType, classify_request, extract_fields, build_pending_request
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


@pytest.mark.asyncio
async def test_expense_pending_parses_multiple_fields_from_single_message(monkeypatch):
    captured = {}

    async def fake_extract_fields(*args, **kwargs):
        # Simulate LLM extraction returning a dict full of nulls.
        return {"amount": None, "currency": None, "date": None, "category": None, "project_code": None}

    async def fake_call_tool(state, service, path, payload, action_type):
        captured["payload"] = payload
        return {"type": action_type, "status": "submitted", "result": {"status": "submitted"}}

    monkeypatch.setattr(domain_agent, "extract_fields", fake_extract_fields)
    monkeypatch.setattr(domain_agent, "_call_tool", fake_call_tool)

    state = ChatState(
        message="I want to send claim request for my hotel stay on 23 Jun 2025 that costs around 2000 Baht in total. The project code is Proj-001",
        domain="ops",
        pending_request={
            "domain": "ops",
            "type": RequestType.EXPENSE,
            "filled": {},
            "missing": ["amount", "currency", "date", "category"],
            "step": "collecting_details",
        },
        actions=[],
        events=[],
    )

    result = await domain_agent.domain_node(state)

    assert result["pending_request"] is None
    assert "expense logged" in result["response"].lower()
    assert captured["payload"]["amount"] == 2000.0
    assert captured["payload"]["currency"] == "THB"
    assert captured["payload"]["date"] == "2025-06-23"
    assert captured["payload"]["category"] == "hotel"
    assert captured["payload"]["project_code"] == "Proj-001"


@pytest.mark.asyncio
async def test_travel_pending_parses_multiple_fields_from_single_message(monkeypatch):
    captured = {}

    async def fake_extract_fields(*args, **kwargs):
        return {"origin": None, "destination": None, "departure_date": None, "return_date": None, "class": None}

    async def fake_call_tool(state, service, path, payload, action_type):
        captured["payload"] = payload
        return {"type": action_type, "status": "submitted", "result": {"status": "submitted"}}

    monkeypatch.setattr(domain_agent, "extract_fields", fake_extract_fields)
    monkeypatch.setattr(domain_agent, "_call_tool", fake_call_tool)

    state = ChatState(
        message="Please book travel from Dubai to Bangkok on 10 Jul 2026 and return on 15 Jul 2026 in economy class",
        domain="ops",
        pending_request={
            "domain": "ops",
            "type": RequestType.TRAVEL,
            "filled": {},
            "missing": ["origin", "destination", "departure_date", "return_date"],
            "step": "collecting_details",
        },
        actions=[],
        events=[],
    )

    result = await domain_agent.domain_node(state)

    assert result["pending_request"] is None
    assert captured["payload"]["origin"] == "Dubai"
    assert captured["payload"]["destination"] == "Bangkok"
    assert captured["payload"]["departure_date"] == "2026-07-10"
    assert captured["payload"]["return_date"] == "2026-07-15"
    assert captured["payload"]["travel_class"] == "economy"


@pytest.mark.asyncio
async def test_leave_pending_parses_multiple_fields_from_single_message(monkeypatch):
    captured = {}

    async def fake_extract_fields(*args, **kwargs):
        return {"leave_type": None, "start_date": None, "end_date": None, "reason": None}

    async def fake_call_tool(state, service, path, payload, action_type):
        captured["payload"] = payload
        return {"type": action_type, "status": "submitted", "result": {"status": "submitted"}}

    monkeypatch.setattr(domain_agent, "extract_fields", fake_extract_fields)
    monkeypatch.setattr(domain_agent, "_call_tool", fake_call_tool)

    state = ChatState(
        message="I need annual leave from 10 Aug 2026 to 12 Aug 2026 for family matters",
        domain="hr",
        pending_request={
            "domain": "hr",
            "type": RequestType.LEAVE,
            "filled": {},
            "missing": ["leave_type", "start_date", "end_date"],
            "step": "collecting_details",
        },
        actions=[],
        events=[],
    )

    result = await domain_agent.domain_node(state)

    assert result["pending_request"] is None
    assert captured["payload"]["leave_type"] == "annual"
    assert captured["payload"]["start_date"] == "2026-08-10"
    assert captured["payload"]["end_date"] == "2026-08-12"


@pytest.mark.asyncio
async def test_access_pending_parses_multiple_fields_from_single_message(monkeypatch):
    captured = {}

    async def fake_extract_fields(*args, **kwargs):
        return {"resource": None, "requested_role": None, "justification": None}

    async def fake_call_tool(state, service, path, payload, action_type):
        captured["payload"] = payload
        return {"type": action_type, "status": "submitted", "result": {"status": "submitted"}}

    monkeypatch.setattr(domain_agent, "extract_fields", fake_extract_fields)
    monkeypatch.setattr(domain_agent, "_call_tool", fake_call_tool)

    state = ChatState(
        message="Please grant write access to repo-payments for deployment tasks",
        domain="it",
        pending_request={
            "domain": "it",
            "type": RequestType.ACCESS,
            "filled": {},
            "missing": ["resource", "requested_role", "justification"],
            "step": "collecting_details",
        },
        actions=[],
        events=[],
    )

    result = await domain_agent.domain_node(state)

    assert result["pending_request"] is None
    assert captured["payload"]["requested_role"] == "editor"
    assert captured["payload"]["resource"] == "repo-payments"
    assert "deployment tasks" in captured["payload"]["justification"].lower()


@pytest.mark.asyncio
async def test_ticket_pending_parses_subtype_description_and_location(monkeypatch):
    captured = {}

    async def fake_extract_fields(*args, **kwargs):
        return {"subtype": None, "description": None, "location": None}

    async def fake_call_tool(state, service, path, payload, action_type):
        captured["payload"] = payload
        return {"type": action_type, "status": "submitted", "result": {"status": "submitted"}}

    monkeypatch.setattr(domain_agent, "extract_fields", fake_extract_fields)
    monkeypatch.setattr(domain_agent, "_call_tool", fake_call_tool)

    state = ChatState(
        message="The AC is broken in Room 12",
        domain="it",
        pending_request={
            "domain": "it",
            "type": RequestType.TICKET,
            "filled": {},
            "missing": ["subtype", "description", "location"],
            "step": "collecting_details",
        },
        actions=[],
        events=[],
    )

    result = await domain_agent.domain_node(state)

    assert result["pending_request"] is None
    assert captured["payload"]["type"] == "facilities"
    assert "ac is broken" in captured["payload"]["description"].lower()
    assert "room 12" in captured["payload"]["location"].lower()


@pytest.mark.asyncio
async def test_workspace_pending_parses_resource_and_time_span(monkeypatch):
    captured = {}

    async def fake_extract_fields(*args, **kwargs):
        return {"resource_type": None, "resource_name": None, "start_time": None, "end_time": None}

    async def fake_call_tool(state, service, path, payload, action_type):
        captured["path"] = path
        captured["payload"] = payload
        return {"type": action_type, "status": "submitted", "result": {"status": "submitted"}}

    monkeypatch.setattr(domain_agent, "extract_fields", fake_extract_fields)
    monkeypatch.setattr(domain_agent, "_call_tool", fake_call_tool)

    state = ChatState(
        message="Book room 7 from 2026-08-10 10:00 to 2026-08-10 11:00",
        domain="workspace",
        pending_request={
            "domain": "workspace",
            "type": RequestType.WORKSPACE_BOOKING,
            "filled": {},
            "missing": ["resource_type", "resource_name", "start_time", "end_time"],
            "step": "collecting_details",
        },
        actions=[],
        events=[],
    )

    result = await domain_agent.domain_node(state)

    assert result["pending_request"] is None
    assert captured["path"] == "/rooms/7/book"
    assert captured["payload"]["resource_name"] == "Room 7"
    assert captured["payload"]["start_time"] == "2026-08-10 10:00"
    assert captured["payload"]["end_time"] == "2026-08-10 11:00"


@pytest.mark.asyncio
async def test_classify_request_normalizes_expense_currency_and_amount(monkeypatch):
    async def fake_call_llm_json(*args, **kwargs):
        return {
            "request_type": "expense",
            "fields": {
                "amount": "around 2000 baht in total",
                "currency": "Baht",
                "date": "23 Jun 2025",
                "category": "hotel",
            },
        }

    monkeypatch.setattr("app.agents.clarification.call_llm_json", fake_call_llm_json)
    request_type, fields = await classify_request("ops", "expense text")
    assert request_type == RequestType.EXPENSE
    assert fields["amount"] == 2000.0
    assert fields["currency"] == "THB"
    assert fields["date"] == "2025-06-23"


def test_build_pending_request_marks_invalid_currency_missing():
    pending = build_pending_request(
        "ops",
        RequestType.EXPENSE,
        {"amount": "100", "currency": "baht coins", "date": "2025-06-23", "category": "hotel"},
    )
    assert "currency" not in pending["missing"]

    pending_invalid = build_pending_request(
        "ops",
        RequestType.EXPENSE,
        {"amount": "100", "currency": "money", "date": "2025-06-23", "category": "hotel"},
    )
    assert "currency" in pending_invalid["missing"]
