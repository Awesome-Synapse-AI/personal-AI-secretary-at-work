import structlog
from langsmith import traceable

from app.llm_client import call_llm_json
from app.state import ChatState

logger = structlog.get_logger("router_agent")

DOMAIN_VALUES = {"workspace", "hr", "ops", "it", "doc_qa", "generic"}
SENSITIVITY_VALUES = {"normal", "hr_personal", "salary", "access"}

LLM_SYSTEM_PROMPT = (
    "You are a classifier for an internal employee assistant. "
    "Return only a single JSON object with keys domain and sensitivity. "
    "Do not include reasoning, code fences, or any other text. "
    "Domain must be one of: workspace, hr, ops, it, doc_qa, generic. "
    "Sensitivity must be one of: normal, hr_personal, salary, access. "
    "If unsure, use generic and normal. "
    "Examples:\n"
    "HR:\n"
    "Input: I need sick leave tomorrow -> {\"domain\":\"hr\",\"sensitivity\":\"hr_personal\"}\n"
    "Input: Request annual leave 2026-03-10 to 2026-03-12 -> {\"domain\":\"hr\",\"sensitivity\":\"normal\"}\n"
    "Input: How many vacation days do I have left? -> {\"domain\":\"hr\",\"sensitivity\":\"normal\"}\n"
    "Input: Unpaid leave next week for personal reasons -> {\"domain\":\"hr\",\"sensitivity\":\"hr_personal\"}\n"
    "Input: Update my leave request -> {\"domain\":\"hr\",\"sensitivity\":\"normal\"}\n"
    "OPS:\n"
    "Input: I want to send request for expense -> {\"domain\":\"ops\",\"sensitivity\":\"normal\"}\n"
    "Input: Reimburse $45 taxi from yesterday -> {\"domain\":\"ops\",\"sensitivity\":\"normal\"}\n"
    "Input: Book a flight from NYC to LAX -> {\"domain\":\"ops\",\"sensitivity\":\"normal\"}\n"
    "Input: Submit a travel request for next month -> {\"domain\":\"ops\",\"sensitivity\":\"normal\"}\n"
    "Input: Hotel expense for 2026-02-20 -> {\"domain\":\"ops\",\"sensitivity\":\"normal\"}\n"
    "IT:\n"
    "Input: VPN keeps dropping -> {\"domain\":\"it\",\"sensitivity\":\"access\"}\n"
    "Input: I need access to repo analytics -> {\"domain\":\"it\",\"sensitivity\":\"access\"}\n"
    "Input: Password reset required -> {\"domain\":\"it\",\"sensitivity\":\"access\"}\n"
    "Input: File an IT ticket for laptop issue -> {\"domain\":\"it\",\"sensitivity\":\"access\"}\n"
    "Input: Grant me admin access to Jira -> {\"domain\":\"it\",\"sensitivity\":\"access\"}\n"
    "Workspace:\n"
    "Input: Book a room for 2pm -> {\"domain\":\"workspace\",\"sensitivity\":\"normal\"}\n"
    "Input: Reserve a hot desk tomorrow -> {\"domain\":\"workspace\",\"sensitivity\":\"normal\"}\n"
    "Input: Book parking spot B2 -> {\"domain\":\"workspace\",\"sensitivity\":\"normal\"}\n"
    "Input: Reserve a projector 1-2pm -> {\"domain\":\"workspace\",\"sensitivity\":\"normal\"}\n"
    "Input: Meeting room unavailable -> {\"domain\":\"workspace\",\"sensitivity\":\"normal\"}\n"
    "Doc_QA:\n"
    "Input: Search the handbook for travel policy -> {\"domain\":\"doc_qa\",\"sensitivity\":\"normal\"}\n"
    "Input: Summarize the onboarding PDF -> {\"domain\":\"doc_qa\",\"sensitivity\":\"normal\"}\n"
    "Input: What is the per diem limit? -> {\"domain\":\"doc_qa\",\"sensitivity\":\"normal\"}\n"
    "Input: Find my uploaded document -> {\"domain\":\"doc_qa\",\"sensitivity\":\"normal\"}\n"
    "Input: Upload a policy document -> {\"domain\":\"doc_qa\",\"sensitivity\":\"normal\"}\n"
    "Input: What are the core working hours? -> {\"domain\":\"doc_qa\",\"sensitivity\":\"normal\"}\n"
    "Input: What is our harassment policy? -> {\"domain\":\"doc_qa\",\"sensitivity\":\"normal\"}\n"
    "Input: Do we require MFA or VPN on public Wi‑Fi? -> {\"domain\":\"doc_qa\",\"sensitivity\":\"access\"}\n"
    "Input: What is the mileage reimbursement rate? -> {\"domain\":\"doc_qa\",\"sensitivity\":\"normal\"}\n"
    "Input: Are personal devices allowed (BYOD)? -> {\"domain\":\"doc_qa\",\"sensitivity\":\"access\"}\n"
    "Input: How do I approve domestic travel? -> {\"domain\":\"doc_qa\",\"sensitivity\":\"normal\"}\n"
    "Generic:\n"
    "Input: Hello -> {\"domain\":\"generic\",\"sensitivity\":\"normal\"}\n"
    "Input: What can you do? -> {\"domain\":\"generic\",\"sensitivity\":\"normal\"}\n"
    "Input: Thanks -> {\"domain\":\"generic\",\"sensitivity\":\"normal\"}\n"
    "Input: Tell me a joke -> {\"domain\":\"generic\",\"sensitivity\":\"normal\"}\n"
    "Input: How are you? -> {\"domain\":\"generic\",\"sensitivity\":\"normal\"}\n"
)

