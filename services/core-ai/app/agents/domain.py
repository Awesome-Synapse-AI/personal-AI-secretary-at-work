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
        updates = await _extract_pending_updates(pending, message)
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
        updates = await _extract_pending_updates(pending, message)
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
        updates = await _extract_pending_updates(pending, message)
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
    for value in ("room", "desk", "equipment", "parking"):
        if value in low:
            fields["resource_type"] = value
            break
    if "resource_type" in fields:
        m = re.search(rf"\b{fields['resource_type']}\s*#?\s*([A-Za-z0-9\-]+)", text, flags=re.IGNORECASE)
        if m:
            token = m.group(1)
            fields["resource_name"] = f"{fields['resource_type'].title()} {token}"
            if token.isdigit():
                fields["resource_id"] = int(token)
    span = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+?)(?=$|[,.])", text, flags=re.IGNORECASE)
    if span:
        fields["start_time"] = span.group(1).strip()
        fields["end_time"] = span.group(2).strip()
    return fields


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

