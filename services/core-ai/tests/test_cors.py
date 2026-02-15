from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.main import create_app


def test_cors_origins_parser(monkeypatch):
    monkeypatch.setattr(settings, "cors_allow_origins", "http://localhost:3000, http://127.0.0.1:3000")
    assert settings.cors_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_app_registers_cors_middleware():
    app = create_app()
    cors = next((m for m in app.user_middleware if m.cls is CORSMiddleware), None)
    assert cors is not None
    assert cors.kwargs["allow_origins"] == settings.cors_origins
    assert cors.kwargs["allow_headers"] == ["*"]
