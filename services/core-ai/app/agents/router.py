import structlog
from langsmith import traceable

from app.agents.clarification import _as_request_type, classify_request
from app.llm_client import call_llm_json
from app.state import ChatState

logger = structlog.get_logger("router_agent")

MAIN_ROUTE_VALUES = {"request", "doc_qa", "generic"}
REQUEST_SUBMISSION_DOMAINS = {"workspace", "hr", "ops", "it"}
SENSITIVITY_VALUES = {"normal", "hr_personal", "salary", "access"}

MAIN_ROUTE_PROMPT = (
    "You are a stage-1 router for an internal employee assistant. "
    "Return only a single JSON object with keys main_route and sensitivity. "
    "Do not include reasoning, code fences, or any other text. "
    "main_route must be one of: request, doc_qa, generic. "
    "Sensitivity must be one of: normal, hr_personal, salary, access. "
    "request means user is asking to submit, change, or execute an operational action. "
    "doc_qa means user asks for information/explanations/rules from documents and policy manuals. "
    "generic means casual/help/greetings/other non-request text. "
    "Coverage note: the document corpus includes HR policy and Travel & Expense policy (and related company policies). "
    "Route to doc_qa for policy/manual topics such as compensation and benefits, working hours/core hours, "
    "leave rules and conduct, reimbursable expenses, per diem, travel approval workflow, receipts, and limits. "
    "Route to doc_qa even when the user does not mention the word 'policy' if the intent is to ask for rules or explanations. "
    "Use request only when the user wants the assistant to take action now (submit/file/book/create/update/cancel a request). "
    "If unsure, use generic and normal. "
    "Examples:\n"
    "Input: I need sick leave tomorrow -> {\"main_route\":\"request\",\"sensitivity\":\"hr_personal\"}\n"
    "Input: Reimburse taxi fare from yesterday -> {\"main_route\":\"request\",\"sensitivity\":\"normal\"}\n"
    "Input: Book a meeting room at 2pm -> {\"main_route\":\"request\",\"sensitivity\":\"normal\"}\n"
    "Input: Explain compensation and benefits -> {\"main_route\":\"doc_qa\",\"sensitivity\":\"normal\"}\n"
    "Input: What are standard work hours and core hours? -> {\"main_route\":\"doc_qa\",\"sensitivity\":\"normal\"}\n"
    "Input: What expenses are reimbursable and what receipts are required? -> {\"main_route\":\"doc_qa\",\"sensitivity\":\"normal\"}\n"
    "Input: What is the domestic travel approval workflow? -> {\"main_route\":\"doc_qa\",\"sensitivity\":\"normal\"}\n"
    "Input: Do we require VPN on public Wi-Fi? -> {\"main_route\":\"doc_qa\",\"sensitivity\":\"access\"}\n"
    "Input: Hello -> {\"main_route\":\"generic\",\"sensitivity\":\"normal\"}\n"
)

REQUEST_DOMAIN_PROMPT = (
    "You are a stage-2 request router for an internal employee assistant. "
    "Return only a single JSON object with key request_domain. "
    "request_domain must be one of: workspace, hr, ops, it. "
    "Do not include reasoning, code fences, or any other text. "
    "Disambiguation: travel/transport requests for client visits or trips belong to ops, "
    "while workspace is only for office resource booking (room/desk/equipment/parking). "
    "Examples:\n"
    "Input: I need sick leave tomorrow -> {\"request_domain\":\"hr\"}\n"
    "Input: Reimburse $45 taxi from yesterday -> {\"request_domain\":\"ops\"}\n"
    "Input: I want to reserve a car to travel to customer company tomorrow -> {\"request_domain\":\"ops\"}\n"
    "Input: Arrange transport for client site visit and return by 7pm -> {\"request_domain\":\"ops\"}\n"
    "Input: VPN keeps dropping -> {\"request_domain\":\"it\"}\n"
    "Input: Reserve room A for 3pm -> {\"request_domain\":\"workspace\"}\n"
)

DOC_SCOPE_PROMPT = (
    "You decide which policy collection a question belongs to. "
    "Return only JSON with key doc_scope. "
    "Choices: policy_hr, policy_it, policy_travel_expense. "
    "If unsure, choose policy_travel_expense. "
)


def _parse_main_route_output(payload: dict | None) -> tuple[str, str] | None:
    if not isinstance(payload, dict):
        return None
    main_route = payload.get("main_route")
    sensitivity = payload.get("sensitivity")
    if main_route not in MAIN_ROUTE_VALUES or sensitivity not in SENSITIVITY_VALUES:
        return None
    return main_route, sensitivity


