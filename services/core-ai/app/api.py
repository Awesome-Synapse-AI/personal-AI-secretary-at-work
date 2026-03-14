import os
import asyncio
import uuid
import io
from pathlib import Path
from datetime import date, datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, File, Form, Header
from sqlmodel import Session, select
from sqlalchemy import func
from dateutil import parser as dateparser
import httpx
import numpy as np
import boto3
import pytesseract
from PIL import Image
from llama_index.core import Document as LlamaDocument, VectorStoreIndex
from llama_index.core.node_parser import HierarchicalNodeParser, SentenceSplitter
from llama_index.core.schema import TextNode
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.auth import get_current_user, get_user_from_token, require_roles
from app.audit import record_audit_log
from app.chat_service import handle_chat
from app.db import get_session
from app.config import settings
from app.llm_client import call_llm_text
from app.models import (
    AccessRequest as AccessRequestModel,
    LeaveEntitlement,
    LeaveRequest as LeaveRequestModel,
    Expense as ExpenseModel,
    Ticket as TicketModel,
    TravelRequest as TravelModel,
    Room,
    Desk,
    Equipment,
    ParkingSpot,
    Booking,
    ResourceType,
    BookingRequestInput,
    LeaveRequestInput,
    EntitlementUpsert,
    ExpenseInput,
    ExpenseDecision,
    TravelInput,
    TravelDecision,
    ReceiptInput,
    TicketUpdateInput,
    AccessRequestInput,
    TicketInput,
    AccessStatus,
    RequestedRole,
    TicketStatus,
    TicketType,
    CalendarEvent,
    CalendarEventInput,
    EventSource,
    Document,
    DocumentChunk,
    DocumentSearchInput,
)
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ChatSessionMeta,
    ChatSessionRenameRequest,
    ChatSessionMessagesResponse,
    ChatMessagePayload,
    UserContext,
)
from app.utils import iter_tokens, utcnow

router = APIRouter()


# ---------- helpers ----------

def _current_user_id(user: Optional[UserContext]) -> str:
    return user.sub if user and user.sub else "demo-user"


def _tenant_from_header(tenant_id_header: str | None) -> str:
    return tenant_id_header or settings.default_tenant_id


def _serialize_session_meta(doc: dict) -> ChatSessionMeta:
    updated = doc.get("updated_at")
    ts = int(updated.timestamp() * 1000) if updated else 0
    return ChatSessionMeta(id=str(doc.get("_id")), title=doc.get("title"), updated_at=ts)


def _serialize_chat_message(row: dict) -> ChatMessagePayload:
    created = row.get("created_at")
    return ChatMessagePayload(
        id=str(row.get("_id")),
        role=row.get("role", ""),
        content=row.get("content", ""),
        created_at=int(created.timestamp() * 1000) if created else 0,
        pending_request=row.get("pending_request"),
        actions=row.get("actions"),
        events=row.get("events"),
    )


def _derive_session_title(messages: list[dict]) -> str:
    def _trim(text: str) -> str:
        if not text:
            return "New chat"
        first_line = text.strip().splitlines()[0]
        snippet = first_line[:60].rstrip()
        return f"{snippet}..." if len(first_line) > 60 else (snippet or "New chat")

    for msg in messages:
        role = getattr(msg, "role", None)
        if role is None and isinstance(msg, dict):
            role = msg.get("role")
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        if role == "user" and content:
            return _trim(content)
    if messages:
        first = messages[0]
        content = getattr(first, "content", None)
        if content is None and isinstance(first, dict):
            content = first.get("content")
        return _trim(content)
    return "New chat"


