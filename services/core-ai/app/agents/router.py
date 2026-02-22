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
    "Generic:\n"
    "Input: Hello -> {\"domain\":\"generic\",\"sensitivity\":\"normal\"}\n"
    "Input: What can you do? -> {\"domain\":\"generic\",\"sensitivity\":\"normal\"}\n"
    "Input: Thanks -> {\"domain\":\"generic\",\"sensitivity\":\"normal\"}\n"
    "Input: Tell me a joke -> {\"domain\":\"generic\",\"sensitivity\":\"normal\"}\n"
    "Input: How are you? -> {\"domain\":\"generic\",\"sensitivity\":\"normal\"}\n"
)


def _parse_llm_output(text: str) -> tuple[str, str] | None:
    domain = text.get("domain") if isinstance(text, dict) else None
    sensitivity = text.get("sensitivity") if isinstance(text, dict) else None
    if domain not in DOMAIN_VALUES or sensitivity not in SENSITIVITY_VALUES:
        return None
    return domain, sensitivity


def _heuristic_route(message: str) -> tuple[str, str]:
    lower = message.lower()
    if any(word in lower for word in ["leave", "sick", "vacation", "holiday", "pto"]):
        return "hr", "normal"
    if any(word in lower for word in ["expense", "receipt", "travel", "flight", "hotel"]):
        return "ops", "normal"
    if any(word in lower for word in ["access", "password", "login", "ticket", "it"]):
        return "it", "access"
    if any(word in lower for word in ["policy", "document", "pdf", "handbook", "guide", "search docs", "upload"]):
        return "doc_qa", "normal"
    return "generic", "normal"


def _override_domain(message: str, domain: str) -> str:
    """
    Override LLM routing when high-confidence keywords appear.
    """
    lower = message.lower()
    if any(word in lower for word in ["expense", "receipt", "reimburse", "reimbursement", "claim", "travel", "flight", "hotel"]):
        return "ops"
    if any(word in lower for word in ["leave", "sick", "vacation", "holiday", "pto"]):
        return "hr"
    if any(word in lower for word in ["access", "password", "login", "vpn", "ticket", "it"]):
        return "it"
    if any(word in lower for word in ["policy", "document", "pdf", "handbook", "guide", "search docs", "upload"]):
        return "doc_qa"
    return domain


@traceable(name="router_llm_classify", run_type="chain")
async def _classify_with_llm(message: str) -> tuple[str, str] | None:
    payload = await call_llm_json(LLM_SYSTEM_PROMPT, message, max_tokens=64)
    if not payload:
        logger.warning("router_llm_empty_payload")
        return None
    logger.info("router_llm_payload", payload=payload)
    return _parse_llm_output(payload)


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

    _add_event(state, "agent_finished", {"agent": "RouterAgent"})
    return state
