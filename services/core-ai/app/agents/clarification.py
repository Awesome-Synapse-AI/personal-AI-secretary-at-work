from enum import Enum
from typing import Any
import re

import logging
from dateutil import parser as dateparser

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
    "start_date": "Which exact start date do you want? Please use DD/MM/YYYY.",
    "end_date": "Which exact end date do you want? Please use DD/MM/YYYY.",
    "start_time": "What is the start time? (e.g., tomorrow 9am, 2026-02-01 09:00)",
    "end_time": "What is the end time? (e.g., tomorrow 11am, 2026-02-01 11:00)",
    "amount": "How much was the expense?",
    "currency": "What currency was this in (e.g., USD, THB)?",
    "date": "When did this expense occur? Please use DD/MM/YYYY.",
    "category": "What type of expense is this (e.g., taxi, hotel, meal)?",
    "origin": "Which city or airport are you departing from?",
    "destination": "What is the destination city or airport?",
    "departure_date": "What is the departure date? Please use DD/MM/YYYY.",
    "return_date": "What is the return date? Please use DD/MM/YYYY.",
    "preferred_departure_time": "What time do you want to depart? (e.g., 9:00 AM)",
    "preferred_return_time": "What time do you want to return? (e.g., 7:00 PM)",
    "subtype": "Is this an IT issue or a facilities issue?",
    "description": "Can you describe the issue in a sentence?",
    "location": "Which room or area is this in?",
    "entity": "Which asset is affected (printer, laptop, software, network, etc.)?",
    "resource": "Which system or repo do you need access to?",
    "requested_role": "What level of access do you need (read, write, admin)?",
    "justification": "Briefly explain why you need this access.",
    "needed_by_date": "When do you need this access by? Please use DD/MM/YYYY.",
    "resource_type": "What do you want to book (room, desk, equipment, parking)?",
    "resource_name": "Which resource should I book (e.g., Room 1, Desk #2)?",
    "incident_date": "When did this issue happen? Please use DD/MM/YYYY.",
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
    RequestType.TRAVEL: [
        "origin",
        "destination",
        "departure_date",
        "return_date",
        "preferred_departure_time",
        "preferred_return_time",
        "class",
    ],
    RequestType.ACCESS: ["resource", "requested_role", "justification", "needed_by_date"],
    RequestType.TICKET: ["subtype", "description", "location", "entity", "incident_date"],
    RequestType.WORKSPACE_BOOKING: [
        "resource_type",
        "resource_name",
        "resource_id",
        "start_time",
        "end_time",
        "location",
        "description",
    ],
}