async def _summarize_session_title(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return "New chat"
    prompt = (
        "Create a short, human-readable chat session title that summarizes the user's intent. "
        "Rules: 5-10 words, title case, no quotes, no trailing punctuation. "
        "Do not copy the full sentence.\n"
        "Input: I want to reserve a car to travel to customer company whole day on 18/May/2026\n"
        "Output: Customer Visit Car Booking"
    )
    try:
        title = await call_llm_text(prompt, raw, max_tokens=24)
    except Exception:
        title = None
    fallback = _derive_session_title([{"role": "user", "content": raw}])
    return _normalize_session_title(cleaned=(title or ""), fallback=fallback)


def _normalize_session_title(cleaned: str, fallback: str) -> str:
    title = " ".join(cleaned.split()).strip("\"'` \t\r\n").rstrip(".!?;:")
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
        normalized = fallback or "New chat"
    return normalized[:80]


def _get_entitlement(session: Session, user_id: str, year: int, leave_type: str, month: int | None = None) -> LeaveEntitlement | None:
    return session.exec(
        select(LeaveEntitlement).where(
            LeaveEntitlement.user_id == user_id,
            LeaveEntitlement.year == year,
            LeaveEntitlement.leave_type == leave_type,
            LeaveEntitlement.month == month,
        )
    ).first()


def _default_entitlement_days(leave_type: str, month: int | None) -> float:
    lt = leave_type.lower()
    if lt == "sick":
        return 30.0  # per year
    if lt == "annual":
        return 8.0
    if lt == "business":
        return 7.0
    if lt == "wedding":
        return 3.0
    if lt in {"bravement", "bereavement"}:
        return 5.0
    return 0.0


def _assert_available(session: Session, resource_type: ResourceType, resource_id: int, start: datetime, end: datetime) -> None:
    overlap = session.exec(
        select(Booking).where(
            Booking.resource_type == resource_type,
            Booking.resource_id == resource_id,
            Booking.status.in_(["confirmed", "submitted"]),
            Booking.start_time < end,
            Booking.end_time > start,
        )
    ).first()
    if overlap:
        available = _available_resources(session, resource_type, start, end)
        raise HTTPException(409, {"error": "Time slot is already booked for this resource", "available": available})


def _available_resources(session: Session, resource_type: ResourceType, start: datetime, end: datetime) -> list[dict]:
    if resource_type == ResourceType.ROOM:
        resources = session.exec(select(Room)).all()
    elif resource_type == ResourceType.DESK:
        resources = session.exec(select(Desk)).all()
    elif resource_type == ResourceType.EQUIPMENT:
        resources = session.exec(select(Equipment)).all()
    else:
        resources = session.exec(select(ParkingSpot)).all()

    available: list[dict] = []
    for res in resources:
        conflict = session.exec(
            select(Booking).where(
                Booking.resource_type == resource_type,
                Booking.resource_id == res.id,
                Booking.status.in_(["confirmed", "submitted"]),
                Booking.start_time < end,
                Booking.end_time > start,
            )
        ).first()
        if not conflict:
            available.append({"name": res.name, "id": res.id})
    return available


def _parse_time_range(start_text: str, end_text: str) -> tuple[datetime, datetime]:
    try:
        start_dt = dateparser.parse(start_text, dayfirst=True, yearfirst=False)
        end_dt = dateparser.parse(end_text, dayfirst=True, yearfirst=False)
    except Exception:
        raise HTTPException(400, "Cannot parse provided start/end time. Please use a clear time expression.")
    if not start_dt or not end_dt:
        raise HTTPException(400, "Cannot parse provided start/end time. Please use a clear time expression.")
    if end_dt <= start_dt:
        raise HTTPException(400, "End time must be after start time.")
    return start_dt, end_dt


def _validate_expense(expense: ExpenseInput) -> None:
    if expense.amount <= 0:
        raise HTTPException(400, "Amount must be greater than zero")
    if not expense.currency or len(expense.currency.strip()) != 3:
        raise HTTPException(400, "Currency must be a 3-letter code")
    try:
        _as_date(expense.date)
    except Exception:
        raise HTTPException(400, "Date must be in DD/MM/YYYY format")
    if not expense.category:
        raise HTTPException(400, "Category is required")


def _validate_travel(travel: TravelInput) -> None:
    if not travel.origin or not travel.destination:
        raise HTTPException(400, "Origin and destination are required")
    try:
        dep = _as_date(travel.departure_date)
    except HTTPException as exc:
        raise HTTPException(400, "departure_date must be DD/MM/YYYY") from exc
    ret = None
    if travel.return_date:
        try:
            ret = _as_date(travel.return_date)
        except HTTPException as exc:
            raise HTTPException(400, "return_date must be DD/MM/YYYY") from exc
    if ret and ret < dep:
        raise HTTPException(400, "return_date must be on/after departure_date")
    if travel.preferred_departure_time:
        try:
            dateparser.parse(travel.preferred_departure_time)
        except Exception:
            raise HTTPException(400, "preferred_departure_time is not understood")
    if travel.preferred_return_time:
        try:
            dateparser.parse(travel.preferred_return_time)
        except Exception:
            raise HTTPException(400, "preferred_return_time is not understood")


def _resource_id_by_name(session: Session, resource_type: ResourceType, name: str | None, fallback_id: str | None) -> int:
    if name:
        if resource_type == ResourceType.DESK:
            res = session.exec(select(Desk).where(Desk.name == name)).first()
        elif resource_type == ResourceType.EQUIPMENT:
            res = session.exec(select(Equipment).where(Equipment.name == name)).first()
        elif resource_type == ResourceType.PARKING:
            res = session.exec(select(ParkingSpot).where(ParkingSpot.name == name)).first()
        else:
            res = session.exec(select(Room).where(Room.name == name)).first()
        if not res:
            raise HTTPException(404, "Resource name not found")
        return res.id  # type: ignore[return-value]
    if fallback_id is None:
        raise HTTPException(400, "resource_name is required when id is not provided")
    return int(fallback_id)


def _calc_days(start: str, end: str) -> float:
    s = _as_date(start)
    e = _as_date(end)
    return (e - s).days + 1


def _as_date(value: str) -> date:
    """
    Parse a date string, accepting day-first formats like DD/MM/YYYY as well as ISO.
    Raises HTTPException if parsing fails.
    """
    try:
        return date.fromisoformat(value)
    except Exception:
        try:
            dt = dateparser.parse(value, dayfirst=True, yearfirst=False)
        except Exception:
            raise HTTPException(400, "Date must be in DD/MM/YYYY format")
        if not dt:
            raise HTTPException(400, "Date must be in DD/MM/YYYY format")
        return dt.date()


def _google_calendar_service():
    enabled = os.getenv("GOOGLE_CALENDAR_ENABLED", "").lower() in {"1", "true", "yes"}
    creds_path = os.getenv("GOOGLE_CALENDAR_CREDENTIALS")
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID")
    if not (enabled and creds_path and calendar_id):
        return None, None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except Exception:
        return None, None
    try:
        scopes = ["https://www.googleapis.com/auth/calendar"]
        creds = service_account.Credentials.from_service_account_file(creds_path, scopes=scopes)
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return service, calendar_id
    except Exception:
        return None, None


def _create_event(
    session: Session,
    user_id: str,
    title: str,
    start_time: datetime,
    end_time: datetime,
    source_type: EventSource,
    source_id: int | None,
    status: str = "busy",
) -> CalendarEvent:
    event = CalendarEvent(
        user_id=user_id,
        title=title,
        start_time=start_time,
        end_time=end_time,
        source_type=source_type,
        source_id=source_id,
        status=status,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    service, calendar_id = _google_calendar_service()
    if service and calendar_id:
        body = {
            "summary": title,
            "start": {"dateTime": start_time.isoformat()},
            "end": {"dateTime": end_time.isoformat()},
            "description": f"{source_type.value} request #{source_id}" if source_id else title,
        }
        try:
            created = service.events().insert(calendarId=calendar_id, body=body, sendUpdates="none").execute()
            event.google_event_id = created.get("id")
            session.add(event)
            session.commit()
            session.refresh(event)
        except Exception:
            # Silently ignore Google Calendar failures to avoid blocking core flow
            pass
    return event


# ---------- Document & Policy Search ----------


_embedding_model_cache = None


def _embedding_model():
    from sentence_transformers import SentenceTransformer  # lazy import

    class _FallbackEmbedder:
        def __init__(self, dim: int, normalize: bool = True):
            self.dim = dim
            self.normalize = normalize

        def encode(self, texts, normalize_embeddings=True):
            normalize = self.normalize and normalize_embeddings
            vecs = []
            for text in texts:
                seed = abs(hash(text)) % (2**32)
                rng = np.random.default_rng(seed)
                v = rng.normal(size=self.dim)
                if normalize:
                    norm = np.linalg.norm(v)
                    if norm > 0:
                        v = v / norm
                vecs.append(v)
            return np.stack(vecs)

    global _embedding_model_cache
    if _embedding_model_cache is not None:
        return _embedding_model_cache

    try:
        cache_kwargs = {}
        if settings.huggingface_hub_cache:
            os.environ["HUGGINGFACE_HUB_CACHE"] = settings.huggingface_hub_cache
            cache_kwargs["cache_folder"] = settings.huggingface_hub_cache

        _embedding_model_cache = SentenceTransformer(
            settings.embedding_model_name,
            trust_remote_code=True,
            device=settings.embedding_device,
            use_auth_token=settings.hf_token,
            **cache_kwargs,
        )
    except Exception as exc:  # pragma: no cover - best-effort fallback
        print(f"_embedding_model: failed to load {settings.embedding_model_name}, using hashed fallback: {exc}", flush=True)
        _embedding_model_cache = _FallbackEmbedder(settings.embedding_vector_size, settings.embedding_normalize)
    return _embedding_model_cache


async def _embed_texts(texts: List[str]) -> list[list[float]]:
    if not texts:
        return []
    # run synchronous encoding in thread to avoid blocking event loop
    loop = asyncio.get_running_loop()
    model = _embedding_model()
    return await loop.run_in_executor(
        None,
        lambda: model.encode(
            texts,
            normalize_embeddings=settings.embedding_normalize,
        ).tolist(),
    )


def _serialize_vec(vec: list[float]) -> bytes:
    return np.array(vec, dtype=np.float32).tobytes()


def _deserialize_vec(blob: bytes) -> list[float]:
    return np.frombuffer(blob, dtype=np.float32).tolist()


def _hf_embed_model():
    cache = settings.huggingface_hub_cache
    if cache:
        os.environ["HUGGINGFACE_HUB_CACHE"] = cache
        os.environ["TRANSFORMERS_CACHE"] = cache
        os.environ["HF_HOME"] = cache
    return HuggingFaceEmbedding(
        model_name=settings.embedding_model_name,
        cache_folder=cache,
        device=settings.embedding_device,
    )


def _qdrant_store(collection: str | None, vector_size: int | None = None):
    client = _qdrant_client()
    if not client or not collection:
        return None
    kwargs = {}
    if vector_size:
        kwargs["vector_size"] = vector_size
    return QdrantVectorStore(client=client, collection_name=collection, prefer_grpc=False, **kwargs)


def _qdrant_client():
    host = settings.qdrant_host
    if not host:
        return None
    from qdrant_client import QdrantClient

    return QdrantClient(
        host=host,
        port=int(settings.qdrant_port),
        api_key=settings.qdrant_api_key,
        timeout=5,
        check_compatibility=False,
    )


def _ensure_collection(client, size: int, collection: str):
    from qdrant_client.http import models as qmodels

    if size <= 0:
        size = settings.embedding_vector_size

    collections = {c.name: c for c in client.get_collections().collections}
    existing = collections.get(collection)
    if existing:
        try:
            info = client.get_collection(collection)
            existing_size = info.vectors_count if hasattr(info, "vectors_count") else None
            if hasattr(info, "config") and hasattr(info.config, "params") and hasattr(info.config.params, "size"):
                existing_size = info.config.params.size
            if existing_size and existing_size != size:
                client.delete_collection(collection_name=collection)
                existing = None
        except Exception:
            pass

    if not existing:
        client.create_collection(
            collection_name=collection,
            vectors_config=qmodels.VectorParams(size=size, distance=qmodels.Distance.COSINE),
        )


def _upload_dir() -> Path:
    p = Path(settings.upload_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _storage_client():
    if not settings.storage_endpoint:
        return None
    return boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint,
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key,
        region_name=settings.storage_region,
        use_ssl=settings.storage_use_ssl,
    )


def _choose_collection(scope: str | None, source: str | None) -> str:
    if scope == "policy_hr":
        return settings.qdrant_collection_policy_hr
    if scope == "policy_it":
        return settings.qdrant_collection_policy_it
    if scope == "policy_travel_expense":
        return settings.qdrant_collection_policy_travel_expense
    return settings.qdrant_collection_user_docs


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk_words = words[i : i + chunk_size]
        chunks.append(" ".join(chunk_words))
        i += max(1, chunk_size - overlap)
    return chunks if chunks else []


def _ocr_bytes(content: bytes, content_type: str | None) -> str:
    if not content_type:
        return ""
    if "image" in content_type:
        image = Image.open(io.BytesIO(content))
        if settings.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
        return pytesseract.image_to_string(image)
    return ""

# ---------- Workspace ----------


@router.get("/domain/rooms")
async def list_rooms(session: Session = Depends(get_session)):
    rooms = session.exec(select(Room)).all()
    return {"rooms": rooms}


@router.get("/domain/desks")
async def list_desks(session: Session = Depends(get_session)):
    desks = session.exec(select(Desk)).all()
    return {"desks": desks}


@router.get("/domain/equipment")
async def list_equipment(session: Session = Depends(get_session)):
    equipment = session.exec(select(Equipment)).all()
    return {"equipment": equipment}


@router.get("/domain/parking")
async def list_parking(session: Session = Depends(get_session)):
    spots = session.exec(select(ParkingSpot)).all()
    return {"parking": spots}


@router.post("/domain/rooms/{room_id}/book")
async def book_room(room_id: int, payload: BookingRequestInput, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)):
    user_id = _current_user_id(user)
    start_dt, end_dt = _parse_time_range(payload.start_time, payload.end_time)
    _assert_available(session, ResourceType.ROOM, room_id, start_dt, end_dt)
    booking = Booking(
        user_id=user_id,
        resource_type=ResourceType.ROOM,
        resource_id=room_id,
        start_time=start_dt,
        end_time=end_dt,
        status="confirmed",
    )
    session.add(booking)
    session.commit()
    session.refresh(booking)
    _create_event(
        session,
        user_id,
        title=f"Room booking ({room_id})",
        start_time=start_dt,
        end_time=end_dt,
        source_type=EventSource.WORKSPACE,
        source_id=booking.id,
    )
    return {"status": "submitted", "booking": booking}


@router.post("/domain/desks/book")
async def book_desk(payload: BookingRequestInput, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)):
    desk_id = _resource_id_by_name(session, ResourceType.DESK, payload.resource_name, payload.desk_id)
    user_id = _current_user_id(user)
    start_dt, end_dt = _parse_time_range(payload.start_time, payload.end_time)
    _assert_available(session, ResourceType.DESK, desk_id, start_dt, end_dt)
    booking = Booking(
        user_id=user_id,
        resource_type=ResourceType.DESK,
        resource_id=desk_id,
        start_time=start_dt,
        end_time=end_dt,
        status="confirmed",
    )
    session.add(booking)
    session.commit()
    session.refresh(booking)
    _create_event(
        session,
        user_id,
        title=f"Desk booking ({desk_id})",
        start_time=start_dt,
        end_time=end_dt,
        source_type=EventSource.WORKSPACE,
        source_id=booking.id,
    )
    return {"status": "submitted", "booking": booking}


