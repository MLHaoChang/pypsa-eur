<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 12 — The study's network revision is read under a separate lock hold from its copy

Status: needs-triage
Owner: unassigned (GK B non-blocking note on #1645; predates it)
Size: S
Gated: GK B
Likely files: pypsa-gui/backend/services/adequacy/eh_study_runner.py (~l.458, `network_revision` capture); pypsa-gui/backend/services/adequacy/eh_study.py (~l.1205, the copy)
Journey fit: run → results (the "edited since" marker).

## Problem

`network_revision` is captured under one acquisition of the network lock, and the study's private copy is made under another. An edit that lands between the two is in the copy but not in the revision. The result can then read "edited since" when it is not, or miss an edit that is in the copy.

## Acceptance

- The revision and the copy are taken in one lock hold, and a test pins it (an edit between the two must not split them).
