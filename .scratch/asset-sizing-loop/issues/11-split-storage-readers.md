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
- Slice 2 (open): `services/adequacy/portfolio.py`, `site_peak.py`, `smoothing.py`, `redundancy.py`, `levers.py`
  (`storage_duration`), `eh_readiness.py`, `activity.py`; the dispatch walk of `compare/economics.py`;
  `services/results/dispatch_digest.py`, `results/billing.py`, `results/load_shape.py`;
  `services/asset_results/compute.py` (StorageUnit energy capacity). Also open from slice 1: per-period vintages of
  a split storage's Store in storage cycling (today the Store's final size in every period).