@router.post("/domain/equipment/reserve")
async def reserve_equipment(payload: BookingRequestInput, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)):
    equipment_id = _resource_id_by_name(session, ResourceType.EQUIPMENT, payload.resource_name, payload.equipment_id)
    user_id = _current_user_id(user)
    start_dt, end_dt = _parse_time_range(payload.start_time, payload.end_time)
    _assert_available(session, ResourceType.EQUIPMENT, equipment_id, start_dt, end_dt)
    booking = Booking(
        user_id=user_id,
        resource_type=ResourceType.EQUIPMENT,
        resource_id=equipment_id,
        start_time=start_dt,
        end_time=end_dt,
        status="confirmed",
    )
    session.add(booking)
    session.commit()
    session.refresh(booking)
    _create_event(
        session,
        user_id,
        title=f"Equipment booking ({equipment_id})",
        start_time=start_dt,
        end_time=end_dt,
        source_type=EventSource.WORKSPACE,
        source_id=booking.id,
    )
    return {"status": "submitted", "booking": booking}


@router.post("/domain/parking/book")
async def book_parking(payload: BookingRequestInput, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)):
    spot_id = _resource_id_by_name(session, ResourceType.PARKING, payload.resource_name, payload.parking_spot_id)
    user_id = _current_user_id(user)
    start_dt, end_dt = _parse_time_range(payload.start_time, payload.end_time)
    _assert_available(session, ResourceType.PARKING, spot_id, start_dt, end_dt)
    booking = Booking(
        user_id=user_id,
        resource_type=ResourceType.PARKING,
        resource_id=spot_id,
        start_time=start_dt,
        end_time=end_dt,
        status="confirmed",
    )
    session.add(booking)
    session.commit()
    session.refresh(booking)
    _create_event(
        session,
        user_id,
        title=f"Parking booking ({spot_id})",
        start_time=start_dt,
        end_time=end_dt,
        source_type=EventSource.WORKSPACE,
        source_id=booking.id,
    )
    return {"status": "submitted", "booking": booking}


def _booking_with_resource(session: Session, booking: Booking) -> dict:
    name = None
    if booking.resource_type == ResourceType.ROOM:
        res = session.get(Room, booking.resource_id)
        name = res.name if res else None
    elif booking.resource_type == ResourceType.DESK:
        res = session.get(Desk, booking.resource_id)
        name = res.name if res else None
    elif booking.resource_type == ResourceType.EQUIPMENT:
        res = session.get(Equipment, booking.resource_id)
        name = res.name if res else None
    elif booking.resource_type == ResourceType.PARKING:
        res = session.get(ParkingSpot, booking.resource_id)
        name = res.name if res else None
    payload = booking.dict()
    payload["resource_name"] = name
    return payload


