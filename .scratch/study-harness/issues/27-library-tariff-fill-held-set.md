<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 27 — Library-tariff fill: held set counts shipped tariffs, and names read as "None"

Status: done
Type: bug
Blocked by: 04
Gated: yes (GK B)
Model tier: opus
Design: [design.md](../design.md) §6.1 (input gate loop L1, data-gap fills)

Found by the FILL-10-6 builder while building #1097; GK B (20) verified the reasoning (not run). It touches the same area as issue 26 (the plan card naming held tariffs), so it may be folded into that work.

## Defect (master, `pypsa-gui/backend/services/run_envelope.py`, `_library_tariffs`)

1. **Shipped tariffs count as held.** The function lists the shipped common tariffs as held by the organisation. Two ship today, so a grant always sees three or more held tariffs, and an L1 `attach_tariff` fill is never admitted: it is refused `not_granted_source` ("both held, you choose"). The `library_tariff` data-gap fill is dead in practice.
2. **Names read as "None".** It reads `ref["name"]`, but a `LibraryItemRef` carries `id`, so every held tariff is named "None" and the plan row reads "None v1 and None v1 are both held".

## Acceptance

- The held set counts only the tariffs the organisation itself holds (decide with GK B how shipped common tariffs count; record it in Comments), so a project with exactly one held tariff gets an admitted `attach_tariff` fill.
- Held tariffs are named from the ref's `id` (or the item's display name), never "None"; the plan row names them.
- Tests: one held tariff → fill admitted; two held → refused `not_granted_source` with both real names; shipped-only → per the GK B ruling. Each test is shown red against the current code first, and mutation-checked against the guard it covers (GK B candidate 11).

## Comments

- GK B (20) plan verdict GO WITH CHANGES, confirmed by GK B (21). Q1 A: only the organisation's own tariffs are held. A shipped common tariff is left out when its ref equals the shipped ref exactly (kind, id, version, hash), so shipped-only means nothing is held. Q2 A: issue 26 stays separate.
- R1: an own tariff that shares a shipped id but has a different hash or version still counts. R2: a held ref with no id fails closed, and no row reads "None". R3: every new test is red on master and catches the mutation of its own guard (PR #1109). R4: the `common.names` monkeypatch is removed from #1097's stepwise test. R5: the change stays inside `_library_tariffs`.
- R2 deviation, put to GK B (21): a ref with no id is refused as `new_instance` (the grant records no tariff), not as `not_granted_source`.
