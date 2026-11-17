<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Current-view write: refusal test and utterance generation under the lock

Status: ready-for-agent
Type: task
Blocked by: none (follows #1657, live-voice 03a)

GK B 65 follow-ups from the #1657 review (GK OK @ddd4f75f).

- N1: add a test that a turn refused before its user message is kept (vision or
  capability refusal in `harness/loop.py`) records nothing in `refs.current`.
  Today the mutant that moves the `record_current_view` call back above the
  refusals survives.
- N2: `obsolete()` is read at the write site in `loop.py` outside
  `durable_conversation._LOCK`. A barge-in that bumps only the utterance
  generation in that window can still land one stale view. Either check the
  utterance generation under `_LOCK` inside `record_current_view`, or record
  the residual risk here with the reason it is acceptable.

Files: `pypsa-gui/backend/harness/loop.py`,
`pypsa-gui/backend/services/durable_conversation.py`,
`pypsa-gui/backend/tests/test_lv03_current_view.py`. Reviewer: GK B.

## Comments
