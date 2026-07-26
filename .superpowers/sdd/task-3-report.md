# Task 3 Report: Auth service (passwords, sessions, tokens)

## Status

DONE_WITH_CONCERNS

## Scope completed

- Created `pypsa-gui/backend/services/auth_service.py`
- Created `pypsa-gui/backend/tests/test_auth_service.py`
- Did not implement HTTP routers or FastAPI auth dependencies (correctly deferred to Task 4)

## TDD flow

### RED

Added the auth service test file first, covering:

- password hash / verify round-trip
- session creation, resolution, revocation, and bulk revocation
- expired-session rejection
- one-time password tokens
- token-purpose enforcement
- hashed-only storage for sessions and auth tokens

Initial failing verification:

```bash
cd /workspace
$HOME/.pixi/bin/pixi run python -m pytest pypsa-gui/backend/tests/test_auth_service.py -v
```

Observed failure:

- `ModuleNotFoundError: No module named 'services.auth_service'`

This confirmed the test was exercising missing implementation rather than passing accidentally.

### GREEN

Implemented `services/auth_service.py` with:

- `hash_password(password: str) -> str`
- `verify_password(password: str, password_hash: str) -> bool`
- `create_session(db, user_id) -> tuple[str, Session]`
- `resolve_session(db, raw_token) -> User | None`
- `revoke_session(db, raw_token) -> None`
- `revoke_all_sessions_for_user(db, user_id) -> None`
- `issue_password_token(db, user_id, purpose) -> str`
- `consume_password_token(db, raw_token, purpose) -> User | None`

Implementation details:

- Passwords use Argon2 through `pwdlib.PasswordHash.recommended()`
- Session and password-token values are generated as random raw tokens and stored only as SHA-256 digests
- Expiry / revocation checks use UTC-aware `datetime.now(timezone.utc)`
- Retrieved datetimes are normalized to UTC before comparison to stay compatible with SQLite-backed tests

### Verified GREEN

Focused auth test:

```bash
cd /workspace
$HOME/.pixi/bin/pixi run python -m pytest pypsa-gui/backend/tests/test_auth_service.py -v
```

Result:

- `6 passed`

Final verification sweep on the affected slice:

```bash
cd /workspace
$HOME/.pixi/bin/pixi run python -m pytest pypsa-gui/backend/tests/test_db_models.py pypsa-gui/backend/tests/test_auth_service.py -v
```

Result:

- `8 passed`

## Notes on behavior

- `consume_password_token(...)` returns `None` when the token is missing, expired, already used, or for the wrong purpose. This matches the task brief's example test, even though the interface summary line omits the optional return case.
- `secret_key` from settings is not yet used in Task 3 because the brief specifically calls for random opaque tokens whose stored form is a hash. Cookie signing / request auth wiring belongs to Task 4.

## Concerns

1. The focused verification slice is green, but I did not run the full backend pytest suite because Task 3 only adds a new service and test file.
2. The existing backend test harness emits three unrelated deprecation warnings during collection/startup (`fastapi` / `starlette` lifespan/testclient warnings).
