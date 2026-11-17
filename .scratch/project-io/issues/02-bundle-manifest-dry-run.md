# 02 Bundle manifest and import dry run

Status: ready-for-agent
Blocked by: 01

`manifest.json` in the bundle zip: schema version, app version, project kind, and the sha256 and size of each member. Import refuses a corrupted or newer bundle in plain words; an old bundle with no manifest imports with a warning. `POST /projects/import/bundle?dry_run=true` answers what an import would create and writes nothing.

## Comments

GK B (63), 2026-11-17, plan GO with conditions:
- I7. Every member except the manifest is checksummed. A missing or extra member, a size mismatch or a hash mismatch is refused in plain words. The no-manifest path imports with a warning and is tested. A newer `schema_version` is refused.
- I8. The dry run writes nothing: the DB row count and the project folder tree are unchanged, staging is cleaned, and the answer equals what a real import would act on. Same ACL as a real import. A new route variant: run route_chat_disposition, ACL, write-surface, golden_coverage and `route_parity --check` at the trial merge.
- I9. Mutants on each refusal (name, kind, traversal, checksum, schema) and on the cleanup, each killed by its own test; survivors listed in the PR body.
