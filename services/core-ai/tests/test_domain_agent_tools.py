import asyncio
from typing import Callable

import httpx
import pytest
from fastapi import FastAPI
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool

from app import api
from app.agents import domain
from app.agents.clarification import RequestType
from app.agents.tools import tool_runner
from app.config import settings
from app.db import get_session
from app.models import (
    LeaveEntitlement,
    LeaveRequest,
    Room,
    Booking,
    Expense,
    TravelRequest,
    AccessRequest,
    Ticket,
)
from app.state import ChatState


def _make_state(message: str, domain_name: str) -> ChatState:
    return ChatState(message=message, domain=domain_name, pending_request=None, actions=[], events=[])


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def app(engine):
    app = FastAPI()
    app.include_router(api.router, prefix=settings.api_prefix)
    app.state.session_store = object()

    def _get_session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _get_session_override
    return app


@pytest.fixture(autouse=True)
def enable_tools(monkeypatch):
    monkeypatch.setattr(settings, "tools_enabled", True)


@pytest.fixture()
def tool_client(app, monkeypatch):
    # point tool_runner to the in-process FastAPI app
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    monkeypatch.setattr(tool_runner, "_client", client)
    monkeypatch.setattr(settings, "domain_service_url", "http://test/api/v1/domain")
    yield client
    asyncio.run(client.aclose())


def _seed_leave_entitlement(engine, days=5):
    with Session(engine) as session:
        session.add(LeaveEntitlement(user_id="local-user", year=2026, leave_type="annual", days_available=days))
        session.commit()


def _seed_workspace(engine):
    with Session(engine) as session:
        session.add(Room(id=7, name="Ocean", capacity=6))
        session.commit()


def test_domain_agent_calls_leave_tool_success(engine, tool_client, monkeypatch):
    _seed_leave_entitlement(engine)

    async def fake_classify(domain_name, message):
        return RequestType.LEAVE, {
            "leave_type": "annual",
            "start_date": "2026-02-01",
            "end_date": "2026-02-03",
            "reason": "trip",
        }

    monkeypatch.setattr(domain, "classify_request", fake_classify)

    state = _make_state("I need annual leave Feb 1-3 2026 for a trip", "hr")
    result = asyncio.run(domain.domain_node(state))

    assert result["actions"][0]["status"] == "submitted"
    assert "Leave request captured" in result["response"]

    with Session(engine) as session:
        assert len(session.exec(select(LeaveRequest)).all()) == 1


def test_domain_agent_propagates_tool_failure(engine, tool_client, monkeypatch):
    _seed_leave_entitlement(engine)

    async def fake_classify(domain_name, message):
        return RequestType.LEAVE, {
            "leave_type": "annual",
            "start_date": "2026-02-01",
            "end_date": "2026-02-10",
            "reason": "too long",
        }

    monkeypatch.setattr(domain, "classify_request", fake_classify)

    state = _make_state("Annual leave Feb 1-10", "hr")
    result = asyncio.run(domain.domain_node(state))

    assert result["actions"][0]["status"] == "failed"
    assert "failed" in result["response"].lower()


def test_domain_agent_workspace_booking_calls_real_endpoint(engine, tool_client, monkeypatch):
    _seed_workspace(engine)

    async def fake_classify(domain_name, message):
        return RequestType.WORKSPACE_BOOKING, {
            "resource_type": "room",
            "resource_id": 7,
            "resource_name": "Ocean",
            "start_time": "2026-02-10T10:00:00",
            "end_time": "2026-02-10T11:00:00",
        }

    monkeypatch.setattr(domain, "classify_request", fake_classify)

    state = _make_state("Book Ocean room from 10 to 11 on Feb 10 2026", "workspace")
    result = asyncio.run(domain.domain_node(state))

    assert result["actions"][0]["status"] == "submitted"
    assert "Booking" in result["response"]

    with Session(engine) as session:
        assert len(session.exec(select(Booking)).all()) == 1


