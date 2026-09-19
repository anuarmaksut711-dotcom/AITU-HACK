from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", hide_input_in_errors=True)

    app_env: Literal["development", "production"] = "development"
    database_url: str = "postgresql+psycopg://aimeet@localhost:5432/aimeet"
    cookie_secure: bool = False
    cookie_name: str = "aimeet_session"
    allowed_origins: str = "http://localhost:8080"
    session_ttl_seconds: int = Field(default=43_200, ge=300, le=604_800)
    login_attempt_limit: int = Field(default=10, ge=1, le=100)
    login_ip_attempt_limit: int = Field(default=30, ge=1, le=1000)
    login_window_seconds: int = Field(default=900, ge=60, le=3600)
    max_request_bytes: int = Field(default=1_100_000, ge=1024, le=10_000_000)
    audio_dir: Path = Path("data/audio")
    max_audio_bytes: int = Field(default=104_857_600, ge=1024, le=104_857_600)
    max_audio_seconds: int = Field(default=7200, ge=1, le=7200)
    stt_model_path: Path = Path("models/whisper-large-v3-turbo")
    stt_device: Literal["cpu", "cuda"] = "cpu"
    stt_compute_type: Literal["int8", "float16", "int8_float16", "float32"] = "int8"
    stt_cpu_threads: int = Field(default=4, ge=1, le=64)
    stt_diarization_enabled: bool = True
    stt_diarization_model_path: Path = Path("models/speaker-diarization")
    stt_diarization_threshold: float = Field(default=0.9, gt=0, lt=2)
    stt_timeout_seconds: int = Field(default=14_400, ge=30, le=86_400)
    job_lease_seconds: int = Field(default=60, ge=15, le=600)
    job_max_attempts: int = Field(default=3, ge=1, le=5)
    livekit_api_url: str = "http://livekit:7880"
    livekit_public_url: str = "/livekit"
    livekit_api_key: str = "soyle-local"
    livekit_api_secret: SecretStr = SecretStr("")
    live_max_participants: int = Field(default=12, ge=2, le=32)
    live_max_minutes: int = Field(default=120, ge=1, le=240)
    live_analysis_interval: int = Field(default=12, ge=5, le=60)
    live_analysis_reasoning: str = "low"

    intelligence_provider: Literal["ollama", "openai"] = "ollama"
    intelligence_openai_model: str = "gpt-5.6-luna"
    intelligence_reasoning_effort: Literal["low", "medium", "high"] = "low"
    intelligence_model: str = "qwen3:8b"
    intelligence_local_url: str = "http://host.docker.internal:11434"

    rag_offline: bool = False
    rag_llm_provider: Literal["openai", "ollama", "local_openai"] = "openai"
    rag_llm_model: str = "gpt-5.6-luna"
    rag_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "max"
    rag_embedding_provider: Literal["openai", "ollama", "local_openai"] = "openai"
    rag_embedding_model: str = "text-embedding-3-small"
    rag_embedding_dimensions: int = Field(default=1536, ge=1, le=4096)
    rag_embedding_revision: str = "1"
    openai_api_key: SecretStr = SecretStr("")
    rag_local_url: str = "http://host.docker.internal:11434"
    rag_embedding_local_url: str | None = None
    rag_local_api_key: SecretStr = SecretStr("")
    rag_provider_timeout: int = Field(default=180, ge=5, le=300)
    rag_max_output_tokens: int = Field(default=16384, ge=1024, le=32768)
    rag_child_chars: int = Field(default=800, ge=200, le=1600)
    rag_parent_chars: int = Field(default=3200, ge=800, le=8000)
    rag_context_chars: int = Field(default=18000, ge=4000, le=40000)
    rag_candidate_count: int = Field(default=24, ge=4, le=64)
    rag_top_k: int = Field(default=6, ge=1, le=12)
    rag_neighbor_radius: int = Field(default=1, ge=0, le=2)
    rag_min_similarity: float = Field(default=0.25, ge=-1, le=1)
    rag_job_lease_seconds: int = Field(default=600, ge=60, le=3600)
    rag_job_max_attempts: int = Field(default=3, ge=1, le=10)

    @property
    def origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.allowed_origins.split(",")]

    @model_validator(mode="after")
    def validate_deployment(self) -> "Settings":
        if self.rag_parent_chars < self.rag_child_chars:
            raise ValueError("RAG_PARENT_CHARS must be >= RAG_CHILD_CHARS")
        if self.rag_job_lease_seconds <= self.rag_provider_timeout:
            raise ValueError("RAG_JOB_LEASE_SECONDS must exceed provider timeout")
        if self.rag_offline and "openai" in (self.rag_llm_provider, self.rag_embedding_provider):
            raise ValueError("RAG_OFFLINE requires local generation AND embeddings")
        for endpoint in filter(None, (self.rag_local_url, self.rag_embedding_local_url)):
            local = urlsplit(endpoint)
            if (
                local.scheme not in {"http", "https"}
                or not local.hostname
                or local.username
                or local.password
                or local.query
                or local.fragment
                or local.path.rstrip("/") not in {"", "/v1"}
            ):
                raise ValueError(
                    "RAG_LOCAL_URL must be an HTTP(S) origin, optionally ending in /v1"
                )
            if self.rag_offline:
                import ipaddress

                host = local.hostname
                try:
                    address = ipaddress.ip_address(host)
                    permitted = address.is_loopback or address.is_private
                except ValueError:
                    permitted = host in {
                        "localhost",
                        "host.docker.internal",
                        "ollama",
                        "vllm",
                        "llama",
                    }
                if not permitted:
                    raise ValueError("Offline provider must use a local host or private IP")
        if not self.origins or any(not origin for origin in self.origins):
            raise ValueError("ALLOWED_ORIGINS must contain explicit origins")
        for origin in self.origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or "*" in origin
            ):
                raise ValueError("ALLOWED_ORIGINS must contain exact HTTP(S) origins")
        if self.app_env == "production":
            if not self.cookie_secure:
                raise ValueError("Production requires COOKIE_SECURE=true")
            if any(not origin.startswith("https://") for origin in self.origins):
                raise ValueError("Production requires HTTPS ALLOWED_ORIGINS")
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("Production requires PostgreSQL with psycopg")
        return self
