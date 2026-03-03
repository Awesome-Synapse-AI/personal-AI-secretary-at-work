from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.tools import tool_runner
from app.api import router
from app.config import settings
from app.db import init_db
from app.logging_config import configure_logging
from app.mongo import create_mongo_client
from app.memory.session_store import SessionStore
from app.observability import RequestContextMiddleware, metrics_endpoint


async def _ensure_indexes(mongo_db):
    sessions = mongo_db[settings.mongo_chat_session_collection]
    messages = mongo_db[settings.mongo_chat_message_collection]
    await sessions.create_index([("tenant_id", 1), ("updated_at", -1)])
    await messages.create_index([("session_id", 1), ("created_at", 1)])


@asynccontextmanager
async def lifespan(app: FastAPI):
    mongo_client = None
    mongo_db = None
    try:
        mongo_client = await create_mongo_client()
        mongo_db = mongo_client[settings.mongo_db_name]
        await _ensure_indexes(mongo_db)
    except Exception as exc:  # pragma: no cover
        print(f"lifespan: Mongo unavailable, continuing without it: {exc}", flush=True)
        mongo_client = None
        mongo_db = None

    session_store = SessionStore(settings.redis_url, settings.session_ttl_seconds)
    await session_store.connect()
    app.state.session_store = session_store
    app.state.mongo_client = mongo_client
    app.state.mongo_db = mongo_db
    init_db()
    yield
    await session_store.close()
    if mongo_client:
        mongo_client.close()
    await tool_runner.close()


def create_app() -> FastAPI:
    configure_logging(settings.log_level)
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Tenant-ID"],
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router, prefix=settings.api_prefix)
    app.add_api_route("/metrics", metrics_endpoint, include_in_schema=False)
    return app


app = create_app()
