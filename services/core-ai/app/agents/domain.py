from typing import Any

from app.state import ChatState
from app.agents.clarification import (
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
        return "I can help with leave requests and HR policy questions."
    if domain == "ops":
        return "I can help with expenses and travel requests."
    if domain == "it":
        return "I can help with IT issues and access requests."
    if domain == "workspace":
        return "I can help with room and desk bookings or facilities issues."
    if domain == "doc_qa":
        return "I can answer questions about your documents and policies."
    return "How can I help?"


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
        response = _domain_intro(domain)
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
    if pending and pending.get("type") == "leave":
        updates = await extract_fields("leave", message)
        pending = update_pending_request(pending, updates)
        if pending["missing"]:
            return next_question(pending), pending, []
        action = await _submit_leave_request(pending, state)
        return _leave_success(pending), None, [action]

    request_type, fields = await classify_request("hr", message)
    if request_type == "leave":
        pending = build_pending_request("hr", "leave", fields)
        if pending["missing"]:
            return next_question(pending), pending, []
        action = await _submit_leave_request(pending, state)
        return _leave_success(pending), None, [action]

    return _domain_intro("hr"), pending, []


async def _handle_ops(
    message: str, pending: dict[str, Any] | None, state: ChatState
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    if pending and pending.get("type") in {"expense", "travel"}:
        updates = await extract_fields(pending.get("type", ""), message)
        pending = update_pending_request(pending, updates)
        if pending["missing"]:
            return next_question(pending), pending, []
        if pending.get("type") == "expense":
            action = await _submit_expense_request(pending, state)
            return _expense_success(pending), None, [action]
        action = await _submit_travel_request(pending, state)
        return _travel_success(pending), None, [action]

    request_type, fields = await classify_request("ops", message)
    if request_type in {"expense", "travel"}:
        pending = build_pending_request("ops", request_type, fields)
        if pending["missing"]:
            return next_question(pending), pending, []
        if request_type == "expense":
            action = await _submit_expense_request(pending, state)
            return _expense_success(pending), None, [action]
        action = await _submit_travel_request(pending, state)
        return _travel_success(pending), None, [action]

    return _domain_intro("ops"), pending, []


async def _handle_it(
    message: str, pending: dict[str, Any] | None, state: ChatState
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    if pending and pending.get("type") in {"access", "ticket"}:
        updates = await extract_fields(pending.get("type", ""), message)
        pending = update_pending_request(pending, updates)
        if pending["missing"]:
            return next_question(pending), pending, []
        if pending.get("type") == "access":
            action = await _submit_access_request(pending, state)
            return _access_success(pending), None, [action]
        action = await _submit_ticket_request(pending, state)
        return _ticket_success(pending), None, [action]

    request_type, fields = await classify_request("it", message)
    if request_type in {"access", "ticket"}:
        pending = build_pending_request("it", request_type, fields)
        if pending["missing"]:
            return next_question(pending), pending, []
        if request_type == "access":
            action = await _submit_access_request(pending, state)
            return _access_success(pending), None, [action]
        action = await _submit_ticket_request(pending, state)
        return _ticket_success(pending), None, [action]

    return _domain_intro("it"), pending, []


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


def _expense_success(pending: dict[str, Any]) -> str:
    filled = pending["filled"]
    return (
        "Expense logged for "
        f"{filled.get('amount')} {filled.get('currency')} ({filled.get('category')}) on {filled.get('date')}."
    )


def _travel_success(pending: dict[str, Any]) -> str:
    filled = pending["filled"]
    return (
        "Travel request captured from "
        f"{filled.get('origin')} to {filled.get('destination')} on {filled.get('departure_date')}."
    )


def _ticket_success(pending: dict[str, Any]) -> str:
    subtype = pending.get("subtype", pending["filled"].get("subtype", "it"))
    return f"Ticket captured for {subtype} support."


def _access_success(pending: dict[str, Any]) -> str:
    filled = pending["filled"]
    return (
        "Access request captured for "
        f"{filled.get('resource')} with {filled.get('requested_role')} access."
    )
