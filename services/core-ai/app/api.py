from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.auth import get_current_user, get_user_from_token
from app.chat_service import handle_chat
from app.schemas.chat import ChatRequest, ChatResponse, UserContext
from app.utils import iter_tokens

router = APIRouter()


# ---------- Domain endpoints (co-hosted) ---------- #


class BookingRequestModel(BaseModel):
    user_id: str | None = None
    room_id: str | None = None
    desk_id: str | None = None
    equipment_id: str | None = None
    parking_spot_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None


@router.get("/domain/rooms")
async def list_rooms():
    return {"rooms": [{"id": "room-1", "name": "Room 1"}, {"id": "room-2", "name": "Room 2"}]}


@router.post("/domain/rooms/{room_id}/book")
async def book_room(room_id: str, payload: BookingRequestModel):
    return {"status": "submitted", "room_id": room_id, "payload": payload.model_dump()}


@router.post("/domain/desks/book")
async def book_desk(payload: BookingRequestModel):
    return {"status": "submitted", "desk_id": payload.desk_id, "payload": payload.model_dump()}


@router.post("/domain/equipment/reserve")
async def reserve_equipment(payload: BookingRequestModel):
    if not payload.equipment_id:
        raise HTTPException(400, "equipment_id required")
    return {"status": "submitted", "equipment_id": payload.equipment_id, "payload": payload.model_dump()}


@router.post("/domain/parking/book")
async def book_parking(payload: BookingRequestModel):
    if not payload.parking_spot_id:
        raise HTTPException(400, "parking_spot_id required")
    return {"status": "submitted", "parking_spot_id": payload.parking_spot_id, "payload": payload.model_dump()}


# Leave
class LeaveRequest(BaseModel):
    leave_type: str
    start_date: str
    end_date: str
    reason: str | None = None


@router.get("/domain/entitlements/me")
async def entitlements_me(year: int | None = None):
    return {"year": year, "available_days": 10}


@router.get("/domain/entitlements/{user_id}")
async def entitlements_user(user_id: str, year: int | None = None):
    return {"user_id": user_id, "year": year, "available_days": 10}


@router.post("/domain/requests")
async def create_leave_request(payload: LeaveRequest):
    return {"status": "submitted", "request": payload.model_dump()}


@router.get("/domain/requests/me")
async def list_my_requests():
    return {"requests": []}


@router.post("/domain/requests/{request_id}/approve")
async def approve_leave_request(request_id: str):
    return {"status": "approved", "request_id": request_id}


@router.post("/domain/requests/{request_id}/reject")
async def reject_leave_request(request_id: str, reason: str | None = None):
    return {"status": "rejected", "request_id": request_id, "reason": reason}


# Expense & Travel
class Expense(BaseModel):
    amount: float
    currency: str
    date: str
    category: str
    project_code: str | None = None


class Travel(BaseModel):
    origin: str
    destination: str
    departure_date: str
    return_date: str | None = None
    travel_class: str | None = None


@router.post("/domain/expenses")
async def create_expense(expense: Expense):
    return {"status": "submitted", "expense": expense.model_dump()}


@router.post("/domain/travel-requests")
async def create_travel(travel: Travel):
    return {"status": "submitted", "travel": travel.model_dump()}


class Receipt(BaseModel):
    url: str | None = None
    content_type: str | None = None


@router.post("/domain/expenses/{expense_id}/attach-receipt")
async def attach_receipt(expense_id: str, receipt: Receipt):
    return {"status": "submitted", "expense_id": expense_id, "receipt": receipt.model_dump()}


@router.get("/domain/expenses/me")
async def list_my_expenses():
    return {"expenses": []}


@router.get("/domain/travel-requests/me")
async def list_my_travel_requests():
    return {"travel_requests": []}


# Tickets
class Ticket(BaseModel):
    type: str
    category: str | None = None
    description: str
    location: str | None = None
    priority: str | None = None


@router.post("/domain/tickets")
async def create_ticket(ticket: Ticket):
    if ticket.type not in {"it", "facilities"}:
        raise HTTPException(400, "type must be 'it' or 'facilities'")
    return {"status": "submitted", "ticket": ticket.model_dump()}


@router.get("/domain/tickets/{ticket_id}")
async def get_ticket(ticket_id: str):
    return {"ticket_id": ticket_id, "status": "open"}


@router.get("/domain/tickets/me")
async def list_my_tickets():
    return {"tickets": []}


class TicketUpdate(BaseModel):
    status: str | None = None
    assignee: str | None = None
    comment: str | None = None


@router.patch("/domain/tickets/{ticket_id}")
async def update_ticket(ticket_id: str, payload: TicketUpdate):
    return {"ticket_id": ticket_id, "update": payload.model_dump()}


# Access
class AccessRequest(BaseModel):
    resource: str
    requested_role: str
    justification: str


@router.post("/domain/access-requests")
async def create_access_request(payload: AccessRequest):
    return {"status": "submitted", "access_request": payload.model_dump()}


@router.get("/domain/access-requests/me")
async def list_my_access_requests():
    return {"access_requests": []}


@router.get("/domain/access-requests")
async def list_access_requests(status: str | None = None):
    return {"status_filter": status, "access_requests": []}


@router.post("/domain/access-requests/{request_id}/approve")
async def approve_access_request(request_id: str):
    return {"status": "approved", "request_id": request_id}


@router.post("/domain/access-requests/{request_id}/reject")
async def reject_access_request(request_id: str, reason: str | None = None):
    return {"status": "rejected", "request_id": request_id, "reason": reason}


# Calendar
@router.get("/domain/availability")
async def availability(user: str | None = None):
    return {"user": user, "slots": []}


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