@router.get("/domain/bookings/me")
async def bookings_me(
    session: Session = Depends(get_session),
    user: UserContext = Depends(get_current_user),
):
    user_id = _current_user_id(user)
    bookings = session.exec(select(Booking).where(Booking.user_id == user_id).order_by(Booking.created_at.desc())).all()
    return {"bookings": [_booking_with_resource(session, b) for b in bookings]}


# ---------- Leave ----------


@router.get("/domain/entitlements/me")
async def entitlements_me(
    year: int | None = None,
    leave_type: str = "annual",
    month: int | None = None,
    session: Session = Depends(get_session),
    user: UserContext = Depends(get_current_user),
):
    user_id = _current_user_id(user)
    year = year or utcnow().year
    ent = _get_entitlement(session, user_id, year, leave_type, month)
    available = ent.days_available if ent else 0.0
    return {"user_id": user_id, "year": year, "month": month, "leave_type": leave_type, "available_days": available}


@router.get("/domain/entitlements/{user_id}")
async def entitlements_user(
    user_id: str,
    year: int | None = None,
    leave_type: str = "annual",
    month: int | None = None,
    session: Session = Depends(get_session),
    user: UserContext = Depends(get_current_user),
):
    require_roles(user, {"hr_approver", "manager", "system_admin"}, "view_entitlements")
    year = year or utcnow().year
    ent = _get_entitlement(session, user_id, year, leave_type, month)
    available = ent.days_available if ent else 0.0
    return {"user_id": user_id, "year": year, "month": month, "leave_type": leave_type, "available_days": available}


@router.post("/domain/entitlements")
async def upsert_entitlement(
    payload: EntitlementUpsert,
    session: Session = Depends(get_session),
    user: UserContext = Depends(get_current_user),
):
    require_roles(user, {"hr_approver", "manager", "system_admin"}, "manage_entitlements")
    ent = _get_entitlement(session, payload.user_id, payload.year, payload.leave_type, payload.month)
    if ent:
        ent.days_available = payload.days_available
    else:
        ent = LeaveEntitlement(
            user_id=payload.user_id,
            year=payload.year,
            leave_type=payload.leave_type,
            month=payload.month,
            days_available=payload.days_available,
        )
        session.add(ent)
    session.commit()
    session.refresh(ent)
    return {"entitlement": ent}


@router.post("/domain/requests")
async def create_leave_request(
    payload: LeaveRequestInput, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)
):
    user_id = _current_user_id(user)
    requested_days = _calc_days(payload.start_date, payload.end_date)
    start = _as_date(payload.start_date)
    end = _as_date(payload.end_date)
    month = None  # yearly accrual for all leave types
    ent = _get_entitlement(session, user_id, start.year, payload.leave_type, month)

    # In local/dev (auth disabled) auto-provision or top-up entitlement so flows don't break.
    if not ent and settings.auth_disabled:
        ent = LeaveEntitlement(
            user_id=user_id,
            year=start.year,
            leave_type=payload.leave_type,
            days_available=_default_entitlement_days(payload.leave_type, month) or requested_days,
            month=month,
        )
        session.add(ent)
        session.commit()
        session.refresh(ent)

    if not ent:
        raise HTTPException(400, "No entitlement configured for this leave type/year")

    if requested_days > ent.days_available:
        if settings.auth_disabled:
            # top up just enough to cover the request in dev so submission succeeds
            ent.days_available = requested_days
        else:
            raise HTTPException(400, "Not enough leave balance")

    ent.days_available -= requested_days
    lr = LeaveRequestModel(
        user_id=user_id,
        leave_type=payload.leave_type,
        start_date=start,
        end_date=end,
        reason=payload.reason,
        status="submitted",
        requested_days=requested_days,
    )
    session.add(lr)
    session.add(ent)
    session.commit()
    session.refresh(lr)
    _create_event(
        session,
        user_id,
        title=f"Leave: {payload.leave_type}",
        start_time=datetime.combine(start, datetime.min.time()),
        end_time=datetime.combine(end, datetime.max.time()),
        source_type=EventSource.LEAVE,
        source_id=lr.id,
    )
    return {"status": "submitted", "request": lr}


@router.get("/domain/requests/me")
async def list_my_requests(session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)):
    user_id = _current_user_id(user)
    results = session.exec(select(LeaveRequestModel).where(LeaveRequestModel.user_id == user_id)).all()
    return {"requests": results}


@router.get("/domain/requests")
async def list_requests(
    status: str | None = None,
    session: Session = Depends(get_session),
    user: UserContext = Depends(get_current_user),
):
    require_roles(user, {"hr_approver", "manager", "system_admin"}, "list_leave_requests")
    stmt = select(LeaveRequestModel)
    if status:
        stmt = stmt.where(LeaveRequestModel.status == status)
    results = session.exec(stmt.order_by(LeaveRequestModel.created_at.desc())).all()
    return {"requests": results}


@router.post("/domain/requests/{request_id}/approve")
async def approve_leave_request(
    request_id: int, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)
):
    require_roles(user, {"hr_approver", "manager", "system_admin"}, "approve_leave")
    lr = session.get(LeaveRequestModel, request_id)
    if not lr:
        raise HTTPException(404, "request not found")
    lr.status = "approved"
    lr.approver_id = _current_user_id(user)
    lr.updated_at = utcnow()
    session.add(lr)
    record_audit_log(
        session,
        actor_id=_current_user_id(user),
        action="leave_request_approved",
        target_type="leave_request",
        target_id=lr.id,
    )
    session.commit()
    session.refresh(lr)
    return {"status": "approved", "request": lr}


@router.post("/domain/requests/{request_id}/reject")
async def reject_leave_request(
    request_id: int, reason: str | None = None, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)
):
    require_roles(user, {"hr_approver", "manager", "system_admin"}, "reject_leave")
    lr = session.get(LeaveRequestModel, request_id)
    if not lr:
        raise HTTPException(404, "request not found")
    lr.status = "rejected"
    lr.reject_reason = reason
    lr.approver_id = _current_user_id(user)
    lr.updated_at = utcnow()
    session.add(lr)
    record_audit_log(
        session,
        actor_id=_current_user_id(user),
        action="leave_request_rejected",
        target_type="leave_request",
        target_id=lr.id,
        details={"reason": reason},
    )
    session.commit()
    session.refresh(lr)
    return {"status": "rejected", "request": lr}


# ---------- Expense & Travel ----------


@router.post("/domain/expenses")
async def create_expense(
    expense: ExpenseInput, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)
):
    _validate_expense(expense)
    data = ExpenseModel(
        user_id=_current_user_id(user),
        amount=expense.amount,
        currency=expense.currency,
        date=date.fromisoformat(expense.date),
        category=expense.category,
        project_code=expense.project_code,
        status="submitted",
    )
    session.add(data)
    session.commit()
    session.refresh(data)
    return {"status": "submitted", "expense": data}


