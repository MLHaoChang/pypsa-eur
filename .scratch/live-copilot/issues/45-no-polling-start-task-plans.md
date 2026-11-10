<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-45 — No polling: renewable-profile and investment-decision use a `start_task` plan

Status: ready-for-agent
Type: task
Model: Sonnet
Reviewers: GK B
Blocked by: none (refines [LC-12](12-job-completion-continuation.md), which is done)
Source: owner decision C1C9-FILE-1 (2026-11-10, "C5, C4, C3"); change C5 of the workflow review (artifact RKkscAxy5yZG6EuxjWH71W, "Walkthrough check"); `.scratch/ui-modern/audit.md`.
Evidence: **confirmed, text only.** The chat was not walked (no API key); the claim was read from the workflow text and the code, not measured. In the panel UI a run shows its own progress, so the cost is in chat only.

## What

Two workflows make the model poll a long job itself:

- `harness/workflows/renewable-profile.md` (generate step, ~line 123): polls `solve_queue_list` until the profile job completes.
- `harness/workflows/investment-decision.md` (verdict step, lines 48-51): runs `run_decision_study`, polls `get_study_findings`, runs stage `tornado`, polls again.

Rewrite both steps as a one-step `start_task` plan, as the sensitivity skill does ("do not poll"), so the conversation continues by itself when the job ends. For investment-decision the plan covers both runs (options, then tornado), so the user is not asked to come back between them.

Add a decision-study adapter to `harness/job_adapters.py` if `wait_for_job` does not reach those jobs yet (the review did not check this; check first).

## Why

Polling costs wasted turns and tokens, and the user must return and ask "is it done?". Plans and continuation already exist (`harness/continuation.py`).

## Done when

- Neither workflow text tells the model to poll; each names the plan step and what the continuation does on completion and on a failed job (the refusal is read to the user).
- A test shows each path continuing without a poll call, including the failed-job path.
- Safety rules stay: the run cards are still the user's own cards (`run_decision_study` approval unchanged); no envelope admission.
- Release-note fragment added.

## Likely files

`pypsa-gui/backend/harness/workflows/renewable-profile.md`, `investment-decision.md`, `harness/job_adapters.py`, `harness/continuation.py`, `harness/skills/sensitivity/SKILL.md` (the pattern), tests under `pypsa-gui/backend/tests/`.
