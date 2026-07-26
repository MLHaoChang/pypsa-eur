from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_BACKEND / ".env"), extra="ignore")

    pypsa_gui_auth_enabled: bool = False
    database_url: str = "postgresql+psycopg://pypsa:pypsa@localhost:5432/pypsa_gui"
    secret_key: str = "dev-only-change-me"
    session_cookie_name: str = "pypsa_gui_session"
    session_ttl_hours: int = 72
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
