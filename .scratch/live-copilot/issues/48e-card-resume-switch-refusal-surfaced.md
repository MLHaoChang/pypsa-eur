<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-48e — A card's resume refused by the goal-switch guard is shown, not swallowed

Status: done
Model tier: opus
Size: S
Blocked by: none
Gated: GK B
Likely files:
- pypsa-gui/backend/services/run_envelope.py (the data-request answer path that calls `assistant_tasks.resume_task`, ~2392)
- pypsa-gui/backend/services/assistant_tasks.py (`_refuse_switch`)
Type: task
Parent: [48d-wire-recovery-rows.md](48d-wire-recovery-rows.md) (GK B N-b on #1515)

Since #1515 (owner decision LC48D-GOAL-1), `resume_task` refuses with 409 `switch_in_progress` when the chat session's task is another task that runs. When the user answers a run's data request, `run_envelope` stores the answer and then calls `resume_task`. If another task of that chat runs at that moment, the 409 is caught by the broad `except` there and only logged. The answer stands, but the run it belongs to stays waiting, and nothing tells the user why.

Owner decision LC48E-SURFACE-1: surface the refusal on the card (name the active task); no auto-resume.

## Done when

- The refusal reaches the user: the card or the chat shows that the run waits for the active task, naming it as the 409's `active` does. Alternatively the run resumes on its own once the active task ends; pick one with the owner.
- A test drives the data-request answer while another task of the same chat runs. It asserts the answer is stored, the run is not resumed, and the user-visible signal is present. The test is red before the fix.

## Resolution

Owner decision LC48E-SURFACE-1: show the refusal, no auto-resume. `run_envelope.answering` catches the 409 `switch_in_progress` from `resume_task` and records `run.held` (the 409's `active`) through `assistant_tasks.note_held`; `_run_view` carries it and the RunMonitor's "Waiting for you" block shows `run-held`. The next resume the guard lets through drops it. Tests: `test_a_resume_refused_by_the_switch_guard_is_shown_on_the_run_card`, `test_the_held_note_goes_once_the_run_resumes` (tests/test_run_intake_grant.py); the RunMonitor vitest `held_by_switch`.
