import os
import uuid
import io
from pathlib import Path
from datetime import date, datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, File, Form
from sqlmodel import Session, select
from sqlalchemy import func
from dateutil import parser as dateparser
import httpx
import numpy as np
import boto3
import pytesseract
from PIL import Image

from app.auth import get_current_user, get_user_from_token
from app.chat_service import handle_chat
from app.db import get_session
from app.config import settings
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
from app.schemas.chat import ChatRequest, ChatResponse, UserContext
from app.utils import iter_tokens, utcnow

router = APIRouter()


# ---------- helpers ----------

def _current_user_id(user: Optional[UserContext]) -> str:
    return user.sub if user and user.sub else "demo-user"


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
        start_dt = dateparser.parse(start_text)
        end_dt = dateparser.parse(end_text)
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
        date.fromisoformat(expense.date)
    except Exception:
        raise HTTPException(400, "Date must be in YYYY-MM-DD format")
    if not expense.category:
        raise HTTPException(400, "Category is required")


def _validate_travel(travel: TravelInput) -> None:
    if not travel.origin or not travel.destination:
        raise HTTPException(400, "Origin and destination are required")
    try:
        dep = date.fromisoformat(travel.departure_date)
    except Exception:
        raise HTTPException(400, "departure_date must be YYYY-MM-DD")
    ret = None
    if travel.return_date:
        try:
            ret = date.fromisoformat(travel.return_date)
        except Exception:
            raise HTTPException(400, "return_date must be YYYY-MM-DD")
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
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    return (e - s).days + 1


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


def _embedding_url() -> str:
    return settings.embedding_url


async def _embed_texts(texts: List[str]) -> list[list[float]]:
    if not texts:
        return []
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(_embedding_url(), json={"texts": texts})
        resp.raise_for_status()
        data = resp.json()
    vectors = data.get("vectors", [])
    if not isinstance(vectors, list):
        raise HTTPException(502, "Invalid embedding service response")
    return vectors


def _serialize_vec(vec: list[float]) -> bytes:
    return np.array(vec, dtype=np.float32).tobytes()


def _deserialize_vec(blob: bytes) -> list[float]:
    return np.frombuffer(blob, dtype=np.float32).tolist()


def _qdrant_client():
    host = settings.qdrant_host
    if not host:
        return None
    from qdrant_client import QdrantClient

    return QdrantClient(
        host=host,
        port=int(settings.qdrant_port),
        api_key=settings.qdrant_api_key,
    )


def _ensure_collection(client, size: int, collection: str):
    from qdrant_client.http import models as qmodels

    collections = client.get_collections().collections
    if not any(c.name == collection for c in collections):
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


def _chunk_text(text: str, chunk_size: int = 1500, overlap: int = 200) -> list[str]:
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
):
    year = year or utcnow().year
    ent = _get_entitlement(session, user_id, year, leave_type, month)
    available = ent.days_available if ent else 0.0
    return {"user_id": user_id, "year": year, "month": month, "leave_type": leave_type, "available_days": available}


@router.post("/domain/entitlements")
async def upsert_entitlement(payload: EntitlementUpsert, session: Session = Depends(get_session)):
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
    start = date.fromisoformat(payload.start_date)
    month = None  # yearly accrual for all leave types
    ent = _get_entitlement(session, user_id, start.year, payload.leave_type, month)
    if not ent:
        raise HTTPException(400, "No entitlement configured for this leave type/year")
    if requested_days > ent.days_available:
        raise HTTPException(400, "Not enough leave balance")
    ent.days_available -= requested_days
    lr = LeaveRequestModel(
        user_id=user_id,
        leave_type=payload.leave_type,
        start_date=date.fromisoformat(payload.start_date),
        end_date=date.fromisoformat(payload.end_date),
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
        start_time=datetime.combine(date.fromisoformat(payload.start_date), datetime.min.time()),
        end_time=datetime.combine(date.fromisoformat(payload.end_date), datetime.max.time()),
        source_type=EventSource.LEAVE,
        source_id=lr.id,
    )
    return {"status": "submitted", "request": lr}


@router.get("/domain/requests/me")
async def list_my_requests(session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)):
    user_id = _current_user_id(user)
    results = session.exec(select(LeaveRequestModel).where(LeaveRequestModel.user_id == user_id)).all()
    return {"requests": results}


