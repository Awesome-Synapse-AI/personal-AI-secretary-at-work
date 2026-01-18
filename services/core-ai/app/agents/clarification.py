from typing import Any

from app.llm_client import call_llm_json

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
    "subtype": "Is this an IT issue or a facilities issue?",
    "description": "Can you describe the issue in a sentence?",
    "location": "Which room or area is this in?",
    "resource": "Which system or repo do you need access to?",
    "requested_role": "What level of access do you need (read, write, admin)?",
    "justification": "Briefly explain why you need this access.",
}

REQUEST_TYPES_BY_DOMAIN = {
    "hr": ["leave"],
    "ops": ["expense", "travel"],
    "it": ["access", "ticket"],
}

FIELD_SETS = {
    "leave": ["leave_type", "start_date", "end_date", "reason"],
    "expense": ["amount", "currency", "date", "category", "project_code"],
    "travel": ["origin", "destination", "departure_date", "return_date", "class"],
    "access": ["resource", "requested_role", "justification"],
    "ticket": ["subtype", "description", "location"],
}

FIELD_DESCRIPTIONS = {
    "leave": "leave_type, start_date (YYYY-MM-DD), end_date (YYYY-MM-DD), reason",
    "expense": "amount (number), currency (ISO 4217), date (YYYY-MM-DD), category, project_code",
    "travel": "origin, destination, departure_date (YYYY-MM-DD), return_date (YYYY-MM-DD), class",
    "access": "resource, requested_role (read/write/admin), justification",
    "ticket": "subtype (it or facilities), description, location",
}


def build_pending_request(
    domain: str, request_type: str, fields: dict[str, Any]
) -> dict[str, Any]:
    filled = _normalize_fields(request_type, fields)
    pending = {
        "domain": domain,
        "type": request_type,
        "filled": filled,
        "missing": [],
        "step": "collecting_details",
    }
    pending["missing"] = _missing_fields(pending)
    if request_type == "ticket":
        pending["subtype"] = filled.get("subtype")
    return pending


def update_pending_request(
    pending: dict[str, Any], updates: dict[str, Any]
) -> dict[str, Any]:
    request_type = pending.get("type", "")
    filled = dict(pending.get("filled", {}))
    for key, value in _normalize_fields(request_type, updates).items():
        if value is not None:
            filled[key] = value
    pending["filled"] = filled
    pending["missing"] = _missing_fields(pending)
    if request_type == "ticket":
        pending["subtype"] = filled.get("subtype", pending.get("subtype"))
    return pending


async def classify_request(domain: str, message: str) -> tuple[str | None, dict[str, Any]]:
    allowed = REQUEST_TYPES_BY_DOMAIN.get(domain, [])
    if not allowed:
        return None, {}
    prompt = _classification_prompt(domain, allowed)
    payload = await call_llm_json(prompt, message, max_tokens=256)
    if not payload:
        return None, {}
    request_type = payload.get("request_type")
    if request_type not in allowed:
        return None, {}
    fields = payload.get("fields", {})
    return request_type, _normalize_fields(request_type, fields)


async def extract_fields(request_type: str, message: str) -> dict[str, Any]:
    if request_type not in FIELD_SETS:
        return {}
    prompt = _extraction_prompt(request_type)
    payload = await call_llm_json(prompt, message, max_tokens=256)
    if not payload:
        return {}
    declared = payload.get("request_type")
    if declared and declared != request_type:
        return {}
    fields = payload.get("fields", {})
    return _normalize_fields(request_type, fields)


def next_question(pending: dict[str, Any]) -> str:
    missing = pending.get("missing", [])
    if not missing:
        return ""
    return QUESTION_MAP.get(missing[0], "Could you clarify the missing detail?")


def _classification_prompt(domain: str, allowed: list[str]) -> str:
    details = "; ".join(f"{name}: {FIELD_DESCRIPTIONS[name]}" for name in allowed)
    allowed_values = ", ".join(allowed)
    return (
        "You classify employee requests and extract fields. "
        "Return only JSON with keys request_type and fields. "
        f"Domain: {domain}. "
        f"request_type must be one of: {allowed_values}. "
        f"For each type, fields are: {details}. "
        "Use null for unknown values."
    )


def _extraction_prompt(request_type: str) -> str:
    field_desc = FIELD_DESCRIPTIONS[request_type]
    return (
        "You extract fields for a single request type. "
        "Return only JSON with keys request_type and fields. "
        f"request_type must be '{request_type}'. "
        f"fields must include: {field_desc}. "
        "Use null for unknown values."
    )


def _normalize_fields(request_type: str, fields: dict[str, Any] | None) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    allowed = FIELD_SETS.get(request_type, [])
    source = fields if isinstance(fields, dict) else {}
    for key in allowed:
        value = source.get(key)
        if isinstance(value, str) and not value.strip():
            value = None
        normalized[key] = value
    return normalized


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
        required = ["subtype", "description"]
        if filled.get("subtype") == "facilities":
            required.append("location")
    else:
        required = []

    return [key for key in required if not filled.get(key)]
