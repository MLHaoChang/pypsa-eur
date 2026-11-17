# 01 Project contents table, missing files, safe bundle import

Status: ready-for-agent

One table (`services/project_contents.py`) says which files and folders make up a project and which operations carry each one: bundle export/import, Save-As, scenario fork, snapshot. The existing lists in `routers/projects.py`, `routers/snapshots.py` and `services/study/forks.py` are derived from it.

- Carry what is dropped today: `eh_template.json`, `cases.json`, `gridspine/` (snapshot and fork leave out `gridspine/run/`), the importer sidecars and `import_report.json`, and the project kind (in `metadata.json`).
- `experiments/` stays excluded on purpose (its plans name other projects).
- `import_bundle` stages, checks and refuses before it creates the project row; a late failure removes the row and the folder.
- `import_bundle` runs `network_crud.rederive_imported_parts` (TCU 10), as the upload path does.

## Comments

GK B (63), 2026-11-17, plan GO with conditions:
- Q: A. `gridspine/run/` travels with the bundle and with Save-As, not with snapshots or forks. A copied run reads current only through the normal stale checks, never because it was copied; test that it reads stale after an edit.
- I1. `project_kind` comes from `metadata.json`, checked against the allowed kinds. An unknown, missing or malformed kind follows one stated rule (a plain refusal, or the column default with a warning). Never a 500. The kind never widens access or features beyond what the importing user already has.
- I2. Safe staging. Refuse absolute paths, `..`, drive letters, symlinks, members that collide once folded (case, trailing dot or space), and an uncompressed total above a stated cap. Test each case. Extract only members the table allows; report any other member and never write it.
- I3. Cleanup. A failure injected at each step after `create_root` leaves no row and no folder, and a retry succeeds. Two concurrent imports with the same name: one wins, the other gets a clean 409, no orphan.
- I4. The guard test works both ways. Every `SIDECAR_NAME`/`*_FILE` constant is in the table or on the excluded list, and every table entry is written by some writer. `experiments/` stays excluded, with its existing pin.
- I5. `bundle_version` changes once. Snapshots and bundles taken before this change still restore (a test for each). Restoring an old snapshot that doesn't list the new entries leaves the project's current copies in place.
- I6. Template native byte-identity, `test_bundle_provenance` and the snapshot and fork suites pass at the trial merge.
- I10. Call `network_crud.rederive_imported_parts` itself, never a fork, on the staged network before `create_root`. Tests: a non-finite part scale is a 422 with no row and no folder; a stale `capital_cost` is re-derived; a mutant that drops the call is killed by both. The PR body states whether snapshot restore or Save-As can bring back a priced composite row without this guard; if one can, guard it here or file it.
