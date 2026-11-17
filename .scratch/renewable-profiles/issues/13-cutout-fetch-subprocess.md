<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-13 — fetch ERA5 cutout months in a subprocess

Status: done (#1569)
Model: Opus
Type: task
Baseline: branch `claude/cutout-netcdf-lock` (the cutout cache's month check and merge under the process netCDF lock)
Raised by: the engine gatekeeper, reviewing the netCDF lock fixes (#468 and its cutout follow-up), 2026-10-20

netCDF4/HDF5 is not thread-safe per process. Pending profile I/O, project save/load and the cutout cache's
month check and merge now take `PyPSAService.get_netcdf_io_lock()`. The month **download** does not: a month
can take minutes, and holding the process lock that long would stall every project save.

`cutout_cache._download_month` calls `atlite.Cutout(...).prepare()` on a daemon thread of the queue worker.
`prepare()` does not just fetch bytes: atlite opens the CDS download and writes the month through
xarray/netCDF4 **in this process**, so its HDF5 calls can still collide with a save, a pending write or
another reader, which crashes the process.

## What to build

- Run each month's fetch (`_download_month`) in a subprocess (`multiprocessing`, `spawn` context). It
  gets its own HDF5 state, so it needs no lock.
- The parent keeps the timeout (kill the child), the abort `stop_event` (terminate the child), the
  typed errors (pass the exception kind and message back over a pipe), and the resume rule
  (`_valid_month` before merging).
- The parent merges the months under the lock, as today.

## Done when

- A test runs a month fetch, faked to write through xarray in the child, beside network save/load threads
  and pending writers, with no crash. It crashes when the fetch runs in-process.
- The CDS timeout and abort still end the job with their typed errors.

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role ready-for-agent

Was: `needs-triage`. Files: `cutout_cache._download_month` and its queue worker in `pypsa-gui/backend/services/` (spawn-context subprocess, pipe for typed errors, timeout kill and stop_event terminate), tests with a fake fetch. Blockers: the branch `claude/cutout-netcdf-lock` fixes must be on master first (check `git log origin/master`). Reviewer: GK B. Opus because process and lock safety is easy to get wrong.

### 2026-11-17: plan GO from GK B2, with conditions (session_01G7qCukJE2zt5D3dN1X1BbL builds)

- R1. The committed crash test keeps only the subprocess leg (exit 0, complete cutout). The in-process segfault is nondeterministic, so it stays as evidence in the PR body.
- R2. Pin "the default downloader runs in a child" by pid: a picklable fake patched in as the default writes `os.getpid()`. It differs from the parent's pid for `downloader=None` and equals it for an injected downloader with `fetch_in_subprocess=None`. The mutant "default → False" goes red.
- R3. Exit race: a child that sends ok and exits at once reads as ok (20 repeats). A child that calls `os._exit(3)` reports exit code 3.
- R4. Typed errors are rebuilt by exact class (module + qualname), never by bare name. A foreign `CutoutDownloadError` takes the generic path. Each table entry round-trips its type and message.
- R5. After timeout or abort the child is dead (`not is_alive()`, exitcode set). The partial month is never counted, and the next run re-fetches it. Abort keeps earlier valid months byte-identical.
- R6. "ok but no readable data" → CutoutDownloadError on both the thread and the child path.
- R7. The child sees CDS credentials set in `os.environ` after import.
- Each guard gets a targeted mutant on its own test, listed in the PR body.

GK A input (the same day): the child writes to a staging name (`mNN.fetch.nc`), and only the parent renames it after `_valid_month`. The child target is a module-level function. Credentials go by env only. The pipe and process are closed on every path, and no child is left (`active_children()` empty).
