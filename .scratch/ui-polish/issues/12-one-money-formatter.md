<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# One money formatter

Status: ready-for-agent
Type: polish (consistency)
Found by: UX (12), #1194 and #1199 reviews, relayed by the coordinator. This is also DOCS-1's fmtEurShort item, so don't file it twice.

## Problem

`pages/results/GridRiskTab.tsx` (#1194) formats money with its own `fmtEurShort`, while `pages/DcBackupSection.tsx` (#1199) has `money()`. Other money helpers exist across the frontend too (economics, decision, investment). Owner rule UX-RULES-1107: one money notation per section, in the `money()` style.

## Fix

One shared `money()` helper, used by GridRiskTab and DcBackupSection, with a unit test that pins its notation. Migrating the other helpers is a follow-up unless it is trivial. Reviewer: UX.
