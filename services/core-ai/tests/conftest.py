import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import api  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import get_session  # noqa: E402


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def app(engine):
    application = FastAPI()
    application.include_router(api.router, prefix=settings.api_prefix)
    application.state.session_store = object()

    def _get_session_override():
        with Session(engine) as session:
            yield session

    application.dependency_overrides[get_session] = _get_session_override
    return application


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def session(engine):
    with Session(engine) as session:
        yield session
