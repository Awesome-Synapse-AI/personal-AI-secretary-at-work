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

    # MinIO / S3-compatible storage for documents
    storage_endpoint: str | None = None  # e.g., http://minio:9000
    storage_bucket: str = "documents"
    storage_access_key: str | None = None
    storage_secret_key: str | None = None
    storage_use_ssl: bool = False
    storage_region: str | None = None

    # OCR / Tesseract
    tesseract_cmd: str | None = None  # set to tesseract binary path if not on PATH

    # Qdrant / embeddings
    qdrant_host: str | None = None
    qdrant_port: int = 6333
    qdrant_api_key: str | None = None
    qdrant_collection_user_docs: str = "user_docs"
    qdrant_collection_policy_hr: str = "policy_hr"
    qdrant_collection_policy_it: str = "policy_it"
    qdrant_collection_policy_travel_expense: str = "policy_travel_expense"
    embedding_url: str = "http://embedding-svc:8000/embed"

    upload_dir: str = "./data/uploads"


settings = Settings()
