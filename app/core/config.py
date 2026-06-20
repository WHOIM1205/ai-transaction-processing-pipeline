"""Centralised application configuration.

WHY THIS FILE EXISTS
--------------------
Every component (API, worker, Alembic) needs the same connection strings and
runtime settings. Defining them in ONE typed, validated place means:
  * no scattered `os.getenv` calls with inconsistent defaults,
  * a single source of truth that fails fast on misconfiguration,
  * easy overriding via environment variables in Docker Compose.

Built on `pydantic-settings`, so values are read from environment variables
(and an optional `.env` file for local non-Docker runs) and type-coerced.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed settings loaded from the environment.

    Field names map 1:1 to environment variable names (case-insensitive).
    Defaults target local development; Docker Compose overrides the hosts.
    """

    # `.env` is convenient for running outside Docker. Inside containers the
    # values come from the Compose `environment:` block. `extra="ignore"`
    # keeps unrelated env vars (e.g. PATH) from raising validation errors.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---------------------------------------------------------
    APP_NAME: str = "transaction-pipeline"
    APP_ENV: str = "local"          # local | staging | production
    LOG_LEVEL: str = "INFO"
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # --- PostgreSQL ----------------------------------------------------------
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "transactions"

    # --- Redis ---------------------------------------------------------------
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # --- LLM (Gemini) --------------------------------------------------------
    # Empty key => LLM is treated as unavailable: classification rows are marked
    # llm_failed and the narrative/risk_level are left null (graceful degradation,
    # so the system stays fully demoable with no credentials).
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-1.5-flash"
    LLM_BATCH_SIZE: int = 25            # transactions per classification call
    LLM_MAX_RETRIES: int = 3            # attempts per LLM call (brief: up to 3)
    LLM_RETRY_BASE_DELAY: float = 1.0   # seconds; doubles each retry (exp backoff)

    # --- Uploads -------------------------------------------------------------
    # Directory where raw uploaded CSVs are stored (claim-check pattern: the API
    # writes the file, the worker reads it later by job id). In Docker this is a
    # volume shared between the api and worker containers.
    UPLOAD_DIR: str = "/data/uploads"
    # Hard cap on upload size (bytes) — rejected with 413 above this. 5 MiB is
    # generous for the ~90-row files in scope while guarding against abuse.
    MAX_UPLOAD_BYTES: int = 5 * 1024 * 1024

    # --- Celery --------------------------------------------------------------
    # Left as None so they default to the Redis URL below. Allows pointing the
    # broker/backend at a dedicated instance later without code changes.
    CELERY_BROKER_URL: str | None = None
    CELERY_RESULT_BACKEND: str | None = None

    # --- Derived values ------------------------------------------------------
    # Assembled rather than stored so there is exactly one place that knows the
    # URL shape. Plain properties (not settings fields) keep them read-only.
    @property
    def DATABASE_URL(self) -> str:
        """SQLAlchemy/psycopg2 connection URL used by the engine and Alembic."""
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def REDIS_URL(self) -> str:
        """Base Redis URL reused by Celery and health checks."""
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def celery_broker_url(self) -> str:
        return self.CELERY_BROKER_URL or self.REDIS_URL

    @property
    def celery_result_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton `Settings` instance.

    Caching avoids re-parsing the environment on every import/request and gives
    every module the same object. Used as a FastAPI dependency and imported
    directly by the worker and Alembic.
    """
    return Settings()
