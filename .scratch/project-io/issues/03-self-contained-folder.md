# 03 Self-contained project folder

Status: ready-for-agent (library part; weather scope open)
Blocked by: 01, 02

A project folder (and so its bundle) holds everything it needs to open and solve on another machine, without the org Library or the shared weather cache.

- **Pinned library items are copied in.** Today `library_refs.json` (`services/library/bundle_pins.py`) holds `{kind, id, version, hash}` only; the bytes stay in `<projects_root>/.library/<org>/` (series `*.csv.gz`, items `items/<kind>/*.json`). The project gets a `library/` folder with the pinned version of each series and item, under the same hash. Loads read the project copy; `check_pins` checks the copy against its pin, not the org Library. A bundle carries `library/` (project_contents.TABLE, all operations).
- **Weather: what the project used is kept in the project.** Cutouts live in the shared cache (`services/profile_gen/cutout_cache.py`); the project holds only the generated series (`user_ts.json`) and `profile_weather` in metadata.json. Scope: see the open question below.
- **"Newer in library" notice.** When the org Library has a later version of a pinned item, `load_project` reports it (`library_issues`, a new reason `newer_in_library`, with the pinned and the latest version). The project keeps its pinned copy until the user re-pins; nothing changes on its own.
- **Migration.** An existing project with pins and no `library/` copy gets the copies on its next load, from the org Library, when the pinned version and hash are still there. A pin whose version is gone stays `library_ref_stale` (`missing`), as today, and the project still opens. The migration is idempotent and writes only `library/`.

## Open questions

- Weather scope (to the owner): keep the generated series only (today's state, plus the cutout's bbox, year and sha256 recorded in the project), or also copy the cutout cells the project used, so profiles can be re-generated offline.

## Comments

GK B3, 2026-11-17, plan GO with changes (library part):
- L1. Pin id, version and kind are untrusted: strict pattern (no `/`, `\`, `..`, NUL; length cap), paths resolved inside `library/`; on import, export and migration. A traversal-shaped pin in a bundle is refused, leaving nothing.
- L2. A copy is used only when its bytes hash to the pin; else the org Library at the pinned hash, else `missing`, and the issue names the tampered copy. One resolver for loads, check_pins and the import check.
- L3. Migration writes are atomic (temp + rename), race-safe, and touch no dirty flag, fingerprint, provenance, freshness or updated_at. A viewer's or non-lock-holder's load only reads; an editor holding the lock fills the copies.
- L4. Import into another org: copies serve this project only; nothing written to the target org's Library; no cross-org lookup.
- L5. Import verifies each copy against its pin before the row exists. A pin with no copy stays `missing`; a copy with no pin is an extra member (name which check refuses it).
- L6. `library/` in all four operations and in snapshot restore.
- L7. `newer_in_library` is read-only: pin, `library/` and `library_refs.json` bytes unchanged after put_item and a load.
- L8. PR body: Journey fit line; mutants on bundle_pins, the readers and the import check; targeted mutants (drop the copy hash check, drop the path pattern, migration writing when not lock-holder).
