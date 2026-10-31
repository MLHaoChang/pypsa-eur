<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-11 — Readers of split (Store + Link) storage

Status: in progress (slice 1)
Type: task
Blocked by: 02
Reviewer: GK A (adequacy) + GK B (results)

Spec: [spec.md](../spec.md). Raised by GK B's plan review of AS-02 (condition 9).

## Scope

AS-02 models storage as a `Store` with charger and discharger `Link`s (`services/solver/split_storage.py`). No
project is migrated automatically, so today's projects are unchanged. A storage built this way is invisible to the
readers that take batteries only from `StorageUnit` (`p_nom x max_hours`):

- adequacy Monte Carlo dispatch `services/adequacy/mc.py` (and `mc_zonal.py`, `elcc.py`, which reuse it);
  `portfolio.py`, `site_peak.py`, `smoothing.py`, `redundancy.py`, `levers.py` (`storage_duration`),
  `eh_readiness.py`, `activity.py`;
- `services/compare/storage_cycling.py`, `compare/dispatch.py`, and the dispatch walk of `compare/economics.py`;
- `services/results/dispatch_digest.py`, `results/billing.py`, `results/load_shape.py`;
- `services/asset_results/compute.py` energy capacity for `StorageUnit` (the Store metric set is separate).

Results views, the cost breakdown, carrier KPIs, emissions, the investment case's capex (class-agnostic asset
table) and `compare/capacity.py` already read Stores.

## Acceptance

- [ ] One helper lists every storage as (power MW, energy MWh, efficiencies) from both layouts.
- [ ] The adequacy Monte Carlo and ELCC count a split storage like the equal `StorageUnit` (test: same ENS).
- [ ] Storage cycling and compare dispatch show a split storage (test with the AS-02 hand case).

## PRs

## Comments

- 2026-10-31, slice 1 (owner decision AS11-SLICE-1): one helper `services/solver/storage_inventory.py`
  (`list_storages`, `split_ac_flows`) lists both layouts; the adequacy Monte Carlo (`mc.snapshot_inputs`, which
  `mc_zonal.py` via `eh_stages` and `elcc.py` reuse), `compare/storage_cycling.py` and `compare/dispatch.py` read it.
  Tests: `pypsa-gui/backend/tests/test_split_storage_readers.py`. Acceptance boxes 1–3 are met by slice 1.
- GK A plan GO for slice 1 (2026-10-31), conditions: (1) split P = min(charger size, discharger size x
  efficiency), named in a note when they differ by > 1e-6 relative; (2) Link `p_max_pu` of both Links derates P
  (lowest value per block); (3) E follows the StorageUnit's max-over-blocks P ratio, migrated-equivalence ENS/LOLE
  bit-identical; (4) `StorageEntry` carries `standing_loss` (the MC ignores it in both layouts); (5) no silent
  skips: a split missing a Link and an untagged Store + Links are named in `MCInputs.storage_notes` and the MC
  result's `storage_notes`; (6) cycles = throughput / (2 x E), throughput = charger p0 + |discharger p1|;
  (7) the StorageUnit path is unchanged.
- GK A ruling on PR #1004 (option a): the split reading applies both Links' `p_max_pu`; a split storage that a
  Link's `p_max_pu` of 0 blocks throughout (a migrated discharge-only unit) is named in `storage_notes`, not
  counted. Follow-up: the StorageUnit reader ignores `p_max_pu`/`p_min_pu` (SU availability), so the two layouts
  differ on non-default limits until it reads them.
- Out of slice 1: counting an untagged Store with charging/discharging Links (an imported PyPSA-Eur battery);
  it is only named today. Slice 2 or later.
- Slice 2 (open): `services/adequacy/portfolio.py`, `site_peak.py`, `smoothing.py`, `redundancy.py`, `levers.py`
  (`storage_duration`), `eh_readiness.py`, `activity.py`; the dispatch walk of `compare/economics.py`;
  `services/results/dispatch_digest.py`, `results/billing.py`, `results/load_shape.py`;
  `services/asset_results/compute.py` (StorageUnit energy capacity). Also open from slice 1: per-period vintages of
  a split storage's Store in storage cycling (today the Store's final size in every period).
