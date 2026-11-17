<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 56 — An unlisted supply with p_max_pu = 0 in every hour still refuses the island tie

Status: needs-info
Gated: GK A
Type: bug
Parent: SH 49 (#1567) review follow-up

## Problem

SH 49 leaves an unlisted supply out only when its size is exactly 0. A generator whose `p_max_pu` is 0 in every
hour can deliver nothing either, but still marks its bus as the mainland's, so the tie reads `unit_on_mainland`.

## Waiting on

An owner decision, sent to the coordinator as options: (1) leave it out when 0 in every hour of this network
(recommended), (2) keep refusing, (3) leave it out and name it as a warning on the tie row.
