<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 13 After a hard kill, the dispatch progress record stays and its elapsed time keeps growing

Status: needs-triage
Blocked by: none
Source: follow-up from 08, GK A review of #1816 (follow-up 1).

## Problem

08 writes a live `progress.json` (bound to the run by the nonce in `progress.run`) and removes both in `Progress.close()`, which runs when the run ends, whether it finished or failed. A hard kill (SIGKILL, OOM, container reclaimed) skips `close()`. Both files stay, the nonces still match, and `status.py` keeps overlaying the record on the running stage, so `elapsed_s` (now − `stage_started_at`) keeps growing for a run that is dead.

## Acceptance criteria

- [ ] A record whose run is no longer alive is not overlaid: either a liveness bound (for example `updated_at` older than a stated limit while no queue job holds the project) or a check that the worker is alive.
- [ ] A test leaves a record with a matching nonce and no live run, and the status shows no growing elapsed time.

## Likely files

`gridspine/drivers/status.py` (`_live_record`), `gridspine/drivers/progress.py`, the gridspine queue job in `pypsa-gui/backend`.
