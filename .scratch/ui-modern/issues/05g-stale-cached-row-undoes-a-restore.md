<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# A stale cached row in updateAsset can undo a restore's change

Status: needs-triage
Gated: GK B.
Type: bug
Parent: 05d (#1565), GK B2 review of 93dfa2c1.
Blocked by: 05d merged.

`updateAsset` (`pypsa-gui/frontend/src/utils/assetWrite.ts`) spreads the cached row under the card's patch and PUTs the whole row. If a snapshot restore (or an undo, or another tab) has changed the asset since the cache was filled, and the card's form was opened before the refetch, then the PUT writes the stale columns back. For example, a restore moves the asset back to its old bus, the stale row still holds the new bus, and the save quietly moves it again. 05d's `X-Cost-Basis` check refuses this only when the bus unit differs and the save carries a claim. Other columns (bus between two energy buses, efficiencies, bounds) are rewritten silently.

## Done when

- A save whose base row is older than the network's current revision is refused (409, typed) or rebased on the fresh row before it is sent. Which of the two is decided at plan time with GK B.
- A test: restore after the card opened, then Save, leaves the restored columns as they were unless the user changed them.

## Comments

> *Filed by AI from GK B2's review of #1565.*
