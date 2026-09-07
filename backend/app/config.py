"""Application configuration.

Every external credential is optional. The backend must start, serve
``/health`` and run the entire intelligence engine with no ``.env`` file at
all - that is what keeps the deterministic engine testable without Postgres,
Upstox or Groq.

Values are read from the environment, then from ``.env`` at the project root,
then from ``backend/.env``. Paths are resolved relative to this file rather
than the working directory, so behaviour does not change based on where
uvicorn or pytest happened to be launched from.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, unquote

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "lumen-backend"
    environment: str = "development"
    debug: bool = True
    api_prefix: str = "/api/v1"

    # --- Datastores (optional in development) -------------------------------
    database_url: str | None = None
    redis_url: str | None = None

    # TEMPORARY (demo deployment): acknowledge that SQLite is being used in
    # production on purpose. PostgreSQL remains fully supported and is still
    # the correct choice - this only downgrades the health warning from
    # "misconfigured" to a factual note, so the deployment is transparent
    # rather than alarming. Remove this once DATABASE_URL points at Postgres.
    allow_sqlite_in_production: bool = False

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str | None) -> str | None:
        """Make a provider-issued PostgreSQL URL usable as-is.

        Two things routinely break a copied Supabase/Render/Neon connection
        string, and both are fixed here so the URL can be pasted unedited:

        1. **Driver.** Providers emit a bare ``postgresql://``, which SQLAlchemy
           resolves to psycopg2. This project installs only ``psycopg[binary]``
           v3, so an unedited URL fails with ``ModuleNotFoundError: psycopg2``.

        2. **Credential escaping.** Generated passwords routinely contain
           ``@``, ``[``, ``]`` and other RFC 3986 reserved characters. Left
           raw, the URL contains two ``@`` signs and the parser picks the wrong
           host - producing a baffling DNS error rather than an auth error.

        Deliberately conservative: explicit drivers (``postgresql+asyncpg``),
        non-PostgreSQL URLs (SQLite in tests) and already-encoded credentials
        are all left untouched, so this can only fix a URL, never break one.
        """
        if not value:
            return value

        scheme, separator, rest = value.partition("://")
        if not separator:
            return value

        # 1. Driver. "postgres" is the legacy alias some providers still emit.
        if scheme in {"postgres", "postgresql"}:
            scheme = "postgresql+psycopg"
        elif not scheme.startswith("postgresql"):
            return value

        # 2. Credentials. Userinfo is everything before the LAST "@"
        #    (RFC 3986), which is what makes an unescaped "@" recoverable.
        userinfo, at_sign, host = rest.rpartition("@")
        if not at_sign:
            return f"{scheme}://{rest}"

        # Decode-then-encode, which is idempotent and therefore safe to apply
        # unconditionally. It normalizes all three states an operator can
        # produce: never escaped, fully escaped, and - the nastiest - partly
        # escaped, where someone hand-fixed one character and left the rest.
        #
        # The one case this cannot resolve is a password whose literal text
        # spells a valid escape (a real "%5B"), because a URL genuinely cannot
        # distinguish that from an encoded "[". Use an alphanumeric password if
        # that is ever a concern.
        username, colon, password = userinfo.partition(":")
        userinfo = quote(unquote(username), safe="")
        if colon:
            userinfo += ":" + quote(unquote(password), safe="")

        return f"{scheme}://{userinfo}@{host}"

    # --- Market data ---------------------------------------------------------
    # Ordered fallback chain. Upstox is NOT required; it is skipped entirely
    # when no token is configured. Replay backstops the chain so the product
    # works with no market access at all.
    market_provider_chain: str = "jugaad,yfinance"
    allow_replay_fallback: bool = True
    quote_cache_seconds: int = 45

    # --- Market data: Upstox (optional) --------------------------------------
    upstox_client_id: str | None = None
    upstox_client_secret: str | None = None
    upstox_redirect_uri: str | None = None
    upstox_access_token: str | None = None
    upstox_api_base: str = "https://api.upstox.com/v2"

    # --- AI explanation layer: Groq ----------------------------------------
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_timeout_seconds: float = 6.0

    # --- Authentication -----------------------------------------------------
    google_client_id: str | None = None
    # Signing key for LUMEN's own session tokens. A development default is
    # used when unset so the backend still boots; production must override it.
    session_secret: str = "lumen-dev-only-not-a-real-secret"
    session_token_ttl_hours: int = 24 * 14
    allow_guest_sessions: bool = True

    # --- Freshness thresholds (seconds) -------------------------------------
    # Deliberately not one universal number: what counts as stale depends on
    # whether the market is open and which provider supplied the value.
    freshness_live_seconds_open: int = 15
    freshness_delayed_seconds_open: int = 120
    freshness_live_seconds_closed: int = 3600
    freshness_delayed_seconds_closed: int = 6 * 3600

    # --- Retention ----------------------------------------------------------
    observation_retention_days: int = 90

    # --- Realtime -----------------------------------------------------------
    # Prices may stream freely; intelligence is recomputed at most this often
    # per instrument so a tick storm cannot trigger a scoring storm.
    intelligence_recompute_seconds: int = 30
    ws_price_broadcast_seconds: float = 1.0

    # --- Notifications ------------------------------------------------------
    notify_min_score: float = 60.0  # High Attention and above only

    # --- News matching ------------------------------------------------------
    news_match_window_minutes: int = 60

    # --- Debug endpoint -----------------------------------------------------
    debug_api_token: str | None = None

    # --- Background polling --------------------------------------------------
    # A single in-process loop. No separate worker, no Redis - both were out
    # of scope, and a free-tier instance cannot host them anyway.
    enable_background_polling: bool = True
    polling_interval_seconds: int = 45
    polling_idle_interval_seconds: int = 900

    # --- CORS ----------------------------------------------------------------
    # Comma-separated. Local development origins are always allowed; the
    # deployed frontend origin is added through this variable.
    cors_origins: str = ""

    @property
    def allowed_origins(self) -> list[str]:
        """Local dev origins plus any configured deployment origins."""
        origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
        origins.extend(
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        )
        return list(dict.fromkeys(origins))

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    def production_warnings(self) -> list[str]:
        """Misconfigurations that matter once this is publicly reachable.

        Reported rather than raised so a misconfigured deploy still boots and
        can be diagnosed through /health, instead of crash-looping silently.
        """
        warnings: list[str] = []
        if not self.is_production:
            return warnings
        if self.session_secret == "lumen-dev-only-not-a-real-secret":
            warnings.append("SESSION_SECRET is still the development default.")
        if not self.database_url:
            warnings.append("DATABASE_URL is not set.")
        if self.database_url and self.database_url.startswith("sqlite"):
            if self.allow_sqlite_in_production:
                # Deliberate, acknowledged demo deployment. Still surfaced, so
                # nobody mistakes it for a durable database.
                warnings.append(
                    "Running on SQLite by configuration (demo deployment): "
                    "storage is ephemeral and resets on restart."
                )
            else:
                warnings.append("SQLite must not be used in production.")
        if self.debug:
            warnings.append("DEBUG is enabled in production.")
        if not self.cors_origins:
            warnings.append("CORS_ORIGINS is not set; only localhost is allowed.")
        return warnings

    @property
    def has_database(self) -> bool:
        return bool(self.database_url)

    @property
    def has_redis(self) -> bool:
        return bool(self.redis_url)

    @property
    def has_upstox_credentials(self) -> bool:
        return bool(self.upstox_client_id and self.upstox_client_secret)

    @property
    def has_upstox_token(self) -> bool:
        return bool(self.upstox_access_token)

    @property
    def has_groq(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def has_google_auth(self) -> bool:
        return bool(self.google_client_id)

    def capability_report(self) -> dict[str, bool]:
        """Which optional integrations are configured.

        Exposed on ``/health`` so the state of the system is visible without
        ever printing a credential.
        """
        return {
            "database": self.has_database,
            "redis": self.has_redis,
            "upstox": self.has_upstox_credentials,
            "upstox_token": self.has_upstox_token,
            "groq": self.has_groq,
            "google_auth": self.has_google_auth,
            "replay_fallback": self.allow_replay_fallback,
        }


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance. Cache is clearable in tests via ``cache_clear``."""
    return Settings()
