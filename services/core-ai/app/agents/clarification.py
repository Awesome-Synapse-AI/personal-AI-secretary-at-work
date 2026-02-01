from enum import Enum
from typing import Any

import logging

from app.llm_client import call_llm_json

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


class RequestType(str, Enum):
    LEAVE = "leave"
    EXPENSE = "expense"
    TRAVEL = "travel"
    ACCESS = "access"
    TICKET = "ticket"
    WORKSPACE_BOOKING = "workspace_booking"


QUESTION_MAP = {
    "leave_type": "What kind of leave do you want to take (e.g., annual, sick)?",
    "start_date": "Which exact start date do you want? Please use YYYY-MM-DD.",
    "end_date": "Which exact end date do you want? Please use YYYY-MM-DD.",
    "start_time": "What is the start time? (e.g., tomorrow 9am, 2026-02-01 09:00)",
    "end_time": "What is the end time? (e.g., tomorrow 11am, 2026-02-01 11:00)",
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
    "resource_type": "What do you want to book (room, desk, equipment, parking)?",
    "resource_name": "Which resource should I book (e.g., Room 1, Desk #2)?",
}

REQUEST_TYPES_BY_DOMAIN = {
    "hr": [RequestType.LEAVE],
    "ops": [RequestType.EXPENSE, RequestType.TRAVEL],
    "it": [RequestType.ACCESS, RequestType.TICKET],
    "workspace": [RequestType.WORKSPACE_BOOKING],
}

FIELD_SETS = {
    RequestType.LEAVE: ["leave_type", "start_date", "end_date", "reason"],
    RequestType.EXPENSE: ["amount", "currency", "date", "category", "project_code"],
    RequestType.TRAVEL: ["origin", "destination", "departure_date", "return_date", "class"],
    RequestType.ACCESS: ["resource", "requested_role", "justification"],
    RequestType.TICKET: ["subtype", "description", "location"],
    RequestType.WORKSPACE_BOOKING: [
        "resource_type",
        "resource_name",
        "start_time",
        "end_time",
        "location",
        "description",
    ],
}

FIELD_DESCRIPTIONS = {
    RequestType.LEAVE: "leave_type, start_date (YYYY-MM-DD), end_date (YYYY-MM-DD), reason",
    RequestType.EXPENSE: "amount (number), currency (ISO 4217), date (YYYY-MM-DD), category, project_code",
    RequestType.TRAVEL: "origin, destination, departure_date (YYYY-MM-DD), return_date (YYYY-MM-DD), class",
    RequestType.ACCESS: "resource, requested_role (read/write/admin), justification",
    RequestType.TICKET: "subtype (it or facilities), description, location",
    RequestType.WORKSPACE_BOOKING: "resource_type (room/desk/equipment/parking), resource_name, start_time (natural language ok), end_time (natural language ok), location, description",
}