def test_domain_agent_expense_success(engine, tool_client, monkeypatch):
    async def fake_classify(domain_name, message):
        return RequestType.EXPENSE, {
            "amount": 12.5,
            "currency": "USD",
            "date": "2026-02-01",
            "category": "meal",
        }

    monkeypatch.setattr(domain, "classify_request", fake_classify)

    state = _make_state("Log a $12.5 meal yesterday", "ops")
    result = asyncio.run(domain.domain_node(state))

    assert result["actions"][0]["status"] == "submitted"
    with Session(engine) as session:
        assert len(session.exec(select(Expense)).all()) == 1


def test_domain_agent_expense_failure(engine, tool_client, monkeypatch):
    async def fake_classify(domain_name, message):
        return RequestType.EXPENSE, {
            "amount": -5,
            "currency": "USD",
            "date": "2026-02-01",
            "category": "meal",
        }

    monkeypatch.setattr(domain, "classify_request", fake_classify)

    state = _make_state("Log negative expense", "ops")
    result = asyncio.run(domain.domain_node(state))

    assert result["actions"][0]["status"] == "failed"


def test_domain_agent_travel_success(engine, tool_client, monkeypatch):
    async def fake_classify(domain_name, message):
        return RequestType.TRAVEL, {
            "origin": "NYC",
            "destination": "LAX",
            "departure_date": "2026-03-01",
            "return_date": "2026-03-05",
        }

    monkeypatch.setattr(domain, "classify_request", fake_classify)

    state = _make_state("Book travel NYC to LAX Mar 1-5", "ops")
    result = asyncio.run(domain.domain_node(state))

    assert result["actions"][0]["status"] == "submitted"
    with Session(engine) as session:
        assert len(session.exec(select(TravelRequest)).all()) == 1


def test_domain_agent_access_success(engine, tool_client, monkeypatch):
    async def fake_classify(domain_name, message):
        return RequestType.ACCESS, {
            "resource": "data-lake",
            "requested_role": "viewer",
            "justification": "need reports",
        }

    monkeypatch.setattr(domain, "classify_request", fake_classify)

    state = _make_state("Give me viewer access to data-lake", "it")
    result = asyncio.run(domain.domain_node(state))

    assert result["actions"][0]["status"] == "submitted"
    with Session(engine) as session:
        assert len(session.exec(select(AccessRequest)).all()) == 1


def test_domain_agent_access_failure_invalid_role(engine, tool_client, monkeypatch):
    async def fake_classify(domain_name, message):
        return RequestType.ACCESS, {
            "resource": "data-lake",
            "requested_role": "invalid",
            "justification": "need reports",
        }

    monkeypatch.setattr(domain, "classify_request", fake_classify)

    state = _make_state("Need invalid access role", "it")
    result = asyncio.run(domain.domain_node(state))

    assert result["actions"][0]["status"] == "failed"


def test_domain_agent_ticket_success(engine, tool_client, monkeypatch):
    async def fake_classify(domain_name, message):
        return RequestType.TICKET, {
            "subtype": "it",
            "description": "Laptop not booting",
        }

    monkeypatch.setattr(domain, "classify_request", fake_classify)

    state = _make_state("My laptop will not boot", "it")
    result = asyncio.run(domain.domain_node(state))

    assert result["actions"][0]["status"] == "submitted"
    with Session(engine) as session:
        assert len(session.exec(select(Ticket)).all()) == 1


def test_domain_agent_ticket_failure_invalid_subtype(engine, tool_client, monkeypatch):
    async def fake_classify(domain_name, message):
        return RequestType.TICKET, {
            "subtype": "weird",
            "description": "Some issue",
        }

    monkeypatch.setattr(domain, "classify_request", fake_classify)

    state = _make_state("Weird ticket", "it")
    result = asyncio.run(domain.domain_node(state))

    assert result["actions"][0]["status"] == "failed"
