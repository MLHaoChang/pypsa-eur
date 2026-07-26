# Task 4 Report: Auth HTTP API + dependencies

## Scope delivered

- Added top-level auth dependencies in `pypsa-gui/backend/deps.py`:
  - `optional_user(request, db) -> User | None`
  - `require_user(request, db) -> User`
- Added `pypsa-gui/backend/routers/auth.py` with:
  - `POST /api/auth/login`
  - `POST /api/auth/logout`
  - `GET /api/auth/me`
  - `POST /api/auth/forgot-password`
  - `POST /api/auth/reset-password`
  - `POST /api/auth/set-password`
- Mounted auth router and enforced `/api/*` auth gating in `pypsa-gui/backend/main.py`, with public allowlist for:
  - `/api/auth/login`
  - `/api/auth/forgot-password`
  - `/api/auth/reset-password`
  - `/api/auth/set-password`
  - `/api/health`
- Added minimal mail stub in `pypsa-gui/backend/services/email_service.py`.
- Extended `pypsa-gui/backend/services/auth_service.py` to:
  - reject sessions for non-active users
  - invalidate prior unused password tokens of the same purpose on reissue
  - atomically consume password token + set password + activate user + revoke sessions
- Added HTTP API coverage in `pypsa-gui/backend/tests/test_auth_api.py`.

## RED evidence

Command:

```bash
export PATH="$HOME/.pixi/bin:$PATH" && pixi run python -m pytest pypsa-gui/backend/tests/test_auth_api.py -q
```

Result:

- Exit code: `1`
- `9` failures
- Expected missing behavior was confirmed:
  - `/api/auth/*` endpoints returned `404`
  - protected route `/api/changelog/` still returned `200` instead of `401`

Representative failures:

```text
FAILED test_login_sets_cookie - assert 404 == 200
FAILED test_me_requires_auth - assert 404 == 401
FAILED test_protected_api_requires_auth_when_enabled - assert 200 == 401
```

## GREEN evidence

Command:

```bash
export PATH="$HOME/.pixi/bin:$PATH" && pixi run python -m pytest pypsa-gui/backend/tests/test_auth_api.py -q
```

Result:

- Exit code: `0`
- `9 passed`

Follow-up verification command:

```bash
export PATH="$HOME/.pixi/bin:$PATH" && pixi run python -m pytest pypsa-gui/backend/tests/test_auth_service.py pypsa-gui/backend/tests/test_auth_api.py -q
```

Result:

- Exit code: `0`
- `15 passed`

## Notes / carry-forward

- The password set/reset path is now single-commit from token validation through password update and session revocation.
- Reissuing a password token for the same user/purpose marks earlier unused tokens as used.
- The new email service is intentionally a stub/outbox recorder so Task 5/9 can replace it with real SMTP/template behavior.

## Files changed

- `pypsa-gui/backend/main.py`
- `pypsa-gui/backend/services/auth_service.py`
- `pypsa-gui/backend/deps.py`
- `pypsa-gui/backend/routers/auth.py`
- `pypsa-gui/backend/services/email_service.py`
- `pypsa-gui/backend/tests/test_auth_api.py`
