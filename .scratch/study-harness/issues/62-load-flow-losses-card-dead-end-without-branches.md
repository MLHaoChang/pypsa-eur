<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 62 Load flow "Transmission losses" card on a network with no branches says "Not modelled … Turn on in Solver settings", a dead end

Status: needs-triage
Blocked by: none (related: 61, the AC power-flow switch on the same tab)
Source: UX 3 follow-up from #1801 / SH-61.

## Problem

On a branchless network (no lines or transformers; an Energy Hub site, for example) Results › Load flow shows the card
"Transmission losses: Not modelled … Turn on in Solver settings" (`frontend/src/pages/results/LoadFlow.tsx`, the
"Transmission losses" sections near lines 875 and 916). Following the advice leads nowhere: there are no branches to lose
energy on, so turning the setting on changes nothing and the card reads the same after the next run.

## Acceptance

- On a network with no branches the card does not tell the user to turn something on. It says there is nothing to model
  (no lines or transformers), or is hidden, matching what issue 61 settles for the AC power-flow switch on the same tab.
- On a network with branches and losses off, the "Turn on in Solver settings" advice stays.

## Notes

Not yet triaged: confirm the wording and the hide-or-explain choice with the UX reviewer, and do it together with 61 so the
tab says one thing.
