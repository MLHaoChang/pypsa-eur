<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 44 Expert: the "Study finished — view report" chip overlaps the report stat line

Status: needs-triage
Source: template-e2e 11 builder (session_01G7qCuic1ojSXRNM6zEx24j), seen while taking #1948's shots; predates #1948.

## What

In Expert, after a study finishes, the "Study finished — view report" chip sits on top of the report's stat line, so part of the line cannot be read. Find the shot in `.scratch/template-e2e/shots/11/`, confirm on master, and fix the layout so the chip and the stat line do not overlap, in light and dark.

Suggested: Sonnet, UX (28) review.
