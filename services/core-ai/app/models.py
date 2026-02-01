from datetime import date, datetime
from typing import Optional
from enum import Enum

from sqlmodel import Field, SQLModel


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
    type: str
    category: Optional[str] = None
    description: str
    location: Optional[str] = None
    priority: Optional[str] = None
    status: str = "open"
    assignee: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AccessRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str
    resource: str
    requested_role: str
    justification: str
    status: str = "pending"
    approver_id: Optional[str] = None
    reject_reason: Optional[str] = None
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
