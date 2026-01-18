from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agents.tools import tool_runner
from app.api import router
from app.config import settings
from app.logging_config import configure_logging
from app.memory.session_store import SessionStore


@asynccontextmanager
async def lifespan(app: FastAPI):
    session_store = SessionStore(settings.redis_url, settings.session_ttl_seconds)
    await session_store.connect()
    app.state.session_store = session_store
    yield
    await session_store.close()
    await tool_runner.close()


def create_app() -> FastAPI:
    configure_logging(settings.log_level)
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(router, prefix=settings.api_prefix)
    return app


app = create_app()
