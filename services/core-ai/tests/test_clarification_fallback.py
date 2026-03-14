import pytest

from app.agents import domain as domain_agent
from app.agents.clarification import (
    RequestType,
    _classification_prompt,
    build_pending_request,
    classify_request,
    extract_fields,
    next_question,
)
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
    assert result["pending_request"]["filled"]["origin"] == "company"
    assert "departure date" in result["response"].lower()


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
    updates = await extract_fields("travel", "from BKK")
    assert updates["origin"] == "BKK"


@pytest.mark.asyncio
async def test_classify_request_travel_infers_same_day_and_times_from_single_sentence(monkeypatch):
    calls = {"n": 0}

    async def fake_call_llm_json(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"request_type": "travel", "fields": {}}
        return {
            "request_type": "travel",
            "fields": {
                "origin": None,
                "destination": "customer company",
                "departure_date": "2026-05-18",
                "return_date": "2026-05-18",
                "preferred_departure_time": "7:00 a.m.",
                "preferred_return_time": "5:00 p.m.",
                "class": None,
            },
        }

    monkeypatch.setattr("app.agents.clarification.call_llm_json", fake_call_llm_json)
    request_type, fields = await classify_request(
        "ops",
        "I want to reserve a car to travel to customer company whole day on 18/May/2026 starting from 7:00 a.m. to 5:00 p.m.",
    )

    assert request_type == RequestType.TRAVEL
    assert fields["origin"] == "company"
    assert fields["destination"] == "customer company"
    assert fields["departure_date"] == "2026-05-18"
    assert fields["return_date"] == "2026-05-18"
    assert fields["preferred_departure_time"].lower().startswith("7:00")
    assert fields["preferred_return_time"].lower().startswith("5:00")


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
        message=(
            "Please book travel from Dubai to Bangkok on 10 Jul 2026 at 9:00 AM "
            "and return on 15 Jul 2026 at 7:00 PM in economy class"
        ),
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
    assert captured["payload"]["preferred_departure_time"] == "9:00 AM"
    assert captured["payload"]["preferred_return_time"] == "7:00 PM"


