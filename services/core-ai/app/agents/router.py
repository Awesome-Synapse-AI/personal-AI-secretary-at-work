from app.llm_client import call_llm_json
from app.state import ChatState

DOMAIN_VALUES = {"workspace", "hr", "ops", "it", "doc_qa", "generic"}
SENSITIVITY_VALUES = {"normal", "hr_personal", "salary", "access"}

LLM_SYSTEM_PROMPT = (
    "You are a classifier for an internal employee assistant. "
    "Return only JSON with keys domain and sensitivity. "
    "Domain must be one of: workspace, hr, ops, it, doc_qa, generic. "
    "Sensitivity must be one of: normal, hr_personal, salary, access. "
    "If unsure, use generic and normal."
)


def _parse_llm_output(text: str) -> tuple[str, str] | None:
    domain = text.get("domain") if isinstance(text, dict) else None
    sensitivity = text.get("sensitivity") if isinstance(text, dict) else None
    if domain not in DOMAIN_VALUES or sensitivity not in SENSITIVITY_VALUES:
        return None
    return domain, sensitivity


async def _classify_with_llm(message: str) -> tuple[str, str] | None:
    payload = await call_llm_json(LLM_SYSTEM_PROMPT, message, max_tokens=64)
    if not payload:
        return None
    return _parse_llm_output(payload)


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
        message = state.get("message", "")
        llm_result = await _classify_with_llm(message)
        if llm_result:
            state["domain"], state["sensitivity"] = llm_result
            _add_event(
                state,
                "router_classified_llm",
                {"domain": state["domain"], "sensitivity": state["sensitivity"]},
            )
        else:
            state["domain"] = "generic"
            state["sensitivity"] = "normal"
            _add_event(state, "router_classified_default", {})

    _add_event(state, "agent_finished", {"agent": "RouterAgent"})
    return state