@router.post("/domain/travel-requests")
async def create_travel(
    travel: TravelInput, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)
):
    _validate_travel(travel)
    dep = date.fromisoformat(travel.departure_date)
    ret = date.fromisoformat(travel.return_date) if travel.return_date else dep
    end_expr = func.coalesce(TravelModel.return_date, TravelModel.departure_date)
    conflict = session.exec(
        select(TravelModel).where(
            TravelModel.status.in_(["approved", "submitted"]),
            TravelModel.departure_date <= ret,
            end_expr >= dep,
        )
    ).first()
    if conflict:
        raise HTTPException(409, "Another travel request overlaps these dates; please choose different dates")

    data = TravelModel(
        user_id=_current_user_id(user),
        origin=travel.origin,
        destination=travel.destination,
        departure_date=date.fromisoformat(travel.departure_date),
        return_date=date.fromisoformat(travel.return_date) if travel.return_date else None,
        travel_class=travel.travel_class,
        preferred_departure_time=travel.preferred_departure_time,
        preferred_return_time=travel.preferred_return_time,
        status="submitted",
    )
    session.add(data)
    session.commit()
    session.refresh(data)
    _create_event(
        session,
        _current_user_id(user),
        title=f"Travel: {travel.origin} -> {travel.destination}",
        start_time=datetime.combine(date.fromisoformat(travel.departure_date), datetime.min.time()),
        end_time=datetime.combine(date.fromisoformat(travel.return_date) if travel.return_date else date.fromisoformat(travel.departure_date), datetime.max.time()),
        source_type=EventSource.TRAVEL,
        source_id=data.id,
    )
    return {"status": "submitted", "travel": data.model_dump(mode="python")}


_receipts: dict[str, dict] = {}


@router.post("/domain/expenses/{expense_id}/attach-receipt")
async def attach_receipt(expense_id: int, receipt: ReceiptInput):
    _receipts[str(expense_id)] = receipt.model_dump()
    return {"status": "submitted", "expense_id": expense_id, "receipt": receipt.model_dump()}


@router.get("/domain/expenses/me")
async def list_my_expenses(session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)):
    user_id = _current_user_id(user)
    results = session.exec(select(ExpenseModel).where(ExpenseModel.user_id == user_id)).all()
    return {"expenses": results}


@router.get("/domain/expenses")
async def list_expenses(
    status: str | None = None,
    session: Session = Depends(get_session),
    user: UserContext = Depends(get_current_user),
):
    require_roles(user, {"admin_approver", "manager", "system_admin"}, "list_expenses")
    stmt = select(ExpenseModel)
    if status:
        stmt = stmt.where(ExpenseModel.status == status)
    results = session.exec(stmt.order_by(ExpenseModel.created_at.desc())).all()
    return {"expenses": results}


@router.get("/domain/travel-requests/me")
async def list_my_travel_requests(session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)):
    user_id = _current_user_id(user)
    results = session.exec(select(TravelModel).where(TravelModel.user_id == user_id)).all()
    return {"travel_requests": results}


@router.get("/domain/travel-requests")
async def list_travel_requests(
    status: str | None = None,
    session: Session = Depends(get_session),
    user: UserContext = Depends(get_current_user),
):
    require_roles(user, {"admin_approver", "manager", "system_admin"}, "list_travel_requests")
    stmt = select(TravelModel)
    if status:
        stmt = stmt.where(TravelModel.status == status)
    results = session.exec(stmt.order_by(TravelModel.created_at.desc())).all()
    return {"travel_requests": results}


@router.post("/domain/expenses/{expense_id}/approve")
async def approve_expense(
    expense_id: int,
    payload: ExpenseDecision | None = None,
    session: Session = Depends(get_session),
    user: UserContext = Depends(get_current_user),
):
    require_roles(user, {"admin_approver", "manager", "system_admin"}, "approve_expense")
    exp = session.get(ExpenseModel, expense_id)
    if not exp:
        raise HTTPException(404, "expense not found")
    exp.status = "approved"
    exp.updated_at = utcnow()
    session.add(exp)
    record_audit_log(
        session,
        actor_id=_current_user_id(user),
        action="expense_approved",
        target_type="expense",
        target_id=exp.id,
        details={"reason": payload.reason if payload else None},
    )
    session.commit()
    session.refresh(exp)
    return {"status": "approved", "expense": exp, "reason": payload.reason if payload else None}


@router.post("/domain/expenses/{expense_id}/reject")
async def reject_expense(
    expense_id: int,
    payload: ExpenseDecision | None = None,
    session: Session = Depends(get_session),
    user: UserContext = Depends(get_current_user),
):
    require_roles(user, {"admin_approver", "manager", "system_admin"}, "reject_expense")
    exp = session.get(ExpenseModel, expense_id)
    if not exp:
        raise HTTPException(404, "expense not found")
    exp.status = "rejected"
    exp.updated_at = utcnow()
    exp.project_code = exp.project_code  # no-op to silence lint
    session.add(exp)
    record_audit_log(
        session,
        actor_id=_current_user_id(user),
        action="expense_rejected",
        target_type="expense",
        target_id=exp.id,
        details={"reason": payload.reason if payload else None},
    )
    session.commit()
    session.refresh(exp)
    return {"status": "rejected", "expense": exp, "reason": payload.reason if payload else None}


@router.post("/domain/travel-requests/{travel_id}/approve")
async def approve_travel(
    travel_id: int,
    payload: TravelDecision | None = None,
    session: Session = Depends(get_session),
    user: UserContext = Depends(get_current_user),
):
    require_roles(user, {"admin_approver", "manager", "system_admin"}, "approve_travel")
    tr = session.get(TravelModel, travel_id)
    if not tr:
        raise HTTPException(404, "travel request not found")
    # check conflicts with already approved travel for this user
    dep = tr.departure_date
    ret = tr.return_date or tr.departure_date
    end_expr = func.coalesce(TravelModel.return_date, TravelModel.departure_date)
    conflict = session.exec(
        select(TravelModel).where(
            TravelModel.status.in_(["approved", "submitted"]),
            TravelModel.id != tr.id,
            TravelModel.departure_date <= ret,
            end_expr >= dep,
        )
    ).first()
    if conflict:
        raise HTTPException(409, "Another travel request overlaps these dates; capacity is full for that window")

    tr.status = "approved"
    tr.updated_at = utcnow()
    session.add(tr)
    record_audit_log(
        session,
        actor_id=_current_user_id(user),
        action="travel_request_approved",
        target_type="travel_request",
        target_id=tr.id,
        details={"reason": payload.reason if payload else None},
    )
    session.commit()
    session.refresh(tr)
    return {"status": "approved", "travel": tr.model_dump(mode="python"), "reason": payload.reason if payload else None}


@router.post("/domain/travel-requests/{travel_id}/reject")
async def reject_travel(
    travel_id: int,
    payload: TravelDecision | None = None,
    session: Session = Depends(get_session),
    user: UserContext = Depends(get_current_user),
):
    require_roles(user, {"admin_approver", "manager", "system_admin"}, "reject_travel")
    tr = session.get(TravelModel, travel_id)
    if not tr:
        raise HTTPException(404, "travel request not found")
    tr.status = "rejected"
    tr.updated_at = utcnow()
    session.add(tr)
    record_audit_log(
        session,
        actor_id=_current_user_id(user),
        action="travel_request_rejected",
        target_type="travel_request",
        target_id=tr.id,
        details={"reason": payload.reason if payload else None},
    )
    session.commit()
    session.refresh(tr)
    return {"status": "rejected", "travel": tr.model_dump(mode="python"), "reason": payload.reason if payload else None}


# ---------- Tickets ----------


@router.post("/domain/tickets")
async def create_ticket(
    ticket: TicketInput, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)
):
    try:
        ticket_type = TicketType(ticket.type)
    except ValueError:
        raise HTTPException(400, f"type must be one of: {', '.join([t.value for t in TicketType])}")
    incident_date = None
    if ticket.incident_date:
        try:
            incident_date = _as_date(ticket.incident_date)
        except HTTPException as exc:
            raise HTTPException(400, "incident_date must be DD/MM/YYYY") from exc
    data = TicketModel(
        user_id=_current_user_id(user),
        type=ticket_type,
        category=ticket.category,
        description=ticket.description,
        location=ticket.location,
        incident_date=incident_date,
        priority=ticket.priority,
        status=TicketStatus.OPEN,
    )
    session.add(data)
    session.commit()
    session.refresh(data)
    return {
        "status": "submitted",
        "message": "Ticket submitted successfully",
        "ticket": data,
    }


