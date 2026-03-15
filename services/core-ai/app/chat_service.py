import uuid
from typing import Any

import structlog
from langsmith import traceable
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agents.langgraph_flow import graph
from app.config import settings
from app.llm_client import call_llm_text
from app.memory.session_store import SessionStore
from app.schemas.chat import UserContext
from app.state import ChatState
from app.utils import utcnow

logger = structlog.get_logger("chat_service")


@traceable(name="handle_chat", run_type="chain")
async def handle_chat(
    session_store: SessionStore,
    message: str,
    session_id: str | None,
    user: UserContext,
    tenant_id: str | None,
    mongo_db: AsyncIOMotorDatabase | None = None,
    event_queue: Any | None = None,
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
    if event_queue is not None:
        state["event_queue"] = event_queue

    result: ChatState = await graph.ainvoke(state)

    if result.get("pending_request"):
        await session_store.set_pending_request(tenant_id, session_id, result["pending_request"])
    else:
        await session_store.clear_pending_request(tenant_id, session_id)

    await session_store.append_message(tenant_id, session_id, "user", message)
    await session_store.append_message(tenant_id, session_id, "assistant", result.get("response", ""))

    session_title = None
    if mongo_db is not None:
        session_title = await _persist_chat_to_mongo(
            mongo_db=mongo_db,
            tenant_id=tenant_id,
            session_id=session_id,
            user_message=message,
            assistant_message=result.get("response", ""),
            pending_request=result.get("pending_request"),
            actions=result.get("actions", []),
            events=result.get("events", []),
        )

    logger.info(
        "chat_handled",
        session_id=session_id,
        tenant_id=tenant_id,
        has_pending=bool(result.get("pending_request")),
        actions=len(result.get("actions", [])),
    )

    return {
        "session_id": session_id,
        "session_title": session_title,
        "message": result.get("response", ""),
        "actions": result.get("actions", []),
        "pending_request": result.get("pending_request"),
        "events": result.get("events", []),
    }


async def _persist_chat_to_mongo(
    mongo_db: AsyncIOMotorDatabase,
    tenant_id: str,
    session_id: str,
    user_message: str,
    assistant_message: str,
    pending_request: dict[str, Any] | None,
    actions: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> str | None:
    sessions = mongo_db[settings.mongo_chat_session_collection]
    messages = mongo_db[settings.mongo_chat_message_collection]
    now = utcnow()

    existing = await sessions.find_one({"_id": session_id, "tenant_id": tenant_id}, {"_id": 1, "title": 1})
    default_title = await _generate_session_title(user_message) if not existing else None
    await sessions.update_one(
        {"_id": session_id, "tenant_id": tenant_id},
        {
            "$setOnInsert": {"created_at": now, "title": default_title or "New chat"},
            "$set": {"updated_at": now},
        },
        upsert=True,
    )

    docs = [
        {
            "_id": str(uuid.uuid4()),
            "session_id": session_id,
            "tenant_id": tenant_id,
            "role": "user",
            "content": user_message,
            "created_at": now,
        },
        {
            "_id": str(uuid.uuid4()),
            "session_id": session_id,
            "tenant_id": tenant_id,
            "role": "assistant",
            "content": assistant_message,
            "pending_request": pending_request,
            "actions": actions or [],
            "events": events or [],
            "created_at": now,
        },
    ]
    await messages.insert_many(docs)

    # return the title stored in sessions collection
    session_doc = await sessions.find_one({"_id": session_id, "tenant_id": tenant_id}, {"title": 1})
    return (session_doc or {}).get("title")


def _default_title(text: str) -> str:
    return _fallback_title(text)


async def _generate_session_title(text: str) -> str:
    fallback = _fallback_title(text)
    if not text or not text.strip():
        return fallback
    prompt = (
        "Create a short, human-readable chat session title. "
        "It must summarize the user's intent. "
        "Rules: 5-10 words, title case, no quotes, no trailing punctuation. "
        "Do not copy the full sentence. "
        "Examples:\n"
        "Input: I want to reserve a car to travel to customer company whole day on 18/May/2026\n"
        "Output: Customer Visit Car Booking\n"
        "Input: Please reimburse taxi 1200 THB from 12/03/2026\n"
        "Output: Taxi Reimbursement Request\n"
        "Input: VPN keeps dropping on my laptop\n"
        "Output: VPN Connectivity Issue"
    )
    try:
        raw = await call_llm_text(prompt, text.strip(), max_tokens=24)
    except Exception:
        return fallback
    if not raw:
        return fallback
    return _normalize_session_title(raw, fallback)


def _fallback_title(text: str) -> str:
    if not text:
        return "New chat"
    first_line = text.strip().splitlines()[0]
    # Keep fallback concise and readable, not raw full input.
    tokens = [t for t in first_line.replace("\n", " ").split(" ") if t]
    short = " ".join(tokens[:8]).strip()
    if not short:
        return "New chat"
    return short[:80]


def _normalize_session_title(raw_title: str | None, fallback: str) -> str:
    title = " ".join((raw_title or "").strip().split())
    title = title.strip("\"'` \t\r\n")
    title = title.rstrip(".!?;:")
    if not title:
        title = fallback

    words = [w for w in title.split(" ") if w]
    if len(words) > 10:
        words = words[:10]
    if len(words) < 5:
        fb_words = [w for w in fallback.split(" ") if w]
        for w in fb_words:
            if len(words) >= 5:
                break
            words.append(w)
    if len(words) < 5:
        for w in ["Request", "Summary", "Chat", "Session", "Title"]:
            if len(words) >= 5:
                break
            words.append(w)

    normalized = " ".join(words).strip()
    if not normalized:
        normalized = fallback
    return normalized[:80]
