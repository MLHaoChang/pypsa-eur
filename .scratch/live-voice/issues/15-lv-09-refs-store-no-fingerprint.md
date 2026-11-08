<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LV-09: stored refs carry no fingerprint

Status: ready-for-agent
Type: task
Found by: LV-09 (`09-durable-conversation.md`); owner list DOCS-1.

## Problem

The current/discussed refs survive rotations but store no fingerprint, so a changed target cannot be told from an unchanged one after resume. Store the fingerprint with each ref. Details: ask the coordinator.

## Where to look

The durable conversation's ref store described in `.scratch/live-voice/issues/09-durable-conversation.md` (lines ~35, ~70).

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: durable conversation ref store (`09-durable-conversation.md` ~35, ~70).
- Blocked by: none.
- Model tier: sonnet.
- Gated: GK B.
- Size: M.
- Store the fingerprint with each current/discussed ref and compare on resume; test a changed target.

### 2026-11-08: plan GO WITH CHANGES (GK B, L1-L5)

- `set_discussed_run` takes `fingerprint`, `revision`, `status`, `mode`; `discuss_project_run` passes the keys present in the server's `get_run_evidence` summary (never a model argument, no schema change).
- L1: each call sets the four anchors from that call and pops any it omits, so run-b never inherits run-a's. L2: an absent key is left off, never stored as None. L3: one test per anchor reads `changed`. L4: stored anchors equal the server read. L5: mutants (pop removed, anchor dropped, None stored) each fail a test.
- The `current` ref (free-form via `checkpoint_thread(refs=...)`) stays unanchored; not changed here.
