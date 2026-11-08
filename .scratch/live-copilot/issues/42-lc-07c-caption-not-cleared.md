<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-07c: the speech-notice caption is not cleared

Status: in-progress
Type: bug
Found by: LC-07c (`07c-speech-notices-caption.md`); owner list DOCS-1.

## Problem

The notice stays after the condition that raised it has ended. Notices clear when their condition ends (review-ux). Clear it on the next admitted final or turn start. Details: ask the coordinator.

## Where to look

The Live caption component that shows the speech notice (see `07c-speech-notices-caption.md`).

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: Live caption component of the speech notice (`07c-speech-notices-caption.md`).
- Blocked by: none.
- Model tier: sonnet.
- Gated: UX (12).
- Size: S.
- Clear on the next admitted final or turn start; test it.