@router.post("/domain/requests/{request_id}/approve")
async def approve_leave_request(
    request_id: int, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)
):
    lr = session.get(LeaveRequestModel, request_id)
    if not lr:
        raise HTTPException(404, "request not found")
    lr.status = "approved"
    lr.approver_id = _current_user_id(user)
    lr.updated_at = utcnow()
    session.add(lr)
    session.commit()
    session.refresh(lr)
    return {"status": "approved", "request": lr}


@router.post("/domain/requests/{request_id}/reject")
async def reject_leave_request(
    request_id: int, reason: str | None = None, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)
):
    lr = session.get(LeaveRequestModel, request_id)
    if not lr:
        raise HTTPException(404, "request not found")
    lr.status = "rejected"
    lr.reject_reason = reason
    lr.approver_id = _current_user_id(user)
    lr.updated_at = utcnow()
    session.add(lr)
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


@router.get("/domain/travel-requests/me")
async def list_my_travel_requests(session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)):
    user_id = _current_user_id(user)
    results = session.exec(select(TravelModel).where(TravelModel.user_id == user_id)).all()
    return {"travel_requests": results}


@router.post("/domain/expenses/{expense_id}/approve")
async def approve_expense(expense_id: int, payload: ExpenseDecision | None = None, session: Session = Depends(get_session)):
    exp = session.get(ExpenseModel, expense_id)
    if not exp:
        raise HTTPException(404, "expense not found")
    exp.status = "approved"
    exp.updated_at = utcnow()
    session.add(exp)
    session.commit()
    session.refresh(exp)
    return {"status": "approved", "expense": exp, "reason": payload.reason if payload else None}


@router.post("/domain/expenses/{expense_id}/reject")
async def reject_expense(expense_id: int, payload: ExpenseDecision | None = None, session: Session = Depends(get_session)):
    exp = session.get(ExpenseModel, expense_id)
    if not exp:
        raise HTTPException(404, "expense not found")
    exp.status = "rejected"
    exp.updated_at = utcnow()
    exp.project_code = exp.project_code  # no-op to silence lint
    session.add(exp)
    session.commit()
    session.refresh(exp)
    return {"status": "rejected", "expense": exp, "reason": payload.reason if payload else None}


@router.post("/domain/travel-requests/{travel_id}/approve")
async def approve_travel(travel_id: int, payload: TravelDecision | None = None, session: Session = Depends(get_session)):
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
    session.commit()
    session.refresh(tr)
    return {"status": "approved", "travel": tr.model_dump(mode="python"), "reason": payload.reason if payload else None}


@router.post("/domain/travel-requests/{travel_id}/reject")
async def reject_travel(travel_id: int, payload: TravelDecision | None = None, session: Session = Depends(get_session)):
    tr = session.get(TravelModel, travel_id)
    if not tr:
        raise HTTPException(404, "travel request not found")
    tr.status = "rejected"
    tr.updated_at = utcnow()
    session.add(tr)
    session.commit()
    session.refresh(tr)
    return {"status": "rejected", "travel": tr.model_dump(mode="python"), "reason": payload.reason if payload else None}


# ---------- Tickets ----------


@router.post("/domain/tickets")
async def create_ticket(
    ticket: TicketInput, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)
):
    if ticket.type not in TicketType:
        raise HTTPException(400, f"type must be one of: {', '.join([t.value for t in TicketType])}")
    data = TicketModel(
        user_id=_current_user_id(user),
        type=TicketType(ticket.type),
        category=ticket.category,
        description=ticket.description,
        location=ticket.location,
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
async def update_ticket(ticket_id: int, payload: TicketUpdateInput, session: Session = Depends(get_session)):
    ticket = session.get(TicketModel, ticket_id)
    if not ticket:
        raise HTTPException(404, "ticket not found")
    updates = payload.model_dump(exclude_none=True)
    if "status" in updates:
        if updates["status"] not in TicketStatus:
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
    if payload.requested_role not in RequestedRole:
        raise HTTPException(400, f"requested_role must be one of: {', '.join([r.value for r in RequestedRole])}")
    user_id = _current_user_id(user)
    dup = session.exec(
        select(AccessRequestModel).where(
            AccessRequestModel.user_id == user_id,
            AccessRequestModel.resource == payload.resource,
            AccessRequestModel.requested_role == RequestedRole(payload.requested_role),
            AccessRequestModel.status.in_([AccessStatus.PENDING, AccessStatus.APPROVED]),
        )
    ).first()
    if dup:
        raise HTTPException(409, "An access request for this resource and role already exists or was approved")

    data = AccessRequestModel(
        user_id=user_id,
        resource=payload.resource,
        requested_role=RequestedRole(payload.requested_role),
        justification=payload.justification,
        status=AccessStatus.PENDING,
    )
    session.add(data)
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
async def list_access_requests(status: str | None = None, session: Session = Depends(get_session)):
    statement = select(AccessRequestModel)
    if status:
        if status not in AccessStatus:
            raise HTTPException(400, f"status must be one of: {', '.join([s.value for s in AccessStatus])}")
        statement = statement.where(AccessRequestModel.status == status)
    return {"access_requests": session.exec(statement).all()}


@router.post("/domain/access-requests/{request_id}/approve")
async def approve_access_request(
    request_id: int, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)
):
    ar = session.get(AccessRequestModel, request_id)
    if not ar:
        raise HTTPException(404, "access request not found")
    ar.status = AccessStatus.APPROVED
    ar.approver_id = _current_user_id(user)
    ar.updated_at = utcnow()
    session.add(ar)
    session.commit()
    session.refresh(ar)
    return {"status": "approved", "request": ar}


