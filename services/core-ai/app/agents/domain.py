from typing import Any
import re

import structlog
from dateutil import parser as dateparser
from langsmith import traceable

from app.state import ChatState
from app.agents.clarification import (
    RequestType,
    _as_request_type,
    build_pending_request,
    classify_request,
    extract_fields,
    next_question,
    update_pending_request,
    _merge_fields,
    _is_missing,
    _normalize_resource_type,
)
from app.agents.tools import tool_runner

logger = structlog.get_logger("domain_agent")


def _add_event(state: ChatState, event_type: str, data: dict | None = None) -> None:
    state.setdefault("events", []).append({"type": event_type, "data": data or {}})


def _domain_intro(domain: str) -> str:
    if domain == "hr":
        return (
            "HR help:<br>"
            "- Create or update leave requests (annual, sick, unpaid)<br>"
            "- Check leave balances and status<br>"
            "- Answer HR policy questions<br>"
            "Examples: \"I need sick leave next Monday\", \"How many vacation days do I have left?\""
        )
    if domain == "ops":
        return (
            "Operations help:<br>"
            "- Log expenses and attach receipts<br>"
            "- Create travel requests (flights, hotels)<br>"
            "- Explain travel/expense policy limits<br>"
            "Examples: \"Add a $45 taxi from yesterday\", \"Book a flight to Singapore next Monday\""
        )
    if domain == "it":
        return (
            "IT help:<br>"
            "- File IT tickets or facilities tickets<br>"
            "- Troubleshoot common issues (VPN, Wi‑Fi, laptop)<br>"
            "- Create access requests for systems/repos<br>"
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


@traceable(name="domain_node", run_type="chain")
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
        response, pending, actions = await _handle_doc_qa(message, pending, state)
    else:
        response = _domain_intro(domain)

    state["response"] = response
    state["pending_request"] = pending
    state.setdefault("actions", []).extend(actions)

    _add_event(state, "agent_finished", {"agent": "DomainAgent", "domain": domain})
    logger.info(
        "domain_result",
        domain=domain,
        pending=bool(pending),
        actions=len(actions),
        sensitivity=state.get("sensitivity"),
    )
    return state


async def _handle_hr(
    message: str, pending: dict[str, Any] | None, state: ChatState
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    if pending and pending.get("type") == RequestType.LEAVE:
        updates = await _extract_pending_updates(pending, message)
        pending = update_pending_request(pending, updates)
        if pending["missing"]:
            return next_question(pending), pending, []
        action = await _submit_leave_request(pending, state)
        if action.get("status") != "submitted":
            prompt = _failure_followup(RequestType.LEAVE, pending, action.get("error"))
            return prompt, pending, [action]
        return _leave_success(pending), None, [action]

    request_type, fields = await classify_request("hr", message)
    if request_type == RequestType.LEAVE:
        pending = build_pending_request("hr", RequestType.LEAVE, fields)
        if pending["missing"]:
            return next_question(pending), pending, []
        action = await _submit_leave_request(pending, state)
        if action.get("status") != "submitted":
            prompt = _failure_followup(RequestType.LEAVE, pending, action.get("error"))
            return prompt, pending, [action]
        return _leave_success(pending), None, [action]

    return _domain_intro("hr"), pending, []


async def _handle_ops(
    message: str, pending: dict[str, Any] | None, state: ChatState
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    if pending and pending.get("type") in {RequestType.EXPENSE, RequestType.TRAVEL}:
        updates = await _extract_pending_updates(pending, message)
        pending = update_pending_request(pending, updates)
        if pending["missing"]:
            return next_question(pending), pending, []
        if pending.get("type") == RequestType.EXPENSE:
            action = await _submit_expense_request(pending, state)
            if action.get("status") != "submitted":
                prompt = _failure_followup(RequestType.EXPENSE, pending, action.get("error"))
                return prompt, pending, [action]
            return _expense_success(pending), None, [action]
        action = await _submit_travel_request(pending, state)
        if action.get("status") != "submitted":
            prompt = _failure_followup(RequestType.TRAVEL, pending, action.get("error"))
            return prompt, pending, [action]
        return _travel_success(pending), None, [action]

    request_type, fields = await classify_request("ops", message)
    if request_type in {RequestType.EXPENSE, RequestType.TRAVEL}:
        pending = build_pending_request("ops", request_type, fields)
        if pending["missing"]:
            return next_question(pending), pending, []
        if request_type == RequestType.EXPENSE:
            action = await _submit_expense_request(pending, state)
            if action.get("status") != "submitted":
                prompt = _failure_followup(RequestType.EXPENSE, pending, action.get("error"))
                return prompt, pending, [action]
            return _expense_success(pending), None, [action]
        action = await _submit_travel_request(pending, state)
        if action.get("status") != "submitted":
            prompt = _failure_followup(RequestType.TRAVEL, pending, action.get("error"))
            return prompt, pending, [action]
        return _travel_success(pending), None, [action]

    return _domain_intro("ops"), pending, []


async def _handle_it(
    message: str, pending: dict[str, Any] | None, state: ChatState
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    if pending and pending.get("type") in {RequestType.ACCESS, RequestType.TICKET}:
        updates = await _extract_pending_updates(pending, message)
        pending = update_pending_request(pending, updates)
        if pending["missing"]:
            return next_question(pending), pending, []
        if pending.get("type") == RequestType.ACCESS:
            action = await _submit_access_request(pending, state)
            if action.get("status") != "submitted":
                prompt = _failure_followup(RequestType.ACCESS, pending, action.get("error"))
                return prompt, pending, [action]
            return _access_success(pending), None, [action]
        action = await _submit_ticket_request(pending, state)
        if action.get("status") != "submitted":
            prompt = _failure_followup(RequestType.TICKET, pending, action.get("error"))
            return prompt, pending, [action]
        return _ticket_success(pending), None, [action]

    request_type, fields = await classify_request("it", message)
    if request_type in {RequestType.ACCESS, RequestType.TICKET}:
        pending = build_pending_request("it", request_type, fields)
        if pending["missing"]:
            return next_question(pending), pending, []
        if request_type == RequestType.ACCESS:
            action = await _submit_access_request(pending, state)
            if action.get("status") != "submitted":
                prompt = _failure_followup(RequestType.ACCESS, pending, action.get("error"))
                return prompt, pending, [action]
            return _access_success(pending), None, [action]
        action = await _submit_ticket_request(pending, state)
        if action.get("status") != "submitted":
            prompt = _failure_followup(RequestType.TICKET, pending, action.get("error"))
            return prompt, pending, [action]
        return _ticket_success(pending), None, [action]

    return _domain_intro("it"), pending, []


async def _handle_workspace(
    message: str, pending: dict[str, Any] | None, state: ChatState
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    if pending and pending.get("type") == RequestType.WORKSPACE_BOOKING:
        updates = await _extract_pending_updates(pending, message)
        pending = update_pending_request(pending, updates)
        if pending["missing"]:
            prompt = await _workspace_prompt(pending, state)
            return prompt, pending, []
        action = await _submit_workspace_booking(pending, state)
        if action.get("status") == "need_room_selection":
            rooms = action.get("choices") or []
            names = ", ".join([str(r.get("name")) for r in rooms if r.get("name")]) or "none available"
            pending["missing"] = ["resource_name"]
            prompt = (
                f"I couldn't find that room. Available rooms: {names}. "
                "Please tell me which room to book (name or number)."
            )
            return prompt, pending, []
        if action.get("status") != "submitted":
            prompt = await _workspace_followup(action, pending, state)
            if prompt:
                return prompt, pending, [action]
            return _workspace_failure(action), None, [action]
        _apply_booking_result_to_pending(pending, action)
        return _workspace_success(pending), None, [action]

    request_type, fields = await classify_request("workspace", message)
    if request_type == RequestType.WORKSPACE_BOOKING:
        # Prefer LLM extraction over heuristics for workspace details.
        llm_fields = await extract_fields(RequestType.WORKSPACE_BOOKING, message)
        enriched_fields = _merge_fields(fields, llm_fields)
        pending = build_pending_request("workspace", request_type, enriched_fields)
        if pending["missing"]:
            prompt = await _workspace_prompt(pending, state)
            return prompt, pending, []
        action = await _submit_workspace_booking(pending, state)
        if action.get("status") == "need_room_selection":
            rooms = action.get("choices") or []
            names = ", ".join([str(r.get("name")) for r in rooms if r.get("name")]) or "none available"
            pending["missing"] = ["resource_name"]
            prompt = (
                f"I couldn't find that room. Available rooms: {names}. "
                "Please tell me which room to book (name or number)."
            )
            return prompt, pending, []
        if action.get("status") != "submitted":
            prompt = await _workspace_followup(action, pending, state)
            if prompt:
                return prompt, pending, [action]
            return _workspace_failure(action), None, [action]
        _apply_booking_result_to_pending(pending, action)
        return _workspace_success(pending), None, [action]

    return _domain_intro("workspace"), pending, []


async def _workspace_prompt(pending: dict[str, Any], state: ChatState) -> str:
    filled = pending.get("filled", {})
    missing = pending.get("missing", [])
    resource_type = (filled.get("resource_type") or "").lower()

    if "resource_name" in missing and resource_type == "room":
        names = _resource_suggestions("rooms", await _list_resources("room"))
        return f"Which room should I book? Available rooms: {names}."
    if "resource_name" in missing and resource_type == "desk":
        names = _resource_suggestions("desks", await _list_resources("desk"))
        return f"Which desk should I book? Available desks: {names}."
    if "resource_name" in missing and resource_type == "equipment":
        names = _resource_suggestions("equipment", await _list_resources("equipment"))
        return f"Which equipment should I reserve? Available equipment: {names}."
    if "resource_name" in missing and resource_type == "parking":
        names = _resource_suggestions("parking", await _list_resources("parking"))
        return f"Which parking spot should I book? Available spots: {names}."
    if "resource_name" in missing:
        return "Which resource should I book (e.g., Room 1, Desk #2)?"
    if "start_time" in missing or "end_time" in missing:
        return "What start and end time should I use? Please give both start and end."
    return next_question(pending)


async def _list_resources(resource_type: str) -> list[dict]:
    key_map = {
        "room": "rooms",
        "desk": "desks",
        "equipment": "equipment",
        "parking": "parking",
    }
    key = key_map.get(resource_type)
    path = f"/{key}" if key else None
    if not path:
        return []
    try:
        result = await tool_runner.call("workspace", "GET", path, {})
        payload = result.get("result", {})
        return payload.get(key) or []
    except Exception:
        return []


def _resource_suggestions(label: str, items: list[dict]) -> str:
    names = ", ".join([str(i.get("name") or i.get("id")) for i in items]) if items else ""
    return names or f"{label} are not available yet"


async def _handle_doc_qa(
    message: str, pending: dict[str, Any] | None, state: ChatState
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    # Simple doc search against indexed documents
    if not message.strip():
        return "Upload a document and ask your question.", pending, []

    action = await _call_tool(
        state,
        "doc_qa",
        "/documents/search",
        {"query": message, "top_k": 3},
        "doc_search",
    )
    if action.get("status") == "failed":
        return f"I couldn't search documents: {action.get('error')}", pending, [action]

    results = action.get("result", {}).get("matches") if action.get("result") else None
    if not results:
        return "I searched your documents but found no relevant matches.", pending, [action]

    lines = ["Here are the most relevant document snippets:"]
    for idx, hit in enumerate(results, 1):
        title = hit.get("title") or "Untitled"
        score = round(float(hit.get("score", 0)), 3)
        path = hit.get("path")
        lines.append(f"{idx}. {title} (score {score}) - {path}")
    return "\n".join(lines), pending, [action]


async def _submit_leave_request(pending: dict[str, Any], state: ChatState) -> dict[str, Any]:
    payload = {
        "leave_type": pending["filled"].get("leave_type"),
        "start_date": _to_iso_date(pending["filled"].get("start_date")),
        "end_date": _to_iso_date(pending["filled"].get("end_date")),
        "reason": pending["filled"].get("reason"),
    }
    return await _call_tool(state, "leave", "/requests", payload, "leave_request")


async def _submit_expense_request(pending: dict[str, Any], state: ChatState) -> dict[str, Any]:
    payload = {
        "amount": pending["filled"].get("amount"),
        "currency": pending["filled"].get("currency"),
        "date": _to_iso_date(pending["filled"].get("date")),
        "category": pending["filled"].get("category"),
        "project_code": pending["filled"].get("project_code"),
    }
    return await _call_tool(state, "expense", "/expenses", payload, "expense_request")


async def _submit_travel_request(pending: dict[str, Any], state: ChatState) -> dict[str, Any]:
    payload = {
        "origin": pending["filled"].get("origin"),
        "destination": pending["filled"].get("destination"),
        "departure_date": _to_iso_date(pending["filled"].get("departure_date")),
        "return_date": _to_iso_date(pending["filled"].get("return_date")),
        "travel_class": pending["filled"].get("class"),
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
    requested_role = _normalize_access_role(pending["filled"].get("requested_role"))
    payload = {
        "resource": pending["filled"].get("resource"),
        "requested_role": requested_role,
        "justification": pending["filled"].get("justification"),
    }
    return await _call_tool(state, "access", "/access-requests", payload, "access_request")


async def _resolve_room_id(resource_name: str | None, resource_id: Any, state: ChatState) -> tuple[int | None, str | None, list[dict]]:
    """
    Resolve a room id using the workspace catalog when the user only provides a name.
    Returns a tuple of (resolved_id, resolved_name, available_rooms) so the caller can ask for clarification.
    """
    rooms: list[dict] = []
    if resource_id is not None:
        try:
            rid = int(resource_id)
            return rid, resource_name, rooms
        except (TypeError, ValueError):
            return None, resource_name, rooms
    if not resource_name:
        return None, None, rooms
    lookup = await tool_runner.call("workspace", "GET", "/rooms", {})
    if lookup.get("status") != "ok":
        _add_event(state, "tool_error", {"service": "workspace", "error": lookup.get("error")})
        return None, None, rooms
    rooms = lookup.get("result", {}).get("rooms") or []
    target = resource_name.strip().lower() if resource_name else ""
    for room in rooms:
        name = str(room.get("name") or "").strip().lower()
        if name == target:
            return room.get("id"), room.get("name"), rooms  # type: ignore[return-value]
    partial = [room for room in rooms if target in str(room.get("name") or "").strip().lower()]
    if len(partial) == 1:
        r = partial[0]
        return r.get("id"), r.get("name"), rooms  # type: ignore[return-value]
    if not partial and len(rooms) == 1:
        r = rooms[0]
        return r.get("id"), r.get("name"), rooms  # type: ignore[return-value]
    return None, None, rooms


def _apply_booking_result_to_pending(pending: dict[str, Any], action: dict[str, Any]) -> None:
    result = action.get("result") or {}
    booking = result.get("booking") if isinstance(result, dict) else None
    if not isinstance(booking, dict):
        return
    filled = pending.setdefault("filled", {})
    for key in ("resource_id", "resource_type", "start_time", "end_time"):
        if booking.get(key) and _is_missing(filled.get(key)):
            filled[key] = booking.get(key)


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
        resolved_id, resolved_name, rooms = await _resolve_room_id(resource_name, resource_id, state)
        if resolved_id is None:
            return {
                "type": "workspace_booking",
                "status": "need_room_selection",
                "error": "Room not found; please pick from the available rooms.",
                "choices": rooms,
            }
        # persist resolved values so downstream success/failure messages have them
        pending.setdefault("filled", {})["resource_id"] = resolved_id
        if resolved_name:
            pending["filled"]["resource_name"] = resolved_name
        path = f"/rooms/{resolved_id}/book"
        payload = {"resource_name": resolved_name or resource_name, "start_time": start_time, "end_time": end_time}
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
            status = (
                result.get("result", {}).get("status")
                or result.get("status")
                or "submitted"
            )
            return {
                "type": action_type,
                "status": status,
                "payload": payload,
                "result": result.get("result"),
                # Surface either an explicit error from the tool or a skip reason
                # (e.g., TOOLS_ENABLED=false) so the user sees a useful message.
                "error": result.get("error") or result.get("reason"),
            }
        except Exception as exc:  # pragma: no cover - network errors
            _add_event(state, "tool_error", {"service": service, "error": str(exc)})
            return {"type": action_type, "status": "failed", "payload": payload}


def _required_fields(req_enum: RequestType | None, filled: dict[str, Any] | None = None) -> list[str]:
    filled = filled or {}
    if req_enum == RequestType.LEAVE:
        return ["leave_type", "start_date", "end_date"]
    if req_enum == RequestType.EXPENSE:
        return ["amount", "currency", "date", "category"]
    if req_enum == RequestType.TRAVEL:
        return ["origin", "destination", "departure_date", "return_date"]
    if req_enum == RequestType.ACCESS:
        return ["resource", "requested_role", "justification"]
    if req_enum == RequestType.TICKET:
        required = ["subtype", "description"]
        if filled.get("subtype") == "facilities":
            required.append("location")
        return required
    if req_enum == RequestType.WORKSPACE_BOOKING:
        return ["resource_type", "resource_name", "start_time", "end_time"]
    return []


def _failure_followup(
    req_enum: RequestType | None,
    pending: dict[str, Any],
    error: object,
    hint: str | None = None,
) -> str:
    fields = _required_fields(req_enum, pending.get("filled", {}))
    if fields:
        pending["missing"] = fields
    message = "I couldn't submit that yet."
    if hint:
        message += f" {hint}"
    elif error:
        message += f" {error}"
    if fields:
        message += f" Please provide/confirm: {', '.join(fields)}."
    return message


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
    name = filled.get("resource_name") or filled.get("resource_id")
    return (
        "Booking confirmed for "
        f"{filled.get('resource_type')} {name} "
        f"from {filled.get('start_time')} to {filled.get('end_time')}."
    )


async def _workspace_followup(action: dict[str, Any], pending: dict[str, Any], state: ChatState) -> str | None:
    err = action.get("error")
    if isinstance(err, dict):
        available = err.get("available") or []
        if available:
            names = ", ".join([str(a.get("name") or a.get("id")) for a in available])
            pending["missing"] = ["resource_name"]
            return f"That slot is already booked. Available options right now: {names}. Which one should I reserve?"
    if isinstance(err, str):
        low = err.lower()
        if "parse" in low and "time" in low:
            pending["missing"] = ["start_time", "end_time"]
            return "I couldn't understand the start/end time. Please give exact start and end (e.g., 2026-02-22 09:00 to 11:00)."
        if "end time must be after start time" in low:
            pending["missing"] = ["start_time", "end_time"]
            return "End time needs to be after start. What start and end times should I use?"
        if "resource name not found" in low or "room not found" in low:
            resource_type = (pending.get("filled", {}).get("resource_type") or "room").lower()
            items = await _list_resources(resource_type)
            if items:
                names = ", ".join([str(r.get("name") or r.get("id")) for r in items])
                label = "rooms" if resource_type == "room" else ("desks" if resource_type == "desk" else ("equipment" if resource_type == "equipment" else "parking"))
                pending["missing"] = ["resource_name"]
                return f"I couldn't find that {resource_type}. Available {label}: {names}. Which should I book?"
    # Fallback: ask for all required fields again
    req_enum = _as_request_type(pending.get("type"))
    hint = err if isinstance(err, str) else None
    return _failure_followup(req_enum, pending, err, hint)


def _workspace_failure(action: dict[str, Any]) -> str:
    error = action.get("error") or "The booking could not be created."
    return f"Booking failed: {error}"


def _to_iso_date(value: object) -> str | None:
    """
    Convert a user-entered date (day/month/year friendly) to ISO for backend APIs.
    """
    if not value:
        return None
    if isinstance(value, str):
        try:
            dt = dateparser.parse(value, dayfirst=True, yearfirst=False, fuzzy=True)
        except Exception:
            return value  # leave as-is; backend will raise a clear error
        if dt:
            return dt.date().isoformat()
    return value if isinstance(value, str) else None


async def _extract_pending_updates(pending: dict[str, Any], message: str) -> dict[str, Any]:
    request_type = _as_request_type(pending.get("type"))
    if request_type is None:
        return {}
    missing = list(pending.get("missing", []))
    updates = await extract_fields(request_type, message)
    merged: dict[str, Any] = {}
    if isinstance(updates, dict):
        for key, value in updates.items():
            if value is not None:
                merged[key] = value

    if request_type == RequestType.WORKSPACE_BOOKING:
        # Only fall back to heuristics if the LLM didn't extract anything useful.
        inferred = {} if merged else _infer_workspace_fields(message)
    else:
        inferred = _infer_fields_from_message(request_type, message, pending)
    for key, value in inferred.items():
        if value is not None and merged.get(key) is None:
            merged[key] = value

    for key in missing:
        if merged.get(key) is not None:
            continue
        coerced = _coerce_answer_for_field(key, message)
        if coerced is not None:
            merged[key] = coerced

    return merged


def _coerce_answer_for_field(field: str, message: str) -> Any:
    text = (message or "").strip()
    if not text:
        return None
    if field in {"start_date", "end_date", "date", "departure_date", "return_date"}:
        if not _looks_like_date_expression(text):
            return None
        return _parse_iso_date_strict(text)
    if field in {"start_time", "end_time", "description", "location", "resource", "resource_name"}:
        return text
    if field in {"origin", "destination", "leave_type", "category", "project_code", "reason", "justification"}:
        if field == "category":
            inferred = _infer_expense_category(text)
            if inferred:
                return inferred
        if field == "project_code":
            inferred = _extract_project_code(text)
            if inferred:
                return inferred
        return text
    if field == "amount":
        return _extract_amount(text)
    if field == "currency":
        symbols = {"$": "USD"}
        for symbol, code in symbols.items():
            if symbol in text:
                return code
        lower = text.lower()
        word_map = {
            "baht": "THB",
            "thb": "THB",
            "dollar": "USD",
            "usd": "USD",
            "euro": "EUR",
            "eur": "EUR",
            "pound": "GBP",
            "gbp": "GBP",
            "yen": "JPY",
            "jpy": "JPY",
        }
        for word, code in word_map.items():
            if re.search(rf"\b{re.escape(word)}s?\b", lower):
                return code
        m = re.search(r"\b([A-Za-z]{3})\b", text)
        return m.group(1).upper() if m else None
    if field == "requested_role":
        return _normalize_access_role(text)
    if field == "subtype":
        lower = text.lower()
        if "facility" in lower:
            return "facilities"
        if "it" in lower:
            return "it"
        return None
    if field == "resource_type":
        lower = text.lower()
        for value in ("room", "desk", "equipment", "parking"):
            if value in lower:
                return value
        return None
    return text


def _normalize_access_role(role: Any) -> str | None:
    if role is None:
        return None
    value = str(role).strip().lower()
    if re.search(r"\b(read|viewer|view)\b", value):
        return "viewer"
    if re.search(r"\b(write|editor|edit)\b", value):
        return "editor"
    if re.search(r"\badmin\b", value):
        return "admin"
    if re.search(r"\bowner\b", value):
        return "owner"
    mapping = {
        "read": "viewer",
        "viewer": "viewer",
        "view": "viewer",
        "write": "editor",
        "editor": "editor",
        "edit": "editor",
        "admin": "admin",
        "owner": "owner",
    }
    return mapping.get(value, value or None)


def _infer_fields_from_message(
    request_type: RequestType, message: str, pending: dict[str, Any]
) -> dict[str, Any]:
    if request_type == RequestType.EXPENSE:
        return _infer_expense_fields(message)
    if request_type == RequestType.TRAVEL:
        return _infer_travel_fields(message)
    if request_type == RequestType.LEAVE:
        return _infer_leave_fields(message)
    if request_type == RequestType.ACCESS:
        return _infer_access_fields(message)
    if request_type == RequestType.TICKET:
        return _infer_ticket_fields(message)
    if request_type == RequestType.WORKSPACE_BOOKING:
        return _infer_workspace_fields(message)
    return {}


def _infer_expense_fields(text: str) -> dict[str, Any]:
    return {
        "amount": _extract_amount(text),
        "currency": _coerce_answer_for_field("currency", text),
        "date": _parse_iso_date_strict(text),
        "category": _infer_expense_category(text),
        "project_code": _extract_project_code(text),
    }


def _infer_travel_fields(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    route = re.search(
        r"\bfrom\s+([A-Za-z][A-Za-z\s\-]{1,40}?)\s+to\s+([A-Za-z][A-Za-z\s\-]{1,40}?)(?=$|\s+on|\s+depart|\s+return|[,.])",
        text,
        flags=re.IGNORECASE,
    )
    if route:
        fields["origin"] = route.group(1).strip()
        fields["destination"] = route.group(2).strip()
    else:
        to_match = re.search(r"\bto\s+([A-Za-z][A-Za-z\s\-]{1,40}?)(?=$|\s+on|\s+depart|\s+return|[,.])", text, flags=re.IGNORECASE)
        from_match = re.search(r"\bfrom\s+([A-Za-z][A-Za-z\s\-]{1,40}?)(?=$|\s+on|\s+depart|\s+return|[,.])", text, flags=re.IGNORECASE)
        if from_match:
            fields["origin"] = from_match.group(1).strip()
        if to_match:
            fields["destination"] = to_match.group(1).strip()

    dates = _extract_iso_dates_from_text(text)
    if len(dates) >= 1:
        fields["departure_date"] = dates[0]
    if len(dates) >= 2:
        fields["return_date"] = dates[1]

    low = text.lower()
    if "business class" in low:
        fields["class"] = "business"
    elif "economy" in low:
        fields["class"] = "economy"
    elif "first class" in low:
        fields["class"] = "first"
    return fields


def _infer_leave_fields(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    low = text.lower()
    leave_types = ("annual", "sick", "unpaid", "business", "wedding", "bereavement")
    for leave_type in leave_types:
        if re.search(rf"\b{leave_type}\b", low):
            fields["leave_type"] = leave_type
            break
    dates = _extract_iso_dates_from_text(text)
    if len(dates) >= 1:
        fields["start_date"] = dates[0]
    if len(dates) >= 2:
        fields["end_date"] = dates[1]
    if re.search(r"\bfor\b", low):
        fields["reason"] = text
    return fields


def _infer_access_fields(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    role = _normalize_access_role(text)
    if role in {"viewer", "editor", "admin", "owner"}:
        fields["requested_role"] = role
    m = re.search(r"\baccess\s+(?:to|for)\s+([A-Za-z0-9._/\-]+)", text, flags=re.IGNORECASE)
    if m:
        fields["resource"] = m.group(1)
    if len(text.strip()) > 8:
        fields["justification"] = text.strip()
    return fields


def _infer_ticket_fields(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {"description": text.strip()}
    low = text.lower()
    fields["subtype"] = "facilities" if any(w in low for w in ("ac", "aircon", "light", "room", "facility")) else "it"
    m = re.search(r"\b(?:in|at)\s+([A-Za-z0-9#\-\s]{2,40})", text, flags=re.IGNORECASE)
    if m:
        fields["location"] = m.group(1).strip().rstrip(".")
    return fields


def _infer_workspace_fields(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    low = text.lower()
    resource_type = _infer_workspace_resource_type(low)
    if resource_type:
        fields["resource_type"] = resource_type

    name = None
    # Patterns like "room 12", "desk #A3", "parking spot B2"
    m = re.search(r"\b(room|desk|equipment|parking|spot)\s*(?:#|no\.|number|id)?\s*([A-Za-z0-9][A-Za-z0-9\-]*)", text, flags=re.IGNORECASE)
    if m:
        rtype = _normalize_resource_type(m.group(1))
        token = m.group(2)
        if rtype:
            fields.setdefault("resource_type", rtype)
        if _valid_resource_token(token):
            name = token
    # Patterns like "Orion room", "Zephyr desk"
    m2 = re.search(r"\b([A-Za-z0-9][A-Za-z0-9\-]+)\s+(room|desk|spot)\b", text, flags=re.IGNORECASE)
    if m2 and not name:
        token = m2.group(1)
        rtype = _normalize_resource_type(m2.group(2))
        if rtype:
            fields.setdefault("resource_type", rtype)
        if _valid_resource_token(token):
            name = token

    if name:
        fields["resource_name"] = name
        if name.isdigit():
            fields["resource_id"] = int(name)

    # Time range: "from X to Y" or "X-Y"
    span = re.search(r"\b(?:from|between)\s+(.+?)\s+(?:to|and|until)\s+(.+?)(?=$|[,.])", text, flags=re.IGNORECASE)
    if not span:
        span = re.search(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(?:-|to|until)\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", text, flags=re.IGNORECASE)
    if span:
        fields["start_time"] = span.group(1).strip()
        fields["end_time"] = span.group(2).strip()
    else:
        single = re.search(r"\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", text, flags=re.IGNORECASE)
        if single:
            fields["start_time"] = single.group(1).strip()

    # If a single date is present, attach it to times if they don't already contain a date.
    dates = _extract_iso_dates_from_text(text)
    if dates:
        date = dates[0]
        if fields.get("start_time") and not _contains_date_token(fields["start_time"]):
            fields["start_time"] = f"{date} {fields['start_time']}"
        if fields.get("end_time") and not _contains_date_token(fields["end_time"]):
            fields["end_time"] = f"{date} {fields['end_time']}"
    return fields


def _infer_workspace_resource_type(text: str) -> str | None:
    synonyms = {
        "room": ["room", "meeting room", "conference room", "boardroom"],
        "desk": ["desk", "hot desk", "workstation", "seat", "cubicle"],
        "equipment": ["equipment", "projector", "monitor", "laptop", "whiteboard", "speaker"],
        "parking": ["parking", "parking spot", "parking space", "garage", "car park"],
    }
    for rtype, words in synonyms.items():
        if any(word in text for word in words):
            return rtype
    return None


def _contains_date_token(text: str) -> bool:
    return bool(
        re.search(
            r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def _valid_resource_token(token: str) -> bool:
    stop = {
        "book",
        "booking",
        "reserve",
        "reservation",
        "want",
        "need",
        "a",
        "the",
        "my",
    }
    return token.lower() not in stop


def _extract_project_code(text: str) -> str | None:
    m = re.search(r"\b([A-Za-z]{2,10}-\d{1,6})\b", text)
    return m.group(1) if m else None


def _infer_expense_category(text: str) -> str | None:
    lower = text.lower()
    keywords = {
        "hotel": "hotel",
        "taxi": "taxi",
        "meal": "meal",
        "food": "meal",
        "flight": "flight",
        "airfare": "flight",
        "train": "train",
        "parking": "parking",
    }
    for word, category in keywords.items():
        if re.search(rf"\b{re.escape(word)}\b", lower):
            return category
    return None


def _extract_amount(text: str) -> float | None:
    raw = text.replace(",", "")
    matches = list(re.finditer(r"\d+(?:\.\d+)?", raw))
    if not matches:
        return None

    best_value: float | None = None
    best_score = -10
    for m in matches:
        value = float(m.group(0))
        score = 0
        context = raw[max(0, m.start() - 12) : min(len(raw), m.end() + 12)].lower()
        wide_context = raw[max(0, m.start() - 20) : min(len(raw), m.end() + 20)].lower()
        if any(token in context for token in ("$", "usd", "baht", "thb", "eur", "gbp", "jpy", "cost", "total", "amount")):
            score += 2
        months = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
        if value.is_integer() and 1900 <= int(value) <= 2100 and any(month in wide_context for month in months):
            score -= 2
        if value < 1:
            score -= 1
        if score > best_score or (score == best_score and (best_value is None or value > best_value)):
            best_score = score
            best_value = value
    return best_value


def _extract_iso_dates_from_text(text: str) -> list[str]:
    matches: list[str] = []
    patterns = [
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        r"\b\d{1,2}[/-][A-Za-z]{3,9}[/-]\d{2,4}\b",
        r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}\b",
        r"\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}\b",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, text):
            iso = _parse_iso_date_strict(m.group(0))
            if iso and iso not in matches:
                matches.append(iso)
    return matches


def _looks_like_date_expression(text: str) -> bool:
    lower = text.lower()
    if re.search(r"\d", lower):
        return True
    if any(word in lower for word in ("today", "tomorrow", "yesterday", "next", "this", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")):
        return True
    if re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", lower):
        return True
    return False


def _parse_iso_date_strict(text: str) -> str | None:
    candidates: list[str] = []
    candidates.append(text)
    patterns = [
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        r"\b\d{1,2}[/-][A-Za-z]{3,9}[/-]\d{2,4}\b",
        r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}\b",
        r"\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}\b",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, text):
            candidates.append(m.group(0))

    for candidate in candidates:
        try:
            dt = dateparser.parse(candidate, dayfirst=True, yearfirst=False, fuzzy=True)
        except Exception:
            dt = None
        if dt:
            return dt.date().isoformat()
    return None

