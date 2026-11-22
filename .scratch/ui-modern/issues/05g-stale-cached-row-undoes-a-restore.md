<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# A stale cached row in updateAsset can undo a restore's change

Status: done
Gated: GK B.
Type: bug
Parent: 05d (#1565), GK B2 review of 93dfa2c1.
Blocked by: 05d merged.

`updateAsset` (`pypsa-gui/frontend/src/utils/assetWrite.ts`) spreads the cached row under the card's patch and PUTs the whole row. If a snapshot restore (or an undo, or another tab) has changed the asset since the cache was filled, and the card's form was opened before the refetch, then the PUT writes the stale columns back. For example, a restore moves the asset back to its old bus, the stale row still holds the new bus, and the save quietly moves it again. 05d's `X-Cost-Basis` check refuses this only when the bus unit differs and the save carries a claim. Other columns (bus between two energy buses, efficiencies, bounds) are rewritten silently.

A second path, which 05d's U1 rule creates. A card save that only moves the bus sends the costs from the cached row and no `X-Cost-Basis`, so the server does not check them. If a restore changed the stored costs or their bus unit after the cache was filled, the stale cost numbers are written back under the new bus with no refusal.

## Done when

- A save whose base row is older than the network's current revision is refused (409, typed) or rebased on the fresh row before it is sent. Which of the two is decided at plan time with GK B.
- A test: restore after the card opened, then Save, leaves the restored columns as they were unless the user changed them.
- A test of the bus-only move after a restore: the costs the restore wrote stay as they are.

## Comments

> *Filed by AI from GK B2's review of #1565.*

**Done in #1859 (GK B4 conditions).**
- Rebase decision: client-side, no backend change (a 409 would fire on every unrelated write; there is no per-asset revision). The PUT is `{...freshRow, ...userChange}`.
- Initial-patch compare: the user's change is the keys where the patch differs from the same builder's result for the form's initial values (`useEditBase`), compared with `Object.is`, no tolerance. Tested on all seven cards.
- Derived columns: the server does not re-derive a generator's capital cost on PUT; StorageUnit and Line omit derived totals; the others keep the fresh stored total.
- At-open basis: the claim and the scaling use the schema the card opened with. This reverses GK B2's T3 ruling (see 05d Comments). The scaling is tested on all seven cards, the claim on the four cost cards.
- No cache fallback: a failed fresh read stops the save; nothing is PUT from the cached row.
- Residual window: a write between the fresh GET and the PUT is still overwritten. After a units flip the labels follow the live schema while values are claimed at-open, and the save is refused.

## Triage (2026-11-20, master 87cf52a7)

Checked by code reading only. Still true: `assetWrite.ts` sends no `X-Cost-Basis` and PUTs the cached row. Blocked by 05d: merged (#1565), so unblocked. Refuse (409) or rebase is decided at plan time with GK B, as written. Gated: GK B.