FIELD_DESCRIPTIONS = {
    RequestType.LEAVE: "leave_type, start_date (DD/MM/YYYY), end_date (DD/MM/YYYY), reason",
    RequestType.EXPENSE: "amount (number), currency (ISO 4217), date (DD/MM/YYYY), category, project_code",
    RequestType.TRAVEL: "origin, destination, departure_date (DD/MM/YYYY), return_date (DD/MM/YYYY), preferred_departure_time, preferred_return_time, class",
    RequestType.ACCESS: "resource, requested_role (read/write/admin), justification, needed_by_date (DD/MM/YYYY)",
    RequestType.TICKET: "subtype (it or facilities), description, location, entity (asset like printer/laptop/software/network), incident_date (DD/MM/YYYY)",
    RequestType.WORKSPACE_BOOKING: "resource_type (room/desk/equipment/parking), resource_name, start_time (include date if present), end_time (include date if present), location, description",
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
        return _heuristic_classify_request(domain, message)
    request_type = payload.get("request_type")
    if isinstance(request_type, str):
        try:
            request_type_enum = RequestType(request_type)
        except ValueError:
            logger.warning("Clarify classify_request invalid type %s for domain %s", request_type, domain)
            print(f"Clarify classify_request invalid type {request_type} for domain {domain}", flush=True)
            return _heuristic_classify_request(domain, message)
    else:
        request_type_enum = None
    if request_type_enum not in allowed:
        logger.warning("Clarify classify_request invalid type %s for domain %s", request_type, domain)
        print(f"Clarify classify_request invalid type {request_type} for domain {domain}", flush=True)
        return _heuristic_classify_request(domain, message)
    fields = _filter_fields_by_evidence(request_type_enum, payload.get("fields", {}), message)
    if request_type_enum == RequestType.TRAVEL:
        fields = await _extract_travel_fields_with_retry(message, fields)
    logger.info("Clarify classify_request payload: %s", payload)
    print(f"Clarify classify_request payload: {payload}", flush=True)
    return request_type_enum, _normalize_fields(request_type_enum, fields)


async def extract_fields(request_type: RequestType | str, message: str) -> dict[str, Any]:
    req_enum = _as_request_type(request_type)
    if not req_enum or req_enum not in FIELD_SETS:
        return {}
    prompt = _extraction_prompt(req_enum)
    payload = await call_llm_json(prompt, message, max_tokens=256)
    if not payload:
        logger.warning("Clarify extract_fields got empty payload for type=%s", req_enum)
        print(f"Clarify extract_fields empty payload type={req_enum}", flush=True)
        return {}
    declared = payload.get("request_type")
    if declared and declared != req_enum:
        try:
            declared_enum = RequestType(declared)
        except ValueError:
            declared_enum = None
        if declared_enum != req_enum:
            logger.warning("Clarify extract_fields mismatched type %s (expected %s)", declared, req_enum)
            print(f"Clarify extract_fields mismatched type {declared} expected {req_enum}", flush=True)
            return {}
    fields = _filter_fields_by_evidence(req_enum, payload.get("fields", {}), message)
    if req_enum == RequestType.TRAVEL:
        fields = await _extract_travel_fields_with_retry(message, fields)
    logger.info("Clarify extract_fields payload: %s", payload)
    print(f"Clarify extract_fields payload: {payload}", flush=True)
    return _normalize_fields(req_enum, fields)


def next_question(pending: dict[str, Any]) -> str:
    missing = pending.get("missing", [])
    if not missing:
        return ""
    prompts: list[str] = []
    for field in missing:
        prompts.append(
            QUESTION_MAP.get(
                field,
                f"Please provide {str(field).replace('_', ' ')}.",
            )
        )
    if len(prompts) == 1:
        return prompts[0]
    return "I still need these details:\n- " + "\n- ".join(prompts)


def _classification_prompt(domain: str, allowed: list[RequestType]) -> str:
    details = "; ".join(f"{name.value}: {FIELD_DESCRIPTIONS[name]}" for name in allowed)
    allowed_values = ", ".join([a.value for a in allowed])
    prompt = (
        "You classify employee requests and extract fields. "
        "Return only a single JSON object with keys request_type and fields. "
        "Do not include reasoning, code fences, or extra text. "
        f"Domain: {domain}. "
        f"request_type must be one of: {allowed_values}. "
        f"For each type, fields are: {details}. "
        "If the user already names what to book (e.g., \"book a room\"), set resource_type accordingly and do not ask again. "
        "Resource type synonyms: meeting room/conference room/boardroom -> room; hot desk/workstation -> desk; "
        "projector/monitor/laptop/whiteboard -> equipment; parking spot/parking space/garage -> parking. "
        "Use null for unknown values."
    )
    if domain == "ops":
        prompt += (
            " Ops disambiguation rules: "
            "choose travel when the user asks to plan/arrange/book transportation or a trip "
            "(including reserve car/taxi for customer/client visit). "
            "choose expense only when the user asks to reimburse/log an already incurred cost "
            "with spend details like amount/currency/receipt. "
            "Examples:\n"
            'Input: "I want to reserve a car to travel to customer company whole day on 12/Mar/2026. I will return back the same day at 7:00 p.m."\n'
            'Output: {"request_type":"travel","fields":{"origin":null,"destination":"customer company","departure_date":"2026-03-12","return_date":"2026-03-12","class":null}}\n'
            'Input: "Arrange transport for client site visit tomorrow and return by 7pm"\n'
            'Output: {"request_type":"travel","fields":{"origin":null,"destination":"client site","departure_date":"2026-03-15","return_date":"2026-03-15","class":null}}\n'
            'Input: "Please reimburse taxi 1200 THB from 12/03/2026"\n'
            'Output: {"request_type":"expense","fields":{"amount":1200,"currency":"THB","date":"2026-03-12","category":"taxi","project_code":null}}'
        )
    return prompt


def _extraction_prompt(request_type: RequestType) -> str:
    field_desc = FIELD_DESCRIPTIONS[request_type]
    base = (
        "You extract fields for a single request type. "
        "Return only a single JSON object with keys request_type and fields. "
        "Do not include reasoning, code fences, or extra text. "
        f"request_type must be '{request_type.value}'. "
        f"fields must include: {field_desc}. "
        "Use null for unknown values."
    )
    guidance = _extraction_guidance(request_type)
    return base + (" " + guidance if guidance else "")


def _extraction_guidance(request_type: RequestType) -> str:
    if request_type == RequestType.LEAVE:
        return (
            "Rules: leave_type should be one of annual, sick, unpaid, business, wedding, bereavement. "
            "Prefer explicit dates; if a range is given, map to start_date and end_date. "
            "Examples:\n"
            'Input: "I need sick leave from 12/03/2026 to 13/03/2026"\n'
            'Output: {"request_type":"leave","fields":{"leave_type":"sick","start_date":"2026-03-12","end_date":"2026-03-13","reason":null}}\n'
            'Input: "Annual leave on 2026-04-10"\n'
            'Output: {"request_type":"leave","fields":{"leave_type":"annual","start_date":"2026-04-10","end_date":"2026-04-10","reason":null}}\n'
            'Input: "Unpaid leave 2026-05-01 for a family matter"\n'
            'Output: {"request_type":"leave","fields":{"leave_type":"unpaid","start_date":"2026-05-01","end_date":"2026-05-01","reason":"family matter"}}'
        )
    if request_type == RequestType.EXPENSE:
        return (
            "Rules: amount must be numeric; currency should be a 3-letter code when mentioned. "
            "Examples:\n"
            'Input: "Log a $45 taxi from yesterday"\n'
            'Output: {"request_type":"expense","fields":{"amount":45,"currency":"USD","date":"2026-02-20","category":"taxi","project_code":null}}\n'
            'Input: "Expense 1200 THB meal on 2026-02-18"\n'
            'Output: {"request_type":"expense","fields":{"amount":1200,"currency":"THB","date":"2026-02-18","category":"meal","project_code":null}}\n'
            'Input: "Reimburse 30 EUR train ticket, project AB-12, on 2026-03-04"\n'
            'Output: {"request_type":"expense","fields":{"amount":30,"currency":"EUR","date":"2026-03-04","category":"train","project_code":"AB-12"}}'
        )
    if request_type == RequestType.TRAVEL:
        return (
            "Rules: origin and destination must be cities/airports; use departure_date and return_date when a range is given. "
            "Capture preferred_departure_time and preferred_return_time when a time is mentioned. "
            "Examples:\n"
            'Input: "Book travel from NYC to LAX Mar 1-5"\n'
            'Output: {"request_type":"travel","fields":{"origin":"NYC","destination":"LAX","departure_date":"2026-03-01","return_date":"2026-03-05","preferred_departure_time":null,"preferred_return_time":null,"class":null}}\n'
            'Input: "Flight Bangkok to Tokyo on 2026-03-20 return 2026-03-25, business class"\n'
            'Output: {"request_type":"travel","fields":{"origin":"Bangkok","destination":"Tokyo","departure_date":"2026-03-20","return_date":"2026-03-25","preferred_departure_time":null,"preferred_return_time":null,"class":"business"}}\n'
            'Input: "Travel to Singapore from Kuala Lumpur on 2026-04-01 leave at 9:00 AM return 7:00 PM"\n'
            'Output: {"request_type":"travel","fields":{"origin":"Kuala Lumpur","destination":"Singapore","departure_date":"2026-04-01","return_date":"2026-04-01","preferred_departure_time":"9:00 AM","preferred_return_time":"7:00 PM","class":null}}'
        )
    if request_type == RequestType.ACCESS:
        return (
            "Rules: requested_role should be read/write/admin. "
            "Examples:\n"
            'Input: "Give me write access to repo analytics for reporting by 15/03/2026"\n'
            'Output: {"request_type":"access","fields":{"resource":"repo analytics","requested_role":"write","justification":"for reporting","needed_by_date":"2026-03-15"}}\n'
            'Input: "Need read access to finance-dashboard"\n'
            'Output: {"request_type":"access","fields":{"resource":"finance-dashboard","requested_role":"read","justification":null,"needed_by_date":null}}\n'
            'Input: "Admin access to Jira because I manage the project from 2026-04-01"\n'
            'Output: {"request_type":"access","fields":{"resource":"Jira","requested_role":"admin","justification":"manage the project","needed_by_date":"2026-04-01"}}'
        )
    if request_type == RequestType.TICKET:
        return (
            "Rules: subtype is it or facilities; description should summarize the issue; entity is the affected asset. "
            "Examples:\n"
            'Input: "AC broken in Room 12 since 10/03/2026"\n'
            'Output: {"request_type":"ticket","fields":{"subtype":"facilities","description":"AC broken","location":"Room 12","entity":"ac","incident_date":"2026-03-10"}}\n'
            'Input: "VPN keeps dropping on my laptop"\n'
            'Output: {"request_type":"ticket","fields":{"subtype":"it","description":"VPN keeps dropping","location":null,"entity":"vpn","incident_date":null}}\n'
            'Input: "Projector not working in Meeting Room A on 2026-04-05"\n'
            'Output: {"request_type":"ticket","fields":{"subtype":"facilities","description":"Projector not working","location":"Meeting Room A","entity":"projector","incident_date":"2026-04-05"}}'
        )
    if request_type == RequestType.WORKSPACE_BOOKING:
        return (
            "Rules: resource_type must be one of room, desk, equipment, parking. "
            "If the user says \"book a room\" or \"meeting room\" set resource_type=\"room\". "
            "If the user mentions hot desk/workstation set resource_type=\"desk\". "
            "If the user mentions projector/monitor/laptop/whiteboard set resource_type=\"equipment\". "
            "If the user mentions parking spot/parking space/garage set resource_type=\"parking\". "
            "Only set resource_name when the user explicitly names a specific resource (e.g., \"Orion\", \"Desk A3\", \"B2\"). "
            "Do not copy the entire sentence into resource_name or start_time/end_time. "
            "Combine the date with the times when possible (YYYY-MM-DD HH:MM 24h is preferred). "
            "If you cannot find start_time or end_time, set them to null instead of echoing the request. "
            "Examples:\n"
            'Input: "I want to reserve a meeting room from 9:00 a.m. to 12:00 p.m. on 18/Oct/2025"\n'
            'Output: {"request_type":"workspace_booking","fields":{"resource_type":"room","resource_name":null,"resource_id":null,"start_time":"2025-10-18 09:00","end_time":"2025-10-18 12:00","location":null,"description":null}}\n'
            'Input: "Reserve a hot desk on 2026-03-25 from 09:00 to 12:00"\n'
            'Output: {"request_type":"workspace_booking","fields":{"resource_type":"desk","resource_name":null,"resource_id":null,"start_time":"2026-03-25 09:00","end_time":"2026-03-25 12:00","location":null,"description":null}}\n'
            'Input: "Reserve parking spot B2 on 2026-03-26 08:00-10:00"\n'
            'Output: {"request_type":"workspace_booking","fields":{"resource_type":"parking","resource_name":"B2","resource_id":null,"start_time":"2026-03-26 08:00","end_time":"2026-03-26 10:00","location":null,"description":null}}'
        )
    return ""


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
        if req_enum == RequestType.EXPENSE and value is not None:
            if key == "amount":
                value = _normalize_amount(value)
            elif key == "currency":
                value = _normalize_currency(value)
        if value and key in {"start_date", "end_date", "date", "departure_date", "return_date", "needed_by_date", "incident_date"}:
            iso = _to_iso_date(value)
            value = iso or value  # fallback to original if parse fails so we can prompt again later
        if req_enum == RequestType.ACCESS and key == "requested_role" and value is not None:
            value = _normalize_requested_role(value)
        if req_enum == RequestType.TICKET and key == "subtype" and value is not None:
            value = _normalize_ticket_subtype(value)
        if req_enum == RequestType.WORKSPACE_BOOKING:
            if key == "resource_type" and value is not None:
                value = _normalize_resource_type(value)
            if key in {"resource_name", "start_time", "end_time"} and isinstance(value, str):
                if not _looks_like_workspace_value(key, value):
                    value = None
        if req_enum == RequestType.TRAVEL and key == "origin" and value is None:
            value = "company"
        normalized[key] = value
    return normalized


def _filter_fields_by_evidence(
    req_enum: RequestType | None, fields: dict[str, Any] | None, message: str
) -> dict[str, Any]:
    if not req_enum or not isinstance(fields, dict):
        return {}
    lower = (message or "").lower()
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", message or "")]

    def _num_in_text(val: Any) -> bool:
        if val is None:
            return False
        try:
            num = float(val)
        except Exception:
            return False
        return any(abs(num - n) < 0.01 for n in nums)

    def _has_substring(val: Any) -> bool:
        return isinstance(val, str) and val.strip().lower() in lower

    def _has_date_evidence() -> bool:
        if re.search(r"\b\d{4}-\d{2}-\d{2}\b", lower):
            return True
        if re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", lower):
            return True
        if re.search(r"\b\d{1,2}[/-][A-Za-z]{3,9}[/-]\d{2,4}\b", lower):
            return True
        if re.search(r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}\b", lower):
            return True
        if re.search(r"\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}\b", lower):
            return True
        if any(word in lower for word in ("today", "tomorrow", "yesterday", "next", "this")):
            return True
        return False

    def _has_time_evidence() -> bool:
        return bool(re.search(r"\b\d{1,2}(?::\d{2})?\s*(am|pm)?\b", lower))

    def _has_currency_evidence(val: Any) -> bool:
        if not isinstance(val, str):
            return False
        code = val.strip().upper()
        symbols = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "THB": "฿"}
        if code in symbols and symbols[code] in message:
            return True
        return bool(re.search(rf"\b{re.escape(code)}\b", lower))

    def _has_project_code() -> bool:
        return bool(re.search(r"\b[A-Za-z]{2,10}-\d{1,6}\b", message or ""))

    def _has_role_evidence(val: Any) -> bool:
        if not isinstance(val, str):
            return False
        return any(word in lower for word in ("read", "write", "admin", "viewer", "editor", "owner"))

    def _has_resource_type(val: Any) -> bool:
        if not isinstance(val, str):
            return False
        return any(word in lower for word in (val.lower(), "room", "desk", "equipment", "parking", "spot"))

    cleaned = dict(fields)
    if req_enum == RequestType.EXPENSE:
        if not _num_in_text(cleaned.get("amount")):
            cleaned["amount"] = None
        if not _has_currency_evidence(cleaned.get("currency")):
            cleaned["currency"] = None
        if not _has_date_evidence():
            cleaned["date"] = None
        if cleaned.get("category") and not _has_substring(cleaned.get("category")):
            cleaned["category"] = None
        if cleaned.get("project_code") and not _has_project_code():
            cleaned["project_code"] = None
    elif req_enum == RequestType.LEAVE:
        if cleaned.get("leave_type") and not _has_substring(cleaned.get("leave_type")):
            cleaned["leave_type"] = None
        if not _has_date_evidence():
            cleaned["start_date"] = None
            cleaned["end_date"] = None
        if cleaned.get("reason") and not _has_substring(cleaned.get("reason")):
            cleaned["reason"] = None
    elif req_enum == RequestType.TRAVEL:
        if (
            cleaned.get("origin")
            and str(cleaned.get("origin")).strip().lower() != "company"
            and not _has_substring(cleaned.get("origin"))
        ):
            cleaned["origin"] = None
        if cleaned.get("destination") and not _has_substring(cleaned.get("destination")):
            cleaned["destination"] = None
        if not _has_date_evidence():
            cleaned["departure_date"] = None
            cleaned["return_date"] = None
        if cleaned.get("class") and not _has_substring(cleaned.get("class")):
            cleaned["class"] = None
    elif req_enum == RequestType.ACCESS:
        if cleaned.get("resource") and not _has_substring(cleaned.get("resource")):
            cleaned["resource"] = None
        if cleaned.get("requested_role") and not _has_role_evidence(cleaned.get("requested_role")):
            cleaned["requested_role"] = None
        if cleaned.get("justification") and not _has_substring(cleaned.get("justification")):
            cleaned["justification"] = None
        if not _has_date_evidence():
            cleaned["needed_by_date"] = None
    elif req_enum == RequestType.TICKET:
        if cleaned.get("subtype") and not _has_substring(cleaned.get("subtype")):
            cleaned["subtype"] = None
        if cleaned.get("description") and not _has_substring(cleaned.get("description")):
            cleaned["description"] = None
        if cleaned.get("location") and not _has_substring(cleaned.get("location")):
            cleaned["location"] = None
        if cleaned.get("entity") and not _has_substring(cleaned.get("entity")):
            cleaned["entity"] = None
        if not _has_date_evidence():
            cleaned["incident_date"] = None
    elif req_enum == RequestType.WORKSPACE_BOOKING:
        if cleaned.get("resource_type") and not _has_resource_type(cleaned.get("resource_type")):
            cleaned["resource_type"] = None
        if cleaned.get("resource_name") and not _has_substring(cleaned.get("resource_name")):
            cleaned["resource_name"] = None
        if not _has_time_evidence():
            cleaned["start_time"] = None
            cleaned["end_time"] = None
    return cleaned


