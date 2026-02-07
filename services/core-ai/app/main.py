from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agents.tools import tool_runner
from app.api import router
from app.config import settings
from app.db import init_db
from app.logging_config import configure_logging
from app.memory.session_store import SessionStore
from app.observability import RequestContextMiddleware, metrics_endpoint


@asynccontextmanager
async def lifespan(app: FastAPI):
    session_store = SessionStore(settings.redis_url, settings.session_ttl_seconds)
    await session_store.connect()
    app.state.session_store = session_store
    init_db()
    yield
    await session_store.close()
    await tool_runner.close()


def create_app() -> FastAPI:
    configure_logging(settings.log_level)
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router, prefix=settings.api_prefix)
    app.add_api_route("/metrics", metrics_endpoint, include_in_schema=False)
    return app


app = create_app()
