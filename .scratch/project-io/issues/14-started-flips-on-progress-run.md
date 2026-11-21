<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 14 `started` in the study status now flips on `progress.run`, with no test

Status: needs-triage
Blocked by: none
Source: follow-up from 08, GK A review of #1816 (follow-up 2).

## Problem

`gridspine/drivers/status.py` reads a run directory as started when it holds any file: `started = outdir.is_dir() and any(outdir.iterdir())`. Since 08, `Progress` writes `progress.run` (and `progress.json`) as soon as a run begins, before any stage artifact exists. That file alone now makes `started` true, so the first stage reads `running`. This is likely right while the run is alive, but it is untested. After a hard kill (see 13), the leftover file keeps the study reading as started.

## Acceptance criteria

- [ ] Decide whether the progress files count as "started" and say so in the code.
- [ ] A test pins the choice: a directory holding only `progress.run`/`progress.json` gives the decided state.

## Likely files

`gridspine/drivers/status.py`, `tests/gridspine/test_study_progress_record.py`.
