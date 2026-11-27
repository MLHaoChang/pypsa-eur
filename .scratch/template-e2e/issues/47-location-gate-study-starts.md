<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 47 — The location run gate does not cover the study-family starts

Status: needs-triage (owner decision: do studies count as "Run" for TEMPLATE-GAPS-1 Q1 a?)
Owner: unassigned
Size: S
Follows: 01 (PR #2082)

## What

#2082 refuses an unlocated project at `POST /run`, `POST /run_ac_pf` and the reliability study start (`check_eh_study_start`), with one predicate (`services/site_location.py`). These starts do not go through `/run` and still solve an unlocated project:

- `routers/studies.py` `start_study_run` (~l.870)
- `routers/studies.py` `start_tornado` (~l.1493)
- the chat `start_supply_architectures` (`services/chat_tools.py` ~l.4158)
- `run_evidence.py` `start_study_run` (~l.1354)

## Acceptance

- If studies count as Run: each start refuses with the 422 typed `site_location_missing` (same predicate, on the study's base project) and has a test; the flow's verdict for them follows.
- If not: record the decision and the exception in `services/site_location.py`.

Noted with it: while the location is missing the study start refuses on location first, so the flow hides the study's other refusals until the location is set.
