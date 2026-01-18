from app.state import ChatState


def _add_event(state: ChatState, event_type: str, data: dict | None = None) -> None:
    state.setdefault("events", []).append({"type": event_type, "data": data or {}})


def _has_role(state: ChatState, role: str) -> bool:
    user = state.get("user", {})
    roles = user.get("roles", [])
    return role in roles


async def guardrail_node(state: ChatState) -> ChatState:
    _add_event(state, "agent_started", {"agent": "GuardrailAgent"})

    sensitivity = state.get("sensitivity", "normal")
    if sensitivity == "salary" and not (
        _has_role(state, "hr_approver") or _has_role(state, "system_admin")
    ):
        state["response"] = "I cannot share salary details. Please contact HR."
        state["actions"] = []
        _add_event(state, "guardrail_blocked", {"reason": "salary_access"})

    _add_event(state, "agent_finished", {"agent": "GuardrailAgent"})
    return state
