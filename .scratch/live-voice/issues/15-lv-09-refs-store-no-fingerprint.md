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
