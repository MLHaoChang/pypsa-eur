<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SummaryTab: a CO₂ cap with null `binding` reads "not binding"

Status: ready-for-agent
Type: task
Gated: UX

## Scope

SummaryTab shows a CO₂ cap whose `binding` is null as "not binding". It should read "—" / not recorded by this solve: the same null-is-not-a-value class as A1.6/A1.7-b (#879). Add a test for null vs false.

Filed from Assurance 02 (8)'s #879 hand-off (option 1).
