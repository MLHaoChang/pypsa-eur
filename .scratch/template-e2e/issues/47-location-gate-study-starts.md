<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 47 — The location run gate does not cover the study-family starts

Status: done (PR: STUDY-GATE-1 follow-up) (owner decision STUDY-GATE-1, 2026-11-27: block studies too; build after #2082 merges)
Owner: unassigned
Size: S
Follows: 01 (PR #2082)

## What

#2082 refuses an unlocated project at `POST /run`, `POST /run_ac_pf` and the reliability study start (`check_eh_study_start`), with one predicate (`services/site_location.py`). These starts do not go through `/run` and still solve an unlocated project:

Corrected list (the first draft named `run_evidence.start_study_run`, which only writes an evidence record and is not a start). Gated in the runner, on the base project's network, after the base is resolved and before the busy check, ownership mark, run record and thread:

- `POST /api/studies/{id}/run` -> `services/study/runner.py` `start_study_run`
- `POST /api/studies/{id}/findings/tornado` -> `services/study/tornado_runner.py` `start_tornado`
- `POST /api/studies/{id}/supply-architectures` -> `supply_architecture_runner.start` (and the chat comparison tool)
- `POST /api/studies/{id}/system-type-map` -> `system_type_map_runner.start`
- chat `run_sensitivity_sweep` -> `services/workflow_tools.py`

The live-network runners are issue 48.

## Acceptance

- Decided (STUDY-GATE-1, extending TEMPLATE-GAPS-1 Q1 a): a study solves the same unlocated network, so studies count as Run. One gated start per study runner; each start refuses with the 422 typed `site_location_missing` (same predicate, on the study's base project) and has a test; the flow's verdict for them follows.

Noted with it: while the location is missing the study start refuses on location first, so the flow hides the study's other refusals until the location is set.
