from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_BACKEND / ".env"), extra="ignore")

    database_url: str = "postgresql+psycopg://pypsa:pypsa@localhost:5432/pypsa_gui"
    secret_key: str = "dev-only-change-me"
    session_cookie_name: str = "pypsa_gui_session"
    session_ttl_hours: int = 72
    # ── Trust boundary (Step 0a) ────────────────────────────────────────────
    # CSRF and CORS are ONE decision, not two. The session cookie goes out as
    # `SameSite=None; Secure` on any HTTPS non-local host (see
    # `routers/auth.py:_cookie_flags`) and CORS runs with credentials, so a
    # credentialed allowlisted origin can read a response — and therefore read
    # the double-submit token and forge with it. Widening the allowlist below
    # is equivalent to disabling CSRF for that origin. Comma-separated; exact
    # scheme://host[:port] matches only, no wildcards.
    cors_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    csrf_cookie_name: str = "pypsa_gui_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    # Login throttle. Counted per (client ip, email) so one attacker cannot
    # lock out an unrelated user by burning that user's budget from elsewhere.
    login_max_attempts: int = 10
    login_attempt_window_seconds: int = 300
    login_block_seconds: int = 900
    password_token_ttl_hours: int = 24
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@localhost"
    public_base_url: str = "http://localhost:5173"
    projects_root: Path = _BACKEND / "projects"
    legacy_root: Path = _BACKEND / "legacy_unclaimed"


@lru_cache
def get_settings() -> Settings:
    return Settings()
