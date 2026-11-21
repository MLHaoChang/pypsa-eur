# import_bundle's project-name checks run late

Status: needs-triage
Type: bug
Found in: TCU 11 (#1628), GK B (65) review.
Owner area: app backend (GK B reviews).
Parent: `11-bundle-import-and-experiment-copy-cost-guard.md`.

## What

`routers/projects.import_bundle` resolves the target name (the `name` query, else the bundle's `metadata.json`
name, else the upload's file name) and then, before checking it, calls `_bundle_network_owning_its_costs(zf)`:
it reads and checks the bundle's `solver_config.json`, writes `network.nc` to a temporary file, parses it with
PyPSA and re-derives every priced row (`network_crud.rederive_imported_parts`). Only after that do the checks
that can refuse the name run:

- `project_registry.require_user(user)`: 401 for an anonymous caller;
- `project_registry.create_root(...)`: 403 from `_org_id_for` when the caller has no org, 409 "Could not find a
  free directory name" from `storage_paths.allocate_storage_path`, and 409 "Project '<name>' already exists"
  from the row insert's `IntegrityError`, which is only found on `db.commit()`.

So a duplicate name, or a caller who cannot create a project at all, pays for the full network parse and
re-derive before being refused, and a bad priced row in such a bundle is reported (422) instead of the name
clash that would refuse it anyway. Nothing is written in either case; the cost is the wasted work and the
misleading first refusal. (The comment there naming `_safe_project_dir` is stale: no such check runs on this
path.)

## Acceptance

- `import_bundle` checks the caller (401/403) and whether the resolved name is free in the caller's org (409,
  same message as today) before any network loading or derive work; the row insert keeps its `IntegrityError`
  backstop for a race.
- Tested: a bundle with a bad priced row imported under a name already taken is refused with the 409 naming the
  name, not the 422, and the open network, project rows and folders are unchanged.
