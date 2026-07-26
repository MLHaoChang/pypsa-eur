# Task 5 Report: Tenancy + admin user/org APIs

## RED

- Added `pypsa-gui/backend/tests/test_tenancy_api.py` first.
- Initial command:

  ```bash
  cd /workspace/pypsa-gui/backend
  export PATH="$HOME/.pixi/bin:$PATH"
  pixi run python -m pytest tests/test_tenancy_api.py -v
  ```

- Initial failures:
  - `POST /api/admin/users` returned `404` for member and admin flows because the admin router was not mounted yet.
  - `POST /api/admin/organizations` returned `404` because the organization endpoint did not exist yet.
  - `pypsa-gui/backend/tools/bootstrap_super_admin.py` did not exist.

## GREEN

- Implemented:
  - `pypsa-gui/backend/services/tenancy_service.py`
  - `pypsa-gui/backend/routers/admin.py`
  - `pypsa-gui/backend/tools/bootstrap_super_admin.py`
  - mounted the admin router in `pypsa-gui/backend/main.py`
- Re-ran focused tenancy tests:

  ```bash
  pixi run python -m pytest tests/test_tenancy_api.py -v
  ```

  Result: `4 passed`

- Re-ran nearby regression coverage:

  ```bash
  pixi run python -m pytest tests/test_db_models.py tests/test_auth_service.py tests/test_auth_api.py tests/test_tenancy_api.py -v
  ```

  Result: `24 passed`

## Notes

- The email layer still uses the existing in-memory stub outbox, which satisfies the task's "stub OK" requirement for set-password delivery.
- Existing FastAPI startup/TestClient deprecation warnings remain unchanged and were not introduced by this task.
