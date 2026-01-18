import re
from typing import Any


DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
AMOUNT_RE = re.compile(r"\b(\d+(?:\.\d{1,2})?)\b")
CURRENCY_CODES = {"USD", "EUR", "THB", "SGD", "JPY", "GBP"}

QUESTION_MAP = {
    "leave_type": "What kind of leave do you want to take (e.g., annual, sick)?",
    "start_date": "Which exact start date do you want? Please use YYYY-MM-DD.",
    "end_date": "Which exact end date do you want? Please use YYYY-MM-DD.",
    "amount": "How much was the expense?",
    "currency": "What currency was this in (e.g., USD, THB)?",
    "date": "When did this expense occur? Please use YYYY-MM-DD.",
    "category": "What type of expense is this (e.g., taxi, hotel, meal)?",
    "origin": "Which city or airport are you departing from?",
    "destination": "What is the destination city or airport?",
    "departure_date": "What is the departure date? Please use YYYY-MM-DD.",
    "return_date": "What is the return date? Please use YYYY-MM-DD.",
    "description": "Can you describe the issue in a sentence?",
    "location": "Which room or area is this in?",
    "resource": "Which system or repo do you need access to?",
    "requested_role": "What level of access do you need (read, write, admin)?",
    "justification": "Briefly explain why you need this access.",
}


def detect_request_type(domain: str, message: str) -> str | None:
    lowered = message.lower()
    if domain == "hr":
        if any(term in lowered for term in ["leave", "vacation", "sick", "time off"]):
            return "leave"
    if domain == "ops":
        if any(term in lowered for term in ["expense", "reimburse", "spent", "receipt"]):
            return "expense"
        if any(term in lowered for term in ["travel", "flight", "hotel", "trip"]):
            return "travel"
    if domain == "it":
        if any(term in lowered for term in ["access", "permission"]):
            return "access"
        if any(term in lowered for term in ["vpn", "wifi", "laptop", "ticket", "broken", "issue"]):
            return "ticket"
    return None


def detect_ticket_subtype(message: str) -> str:
    lowered = message.lower()
    if any(term in lowered for term in ["ac", "aircon", "room", "desk", "office", "lighting"]):
        return "facilities"
    return "it"


def _extract_dates(message: str) -> list[str]:
    return DATE_RE.findall(message)


def _extract_amount(message: str) -> str | None:
    matches = AMOUNT_RE.findall(message)
    if not matches:
        return None
    return matches[0][0]


def _extract_currency(message: str) -> str | None:
    tokens = {token.strip(".,").upper() for token in message.split()}
    for code in CURRENCY_CODES:
        if code in tokens:
            return code
    return None


def _extract_category(message: str) -> str | None:
    lowered = message.lower()
    for category in ["taxi", "hotel", "meal", "flight", "train", "office"]:
        if category in lowered:
            return category
    return None


def _extract_leave_type(message: str) -> str | None:
    lowered = message.lower()
    for leave_type in ["annual", "sick", "unpaid", "maternity", "paternity"]:
        if leave_type in lowered:
            return leave_type
    return None


def _extract_origin_destination(message: str) -> tuple[str | None, str | None]:
    lowered = message.lower()
    match = re.search(r"from\s+([a-zA-Z\s]+?)\s+to\s+([a-zA-Z\s]+)", lowered)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return None, None


