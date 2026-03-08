from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, env_prefix="", case_sensitive=False)

    app_name: str = "core-ai"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    default_tenant_id: str = "default"
    redis_url: str = "redis://localhost:6379/0"
    session_ttl_seconds: int = 86400
    database_url: str = "postgresql://ai:ai_password@postgres:5432/ai_secretary"
    mongo_url: str = "mongodb://mongodb:27017"
    mongo_db_name: str = "ai_secretary"
    mongo_chat_session_collection: str = "chat_sessions"
    mongo_chat_message_collection: str = "chat_messages"

    auth_disabled: bool = True
    keycloak_realm_url: str = "http://localhost:8080/realms/ai-secretary"
    keycloak_client_id: str = "core-ai"
    keycloak_client_secret: str | None = None

    # Enable internal domain tools by default so chat actions (leave, expense, etc.)
    # actually call the co-hosted FastAPI endpoints in local/dev runs.
    tools_enabled: bool = True
    service_auth_token: str | None = None

    llm_base_url: str = "http://llm:11434"
    llm_chat_path: str = "/v1/chat/completions"
    llm_model: str = "qwen3:0.6b"
    llm_api_key: str | None = None
    llm_timeout_seconds: float = 10.0

    # single container mode: domain endpoints mounted in core-ai under /api/v1/domain
    domain_service_url: str = "http://localhost:8000/api/v1/domain"
    # root API base for calling first-party endpoints (e.g., document search)
    core_api_url: str = "http://localhost:8000/api/v1"

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
    # Default to the docker-compose service name so vector indexing works out of the box.
    qdrant_host: str | None = "qdrant"
    qdrant_port: int = 6333
    qdrant_api_key: str | None = None
    qdrant_collection_user_docs: str = "user_docs"
    qdrant_collection_policy_hr: str = "policy_hr"
    qdrant_collection_policy_it: str = "policy_it"
    qdrant_collection_policy_travel_expense: str = "policy_travel_expense"
    # Default to a lightweight, reliable model to avoid long downloads.
    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_device: str = "cpu"  # set to "cuda" if GPU available
    embedding_normalize: bool = True
    embedding_vector_size: int = 384  # dimension for MiniLM
    hf_token: str | None = None  # set to your HF token to avoid rate limits
    huggingface_hub_cache: str | None = "./hf-cache"
    qdrant_similarity_cutoff: float = 0.3
    qdrant_top_k: int = 30

    upload_dir: str = "./data/uploads"

    @property
    def cors_origins(self) -> list[str]:
        raw = (self.cors_allow_origins or "").strip()
        if raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


settings = Settings()