DOC_SCOPE_PROMPT = (
    "You decide which policy collection a question belongs to. "
    "Return only JSON with key doc_scope. "
    "Choices: policy_hr, policy_it, policy_travel_expense. "
    "Use policy_hr for HR handbook topics (leave, core hours, conduct, harassment, benefits, working hours). "
    "Use policy_it for IT/AUP topics (MFA, VPN, passwords, BYOD, devices, data handling, acceptable use). "
    "Use policy_travel_expense for travel/expense policy (per diem, flights, hotels, mileage, approvals, reimbursements). "
    "HR policy highlights: core hours 10:00-15:00, leave types (annual/sick/business/wedding/bereavement), anti-harassment. "
    "IT policy highlights: MFA required, VPN on public Wi-Fi, BYOD allowed with safeguards. "
    "Travel & expense highlights: domestic travel approval flow, per diem for meals/incidental, mileage reimbursement. "
    "If unsure, choose policy_travel_expense. "
    "Examples:\n"
    "Input: What is the per diem for Singapore? -> {\"doc_scope\":\"policy_travel_expense\"}\n"
    "Input: Do we need manager approval for domestic travel? -> {\"doc_scope\":\"policy_travel_expense\"}\n"
    "Input: Are personal devices allowed? -> {\"doc_scope\":\"policy_it\"}\n"
    "Input: What are core working hours? -> {\"doc_scope\":\"policy_hr\"}\n"
    "Input: What is the harassment reporting process? -> {\"doc_scope\":\"policy_hr\"}\n"
    "Input: Do we require VPN on public Wi-Fi? -> {\"doc_scope\":\"policy_it\"}\n"
)


def _parse_llm_output(text: str) -> tuple[str, str] | None:
    domain = text.get("domain") if isinstance(text, dict) else None
    sensitivity = text.get("sensitivity") if isinstance(text, dict) else None
    if domain not in DOMAIN_VALUES or sensitivity not in SENSITIVITY_VALUES:
        return None
    return domain, sensitivity


def _parse_doc_scope_output(text: str) -> str | None:
    if isinstance(text, dict):
        scope = text.get("doc_scope")
        if scope in {"policy_hr", "policy_it", "policy_travel_expense"}:
            return scope
    return None




def _heuristic_route(message: str) -> tuple[str, str]:
    lower = message.lower()
    if ("approve" in lower or "approval" in lower) and any(word in lower for word in ["travel", "expense", "per diem", "meal", "lodging"]):
        return "doc_qa", "normal"
    if any(word in lower for word in ["leave", "sick", "vacation", "holiday", "pto"]):
        return "hr", "normal"
    if any(word in lower for word in ["expense", "receipt", "travel", "flight", "hotel", "per diem", "mileage"]):
        return "ops", "normal"
    if any(word in lower for word in ["access", "password", "login", "ticket", "it", "vpn", "mfa"]):
        return "it", "access"
    if any(word in lower for word in ["policy", "document", "pdf", "handbook", "guide", "search docs", "upload", "per diem", "core hours", "harassment", "mileage", "acceptable use", "byod"]):
        return "doc_qa", "normal"
    return "generic", "normal"


def _override_domain(message: str, domain: str) -> str:
    """
    Override LLM routing when high-confidence keywords appear.
    """
    lower = message.lower()
    # High-confidence policy keywords should be routed to doc_qa regardless of other intents.
    policy_scope = _policy_scope_from_message(lower)
    if policy_scope:
        return "doc_qa"
    if ("approve" in lower or "approval" in lower) and any(word in lower for word in ["travel", "expense", "per diem", "meal", "lodging"]):
        return "doc_qa"
    if any(word in lower for word in ["expense", "receipt", "reimburse", "reimbursement", "claim", "travel", "flight", "hotel", "per diem", "mileage"]):
        return "ops"
    if any(word in lower for word in ["leave", "sick", "vacation", "holiday", "pto", "core hours", "working hours"]):
        return "hr"
    if any(word in lower for word in ["access", "password", "login", "vpn", "mfa", "ticket", "it", "byod", "acceptable use", "encryption"]):
        return "it"
    if any(word in lower for word in ["policy", "document", "pdf", "handbook", "guide", "search docs", "upload", "per diem", "harassment", "discrimination", "mileage", "core hours", "acceptable use"]):
        return "doc_qa"
    return domain


