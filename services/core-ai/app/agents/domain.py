from typing import Any

from app.state import ChatState
from app.agents.clarification import (
    RequestType,
    build_pending_request,
    classify_request,
    extract_fields,
    next_question,
    update_pending_request,
)
from app.agents.tools import tool_runner


def _add_event(state: ChatState, event_type: str, data: dict | None = None) -> None:
    state.setdefault("events", []).append({"type": event_type, "data": data or {}})


def _domain_intro(domain: str) -> str:
    if domain == "hr":
        return (
            "HR help:\n"
            "- Create or update leave requests (annual, sick, unpaid)\n"
            "- Check leave balances and status\n"
            "- Answer HR policy questions\n"
            "Examples: \"I need sick leave next Monday\", \"How many vacation days do I have left?\""
        )
    if domain == "ops":
        return (
            "Operations help:\n"
            "- Log expenses and attach receipts\n"
            "- Create travel requests (flights, hotels)\n"
            "- Explain travel/expense policy limits\n"
            "Examples: \"Add a $45 taxi from yesterday\", \"Book a flight to Singapore next Monday\""
        )
    if domain == "it":
        return (
            "IT help:\n"
            "- File IT tickets or facilities tickets\n"
            "- Troubleshoot common issues (VPN, Wi‑Fi, laptop)\n"
            "- Create access requests for systems/repos\n"
            "Examples: \"VPN keeps dropping\", \"I need write access to Repo X\""
        )
    if domain == "workspace":
        return (
            "Workspace help:\n"
            "- Book meeting rooms, desks, parking, equipment\n"
            "- Raise facilities issues (AC, lights, etc.)\n"
            "Examples: \"Book a room for 6 people at 3pm\", \"The AC is broken in Room 12\""
        )
    if domain == "doc_qa":
        return (
            "Document Q&A:\n"
            "- Answer questions over uploaded documents and policies\n"
            "- Search user docs with filters\n"
            "Examples: \"What is the per diem limit?\", \"Summarize the onboarding PDF\""
        )
    return (
        "Hello. I can help with: HR (leave, policies), Operations (expenses, travel), IT (tickets, access), "
        "Workspace (rooms, desks, facilities), and Document Q&A.\n"
        "Try: \"I need sick leave tomorrow\", \"Log a $30 meal expense\", \"VPN not working\", "
        "\"Book a room at 2pm\", or \"What’s our travel policy per diem?\""
    )


async def domain_node(state: ChatState) -> ChatState:
    _add_event(state, "agent_started", {"agent": "DomainAgent", "domain": state.get("domain")})

    domain = state.get("domain", "generic")
    pending = state.get("pending_request")
    message = state.get("message", "")

    response = ""
    actions: list[dict[str, Any]] = []

    if domain == "hr":
        response, pending, actions = await _handle_hr(message, pending, state)
    elif domain == "ops":
        response, pending, actions = await _handle_ops(message, pending, state)
    elif domain == "it":
        response, pending, actions = await _handle_it(message, pending, state)
    elif domain == "workspace":
        response, pending, actions = await _handle_workspace(message, pending, state)
    elif domain == "doc_qa":
        response = "Upload a document and ask your question."
    else:
        response = _domain_intro(domain)

    state["response"] = response
    state["pending_request"] = pending
    state.setdefault("actions", []).extend(actions)

    _add_event(state, "agent_finished", {"agent": "DomainAgent", "domain": domain})
    return state


