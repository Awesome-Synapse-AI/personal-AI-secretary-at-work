import uuid
from typing import Any

import structlog
from langsmith import traceable

from app.agents.langgraph_flow import graph
from app.config import settings
from app.memory.session_store import SessionStore
from app.schemas.chat import UserContext
from app.state import ChatState

logger = structlog.get_logger("chat_service")


@traceable(name="handle_chat", run_type="chain")
async def handle_chat(
    session_store: SessionStore,
    message: str,
    session_id: str | None,
    user: UserContext,
    tenant_id: str | None,
) -> dict[str, Any]:
    session_id = session_id or str(uuid.uuid4())
    tenant_id = tenant_id or settings.default_tenant_id

    pending_request = await session_store.get_pending_request(tenant_id, session_id)

    state: ChatState = {
        "session_id": session_id,
        "tenant_id": tenant_id,
        "message": message,
        "user": user.model_dump(),
        "pending_request": pending_request,
        "events": [],
        "actions": [],
    }

    result: ChatState = await graph.ainvoke(state)

    if result.get("pending_request"):
        await session_store.set_pending_request(tenant_id, session_id, result["pending_request"])
    else:
        await session_store.clear_pending_request(tenant_id, session_id)

    await session_store.append_message(tenant_id, session_id, "user", message)
    await session_store.append_message(tenant_id, session_id, "assistant", result.get("response", ""))

    logger.info(
        "chat_handled",
        session_id=session_id,
        tenant_id=tenant_id,
        has_pending=bool(result.get("pending_request")),
        actions=len(result.get("actions", [])),
    )

    return {
        "session_id": session_id,
        "message": result.get("response", ""),
        "actions": result.get("actions", []),
        "pending_request": result.get("pending_request"),
        "events": result.get("events", []),
    }
