<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Slice 3: run lessons via a dream job

Status: ready-for-agent (blocked by 02, whose UI part 02b is in progress; Opus; GK B plan first)
Blocked by: 02
Type: feature
Model: Opus. Reviewer: GK B.

A durable harness task, modelled on Anthropic's "Dreaming with a dynamic workflow" cookbook (MEMORY-DREAM-1):
- It reviews past chats and runs: an analyzer per transcript, then one organizer.
- It writes proposed lessons, which the user confirms.
- It is budget-capped and provider-agnostic.

## Comments

### 2026-11-11: triage

Checked on master: `services/memory_store.py` exists (02a, #1309); no dream job anywhere in `services` or `harness`. Verdict: ready-for-agent, blocked by 02 (02b merge) and by the durable-task seam named in the plan.

- Blocked by: 02.
- Likely files: a new `harness/` task module for the dream job, `services/memory_store.py`, `services/memory_tools.py`, tests beside them. Reviewer: GK B (plan first).

Acceptance:
- [ ] A durable harness task runs one analyzer per past transcript and run, then one organizer; a fake provider drives it in tests.
- [ ] It writes only proposed lessons; nothing is stored until the user confirms each (COPILOT-1).
- [ ] A token/cost budget cap stops it with a typed reason and keeps the lessons proposed so far.
- [ ] The same code path works with any provider profile (no provider name in the task).
- [ ] A confirmed lesson is a memory item with its source chats and runs.
