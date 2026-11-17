<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-21: one fetch per cutout key when solves run concurrently

Status: needs-triage
Type: task
Raised by: GK B2 reviewing RP-13 (#1569), finding F2, 2026-11-17. The gap is already on master; #1569 does not add it.

`ensure_cutout` has no per-key lock. With `PYPSA_GUI_MAX_CONCURRENT_SOLVES` > 1, two profile jobs for the same
bbox and year can each fetch the same month into the same `<key>.parts/` directory. Since #1569, both write
`mNN.fetch.nc` and rename it to `mNN.nc`, and both merge into `<key>.nc`. One job can unlink the other's staging
file, or replace a month while the other merges. The default of 1 concurrent solve hides this.

## What to build

- Hold a lock per cutout key (process-wide, keyed by `<key>`) across the month loop and the merge in
  `services/profile_gen/cutout_cache.ensure_cutout`. The second job waits, then finds the complete `<key>.nc`
  (a cache hit) or resumes the valid months.
- The wait must still honour the caller's `stop_event` (abort while waiting).

## Done when

- A test runs two `ensure_cutout` calls for the same key on two threads, with a module-level fake downloader in a
  child. Each month is fetched once, both calls return the same `<key>.nc`, and nothing raises.
- Aborting the waiting call ends it with `CutoutDownloadAborted` without disturbing the running one.