@router.get("/domain/tickets/me")
async def list_my_tickets(session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)):
    user_id = _current_user_id(user)
    results = session.exec(select(TicketModel).where(TicketModel.user_id == user_id)).all()
    return {"tickets": results}


@router.get("/domain/tickets/{ticket_id}")
async def get_ticket(ticket_id: int, session: Session = Depends(get_session)):
    ticket = session.get(TicketModel, ticket_id)
    if not ticket:
        raise HTTPException(404, "ticket not found")
    return ticket


@router.patch("/domain/tickets/{ticket_id}")
async def update_ticket(
    ticket_id: int,
    payload: TicketUpdateInput,
    session: Session = Depends(get_session),
    user: UserContext = Depends(get_current_user),
):
    require_roles(user, {"it_approver", "system_admin"}, "update_ticket")
    ticket = session.get(TicketModel, ticket_id)
    if not ticket:
        raise HTTPException(404, "ticket not found")
    updates = payload.model_dump(exclude_none=True)
    if "status" in updates:
        try:
            updates["status"] = TicketStatus(updates["status"])
        except ValueError:
            raise HTTPException(400, f"status must be one of: {', '.join([s.value for s in TicketStatus])}")
    for k, v in updates.items():
        setattr(ticket, k, v)
    ticket.updated_at = utcnow()
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


# ---------- Access ----------


@router.post("/domain/access-requests")
async def create_access_request(
    payload: AccessRequestInput, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)
):
    try:
        requested_role = RequestedRole(payload.requested_role)
    except ValueError:
        raise HTTPException(400, f"requested_role must be one of: {', '.join([r.value for r in RequestedRole])}")
    user_id = _current_user_id(user)
    dup = session.exec(
        select(AccessRequestModel).where(
            AccessRequestModel.user_id == user_id,
            AccessRequestModel.resource == payload.resource,
            AccessRequestModel.requested_role == requested_role,
            AccessRequestModel.status.in_([AccessStatus.PENDING, AccessStatus.APPROVED]),
        )
    ).first()
    if dup:
        raise HTTPException(409, "An access request for this resource and role already exists or was approved")
    needed_by_date = None
    if payload.needed_by_date:
        try:
            needed_by_date = _as_date(payload.needed_by_date)
        except HTTPException as exc:
            raise HTTPException(400, "needed_by_date must be DD/MM/YYYY") from exc

    data = AccessRequestModel(
        user_id=user_id,
        resource=payload.resource,
        requested_role=requested_role,
        justification=payload.justification,
        needed_by_date=needed_by_date,
        status=AccessStatus.PENDING,
    )
    session.add(data)
    session.flush()
    record_audit_log(
        session,
        actor_id=user_id,
        action="access_request_submitted",
        target_type="access_request",
        target_id=data.id,
        details={"resource": payload.resource, "requested_role": payload.requested_role},
    )
    session.commit()
    session.refresh(data)
    return {
        "status": "submitted",
        "message": "Access request submitted successfully",
        "access_request": data,
    }


@router.get("/domain/access-requests/me")
async def list_my_access_requests(session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)):
    user_id = _current_user_id(user)
    results = session.exec(select(AccessRequestModel).where(AccessRequestModel.user_id == user_id)).all()
    return {"access_requests": results}


@router.get("/domain/access-requests")
async def list_access_requests(
    status: str | None = None,
    session: Session = Depends(get_session),
    user: UserContext = Depends(get_current_user),
):
    require_roles(user, {"it_approver", "system_admin", "admin_approver"}, "list_access_requests")
    statement = select(AccessRequestModel)
    if status:
        try:
            status_enum = AccessStatus(status)
        except ValueError:
            raise HTTPException(400, f"status must be one of: {', '.join([s.value for s in AccessStatus])}")
        statement = statement.where(AccessRequestModel.status == status_enum)
    return {"access_requests": session.exec(statement).all()}


@router.post("/domain/access-requests/{request_id}/approve")
async def approve_access_request(
    request_id: int, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)
):
    require_roles(user, {"it_approver", "system_admin"}, "approve_access")
    ar = session.get(AccessRequestModel, request_id)
    if not ar:
        raise HTTPException(404, "access request not found")
    ar.status = AccessStatus.APPROVED
    ar.approver_id = _current_user_id(user)
    ar.updated_at = utcnow()
    session.add(ar)
    record_audit_log(
        session,
        actor_id=_current_user_id(user),
        action="access_request_approved",
        target_type="access_request",
        target_id=ar.id,
    )
    session.commit()
    session.refresh(ar)
    return {"status": "approved", "request": ar}


@router.post("/domain/access-requests/{request_id}/reject")
async def reject_access_request(
    request_id: int, reason: str | None = None, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)
):
    require_roles(user, {"it_approver", "system_admin"}, "reject_access")
    ar = session.get(AccessRequestModel, request_id)
    if not ar:
        raise HTTPException(404, "access request not found")
    ar.status = AccessStatus.REJECTED
    ar.reject_reason = reason
    ar.approver_id = _current_user_id(user)
    ar.updated_at = utcnow()
    session.add(ar)
    record_audit_log(
        session,
        actor_id=_current_user_id(user),
        action="access_request_rejected",
        target_type="access_request",
        target_id=ar.id,
        details={"reason": reason},
    )
    session.commit()
    session.refresh(ar)
    return {"status": "rejected", "request": ar}


# ---------- Calendar ----------


@router.get("/domain/availability")
async def availability(
    user: str | None = None,
    start: str | None = None,
    end: str | None = None,
    session: Session = Depends(get_session),
    current: UserContext = Depends(get_current_user),
):
    user_id = user or _current_user_id(current)
    try:
        start_dt = dateparser.parse(start) if start else utcnow()
        end_dt = dateparser.parse(end) if end else start_dt.replace(hour=23, minute=59, second=59)  # same day default
    except Exception:
        raise HTTPException(400, "Invalid start/end for availability")
    events = session.exec(
        select(CalendarEvent).where(
            CalendarEvent.user_id == user_id,
            CalendarEvent.start_time < end_dt,
            CalendarEvent.end_time > start_dt,
        ).order_by(CalendarEvent.start_time)
    ).all()
    return {"user": user_id, "events": events}


# ---------- Documents ----------


