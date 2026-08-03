from functools import lru_cache
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

import app_paths

_BACKEND = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_BACKEND / ".env"), extra="ignore")

    # SQLite under the per-user app-data dir. Web deployments set DATABASE_URL
    # explicitly (compose, .env), so this default only ever applies to a local
    # run — where the previous Postgres default produced a 503 on every route.
    #
    # default_factory, NOT a class-body expression: a bare `= app_paths.x()` is
    # evaluated once at `import settings`, so a launcher that sets
    # PYPSAGUI_APP_DATA_DIR afterwards would get the stale value.
    database_url: str = Field(default_factory=app_paths.default_database_url)
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
    # All default_factory for the reason given on `database_url` above.
    projects_root: Path = Field(default_factory=app_paths.default_projects_root)
    legacy_root: Path = Field(
        default_factory=lambda: app_paths.app_data_dir() / "legacy_unclaimed"
    )
    # FLAT legacy store, `<root>/<display-name>/network.nc`. Deliberately NOT
    # the same as `projects_root`, which is org-scoped
    # (`<root>/<org_uuid>/<project_uuid>/`). Pointing the flat store at it makes
    # every `<flat root>/<display-name>` lookup in `routers.projects` address an
    # org-UUID directory instead — see the note beside `PROJECTS_DIR` there.
    flat_projects_root: Path = Field(
        default_factory=app_paths.default_flat_projects_root
    )
    # Where `tools/import_legacy` (and the first-run import) looks for projects
    # saved by a pre-desktop install. `None` means "no import configured".
    #
    # The `validation_alias` is load-bearing: this class declares NO
    # `env_prefix`, so without it the field would bind bare `LEGACY_IMPORT_ROOT`
    # while every document and command uses the `PYPSAGUI_` name — and the
    # importer would silently inventory nothing. The other `PYPSAGUI_*`
    # variables work because `app_paths` reads them from `os.environ` directly,
    # not through pydantic.
    legacy_import_root: Path | None = Field(
        default=None, validation_alias="PYPSAGUI_LEGACY_IMPORT_ROOT"
    )
    # Built SPA, served by the backend in local mode. Overridable so a frozen
    # app can point at its bundled copy.
    frontend_dist: Path = Field(
        default_factory=lambda: _BACKEND.parent / "frontend" / "dist"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