def _looks_like_workspace_value(field: str, value: str) -> bool:
    """
    Basic hygiene to avoid the model echoing the whole sentence into a field.
    """
    text = value.strip()
    if not text or len(text) > 120:
        return False
    if field == "resource_name":
        # allow alphanumerics/space/#+- and short names
        if not bool(re.match(r"^[A-Za-z0-9 #+-]{1,40}$", text)):
            return False
        if _is_generic_workspace_resource_name(text):
            return False
        return True
    if field in {"start_time", "end_time"}:
        # must contain a digit to be considered a time stamp
        return any(ch.isdigit() for ch in text)
    return True


def _is_generic_workspace_resource_name(value: str) -> bool:
    text = re.sub(r"\s+", " ", (value or "").strip().lower())
    if not text:
        return True
    generic_exact = {
        "room",
        "meeting room",
        "conference room",
        "boardroom",
        "desk",
        "hot desk",
        "workstation",
        "seat",
        "equipment",
        "projector",
        "monitor",
        "laptop",
        "whiteboard",
        "parking",
        "parking spot",
        "parking space",
        "garage",
    }
    if text in generic_exact:
        return True
    # Generic "<type> <number>" variants are acceptable (e.g., room 12, desk a3, parking b2).
    if re.match(r"^(room|desk|parking(?: spot| space)?|equipment)\s+[a-z0-9-]+$", text):
        return False
    # Reject generic phrases ending in resource type without specific identity.
    if re.match(r"^(meeting|conference|board|hot)\s+(room|desk)$", text):
        return True
    return False


