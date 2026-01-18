from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, env_prefix="", case_sensitive=False)

    app_name: str = "core-ai"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    default_tenant_id: str = "default"
    redis_url: str = "redis://localhost:6379/0"
    session_ttl_seconds: int = 86400

    auth_disabled: bool = True
    keycloak_realm_url: str = "http://localhost:8080/realms/ai-secretary"
    keycloak_client_id: str = "core-ai"
    keycloak_client_secret: str | None = None

    tools_enabled: bool = False
    service_auth_token: str | None = None
    workspace_service_url: str = "http://workspace-svc:8001"
    leave_service_url: str = "http://leave-svc:8002"
    expense_service_url: str = "http://expense-svc:8003"
    ticket_service_url: str = "http://ticket-svc:8004"
    access_service_url: str = "http://access-svc:8005"


settings = Settings()