@router.post("/documents/upload")
async def upload_document(
    file: bytes = File(...),  # raw bytes
    filename: str = Form(...),
    content_type: str | None = Form(None),
    owner: str = Form("system"),
    scope: str = Form("public"),
    source: str = Form("manual"),
    session: Session = Depends(get_session),
):
    # Auto-route known policy filenames into dedicated collections when scope is not specified.
    if scope in {"public", "user_docs"}:
        lower_name = filename.lower()
        if "hr" in lower_name and "policy" in lower_name:
            scope = "policy_hr"
        elif "it" in lower_name and "policy" in lower_name:
            scope = "policy_it"
        elif "travel" in lower_name or "expense" in lower_name:
            scope = "policy_travel_expense"

    name = f"{uuid.uuid4()}_{filename}"
    path = _upload_dir() / name
    with open(path, "wb") as f:
        f.write(file)

    if not content_type:
        import mimetypes

        content_type = mimetypes.guess_type(filename)[0]

    s3_path = None
    client = _storage_client()
    if client:
        try:
            bucket = settings.storage_bucket
            client.create_bucket(Bucket=bucket)  # idempotent if exists on minio
        except Exception:
            pass
        client.upload_file(str(path), settings.storage_bucket, name)
        s3_path = f"s3://{settings.storage_bucket}/{name}"

    text_content = ""
    if content_type and "text" in content_type:
        text_content = file.decode(errors="ignore")
    else:
        # Try PDF text extraction before falling back to OCR.
        if (content_type and "pdf" in content_type.lower()) or filename.lower().endswith(".pdf"):
            try:
                from PyPDF2 import PdfReader  # type: ignore

                reader = PdfReader(io.BytesIO(file))
                extracted = [page.extract_text() or "" for page in reader.pages]
                text_content = "\n".join(extracted).strip()
            except Exception as exc:  # pragma: no cover - best-effort
                print(f"upload_document: PDF text extraction failed: {exc}", flush=True)
                text_content = ""
            # If PDF text is empty, try PyMuPDF text and image-based OCR.
            if not text_content:
                try:
                    import fitz  # PyMuPDF

                    doc_pdf = fitz.open(stream=file, filetype="pdf")
                    extracted = [page.get_text("text") or "" for page in doc_pdf]
                    text_content = "\n".join(extracted).strip()
                    if not text_content:
                        ocr_parts: list[str] = []
                        for page in doc_pdf:
                            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                            ocr_parts.append(pytesseract.image_to_string(img))
                        text_content = "\n".join(ocr_parts).strip()
                except Exception as exc:  # pragma: no cover
                    print(f"upload_document: PyMuPDF fallback failed: {exc}", flush=True)
        if not text_content:
            ocr_text = _ocr_bytes(file, content_type)
            text_content = ocr_text or text_content

    # If still empty (e.g., scanned PDF without OCR), fall back to filename to ensure at least one chunk.
    if not text_content:
        text_content = filename

    doc = Document(
        owner=owner,
        scope=scope,
        source=source,
        title=filename,
        path=s3_path or str(path),
        mime_type=content_type,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)

    # Use hierarchical sentence-based chunking via LlamaIndex.
    chunks: list[str] = []
    try:
        llama_doc = LlamaDocument(text=text_content)
        node_parser = HierarchicalNodeParser.from_defaults(
            node_parser_ids=["large", "mid", "small"],
            node_parser_map={
                "large": SentenceSplitter.from_defaults(chunk_size=1024, chunk_overlap=40),
                "mid": SentenceSplitter.from_defaults(chunk_size=512, chunk_overlap=40),
                "small": SentenceSplitter.from_defaults(chunk_size=128, chunk_overlap=40),
            },
        )
        all_nodes = node_parser.get_nodes_from_documents([llama_doc])
        seen_texts: set[str] = set()
        for node in all_nodes:
            text = node.get_content(metadata_mode="none")
            if text and text not in seen_texts:
                seen_texts.add(text)
                chunks.append(text)
    except Exception as exc:  # pragma: no cover - fallback to simple chunking
        print(f"upload_document: hierarchical chunking failed: {exc}", flush=True)

    if not chunks:
        chunks = _chunk_text(text_content) if text_content else []
    if not chunks:
        chunks = [text_content]

    try:
        vectors = await _embed_texts(chunks)
    except Exception as exc:  # pragma: no cover - embedding failure fallback
        print(f"upload_document: embedding failed, using zero vectors: {exc}", flush=True)
        vectors = [[0.0] * settings.embedding_vector_size for _ in chunks]

    collection = _choose_collection(scope, source)
    vector_size = len(vectors[0]) if vectors else settings.embedding_vector_size
    store = _qdrant_store(collection, vector_size=vector_size)
    if store and vectors:
        try:
            _ensure_collection(store.client, vector_size, collection)
        except Exception as exc:  # pragma: no cover
            print(f"upload_document: ensure_collection failed: {exc}", flush=True)
        nodes = [
            TextNode(
                id_=str(uuid.uuid4()),
                text=chunk,
                metadata={
                    "document_id": doc.id,
                    "chunk_index": idx,
                    "owner": owner,
                    "scope": scope,
                },
                embedding=vectors[idx],
            )
            for idx, chunk in enumerate(chunks)
        ]
        try:
            store.add(nodes)
        except Exception as exc:  # pragma: no cover
            print(f"upload_document: llamaindex store.add failed: {exc}", flush=True)
    elif store:
        print(f"upload_document: no vectors produced for doc {doc.id}; collection ensured '{collection}'", flush=True)

    for idx, chunk in enumerate(chunks):
        session.add(
            DocumentChunk(
                document_id=doc.id,
                content=chunk,
                embedding=_serialize_vec(vectors[idx]) if vectors else None,
                chunk_index=idx,
            )
        )
    session.commit()
    return {"status": "submitted", "document_id": doc.id, "message": "Document uploaded and indexed"}


@router.post("/documents/search")
async def search_documents(payload: DocumentSearchInput, session: Session = Depends(get_session)):
    vectors = await _embed_texts([payload.query])
    if not vectors:
        raise HTTPException(502, "Embedding service unavailable")
    query_vec = vectors[0]
    results: list[dict] = []
    seen: set[tuple[int, int | None]] = set()
    snippet_len = 480

    def _snippet_from_source(sn) -> str:
        text = ""
        try:
            if hasattr(sn, "node") and getattr(sn, "node"):
                text = sn.node.get_content(metadata_mode="none")
            elif hasattr(sn, "text"):
                text = sn.text  # type: ignore[assignment]
        except Exception:
            text = ""
        return (text or "")[:snippet_len]

    client = _qdrant_client()
    collection = payload.collection or _choose_collection(payload.scope, None)
    if client:
        store = _qdrant_store(collection)
        if not store:
            return {"matches": results}
        embed_model = _hf_embed_model()
        index = VectorStoreIndex.from_vector_store(vector_store=store, embed_model=embed_model)
        retriever = index.as_retriever(
            similarity_top_k=payload.top_k,
            similarity_cutoff=settings.qdrant_similarity_cutoff,
        )
        try:
            source_nodes = retriever.retrieve(payload.query)
        except Exception as exc:  # pragma: no cover
            raise HTTPException(502, f"Qdrant query failed: {exc}")

        if source_nodes:
            doc_ids = { (sn.metadata or {}).get("document_id") for sn in source_nodes if sn.metadata }
            doc_rows = session.exec(select(Document).where(Document.id.in_(doc_ids))).all() if doc_ids else []
            id_map = {d.id: d for d in doc_rows}
            for sn in source_nodes:
                meta = sn.metadata or {}
                did = meta.get("document_id")
                doc = id_map.get(did)
                if not doc:
                    continue
                if payload.owner and doc.owner != payload.owner:
                    continue
                if payload.scope and doc.scope != payload.scope:
                    continue
                score_val = 0.0
                try:
                    score_val = float(getattr(sn, "score", 0.0) or 0.0)
                except Exception:
                    score_val = 0.0
                key = (doc.id, meta.get("chunk_index"))
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    {
                        "document_id": doc.id,
                        "title": doc.title,
                        "score": score_val,
                        "chunk_index": meta.get("chunk_index"),
                        "path": doc.path,
                        "snippet": _snippet_from_source(sn),
                    }
                )
        return {"matches": results}

    # fallback to local embeddings if Qdrant not configured
    chunks = session.exec(select(DocumentChunk)).all()
    def dot(a, b): return sum(x * y for x, y in zip(a, b))
    scored = []
    for ch in chunks:
        if not ch.embedding:
            continue
        emb = _deserialize_vec(ch.embedding)
        scored.append((dot(emb, query_vec), ch))
    scored.sort(key=lambda x: x[0], reverse=True)
    for score, ch in scored[: payload.top_k]:
        doc = session.get(Document, ch.document_id)
        if not doc:
            continue
        if payload.owner and doc.owner != payload.owner:
            continue
        if payload.scope and doc.scope != payload.scope:
            continue
        results.append(
            {
                "document_id": doc.id,
                "title": doc.title,
                "score": score,
                "chunk_index": ch.chunk_index,
                "path": doc.path,
                "snippet": (ch.content or "")[:snippet_len],
            }
        )
    return {"matches": results}


