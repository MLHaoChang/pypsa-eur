<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LV-09: stored refs carry no fingerprint

Status: needs-triage
Type: task
Found by: LV-09 (`09-durable-conversation.md`); owner list DOCS-1.

## Problem

The current/discussed refs survive rotations but store no fingerprint, so a changed target cannot be told from an unchanged one after resume. Store the fingerprint with each ref. Details: ask the coordinator.

## Where to look

The durable conversation's ref store described in `.scratch/live-voice/issues/09-durable-conversation.md` (lines ~35, ~70).
