<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-07c: the speech-notice caption is not cleared

Status: needs-triage
Type: bug
Found by: LC-07c (`07c-speech-notices-caption.md`); owner list DOCS-1.

## Problem

The notice stays after the condition that raised it has ended. Notices clear when their condition ends (review-ux). Clear it on the next admitted final or turn start. Details: ask the coordinator.

## Where to look

The Live caption component that shows the speech notice (see `07c-speech-notices-caption.md`).
