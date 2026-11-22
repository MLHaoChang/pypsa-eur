<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# site_scene blender_worker Popen deadlocks (fork in multithreaded process), stalling CI shards

Status: needs-triage
Type: bug
Owner area: app backend (site_scene; GK B reviews).

## What

`pypsa-gui/backend/site_scene/blender_worker.py:447` starts the Blender worker with `subprocess.Popen`. The caller then blocks in
`os.read(errpipe_read)`, waiting for the child to exec or report an exec error. The forked child never reaches exec: it was
forked while another thread in the backend process (for example the solve-queue worker) held a lock, so the child inherits that
lock locked and waits for it forever. The parent waits for the child, so both hang.

## Symptom

- `solve_queue.reset_for_tests` cannot drain the queue within 30 s.
- The stuck child holds the write end of the `| tee pytest.log` pipe, so `tee` never sees end of file and the CI step hangs
  until the job is cancelled.

## Evidence

- #1791 CI, "GUI backend shard 4/8" on 53b746d7: `test_site_scene_blender_job.py::test_a_second_job_while_one_is_active_is_409`.
  The artifact `gui-backend-shard-4-of-8` holds `solve-queue-stall-gw0.txt`.
- Earlier: shard 2/4 on 88efa474.
- A related shard-2 hang is under diagnosis in session_01G7qCujszGqCLzo1wWgpN3j.

## Fix in progress

Fix in progress (session_01G7qCujszGqCLzo1wWgpN3j); a PR is expected. This file stays `needs-triage` until the owner picks a
direction.

## Directions (owner to choose)

1. Fork-safe spawn: `start_new_session=True` plus `close_fds=True`, or `posix_spawn` / a forkserver, so the child never runs
   Python code before exec.
2. Spawn the worker before the backend's worker threads start.
3. Either way, add a spawn timeout so a stuck spawn fails the job with a clear error instead of blocking the caller.

## Constraint

Do not harden `test_site_scene_blender_job.py` (owner constraint). The fix belongs in the spawn code.
