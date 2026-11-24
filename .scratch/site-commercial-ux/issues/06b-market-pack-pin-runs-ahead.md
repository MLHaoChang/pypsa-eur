<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06b The market-pack pin can run ahead of the saved config, with no route to reset it

Status: needs-triage
Blocked by: K8a backend (#1932)
Source: GK B4 (4) residual on #1932 at 95933e62 (not blocking K8a).
Review: GK B.

## Problem

Applying a market pack writes the pin (`market_pack.json`, `services/commercial/market_packs.py` `write_pin`) at apply time. The config change it pins persists only on save or autosave. When the user discards the change, the pin holds cells the config never kept:

- This fails closed: those cells read as user edits (conflicts), so no value is overwritten.
- A product the apply added and the discard dropped reads as "removed by the user" and is never re-added by a later apply of that pack.
- No route resets the pin, so the user cannot get back to a clean first apply.

## Options to weigh

1. Write the pin with the config's save (same transaction) rather than at apply.
2. A reset route (`DELETE .../market_packs/pin`) plus a "Forget what the pack applied" action on the card.
3. On read, drop pin cells whose config value never matched the pinned value since the apply.

## Done when

A discarded apply leaves no pin that blocks a later apply from adding its products, or the user can reset the pin from the card. A test covers apply → discard → apply.