# ---------- Chat / Health ----------


@router.get("/chat/sessions")
async def list_chat_sessions(
    request: Request,
    tenant_id: str | None = Header(default=None, convert_underscores=False, alias="X-Tenant-Id"),
) -> dict[str, list[ChatSessionMeta]]:
    tenant = _tenant_from_header(tenant_id)
    mongo_db: AsyncIOMotorDatabase = request.app.state.mongo_db
    cursor = mongo_db[settings.mongo_chat_session_collection].find({"tenant_id": tenant}).sort("updated_at", -1)
    rows = [doc async for doc in cursor]
    return {"sessions": [_serialize_session_meta(r) for r in rows]}


@router.get("/chat/sessions/{session_id}/messages", response_model=ChatSessionMessagesResponse)
async def get_chat_session_messages(
    session_id: str,
    tenant_id: str | None = Header(default=None, convert_underscores=False, alias="X-Tenant-Id"),
    request: Request = None,
) -> ChatSessionMessagesResponse:
    tenant = _tenant_from_header(tenant_id)
    mongo_db: AsyncIOMotorDatabase = request.app.state.mongo_db
    session_doc = await mongo_db[settings.mongo_chat_session_collection].find_one({"_id": session_id, "tenant_id": tenant})
    if not session_doc:
        raise HTTPException(404, "Session not found")
    cursor = (
        mongo_db[settings.mongo_chat_message_collection]
        .find({"session_id": session_id, "tenant_id": tenant})
        .sort("created_at", 1)
    )
    messages = [async_doc async for async_doc in cursor]
    return ChatSessionMessagesResponse(
        session_id=session_doc["_id"],
        title=session_doc.get("title"),
        messages=[_serialize_chat_message(m) for m in messages],
    )


@router.post("/chat/sessions/{session_id}/title")
async def generate_chat_title(
    session_id: str,
    tenant_id: str | None = Header(default=None, convert_underscores=False, alias="X-Tenant-Id"),
    request: Request = None,
) -> dict[str, str]:
    tenant = _tenant_from_header(tenant_id)
    mongo_db: AsyncIOMotorDatabase = request.app.state.mongo_db
    session_doc = await mongo_db[settings.mongo_chat_session_collection].find_one({"_id": session_id, "tenant_id": tenant})
    if not session_doc:
        raise HTTPException(404, "Session not found")

    cursor = (
        mongo_db[settings.mongo_chat_message_collection]
        .find({"session_id": session_id, "tenant_id": tenant})
        .sort("created_at", 1)
    )
    messages = [async_doc async for async_doc in cursor]

    first_user_content = ""
    for msg in messages:
        if msg.get("role") == "user" and msg.get("content"):
            first_user_content = str(msg.get("content"))
            break
    source_text = first_user_content or _derive_session_title(messages)
    title = await _summarize_session_title(source_text)
    await mongo_db[settings.mongo_chat_session_collection].update_one(
        {"_id": session_id, "tenant_id": tenant},
        {"$set": {"title": title, "updated_at": utcnow()}},
    )
    return {"id": session_id, "title": title or "New chat"}


@router.patch("/chat/sessions/{session_id}")
async def rename_chat_session(
    session_id: str,
    payload: ChatSessionRenameRequest,
    tenant_id: str | None = Header(default=None, convert_underscores=False, alias="X-Tenant-Id"),
    request: Request = None,
) -> dict[str, str]:
    tenant = _tenant_from_header(tenant_id)
    mongo_db: AsyncIOMotorDatabase = request.app.state.mongo_db
    title = (payload.title or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    if len(title) > 120:
        raise HTTPException(400, "title must be <= 120 characters")

    result = await mongo_db[settings.mongo_chat_session_collection].update_one(
        {"_id": session_id, "tenant_id": tenant},
        {"$set": {"title": title, "updated_at": utcnow()}},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Session not found")
    return {"id": session_id, "title": title}


@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    tenant_id: str | None = Header(default=None, convert_underscores=False, alias="X-Tenant-Id"),
    request: Request = None,
) -> dict[str, str]:
    tenant = _tenant_from_header(tenant_id)
    mongo_db: AsyncIOMotorDatabase = request.app.state.mongo_db
    sessions = mongo_db[settings.mongo_chat_session_collection]
    messages = mongo_db[settings.mongo_chat_message_collection]

    delete_result = await sessions.delete_one({"_id": session_id, "tenant_id": tenant})
    if delete_result.deleted_count == 0:
        raise HTTPException(404, "Session not found")

    await messages.delete_many({"session_id": session_id, "tenant_id": tenant})
    session_store = request.app.state.session_store
    await session_store.clear_session(tenant, session_id)
    return {"status": "deleted"}


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> ChatResponse:
    session_store = request.app.state.session_store
    mongo_db: AsyncIOMotorDatabase = request.app.state.mongo_db
    result = await handle_chat(
        session_store,
        payload.message,
        payload.session_id,
        user,
        payload.tenant_id,
        mongo_db=mongo_db,
    )
    return ChatResponse(**result)


@router.websocket("/chat/stream")
async def chat_stream(websocket: WebSocket, session_id: str | None = None) -> None:
    await websocket.accept()
    token = _extract_bearer_token(websocket)
    user = await get_user_from_token(token)
    session_store = websocket.app.state.session_store
    mongo_db: AsyncIOMotorDatabase = websocket.app.state.mongo_db

    try:
        while True:
            incoming = await websocket.receive_json()
            if incoming.get("type") != "user_message":
                continue
            message = incoming.get("message", "")
            tenant_id = incoming.get("tenant_id")

            result = await handle_chat(
                session_store,
                message,
                session_id,
                user,
                tenant_id,
                mongo_db=mongo_db,
            )

            session_id = result.get("session_id", session_id)

            for event in result.get("events", []):
                await websocket.send_json(event)

            for token_chunk in iter_tokens(result.get("message", "")):
                await websocket.send_json({"type": "token_delta", "data": token_chunk})

            await websocket.send_json(
                {
                    "type": "final_response",
                    "session_id": session_id,
                    "session_title": result.get("session_title"),
                    "message": result.get("message", ""),
                    "actions": result.get("actions", []),
                    "pending_request": result.get("pending_request"),
                }
            )
    except WebSocketDisconnect:
        return


def _extract_bearer_token(websocket: WebSocket) -> str | None:
    auth_header = websocket.headers.get("authorization")
    if not auth_header:
        return None
    parts = auth_header.split(" ")
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None
