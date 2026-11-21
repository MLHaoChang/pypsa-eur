<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05 — After a solve the user's own project goes read-only: "Another user is editing"

Status: needs-triage
Impact: medium
Templates: Ammonia (Guided run, switch to Expert, Run LOPF) and Concentrator (reopened after an API restart)
Found by: template walk, 2026-11-21 (master 6b421b06)
Shot: ../shots/conc-lock.png

## What

No other user was signed in (one browser, one account). Twice, a few seconds after an Expert Run LOPF finished, the page showed "Read-only — Another user is editing this project — your changes are disabled until the lock is released". Run LOPF was disabled, edits could not be saved, and the API log shows `POST /api/projects/<name>/lock 409 Conflict` (and `lock/heartbeat 409` on the concentrator).

- Ammonia: Guided › Run study (about 3 min), switch to Expert, Run LOPF (32 s, upper bound): banner. It stayed for more than 2 minutes. Clicking another project tab and then the project tab again cleared it, and the 3 unsaved changes were kept.
- Concentrator: after I restarted the API (to stop the run in `template-e2e` 12) and reopened the project, a 280 s Run LOPF ended in the same banner; it cleared about 2 minutes later (`project_locks.py` TTL is 120 s). The IPP project (10 s solves, one page) never showed it.

I do not know whether the lock is lost because the Guided page and the Expert page are two sessions of the same user, or because a long solve delays the heartbeat. The banner names neither.

## Why it matters

"Another user" is wrong for the user's own session, the Run button is dead, and nothing says when it clears or that switching project tabs does.

## Suggested fix

Say who holds the lock and when it expires, let the same account take it over, and keep the heartbeat alive during a solve. Related: `template-e2e` 25 (read-only during a run).
