<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 26 — Plan card names the held tariffs a run may attach

Status: needs-triage
Type: task
Blocked by: none
Gated: yes (GK B reviews the plan; the UX reviewer reviews the card)
Owner decisions: FILL-10-6
Design: [design.md](../design.md) §7; [04](04-input-gate-agent-loop.md) (gaps grant, `library_tariff`)

## Why

GK B (18) noted on #1072: the envelope's `library_tariff` gap lists the
tariffs the project's organisation holds, by hash, at the grant
(`run_envelope._library_tariffs`), but the plan card the user grants from
does not name them. The user grants an `attach_tariff` fill without seeing
which tariffs it may choose from.

GK B (19) placed this outside the FILL-10-6 backend follow-ups (SH-04 ledger
rule, SH-22 data-gap tools settling from their own receipt): it is a
frontend change with its own UX review.

## What to build

- The plan card shows, under the `attach_tariff` gap, the held tariffs the
  grant resolved (name and short hash), read from the envelope the card
  already renders; nothing new is fetched.
- An empty list says so ("no Library tariff held: the gap stays open").

## Done when

- A frontend test renders a plan card with two held tariffs and with none.
- The UX reviewer has passed the card.