def _extract_access_resource(message: str) -> str | None:
    match = re.search(r"access\s+(?:to|for)\s+([\w\-\s]+)", message, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_access_role(message: str) -> str | None:
    lowered = message.lower()
    for role in ["read", "write", "admin", "owner"]:
        if role in lowered:
            return role
    return None


def _extract_justification(message: str) -> str | None:
    match = re.search(r"because\s+(.+)$", message, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_location(message: str) -> str | None:
    match = re.search(r"(?:in|at)\s+([\w\-\s]+)$", message, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _base_pending(domain: str, request_type: str, filled: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain": domain,
        "type": request_type,
        "filled": filled,
        "missing": [],
        "step": "collecting_details",
    }


def build_pending_request(domain: str, request_type: str, message: str) -> dict[str, Any]:
    filled: dict[str, Any] = {}
    missing: list[str] = []

    if request_type == "leave":
        dates = _extract_dates(message)
        filled = {
            "leave_type": _extract_leave_type(message),
            "start_date": dates[0] if len(dates) > 0 else None,
            "end_date": dates[1] if len(dates) > 1 else None,
            "reason": None,
        }
        missing = [key for key in ["leave_type", "start_date", "end_date"] if not filled.get(key)]
    elif request_type == "expense":
        filled = {
            "amount": _extract_amount(message),
            "currency": _extract_currency(message),
            "date": _extract_dates(message)[0] if _extract_dates(message) else None,
            "category": _extract_category(message),
            "project_code": None,
        }
        missing = [
            key
            for key in ["amount", "currency", "date", "category"]
            if not filled.get(key)
        ]
    elif request_type == "travel":
        origin, destination = _extract_origin_destination(message)
        dates = _extract_dates(message)
        filled = {
            "origin": origin,
            "destination": destination,
            "departure_date": dates[0] if len(dates) > 0 else None,
            "return_date": dates[1] if len(dates) > 1 else None,
            "class": None,
        }
        missing = [
            key
            for key in ["origin", "destination", "departure_date", "return_date"]
            if not filled.get(key)
        ]
    elif request_type == "access":
        filled = {
            "resource": _extract_access_resource(message),
            "requested_role": _extract_access_role(message),
            "justification": _extract_justification(message),
        }
        missing = [
            key
            for key in ["resource", "requested_role", "justification"]
            if not filled.get(key)
        ]
    elif request_type == "ticket":
        subtype = detect_ticket_subtype(message)
        filled = {
            "subtype": subtype,
            "description": message.strip() or None,
            "location": _extract_location(message),
        }
        required = ["description"] if subtype == "it" else ["description", "location"]
        missing = [key for key in required if not filled.get(key)]

    pending = _base_pending(domain, request_type, filled)
    pending["missing"] = missing
    if request_type == "ticket":
        pending["subtype"] = filled.get("subtype")
    return pending


def update_pending_request(pending: dict[str, Any], message: str) -> dict[str, Any]:
    request_type = pending.get("type", "")
    filled = dict(pending.get("filled", {}))

    if request_type == "leave":
        dates = _extract_dates(message)
        filled["leave_type"] = filled.get("leave_type") or _extract_leave_type(message)
        if dates:
            filled["start_date"] = filled.get("start_date") or dates[0]
            if len(dates) > 1:
                filled["end_date"] = filled.get("end_date") or dates[1]
    elif request_type == "expense":
        filled["amount"] = filled.get("amount") or _extract_amount(message)
        filled["currency"] = filled.get("currency") or _extract_currency(message)
        dates = _extract_dates(message)
        filled["date"] = filled.get("date") or (dates[0] if dates else None)
        filled["category"] = filled.get("category") or _extract_category(message)
    elif request_type == "travel":
        origin, destination = _extract_origin_destination(message)
        if origin and not filled.get("origin"):
            filled["origin"] = origin
        if destination and not filled.get("destination"):
            filled["destination"] = destination
        dates = _extract_dates(message)
        if dates:
            filled["departure_date"] = filled.get("departure_date") or dates[0]
            if len(dates) > 1:
                filled["return_date"] = filled.get("return_date") or dates[1]
    elif request_type == "access":
        filled["resource"] = filled.get("resource") or _extract_access_resource(message)
        filled["requested_role"] = filled.get("requested_role") or _extract_access_role(message)
        filled["justification"] = filled.get("justification") or _extract_justification(message)
    elif request_type == "ticket":
        subtype = pending.get("subtype") or detect_ticket_subtype(message)
        filled["subtype"] = subtype
        filled["description"] = filled.get("description") or message.strip()
        filled["location"] = filled.get("location") or _extract_location(message)

    pending["filled"] = filled
    pending["missing"] = _missing_fields(pending)
    return pending


def _missing_fields(pending: dict[str, Any]) -> list[str]:
    request_type = pending.get("type", "")
    filled = pending.get("filled", {})

    if request_type == "leave":
        required = ["leave_type", "start_date", "end_date"]
    elif request_type == "expense":
        required = ["amount", "currency", "date", "category"]
    elif request_type == "travel":
        required = ["origin", "destination", "departure_date", "return_date"]
    elif request_type == "access":
        required = ["resource", "requested_role", "justification"]
    elif request_type == "ticket":
        required = ["description"]
        if filled.get("subtype") == "facilities":
            required.append("location")
    else:
        required = []

    return [key for key in required if not filled.get(key)]


def next_question(pending: dict[str, Any]) -> str:
    missing = pending.get("missing", [])
    if not missing:
        return ""
    return QUESTION_MAP.get(missing[0], "Could you clarify the missing detail?")
