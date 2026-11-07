<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 26 — Plan card names the held tariffs a run may attach

Status: done
Model: Sonnet
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

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role ready-for-agent

Was: `needs-triage`. Frontend change, read from the envelope the card already renders. Files: the plan card in `pypsa-gui/frontend/src/` (Study run plan card) and its test; the envelope field is `run_envelope._library_tariffs`. Blockers: none. Reviewers: GK B (plan) and UX (card).

### 2026-11-07: built (SH-26 session), premise corrected

The "read from the envelope the card already renders; nothing new is fetched"
premise was wrong: `run_envelope.describe()` stripped `at_grant` from gaps, the
frontend `Envelope` type had no `gaps`, and `at_grant` is `{}` until the grant,
so the card carried no held tariffs at all. Coordinator chose to add a
read-only `held` / `held_state` to each plan-state `library_tariff` gap in
`describe()` (`_held_for_plan`, same `_library_tariffs` call the grant uses) and
render it in `EnvelopeSection`. GK B (32) plan GO with K1-K6: only before
approval, an unreadable Library is `unreadable` and not "none", an id-less held
tariff is `unnamed`, lines read `name vN · hash8`, absent fields render as
before, and the grant path (`_gap_instances`, `_rows`) is untouched. The card
says "held now" because the grant may resolve a different list. Shots:
`.scratch/study-harness/evidence/26/`.
