# Task 6 Report: Storage paths + project ACL (tree-aware)

## RED evidence

Command:

```bash
export PATH="$HOME/.pixi/bin:$PATH" && pixi run python -m pytest pypsa-gui/backend/tests/test_project_acl.py -q
```

Result:

- Exit code: `2`
- Collection failed with the expected missing-module error:

```text
E   ModuleNotFoundError: No module named 'services.project_acl'
```

## GREEN evidence

Implemented:

- `pypsa-gui/backend/services/storage_paths.py`
- `pypsa-gui/backend/services/project_acl.py`
- `pypsa-gui/backend/tests/test_project_acl.py`

Focused verification command:

```bash
export PATH="$HOME/.pixi/bin:$PATH" && pixi run python -m pytest pypsa-gui/backend/tests/test_project_acl.py -q
```

Result:

- Exit code: `0`
- `9 passed`

Nearby regression verification command:

```bash
export PATH="$HOME/.pixi/bin:$PATH" && pixi run python -m pytest pypsa-gui/backend/tests/test_project_acl.py pypsa-gui/backend/tests/test_tenancy_api.py pypsa-gui/backend/tests/test_db_models.py -q
```

Result:

- Exit code: `0`
- `15 passed`

## Scope delivered

- Added `storage_path_for(org_id, project_id)` as the filesystem seam for org/project-scoped bundle locations under `settings.projects_root`.
- Added tree-aware ACL helpers:
  - `resolve_tree_root`
  - `can_access_project`
  - `can_manage_membership`
  - `can_delete_project`
  - `list_accessible_projects`
  - `ensure_project_access`
- Enforced the v1 whole-tree rules in the service layer:
  - org membership must match the project org
  - org admins can access/manage/delete across the org
  - creator access inherits down the ancestor chain
  - `ProjectMembership` checks are evaluated on the tree root
  - denied access is surfaced as `HTTPException(404)`
- Added test coverage for:
  - configured storage path resolution
  - root resolution across nested scenarios
  - root membership granting descendant access
  - cross-org denial
  - `roots_only=True` filtering
  - membership-management permissions on descendants
  - root-vs-scenario delete rules
  - 404-on-deny via `ensure_project_access`

## Notes / carry-forward

- Task 6 intentionally stops at the service layer; no router wiring was added.
- `resolve_tree_root` and ancestor walks defensively reject cycles even though tree creation guards are expected to prevent them upstream.
- Existing FastAPI/TestClient deprecation warnings remain unchanged and were not introduced by this task.