async def _handle_hr(
    message: str, pending: dict[str, Any] | None, state: ChatState
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    if pending and pending.get("type") == RequestType.LEAVE:
        updates = await extract_fields(RequestType.LEAVE, message)
        pending = update_pending_request(pending, updates)
        if pending["missing"]:
            return next_question(pending), pending, []
        action = await _submit_leave_request(pending, state)
        if action.get("status") != "submitted":
            return _leave_failure(action), None, [action]
        return _leave_success(pending), None, [action]

    request_type, fields = await classify_request("hr", message)
    if request_type == RequestType.LEAVE:
        pending = build_pending_request("hr", RequestType.LEAVE, fields)
        if pending["missing"]:
            return next_question(pending), pending, []
        action = await _submit_leave_request(pending, state)
        if action.get("status") != "submitted":
            return _leave_failure(action), None, [action]
        return _leave_success(pending), None, [action]

    return _domain_intro("hr"), pending, []


async def _handle_ops(
    message: str, pending: dict[str, Any] | None, state: ChatState
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    if pending and pending.get("type") in {RequestType.EXPENSE, RequestType.TRAVEL}:
        updates = await extract_fields(pending.get("type", ""), message)  # type: ignore[arg-type]
        pending = update_pending_request(pending, updates)
        if pending["missing"]:
            return next_question(pending), pending, []
        if pending.get("type") == RequestType.EXPENSE:
            action = await _submit_expense_request(pending, state)
            if action.get("status") != "submitted":
                return _expense_failure(action), None, [action]
            return _expense_success(pending), None, [action]
        action = await _submit_travel_request(pending, state)
        if action.get("status") != "submitted":
            return _travel_failure(action), None, [action]
        return _travel_success(pending), None, [action]

    request_type, fields = await classify_request("ops", message)
    if request_type in {RequestType.EXPENSE, RequestType.TRAVEL}:
        pending = build_pending_request("ops", request_type, fields)
        if pending["missing"]:
            return next_question(pending), pending, []
        if request_type == RequestType.EXPENSE:
            action = await _submit_expense_request(pending, state)
            if action.get("status") != "submitted":
                return _expense_failure(action), None, [action]
            return _expense_success(pending), None, [action]
        action = await _submit_travel_request(pending, state)
        if action.get("status") != "submitted":
            return _travel_failure(action), None, [action]
        return _travel_success(pending), None, [action]

    return _domain_intro("ops"), pending, []


async def _handle_it(
    message: str, pending: dict[str, Any] | None, state: ChatState
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    if pending and pending.get("type") in {RequestType.ACCESS, RequestType.TICKET}:
        updates = await extract_fields(pending.get("type", ""), message)  # type: ignore[arg-type]
        pending = update_pending_request(pending, updates)
        if pending["missing"]:
            return next_question(pending), pending, []
        if pending.get("type") == RequestType.ACCESS:
            action = await _submit_access_request(pending, state)
            return _access_success(pending), None, [action]
        action = await _submit_ticket_request(pending, state)
        return _ticket_success(pending), None, [action]

    request_type, fields = await classify_request("it", message)
    if request_type in {RequestType.ACCESS, RequestType.TICKET}:
        pending = build_pending_request("it", request_type, fields)
        if pending["missing"]:
            return next_question(pending), pending, []
        if request_type == RequestType.ACCESS:
            action = await _submit_access_request(pending, state)
            return _access_success(pending), None, [action]
        action = await _submit_ticket_request(pending, state)
        return _ticket_success(pending), None, [action]

    return _domain_intro("it"), pending, []


async def _handle_workspace(
    message: str, pending: dict[str, Any] | None, state: ChatState
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    if pending and pending.get("type") == RequestType.WORKSPACE_BOOKING:
        updates = await extract_fields(RequestType.WORKSPACE_BOOKING, message)
        pending = update_pending_request(pending, updates)
        if pending["missing"]:
            return next_question(pending), pending, []
        action = await _submit_workspace_booking(pending, state)
        if action.get("status") != "submitted":
            return _workspace_failure(action), None, [action]
        return _workspace_success(pending), None, [action]

    request_type, fields = await classify_request("workspace", message)
    if request_type == RequestType.WORKSPACE_BOOKING:
        pending = build_pending_request("workspace", request_type, fields)
        if pending["missing"]:
            return next_question(pending), pending, []
        action = await _submit_workspace_booking(pending, state)
        if action.get("status") != "submitted":
            return _workspace_failure(action), None, [action]
        return _workspace_success(pending), None, [action]

    return _domain_intro("workspace"), pending, []


async def _submit_leave_request(pending: dict[str, Any], state: ChatState) -> dict[str, Any]:
    payload = {
        "leave_type": pending["filled"].get("leave_type"),
        "start_date": pending["filled"].get("start_date"),
        "end_date": pending["filled"].get("end_date"),
        "reason": pending["filled"].get("reason"),
    }
    return await _call_tool(state, "leave", "/requests", payload, "leave_request")


async def _submit_expense_request(pending: dict[str, Any], state: ChatState) -> dict[str, Any]:
    payload = {
        "amount": pending["filled"].get("amount"),
        "currency": pending["filled"].get("currency"),
        "date": pending["filled"].get("date"),
        "category": pending["filled"].get("category"),
        "project_code": pending["filled"].get("project_code"),
    }
    return await _call_tool(state, "expense", "/expenses", payload, "expense_request")


async def _submit_travel_request(pending: dict[str, Any], state: ChatState) -> dict[str, Any]:
    payload = {
        "origin": pending["filled"].get("origin"),
        "destination": pending["filled"].get("destination"),
        "departure_date": pending["filled"].get("departure_date"),
        "return_date": pending["filled"].get("return_date"),
        "class": pending["filled"].get("class"),
    }
    return await _call_tool(state, "expense", "/travel-requests", payload, "travel_request")


async def _submit_ticket_request(pending: dict[str, Any], state: ChatState) -> dict[str, Any]:
    payload = {
        "type": pending.get("subtype", pending["filled"].get("subtype", "it")),
        "description": pending["filled"].get("description"),
        "location": pending["filled"].get("location"),
    }
    return await _call_tool(state, "ticket", "/tickets", payload, "ticket_request")


async def _submit_access_request(pending: dict[str, Any], state: ChatState) -> dict[str, Any]:
    payload = {
        "resource": pending["filled"].get("resource"),
        "requested_role": pending["filled"].get("requested_role"),
        "justification": pending["filled"].get("justification"),
    }
    return await _call_tool(state, "access", "/access-requests", payload, "access_request")


async def _submit_workspace_booking(pending: dict[str, Any], state: ChatState) -> dict[str, Any]:
    filled = pending.get("filled", {})
    resource_type = (filled.get("resource_type") or "").lower()
    resource_name = filled.get("resource_name")
    resource_id = filled.get("resource_id")
    start_time = filled.get("start_time")
    end_time = filled.get("end_time")
    if not resource_type or not start_time or not end_time or (not resource_name and not resource_id):
        return {"type": "workspace_booking", "status": "failed", "error": "Missing required fields"}

    if resource_type == "room":
        path = f"/rooms/{resource_id}/book"
        payload = {"resource_name": resource_name, "start_time": start_time, "end_time": end_time}
    elif resource_type == "desk":
        path = "/desks/book"
        payload = {"desk_id": resource_id, "resource_name": resource_name, "start_time": start_time, "end_time": end_time}
    elif resource_type == "equipment":
        path = "/equipment/reserve"
        payload = {
            "equipment_id": resource_id,
            "resource_name": resource_name,
            "start_time": start_time,
            "end_time": end_time,
        }
    elif resource_type == "parking":
        path = "/parking/book"
        payload = {
            "parking_spot_id": resource_id,
            "resource_name": resource_name,
            "start_time": start_time,
            "end_time": end_time,
        }
    else:
        return {"type": "workspace_booking", "status": "failed", "error": "Unknown resource_type"}

    return await _call_tool(state, "workspace", path, payload, "workspace_booking")


async def _call_tool(
    state: ChatState,
    service: str,
    path: str,
    payload: dict[str, Any],
    action_type: str,
) -> dict[str, Any]:
    _add_event(state, "tool_call", {"service": service, "path": path})
    try:
        result = await tool_runner.call(service, "POST", path, payload)
        _add_event(state, "tool_result", {"service": service, "result": result})
        return {"type": action_type, "status": result.get("status", "submitted"), "payload": payload}
    except Exception as exc:  # pragma: no cover - network errors
        _add_event(state, "tool_error", {"service": service, "error": str(exc)})
        return {"type": action_type, "status": "failed", "payload": payload}


def _leave_success(pending: dict[str, Any]) -> str:
    filled = pending["filled"]
    return (
        "Leave request captured for "
        f"{filled.get('leave_type')} leave from {filled.get('start_date')} to {filled.get('end_date')}."
    )


def _leave_failure(action: dict[str, Any]) -> str:
    error = action.get("error") or "The leave request could not be submitted."
    return f"Leave request failed: {error}"


def _expense_success(pending: dict[str, Any]) -> str:
    filled = pending["filled"]
    return (
        "Expense logged for "
        f"{filled.get('amount')} {filled.get('currency')} ({filled.get('category')}) on {filled.get('date')}."
    )


def _expense_failure(action: dict[str, Any]) -> str:
    error = action.get("error") or "The expense could not be submitted."
    return f"Expense submission failed: {error}"


def _travel_success(pending: dict[str, Any]) -> str:
    filled = pending["filled"]
    return (
        "Travel request captured from "
        f"{filled.get('origin')} to {filled.get('destination')} on {filled.get('departure_date')}."
    )


def _travel_failure(action: dict[str, Any]) -> str:
    error = action.get("error") or "The travel request could not be submitted."
    return f"Travel request failed: {error}"


def _ticket_success(pending: dict[str, Any]) -> str:
    subtype = pending.get("subtype", pending["filled"].get("subtype", "it"))
    return f"Ticket captured for {subtype} support."


def _access_success(pending: dict[str, Any]) -> str:
    filled = pending["filled"]
    return (
        "Access request captured for "
        f"{filled.get('resource')} with {filled.get('requested_role')} access."
    )


def _workspace_success(pending: dict[str, Any]) -> str:
    filled = pending["filled"]
    return (
        "Booking confirmed for "
        f"{filled.get('resource_type')} {filled.get('resource_id')} "
        f"from {filled.get('start_time')} to {filled.get('end_time')}."
    )


def _workspace_failure(action: dict[str, Any]) -> str:
    error = action.get("error") or "The booking could not be created."
    return f"Booking failed: {error}"