def _parse_request_domain_output(payload: dict | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    request_domain = payload.get("request_domain")
    return request_domain if request_domain in REQUEST_SUBMISSION_DOMAINS else None


def _parse_doc_scope_output(payload: dict | None) -> str | None:
    if isinstance(payload, dict):
        scope = payload.get("doc_scope")
        if scope in {"policy_hr", "policy_it", "policy_travel_expense"}:
            return scope
    return None


@traceable(name="router_main_route_llm", run_type="chain")
async def _classify_main_route_with_llm(message: str) -> tuple[str, str] | None:
    payload = await call_llm_json(MAIN_ROUTE_PROMPT, message, max_tokens=512)
    if not payload:
        logger.warning("router_main_route_empty_payload")
        return None
    logger.info("router_main_route_payload", payload=payload)
    return _parse_main_route_output(payload)


@traceable(name="router_request_domain_llm", run_type="chain")
async def _classify_request_domain_with_llm(message: str) -> str | None:
    payload = await call_llm_json(REQUEST_DOMAIN_PROMPT, message, max_tokens=512)
    if not payload:
        logger.warning("router_request_domain_empty_payload")
        return None
    logger.info("router_request_domain_payload", payload=payload)
    return _parse_request_domain_output(payload)


@traceable(name="router_doc_scope_llm", run_type="chain")
async def _doc_scope_with_llm(message: str) -> str | None:
    payload = await call_llm_json(DOC_SCOPE_PROMPT, message, max_tokens=512)
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
        pending_domain = pending.get("domain", "generic")
        pending_type = _as_request_type(pending.get("type"))

        state["main_route"] = "request" if pending_domain in REQUEST_SUBMISSION_DOMAINS else pending_domain
        state["domain"] = pending_domain
        state["sub_route"] = pending_type.value if pending_type else pending_domain
        state["sub_route_fields"] = dict(pending.get("filled", {})) if isinstance(pending.get("filled"), dict) else {}
        state["sensitivity"] = pending.get("sensitivity", "normal")

        _add_event(
            state,
            "router_pending",
            {
                "main_route": state["main_route"],
                "domain": state["domain"],
                "sub_route": state["sub_route"],
            },
        )
    else:
        message = state.get("message", "")
        state["sub_route_fields"] = {}

        main_route_result = await _classify_main_route_with_llm(message)
        if main_route_result:
            state["main_route"], state["sensitivity"] = main_route_result
            _add_event(
                state,
                "router_main_classified_llm",
                {"main_route": state["main_route"], "sensitivity": state["sensitivity"]},
            )
        else:
            state["main_route"] = "generic"
            state["sensitivity"] = "normal"
            _add_event(
                state,
                "router_main_classified_default",
                {"reason": "llm_no_class", "main_route": "generic", "sensitivity": "normal"},
            )

        if state["main_route"] == "request":
            request_domain = await _classify_request_domain_with_llm(message)
            if request_domain:
                state["domain"] = request_domain
                _add_event(state, "router_request_domain_llm", {"domain": request_domain})
            else:
                state["main_route"] = "generic"
                state["domain"] = "generic"
                _add_event(
                    state,
                    "router_request_domain_default",
                    {"reason": "llm_no_class", "main_route": "generic", "domain": "generic"},
                )
        elif state["main_route"] == "doc_qa":
            state["domain"] = "doc_qa"
        else:
            state["domain"] = "generic"

    # Stage 2 route for request/doc_qa/generic.
    main_route = state.get("main_route", "generic")
    if main_route == "request" and state.get("domain") in REQUEST_SUBMISSION_DOMAINS:
        if not pending:
            request_type, fields = await classify_request(state["domain"], state.get("message", ""))
            if request_type is not None:
                state["sub_route"] = request_type.value
                state["sub_route_fields"] = fields
                _add_event(
                    state,
                    "router_subroute_classified",
                    {"domain": state["domain"], "sub_route": state["sub_route"]},
                )
            else:
                state["sub_route"] = state["domain"]
                _add_event(
                    state,
                    "router_subroute_default",
                    {"domain": state["domain"], "sub_route": state["sub_route"]},
                )
    elif main_route == "doc_qa":
        state["sub_route"] = "doc_qa"
        if not state.get("doc_scope"):
            llm_scope = await _doc_scope_with_llm(state.get("message", ""))
            state["doc_scope"] = llm_scope or "user_docs"
    else:
        state["sub_route"] = "generic"
        state["domain"] = "generic"

    _add_event(state, "agent_finished", {"agent": "RouterAgent"})
    return state