@router.post("/domain/access-requests/{request_id}/reject")
async def reject_access_request(
    request_id: int, reason: str | None = None, session: Session = Depends(get_session), user: UserContext = Depends(get_current_user)
):
    ar = session.get(AccessRequestModel, request_id)
    if not ar:
        raise HTTPException(404, "access request not found")
    ar.status = AccessStatus.REJECTED
    ar.reject_reason = reason
    ar.approver_id = _current_user_id(user)
    ar.updated_at = utcnow()
    session.add(ar)
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
        ocr_text = _ocr_bytes(file, content_type)
        text_content = ocr_text or text_content

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

    chunks = _chunk_text(text_content) if text_content else []
    vectors = await _embed_texts(chunks) if chunks else []

    q_client = _qdrant_client()
    collection = _choose_collection(scope, source)
    if q_client and vectors:
        _ensure_collection(q_client, len(vectors[0]), collection)
        payloads = [{"document_id": doc.id, "chunk_index": idx, "owner": owner, "scope": scope} for idx, _ in enumerate(vectors)]
        from qdrant_client.http import models as qmodels

        points = [
            qmodels.PointStruct(id=None, vector=vectors[i], payload=payloads[i])
            for i in range(len(vectors))
        ]
        q_client.upsert(collection_name=collection, wait=True, points=points)

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

    client = _qdrant_client()
    collection = payload.collection or _choose_collection(payload.scope, None)
    if client:
        _ensure_collection(client, len(query_vec), collection)
        hits = client.search(
            collection_name=collection,
            query_vector=query_vec,
            limit=payload.top_k,
        )
        doc_ids = [hit.payload.get("document_id") for hit in hits]
        if doc_ids:
            rows = session.exec(select(Document).where(Document.id.in_(doc_ids))).all()
            id_map = {d.id: d for d in rows}
            for hit in hits:
                did = hit.payload.get("document_id")
                doc = id_map.get(did)
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
                        "score": hit.score,
                        "chunk_index": hit.payload.get("chunk_index"),
                        "path": doc.path,
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
            }
        )
    return {"matches": results}


# ---------- Chat / Health ----------


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
    result = await handle_chat(
        session_store,
        payload.message,
        payload.session_id,
        user,
        payload.tenant_id,
    )
    return ChatResponse(**result)


@router.websocket("/chat/stream")
async def chat_stream(websocket: WebSocket, session_id: str | None = None) -> None:
    await websocket.accept()
    token = _extract_bearer_token(websocket)
    user = await get_user_from_token(token)
    session_store = websocket.app.state.session_store

    try:
        while True:
            incoming = await websocket.receive_json()
            if incoming.get("type") != "user_message":
                continue
            message = incoming.get("message", "")
            tenant_id = incoming.get("tenant_id")

            result = await handle_chat(session_store, message, session_id, user, tenant_id)
            session_id = result.get("session_id", session_id)

            for event in result.get("events", []):
                await websocket.send_json(event)

            for token_chunk in iter_tokens(result.get("message", "")):
                await websocket.send_json({"type": "token_delta", "data": token_chunk})

            await websocket.send_json(
                {
                    "type": "final_response",
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
