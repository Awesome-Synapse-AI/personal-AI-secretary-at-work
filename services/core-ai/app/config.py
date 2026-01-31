from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, env_prefix="", case_sensitive=False)

    app_name: str = "core-ai"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    default_tenant_id: str = "default"
    redis_url: str = "redis://localhost:6379/0"
    session_ttl_seconds: int = 86400
    database_url: str = "postgresql://ai:ai_password@postgres:5432/ai_secretary"

    auth_disabled: bool = True
    keycloak_realm_url: str = "http://localhost:8080/realms/ai-secretary"
    keycloak_client_id: str = "core-ai"
    keycloak_client_secret: str | None = None

    tools_enabled: bool = False
    service_auth_token: str | None = None

    llm_base_url: str = "http://llm:11434"
    llm_chat_path: str = "/v1/chat/completions"
    llm_model: str = "qwen3:0.6b"
    llm_api_key: str | None = None
    llm_timeout_seconds: float = 10.0

    # single container mode: domain endpoints mounted in core-ai under /api/v1/domain
    domain_service_url: str = "http://localhost:8000/api/v1/domain"


settings = Settings()
