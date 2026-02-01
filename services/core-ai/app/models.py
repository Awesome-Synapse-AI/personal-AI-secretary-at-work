from datetime import date, datetime
from typing import Optional
from enum import Enum

from sqlmodel import Field, SQLModel
from pydantic import BaseModel


class LeaveEntitlement(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str
    year: int
    leave_type: str
    days_available: float
    month: Optional[int] = Field(default=None, index=True)


class LeaveRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str
    leave_type: str
    start_date: date
    end_date: date
    reason: Optional[str] = None
    status: str = "submitted"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    approver_id: Optional[str] = None
    reject_reason: Optional[str] = None
    requested_days: float = 0.0


class Expense(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str
    amount: float
    currency: str
    date: date
    category: str
    project_code: Optional[str] = None
    status: str = "submitted"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TravelRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str
    origin: str
    destination: str
    departure_date: date
    return_date: Optional[date] = None
    travel_class: Optional[str] = None
    status: str = "submitted"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Ticket(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str
    type: "TicketType"
    category: Optional[str] = None
    description: str
    location: Optional[str] = None
    priority: Optional[str] = None
    status: TicketStatus = Field(default=TicketStatus.OPEN)
    assignee: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)



class RequestedRole(str, Enum):
    VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"
    OWNER = "owner"


class AccessStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class EventSource(str, Enum):
    LEAVE = "leave"
    TRAVEL = "travel"
    WORKSPACE = "workspace"
    GENERIC = "generic"

class AccessRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str
    resource: str
    requested_role: "RequestedRole"
    justification: str
    status: AccessStatus = Field(default=AccessStatus.PENDING)
    approver_id: Optional[str] = None
    reject_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CalendarEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str
    title: str
    start_time: datetime
    end_time: datetime
    source_type: EventSource = Field(default=EventSource.GENERIC, index=True)
    source_id: Optional[int] = Field(default=None, index=True)
    status: str = "busy"
    google_event_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# Workspace resources


class ResourceType(str, Enum):
    ROOM = "room"
    DESK = "desk"
    EQUIPMENT = "equipment"
    PARKING = "parking"


class Room(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    capacity: int = 1
    location: Optional[str] = None


class Desk(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    location: Optional[str] = None


class Equipment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    type: Optional[str] = None


class ParkingSpot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    location: Optional[str] = None


class Booking(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str
    resource_type: ResourceType
    resource_id: int
    start_time: datetime
    end_time: datetime
    status: str = "confirmed"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------- Pydantic request/decision schemas ----------


class TicketType(str, Enum):
    IT = "it"
    FACILITIES = "facilities"
    HR = "hr"
    FINANCE = "finance"
    OTHER = "other"


class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class BookingRequestInput(BaseModel):
    user_id: str | None = None
    room_id: str | None = None
    desk_id: str | None = None
    equipment_id: str | None = None
    parking_spot_id: str | None = None
    resource_name: str | None = None
    resource_type: str | None = None
    start_time: str
    end_time: str


class LeaveRequestInput(BaseModel):
    leave_type: str
    start_date: str
    end_date: str
    reason: str | None = None


class EntitlementUpsert(BaseModel):
    user_id: str
    year: int
    leave_type: str
    days_available: float
    month: int | None = None


class ExpenseInput(BaseModel):
    amount: float
    currency: str
    date: str
    category: str
    project_code: str | None = None


class ExpenseDecision(BaseModel):
    reason: str | None = None


class TravelInput(BaseModel):
    origin: str
    destination: str
    departure_date: str
    return_date: str | None = None
    travel_class: str | None = None
    preferred_departure_time: str | None = None
    preferred_return_time: str | None = None


class TravelDecision(BaseModel):
    reason: str | None = None


class ReceiptInput(BaseModel):
    url: str | None = None
    content_type: str | None = None


class TicketUpdateInput(BaseModel):
    status: str | None = None
    assignee: str | None = None
    comment: str | None = None


class AccessRequestInput(BaseModel):
    resource: str
    requested_role: RequestedRole
    justification: str


class TicketInput(BaseModel):
    type: TicketType
    category: str | None = None
    description: str
    location: str | None = None
    priority: str | None = None


class CalendarEventInput(BaseModel):
    title: str
    start_time: datetime
    end_time: datetime
    source_type: EventSource = EventSource.GENERIC
    source_id: int | None = None