def _merge_fields(*dicts: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for d in dicts:
        for k, v in (d or {}).items():
            if v is not None and _is_missing(merged.get(k)):
                merged[k] = v
    return merged


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
        required = ["resource", "requested_role", "justification", "needed_by_date"]
    elif req_enum == RequestType.TICKET:
        required = ["subtype", "description", "location", "entity", "incident_date"]
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


def _to_iso_date(value: Any) -> str | None:
    """
    Convert a human-entered date to ISO (YYYY-MM-DD).
    Accepts day-first input such as DD/MM/YYYY.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    try:
        dt = dateparser.parse(text, dayfirst=True, yearfirst=False, fuzzy=True)
    except Exception:
        return None
    if not dt:
        return None
    return dt.date().isoformat()


def _travel_retry_prompt() -> str:
    return (
        "You extract travel request fields with high precision. "
        "Return only JSON: {\"request_type\":\"travel\",\"fields\":{...}}. "
        "Do not include any extra text. "
        "Fields must be: origin, destination, departure_date, return_date, preferred_departure_time, preferred_return_time, class. "
        "Rules: "
        "1) If user says whole day or same day with one date, set departure_date and return_date to that same date. "
        "2) Parse 'starting from X to Y' as preferred_departure_time=X and preferred_return_time=Y. "
        "3) If origin is not provided, set origin to null (downstream default will be applied). "
        "4) Destination should be a place only (e.g., 'customer company', not 'travel to customer company whole day'). "
        "5) Use ISO dates YYYY-MM-DD for departure_date/return_date. "
        "Examples:\n"
        "Input: I want to reserve a car to travel to customer company whole day on 18/May/2026 starting from 7:00 a.m. to 5:00 p.m.\n"
        "Output: {\"request_type\":\"travel\",\"fields\":{\"origin\":null,\"destination\":\"customer company\",\"departure_date\":\"2026-05-18\",\"return_date\":\"2026-05-18\",\"preferred_departure_time\":\"7:00 a.m.\",\"preferred_return_time\":\"5:00 p.m.\",\"class\":null}}\n"
        "Input: Book travel from Dubai to Bangkok on 10 Jul 2026 at 9:00 AM and return on 15 Jul 2026 at 7:00 PM in economy class\n"
        "Output: {\"request_type\":\"travel\",\"fields\":{\"origin\":\"Dubai\",\"destination\":\"Bangkok\",\"departure_date\":\"2026-07-10\",\"return_date\":\"2026-07-15\",\"preferred_departure_time\":\"9:00 AM\",\"preferred_return_time\":\"7:00 PM\",\"class\":\"economy\"}}"
    )


async def _extract_travel_fields_with_retry(message: str, seed_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(seed_fields or {})
    required = ("destination", "departure_date", "return_date")
    if all(merged.get(key) is not None for key in required):
        return merged

    payload = await call_llm_json(_travel_retry_prompt(), message, max_tokens=384)
    if not isinstance(payload, dict):
        return merged
    declared = payload.get("request_type")
    if declared and declared != RequestType.TRAVEL.value:
        return merged
    retry_fields = _filter_fields_by_evidence(RequestType.TRAVEL, payload.get("fields", {}), message)
    return _merge_fields(merged, retry_fields)


def _heuristic_classify_request(domain: str, message: str) -> tuple[RequestType | None, dict[str, Any]]:
    """
    Deterministic fallback when LLM classification is unavailable/invalid.
    Keeps flows interactive by entering the correct request type so the
    next-question prompts can collect missing fields.
    """
    lower = (message or "").lower()

    if domain == "ops":
        travel_words = (
            "travel",
            "trip",
            "flight",
            "hotel",
            "book travel",
            "itinerary",
            "customer company",
            "client site",
            "return back",
            "whole day",
        )
        expense_words = ("expense", "receipt", "reimburse", "taxi", "meal", "hotel bill")
        if any(word in lower for word in travel_words):
            return RequestType.TRAVEL, {}
        if any(word in lower for word in expense_words):
            return RequestType.EXPENSE, {}

    if domain == "hr":
        if any(word in lower for word in ("leave", "pto", "vacation", "holiday", "sick")):
            return RequestType.LEAVE, {}

    if domain == "it":
        if any(word in lower for word in ("access", "permission", "repo access", "grant")):
            return RequestType.ACCESS, {}
        if any(word in lower for word in ("ticket", "vpn", "wifi", "password", "laptop", "issue")):
            return RequestType.TICKET, {}

    if domain == "workspace":
        if any(word in lower for word in ("book", "reserve", "room", "desk", "equipment", "parking")):
            return RequestType.WORKSPACE_BOOKING, {}

    return None, {}


def _normalize_amount(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        v = float(value)
        return v if v > 0 else None
    if not isinstance(value, str):
        return None
    cleaned = value.replace(",", "")
    matches = list(re.finditer(r"\d+(?:\.\d+)?", cleaned))
    if not matches:
        return None
    best: float | None = None
    best_score = -10
    for m in matches:
        num = float(m.group(0))
        score = 0
        context = cleaned[max(0, m.start() - 12): min(len(cleaned), m.end() + 12)].lower()
        if any(token in context for token in ("$", "usd", "baht", "thb", "eur", "gbp", "jpy", "cost", "total", "amount")):
            score += 2
        if num.is_integer() and 1900 <= int(num) <= 2100:
            score -= 1
        if score > best_score or (score == best_score and (best is None or num > best)):
            best_score = score
            best = num
    return best


def _normalize_currency(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None
    if "$" in text:
        return "USD"
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
        if re.search(rf"\b{re.escape(word)}s?\b", text):
            return code
    direct = re.search(r"\b([a-z]{3})\b", text)
    if direct:
        return direct.group(1).upper()
    return None


def _normalize_requested_role(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if re.search(r"\b(read|view|viewer)\b", text):
        return "viewer"
    if re.search(r"\b(write|edit|editor)\b", text):
        return "editor"
    if re.search(r"\badmin\b", text):
        return "admin"
    if re.search(r"\bowner\b", text):
        return "owner"
    return None


def _normalize_ticket_subtype(value: Any) -> str | None:
    text = str(value).strip().lower() if value is not None else ""
    if re.search(r"\bfacilit(?:y|ies)\b", text):
        return "facilities"
    if re.search(r"\bit\b", text):
        return "it"
    return None


def _normalize_resource_type(value: Any) -> str | None:
    text = str(value).strip().lower() if value is not None else ""
    for v in ("room", "desk", "equipment", "parking"):
        if v in text:
            return v
    return None
