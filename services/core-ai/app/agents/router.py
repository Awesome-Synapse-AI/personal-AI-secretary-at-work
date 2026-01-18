from app.state import ChatState

DOMAIN_KEYWORDS = {
    "workspace": ["room", "desk", "parking", "equipment", "meeting"],
    "hr": ["leave", "vacation", "sick", "pay", "benefit"],
    "ops": ["expense", "reimburse", "travel", "flight", "hotel"],
    "it": ["vpn", "access", "password", "laptop", "wifi", "ticket"],
    "doc_qa": ["document", "policy", "pdf", "contract"],
}

SENSITIVITY_KEYWORDS = {
    "salary": ["salary", "compensation", "pay"],
    "hr_personal": ["medical", "sick", "leave balance"],
    "access": ["access", "permission", "admin"],
}


def _classify_domain(message: str) -> str:
    lowered = message.lower()
    for domain, keywords in DOMAIN_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return domain
    return "generic"


def _classify_sensitivity(message: str) -> str:
    lowered = message.lower()
    for sensitivity, keywords in SENSITIVITY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return sensitivity
    return "normal"


def _add_event(state: ChatState, event_type: str, data: dict | None = None) -> None:
    state.setdefault("events", []).append({"type": event_type, "data": data or {}})


async def router_node(state: ChatState) -> ChatState:
    _add_event(state, "agent_started", {"agent": "RouterAgent"})

    pending = state.get("pending_request")
    if pending:
        state["domain"] = pending.get("domain", "generic")
        state["sensitivity"] = pending.get("sensitivity", "normal")
        _add_event(state, "router_pending", {"domain": state["domain"]})
    else:
        state["domain"] = _classify_domain(state.get("message", ""))
        state["sensitivity"] = _classify_sensitivity(state.get("message", ""))
        _add_event(
            state,
            "router_classified",
            {"domain": state["domain"], "sensitivity": state["sensitivity"]},
        )

    _add_event(state, "agent_finished", {"agent": "RouterAgent"})
    return state