def build_pending_request(
    domain: str, request_type: RequestType, fields: dict[str, Any]
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
    if request_type == RequestType.TICKET:
        pending["subtype"] = filled.get("subtype")
    if request_type == RequestType.WORKSPACE_BOOKING:
        pending["subtype"] = filled.get("resource_type")
    return pending


def update_pending_request(
    pending: dict[str, Any], updates: dict[str, Any]
) -> dict[str, Any]:
    request_type: RequestType | str = pending.get("type", "")
    filled = dict(pending.get("filled", {}))
    for key, value in _normalize_fields(request_type, updates).items():
        if value is not None:
            filled[key] = value
    pending["filled"] = filled
    pending["missing"] = _missing_fields(pending)
    if request_type == RequestType.TICKET:
        pending["subtype"] = filled.get("subtype", pending.get("subtype"))
    if request_type == RequestType.WORKSPACE_BOOKING:
        pending["subtype"] = filled.get("resource_type", pending.get("subtype"))
    return pending


async def classify_request(domain: str, message: str) -> tuple[RequestType | None, dict[str, Any]]:
    allowed = REQUEST_TYPES_BY_DOMAIN.get(domain, [])
    if not allowed:
        return None, {}
    prompt = _classification_prompt(domain, allowed)
    payload = await call_llm_json(prompt, message, max_tokens=256)
    if not payload:
        logger.warning("Clarify classify_request got empty payload for domain=%s", domain)
        print(f"Clarify classify_request empty payload domain={domain}", flush=True)
        return None, {}
    request_type = payload.get("request_type")
    if isinstance(request_type, str):
        try:
            request_type_enum = RequestType(request_type)
        except ValueError:
            logger.warning("Clarify classify_request invalid type %s for domain %s", request_type, domain)
            print(f"Clarify classify_request invalid type {request_type} for domain {domain}", flush=True)
            return None, {}
    else:
        request_type_enum = None
    if request_type_enum not in allowed:
        logger.warning("Clarify classify_request invalid type %s for domain %s", request_type, domain)
        print(f"Clarify classify_request invalid type {request_type} for domain {domain}", flush=True)
        return None, {}
    fields = payload.get("fields", {})
    logger.info("Clarify classify_request payload: %s", payload)
    print(f"Clarify classify_request payload: {payload}", flush=True)
    return request_type_enum, _normalize_fields(request_type_enum, fields)


async def extract_fields(request_type: RequestType, message: str) -> dict[str, Any]:
    if request_type not in FIELD_SETS:
        return {}
    prompt = _extraction_prompt(request_type)
    payload = await call_llm_json(prompt, message, max_tokens=256)
    if not payload:
        logger.warning("Clarify extract_fields got empty payload for type=%s", request_type)
        print(f"Clarify extract_fields empty payload type={request_type}", flush=True)
        return {}
    declared = payload.get("request_type")
    if declared and declared != request_type:
        try:
            declared_enum = RequestType(declared)
        except ValueError:
            declared_enum = None
        if declared_enum != request_type:
            logger.warning("Clarify extract_fields mismatched type %s (expected %s)", declared, request_type)
            print(f"Clarify extract_fields mismatched type {declared} expected {request_type}", flush=True)
            return {}
    fields = payload.get("fields", {})
    logger.info("Clarify extract_fields payload: %s", payload)
    print(f"Clarify extract_fields payload: {payload}", flush=True)
    return _normalize_fields(request_type, fields)


def next_question(pending: dict[str, Any]) -> str:
    missing = pending.get("missing", [])
    if not missing:
        return ""
    return QUESTION_MAP.get(missing[0], "Could you clarify the missing detail?")


def _classification_prompt(domain: str, allowed: list[RequestType]) -> str:
    details = "; ".join(f"{name.value}: {FIELD_DESCRIPTIONS[name]}" for name in allowed)
    allowed_values = ", ".join([a.value for a in allowed])
    return (
        "You classify employee requests and extract fields. "
        "Return only a single JSON object with keys request_type and fields. "
        "Do not include reasoning, code fences, or extra text. "
        f"Domain: {domain}. "
        f"request_type must be one of: {allowed_values}. "
        f"For each type, fields are: {details}. "
        "Use null for unknown values."
    )


def _extraction_prompt(request_type: RequestType) -> str:
    field_desc = FIELD_DESCRIPTIONS[request_type]
    return (
        "You extract fields for a single request type. "
        "Return only a single JSON object with keys request_type and fields. "
        "Do not include reasoning, code fences, or extra text. "
        f"request_type must be '{request_type.value}'. "
        f"fields must include: {field_desc}. "
        "Use null for unknown values."
    )


def _normalize_fields(request_type: RequestType | str, fields: dict[str, Any] | None) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    req_enum = _as_request_type(request_type)
    if not req_enum:
        return normalized
    allowed = FIELD_SETS.get(req_enum, [])
    source = fields if isinstance(fields, dict) else {}
    for key in allowed or []:
        value = source.get(key)
        if _is_missing(value):
            value = None
        normalized[key] = value
    return normalized


def _missing_fields(pending: dict[str, Any]) -> list[str]:
    request_type = pending.get("type", "")
    req_enum = _as_request_type(request_type)
    filled = pending.get("filled", {})

    if req_enum == RequestType.LEAVE:
        required = ["leave_type", "start_date", "end_date"]
    elif req_enum == RequestType.EXPENSE:
        required = ["amount", "currency", "date", "category"]
    elif req_enum == RequestType.TRAVEL:
        required = ["origin", "destination", "departure_date", "return_date"]
    elif req_enum == RequestType.ACCESS:
        required = ["resource", "requested_role", "justification"]
    elif req_enum == RequestType.TICKET:
        required = ["subtype", "description"]
        if filled.get("subtype") == "facilities":
            required.append("location")
    elif req_enum == RequestType.WORKSPACE_BOOKING:
        required = ["resource_type", "resource_name", "start_time", "end_time"]
    else:
        required = []

    return [key for key in required if _is_missing(filled.get(key))]


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _as_request_type(value: Any) -> RequestType | None:
    if isinstance(value, RequestType):
        return value
    if isinstance(value, str):
        try:
            return RequestType(value)
        except ValueError:
            return None
    return None
