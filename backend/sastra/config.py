"""Runtime configuration, read once from environment variables (prefix SASTRA_).

No secret has a default value. Anything security-relevant fails closed:
if a key is missing the feature that needs it is disabled, never silently
weakened.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"SASTRA_{name}", default)


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw not in (None, "") else default


@dataclass
class Settings:
    data_dir: Path
    database_url: str
    # Secrets (never logged). jwt_secret falls back to a per-process random value,
    # which simply means sessions do not survive restarts — safe, not weak.
    jwt_secret: str
    data_key_b64: str | None  # 32-byte AES key (base64) for encryption at rest
    session_ttl_hours: int = 24 * 14
    environment: str = "development"
    cors_origins: list[str] = field(default_factory=list)
    # Model providers — each is opt-in. Keys stay server-side.
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_fast_model: str = "claude-haiku-5-5"
    openai_compat_base_url: str | None = None  # OpenAI, OpenRouter, Groq, Ollama, llama.cpp server …
    openai_compat_api_key: str | None = None
    openai_compat_model: str | None = None
    openai_compat_is_local: bool = False  # True for Ollama/llama.cpp on your own machine
    embed_model: str | None = None  # embeddings via the OpenAI-compatible endpoint (optional)
    # Research providers
    brave_api_key: str | None = None
    tavily_api_key: str | None = None
    searxng_url: str | None = None
    # Spending caps (USD). 0 means "no paid usage allowed" — fail closed.
    daily_budget_usd: float = 0.50
    monthly_budget_usd: float = 10.0
    # Sandbox
    sandbox_backend: str = "auto"  # auto | docker | process | disabled
    allow_weak_sandbox: bool = False
    sandbox_timeout_s: int = 60
    sandbox_memory_mb: int = 512
    # Network fetch limits
    fetch_max_bytes: int = 3_000_000
    fetch_timeout_s: int = 20
    respect_robots: bool = True
    # Worker
    worker_in_process: bool = True
    worker_poll_s: float = 1.0
    # Memory
    conversation_retention_days: int = 90
    working_context_ttl_hours: int = 24

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.workspaces_dir, self.artifacts_dir, self.backups_dir):
            p.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(p, 0o700)
            except OSError:
                pass


def load_settings() -> Settings:
    data_dir = Path(_env("DATA_DIR", str(Path.home() / ".sastra"))).expanduser()
    return Settings(
        data_dir=data_dir,
        database_url=_env("DATABASE_URL", f"sqlite:///{data_dir / 'sastra.db'}"),
        jwt_secret=_env("JWT_SECRET") or secrets.token_urlsafe(48),
        data_key_b64=_env("DATA_KEY"),
        session_ttl_hours=_env_int("SESSION_TTL_HOURS", 24 * 14),
        environment=_env("ENV", "development"),
        cors_origins=[o for o in (_env("CORS_ORIGINS", "") or "").split(",") if o],
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        anthropic_model=_env("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        anthropic_fast_model=_env("ANTHROPIC_FAST_MODEL", "claude-haiku-5-5"),
        openai_compat_base_url=_env("OPENAI_COMPAT_BASE_URL"),
        openai_compat_api_key=_env("OPENAI_COMPAT_API_KEY"),
        openai_compat_model=_env("OPENAI_COMPAT_MODEL"),
        openai_compat_is_local=_env_bool("OPENAI_COMPAT_IS_LOCAL", False),
        embed_model=_env("EMBED_MODEL"),
        brave_api_key=_env("BRAVE_API_KEY"),
        tavily_api_key=_env("TAVILY_API_KEY"),
        searxng_url=_env("SEARXNG_URL"),
        daily_budget_usd=_env_float("DAILY_BUDGET_USD", 0.50),
        monthly_budget_usd=_env_float("MONTHLY_BUDGET_USD", 10.0),
        sandbox_backend=_env("SANDBOX_BACKEND", "auto"),
        allow_weak_sandbox=_env_bool("ALLOW_WEAK_SANDBOX", False),
        sandbox_timeout_s=_env_int("SANDBOX_TIMEOUT_S", 60),
        sandbox_memory_mb=_env_int("SANDBOX_MEMORY_MB", 512),
        fetch_max_bytes=_env_int("FETCH_MAX_BYTES", 3_000_000),
        fetch_timeout_s=_env_int("FETCH_TIMEOUT_S", 20),
        respect_robots=_env_bool("RESPECT_ROBOTS", True),
        worker_in_process=_env_bool("WORKER_IN_PROCESS", True),
        worker_poll_s=_env_float("WORKER_POLL_S", 1.0),
        conversation_retention_days=_env_int("CONVERSATION_RETENTION_DAYS", 90),
        working_context_ttl_hours=_env_int("WORKING_CONTEXT_TTL_HOURS", 24),
    )