@pytest.mark.asyncio
async def test_travel_pending_whole_day_sentence_submits_without_extra_questions(monkeypatch):
    captured = {}

    async def fake_extract_fields(*args, **kwargs):
        return {}

    async def fake_call_tool(state, service, path, payload, action_type):
        captured["payload"] = payload
        return {"type": action_type, "status": "submitted", "result": {"status": "submitted"}}

    monkeypatch.setattr(domain_agent, "extract_fields", fake_extract_fields)
    monkeypatch.setattr(domain_agent, "_call_tool", fake_call_tool)

    state = ChatState(
        message="I want to reserve a car to travel to customer company whole day on 18/May/2026 starting from 7:00 a.m. to 5:00 p.m.",
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
    assert captured["payload"]["origin"] == "company"
    assert captured["payload"]["destination"] == "customer company"
    assert captured["payload"]["departure_date"] == "2026-05-18"
    assert captured["payload"]["return_date"] == "2026-05-18"
    assert captured["payload"]["preferred_departure_time"].lower().startswith("7:00")
    assert captured["payload"]["preferred_return_time"].lower().startswith("5:00")


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
        return {"resource": None, "requested_role": None, "justification": None, "needed_by_date": None}

    async def fake_call_tool(state, service, path, payload, action_type):
        captured["payload"] = payload
        return {"type": action_type, "status": "submitted", "result": {"status": "submitted"}}

    monkeypatch.setattr(domain_agent, "extract_fields", fake_extract_fields)
    monkeypatch.setattr(domain_agent, "_call_tool", fake_call_tool)

    state = ChatState(
        message="Please grant write access to repo-payments for deployment tasks by 20 Aug 2026",
        domain="it",
        pending_request={
            "domain": "it",
            "type": RequestType.ACCESS,
            "filled": {},
            "missing": ["resource", "requested_role", "justification", "needed_by_date"],
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
    assert captured["payload"]["needed_by_date"] == "2026-08-20"


@pytest.mark.asyncio
async def test_ticket_pending_parses_subtype_description_and_location(monkeypatch):
    captured = {}

    async def fake_extract_fields(*args, **kwargs):
        return {"subtype": None, "description": None, "location": None, "entity": None, "incident_date": None}

    async def fake_call_tool(state, service, path, payload, action_type):
        captured["payload"] = payload
        return {"type": action_type, "status": "submitted", "result": {"status": "submitted"}}

    monkeypatch.setattr(domain_agent, "extract_fields", fake_extract_fields)
    monkeypatch.setattr(domain_agent, "_call_tool", fake_call_tool)

    state = ChatState(
        message="The AC is broken in Room 12 on 10 Aug 2026",
        domain="it",
        pending_request={
            "domain": "it",
            "type": RequestType.TICKET,
            "filled": {},
            "missing": ["subtype", "description", "location", "entity", "incident_date"],
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
    assert captured["payload"]["category"] == "ac"
    assert captured["payload"]["incident_date"] == "2026-08-10"


def test_ticket_missing_fields_require_location_entity():
    pending = build_pending_request(
        "it",
        RequestType.TICKET,
        {"subtype": "it", "description": "printer not working"},
    )
    assert pending["missing"] == ["location", "entity", "incident_date"]


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


def test_classification_prompt_ops_contains_travel_vs_expense_disambiguation():
    prompt = _classification_prompt("ops", [RequestType.EXPENSE, RequestType.TRAVEL]).lower()
    assert "ops disambiguation rules" in prompt
    assert "reserve a car to travel to customer company" in prompt
    assert "request_type\":\"travel" in prompt
    assert "please reimburse taxi 1200 thb" in prompt
    assert "request_type\":\"expense" in prompt


def test_build_pending_travel_defaults_origin_to_company():
    pending = build_pending_request(
        "ops",
        RequestType.TRAVEL,
        {"destination": "Bangkok", "departure_date": "2026-03-12", "return_date": "2026-03-12"},
    )
    assert pending["filled"]["origin"] == "company"
    assert "origin" not in pending["missing"]


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


def test_build_pending_workspace_marks_generic_resource_name_missing():
    pending = build_pending_request(
        "workspace",
        RequestType.WORKSPACE_BOOKING,
        {
            "resource_type": "room",
            "resource_name": "meeting room",
            "start_time": "2026-08-10 10:00",
            "end_time": "2026-08-10 11:00",
        },
    )
    assert "resource_name" in pending["missing"]


def test_build_pending_workspace_accepts_specific_room_name():
    pending = build_pending_request(
        "workspace",
        RequestType.WORKSPACE_BOOKING,
        {
            "resource_type": "room",
            "resource_name": "Room 12",
            "start_time": "2026-08-10 10:00",
            "end_time": "2026-08-10 11:00",
        },
    )
    assert "resource_name" not in pending["missing"]


def test_next_question_lists_all_missing_details():
    pending = {
        "missing": ["resource_type", "resource_name", "start_time", "end_time"],
    }
    text = next_question(pending).lower()
    assert "i still need these details" in text
    assert "what do you want to book" in text
    assert "which resource should i book" in text
    assert "start time" in text
    assert "end time" in text


@pytest.mark.asyncio
async def test_workspace_prompt_lists_all_missing_with_room_suggestions(monkeypatch):
    async def fake_list_resources(resource_type):
        if resource_type == "room":
            return [{"name": "Orion"}, {"name": "Zephyr"}]
        return []

    monkeypatch.setattr(domain_agent, "_list_resources", fake_list_resources)
    pending = {
        "filled": {"resource_type": "room"},
        "missing": ["resource_name", "start_time", "end_time"],
    }
    text = (await domain_agent._workspace_prompt(pending, {})).lower()
    assert "orion" in text and "zephyr" in text
    assert "start and end time" in text


def test_failure_followup_keeps_only_truly_missing_fields():
    pending = {
        "type": RequestType.ACCESS,
        "filled": {
            "resource": "repo-x",
            "requested_role": "editor",
            "justification": None,
            "needed_by_date": "2026-08-20",
        },
        "missing": ["resource", "requested_role", "justification", "needed_by_date"],
    }
    msg = domain_agent._failure_followup(RequestType.ACCESS, pending, None)
    assert pending["missing"] == ["justification"]
    assert "justification" in msg.lower()
    assert "resource" not in msg.lower()