def _policy_scope_from_message(message_lower: str) -> str | None:
    """
    Lightweight heuristic to detect policy questions and map them to a Qdrant collection scope.
    Returns one of: policy_hr, policy_it, policy_travel_expense, or None.
    """
    has_policy_word = any(
        word in message_lower
        for word in [
            "policy",
            "guideline",
            "rule",
            "regulation",
            "handbook",
            "per diem",
            "allowance",
            "reimbursement",
            "claim limit",
            "expense limit",
            "core hours",
            "working hours",
            "code of conduct",
            "harassment",
            "acceptable use",
            "byod",
            "mfa",
            "vpn",
            "password policy",
            "data classification",
            "mileage",
            "approve",
            "approval",
            "workflow",
            "process",
        ]
    )
    if not has_policy_word and "per diem" not in message_lower:
        return None

    if any(
        word in message_lower
        for word in ["travel", "expense", "reimburse", "per diem", "hotel", "flight", "meal", "taxi", "mileage", "airfare", "lodging", "approval"]
    ):
        return "policy_travel_expense"
    if any(
        word in message_lower
        for word in [
            "vpn",
            "password",
            "mfa",
            "2fa",
            "device",
            "laptop",
            "security",
            "infosec",
            "acceptable use",
            "access",
            "data",
            "usb",
            "byod",
            "encryption",
            "patching",
            "password reset",
            "cloud service",
            "saas",
        ]
    ):
        return "policy_it"
    if any(
        word in message_lower
        for word in [
            "leave",
            "vacation",
            "sick",
            "pto",
            "benefit",
            "overtime",
            "working hours",
            "core hours",
            "holiday",
            "probation",
            "termination",
            "notice period",
            "grievance",
            "harassment",
            "discrimination",
            "code of conduct",
        ]
    ):
        return "policy_hr"
    return "policy_travel_expense"  # default policy bucket when unsure but clearly policy-related


@traceable(name="router_llm_classify", run_type="chain")
async def _classify_with_llm(message: str) -> tuple[str, str] | None:
    payload = await call_llm_json(LLM_SYSTEM_PROMPT, message, max_tokens=64)
    if not payload:
        logger.warning("router_llm_empty_payload")
        return None
    logger.info("router_llm_payload", payload=payload)
    return _parse_llm_output(payload)


@traceable(name="router_doc_scope_llm", run_type="chain")
async def _doc_scope_with_llm(message: str) -> str | None:
    payload = await call_llm_json(DOC_SCOPE_PROMPT, message, max_tokens=32)
    if not payload:
        logger.warning("router_doc_scope_empty_payload")
        return None
    logger.info("router_doc_scope_payload", payload=payload)
    return _parse_doc_scope_output(payload)




def _add_event(state: ChatState, event_type: str, data: dict | None = None) -> None:
    state.setdefault("events", []).append({"type": event_type, "data": data or {}})


@traceable(name="router_node", run_type="chain")
async def router_node(state: ChatState) -> ChatState:
    _add_event(state, "agent_started", {"agent": "RouterAgent"})

    pending = state.get("pending_request")
    if pending:
        state["domain"] = pending.get("domain", "generic")
        state["sensitivity"] = pending.get("sensitivity", "normal")
        _add_event(state, "router_pending", {"domain": state["domain"]})
    else:
        message = state.get("message", "")
        llm_result = await _classify_with_llm(message)
        if llm_result:
            state["domain"], state["sensitivity"] = llm_result
            state["domain"] = _override_domain(message, state["domain"])
            _add_event(
                state,
                "router_classified_llm",
                {"domain": state["domain"], "sensitivity": state["sensitivity"]},
            )
        else:
            domain, sensitivity = _heuristic_route(message)
            state["domain"] = domain
            state["sensitivity"] = sensitivity
            _add_event(
                state,
                "router_classified_default",
                {"reason": "llm_no_class", "domain": domain, "sensitivity": sensitivity},
            )

    # Detect policy questions and force doc search routing.
    policy_scope = _policy_scope_from_message(state.get("message", "").lower())
    if policy_scope:
        state["domain"] = "doc_qa"
        state["doc_scope"] = policy_scope
        _add_event(
            state,
            "router_policy_detected",
            {"scope": policy_scope},
        )
    elif state.get("domain") == "doc_qa" and not state.get("doc_scope"):
        message_text = state.get("message", "")
        llm_scope = await _doc_scope_with_llm(message_text)
        if llm_scope:
            state["doc_scope"] = llm_scope
            _add_event(state, "router_doc_scope_llm", {"scope": llm_scope})
        else:
            inferred_scope = _policy_scope_from_message(message_text.lower())
            if inferred_scope:
                state["doc_scope"] = inferred_scope
                _add_event(state, "router_policy_detected", {"scope": inferred_scope})
            else:
                state["doc_scope"] = "user_docs"

    _add_event(state, "agent_finished", {"agent": "RouterAgent"})
    return state
