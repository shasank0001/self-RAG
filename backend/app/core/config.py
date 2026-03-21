from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="self-rag-knowledge-chat", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    api_base_url: str = Field(default="http://localhost:8000", alias="API_BASE_URL")
    cors_allowed_origins: str = Field(default="", alias="CORS_ALLOWED_ORIGINS")

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/selfrag",
        alias="DATABASE_URL",
    )
    alembic_database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/selfrag",
        alias="ALEMBIC_DATABASE_URL",
    )

    clerk_issuer: str = Field(default="", alias="CLERK_ISSUER")
    clerk_jwks_url: str = Field(default="", alias="CLERK_JWKS_URL")
    clerk_audience: str = Field(default="", alias="CLERK_AUDIENCE")
    clerk_secret_key: str = Field(default="", alias="CLERK_SECRET_KEY")
    clerk_publishable_key: str = Field(default="", alias="CLERK_PUBLISHABLE_KEY")
    clerk_webhook_secret: str = Field(default="", alias="CLERK_WEBHOOK_SECRET")

    pinecone_api_key: str = Field(default="", alias="PINECONE_API_KEY")
    pinecone_index_name: str = Field(
        default="self-rag-knowledge-chat",
        validation_alias=AliasChoices("PINECONE_INDEX_NAME", "PINECONE_INDEX"),
    )
    pinecone_region: str = Field(
        default="us-east-1",
        validation_alias=AliasChoices("PINECONE_REGION", "PINECONE_ENVIRONMENT"),
    )
    pinecone_host: str = Field(default="", alias="PINECONE_HOST")

    embedding_provider: str = Field(default="ollama", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="qwen3-embedding:4b", alias="EMBEDDING_MODEL")
    embedding_dimensions: int = Field(default=2560, alias="EMBEDDING_DIMENSIONS")
    vector_index_backend: str = Field(default="pinecone", alias="VECTOR_INDEX_BACKEND")

    pipeline_config_path: str = Field(default="config.yaml", alias="PIPELINE_CONFIG_PATH")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL")
    cerebras_api_key: str = Field(default="", alias="CEREBRAS_API_KEY")
    cerebras_base_url: str = Field(default="https://api.cerebras.ai/v1", alias="CEREBRAS_BASE_URL")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")

    chunk_size: int = Field(default=1000, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=200, alias="CHUNK_OVERLAP")
    ingestion_batch_size: int = Field(default=32, alias="INGESTION_BATCH_SIZE")
    ingestion_max_attempts: int = Field(default=3, alias="INGESTION_MAX_ATTEMPTS")
    ingestion_retry_backoff_seconds: int = Field(default=5, alias="INGESTION_RETRY_BACKOFF_SECONDS")
    upload_max_mb: int = Field(default=15, alias="UPLOAD_MAX_MB")
    ingestion_storage_dir: str = Field(default="data/uploads", alias="INGESTION_STORAGE_DIR")

    chat_stream_heartbeat_interval_ms: int = Field(default=10000, alias="CHAT_STREAM_HEARTBEAT_INTERVAL_MS")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    otel_exporter_otlp_endpoint: str = Field(default="", alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    otel_service_name: str = Field(default="self-rag-backend", alias="OTEL_SERVICE_NAME")
    otel_tracing_enabled: bool = Field(default=True, alias="OTEL_TRACING_ENABLED")

    prometheus_enabled: bool = Field(default=True, alias="PROMETHEUS_ENABLED")
    model_pricing_config: str = Field(default="backend/config/model_pricing.yaml", alias="MODEL_PRICING_CONFIG")

    rate_limit_rps: float = Field(default=20.0, alias="RATE_LIMIT_RPS")
    rate_limit_burst: int = Field(default=120, alias="RATE_LIMIT_BURST")
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    request_body_limit_bytes: int = Field(default=2_097_152, alias="REQUEST_BODY_LIMIT_BYTES")


@lru_cache
def get_settings() -> Settings:
    return Settings()
