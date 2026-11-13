<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 48 — A mainland bus fed through an untagged Link is not detected

Status: needs-triage
Gated: GK A
Type: bug
Parent: SH 46 (#1461) hand-off follow-up

## Problem

SH 46 (#1461) makes `island_survival.tie_state` return `unit_on_mainland` when a sidecar unit's bus hosts an unlisted, non-slack supply or is the grid side of an `eh_role == "grid_import"` Link. A mainland fed through a Link without `eh_role == "grid_import"` is not detected, so the tie can still read `no_tie`.

## Ask

Decide whether an untagged Link from an unlisted supply counts as a mainland feed, and detect it or refuse with words that name the Link.
