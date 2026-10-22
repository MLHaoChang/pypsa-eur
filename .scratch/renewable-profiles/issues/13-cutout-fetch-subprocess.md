<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-13 — fetch ERA5 cutout months in a subprocess

Status: needs-triage
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
