<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-11 — Readers of split (Store + Link) storage

Status: done
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
- 2026-11-01, slice 2 PR A (adequacy): `site_peak.py` (ride-through), `smoothing.py`, `portfolio.py`
  (fingerprint), `redundancy.py` (undo restores Stores), `levers.py` (`storage_duration` sets a split Store to
  hours x P), `eh_readiness.py` (storage count, outage scan) and `activity.py` read split storage through
  `storage_inventory`. Folded in: the StorageUnit reader in `mc.snapshot_inputs` applies `p_max_pu` and
  `-p_min_pu` per block like the split Links (default limits bit-identical; a discharge-only StorageUnit is now
  named, not counted, as its migrated twin is); the blocked-Links note names both Links when they are zero in
  different blocks; a split storage whose discharger `bus1` is not its charger's `bus0` is named, not counted; a
  zonal Monte Carlo pair test. PR B (results readers) follows.
- GK A plan GO for slice 2 PR A (2026-11-01), conditions: (1) a split storage is in service only while its
  charger, discharger and Store all are (latest build year, earliest end of life, all active; test with a Store
  that retires first); (2) the fingerprint hashes what the split readers read (Links' p_max_pu/p_min_pu, static and
  time-varying, the Store's standing_loss, carrier, c_rate_max), adds nothing without split storage, and a test pins
  StorageUnit-only fingerprints to master's values; (3) the redundancy undo restores `stores` and `stores_t`;
  (4) `storage_duration` refuses by name a duration outside the technology's range or below 1/c_rate_max;
  (5) the StorageUnit availability reads static and time-varying p_max_pu/p_min_pu, default limits bit-identical,
  moved anchors listed in the PR body, a release-note line for the change; (6) deliverable energy uses one rule
  for both layouts from the inventory entry, the pair test checks P, E and deliverable MWh; (7) the readiness
  payload docstring names the "StorageUnit" key reuse; (8) existing adequacy suites unchanged at the PR head.
- 2026-11-01, slice 2 PR A (#1010, merged b4afcf56): adequacy readers (portfolio, site_peak, smoothing, redundancy,
  levers `storage_duration`, eh_readiness, activity) read split storage; StorageUnit availability reads
  p_max_pu/p_min_pu (inflow-fed hydro keeps dispatch-only availability, named in `storage_notes`); discharger
  bus1 == charger bus0 checked; zonal-MC split test. The no-asset `storage_duration` message keeps the
  "no StorageUnits" prefix that eh_study's soft-skip matches.
- 2026-11-01, slice 2 PR B (#1012, merged 563a0f1d): results readers (dispatch_digest, billing, load_shape, the
  compare/economics dispatch walk, storage-cycling vintages) read split storage via
  `storage_inventory.results_splits`. Tests: `tests/test_split_storage_results_readers.py`.
  `asset_results/compute.py` needs no change (StorageUnit metric; Stores have their own metric set).
- GK B conditions (PR B): billing settles a split under its Store name, refusing Store + discharger in one
  contract; one name rule `storage_inventory.results_splits` (a Store named like a StorageUnit, or discharger bus1
  != charger bus0, stays out of all five readers); discriminating fixture; mutants; no-split pin; Links picked by
  name; cycling vintages (flat without entry, p_nom_opt fallback).
- GK A conditions (PR B, compare/economics): capex via each row's rule under the Store carrier; VOM includes the
  Store's marginal_cost; StorageUnit sign convention; 2-period pair; untagged batteries unchanged; reconciliation
  with Capacity.
- Follow-ups (not opened): a typed `LeverNotApplicable` instead of eh_study's substring soft-skip; pumped hydro
  with inflow loses its charge-side derate; `storage_duration` repeats `add_split_storage`'s checks (shared
  helper); site_peak ignores a StorageUnit's own active flag (as on master); untagged PyPSA-Eur battery Stores are
  only named; `compare/dispatch.py` (slice 1) does not apply `results_splits`' name/cross-bus rule; neither layout
  books `marginal_cost_storage` in economics; load_shape double-counts SoC on a StorageUnit/Store name collision
  (as on master).
- 2026-11-02, results/compare follow-ups (branch `claude/as11-results-followups`): `compare/dispatch.py` reads
  `results_splits` via `split_signed_ac_p`; compare economics books `marginal_cost_storage` (StorageUnit SoC,
  split Store `e`) in opex/gen_cost; load_shape tells a StorageUnit from a same-named Store (`_Storage.comp`).
- GK A plan GO (item 2, compare economics), conditions: (a) the VOM's cost weights (period weighting included),
  a 2-period case checked by hand (n.statistics.opex leaves the period weighting out); (b) migrate_storage_unit
  refuses marginal_cost_storage by name, so the fixture sets it on the Store after migration and asserts equal
  objectives; carrying it in the migration is a follow-up for split_storage.py's owner; (c) only StorageUnits and
  results_splits Stores, untagged Stores unbooked; (d) mcs 0/absent byte-identical, non-finite skipped;
  (e) mutants: unweighted, Store p for e, split booked twice; (f) release-note line.
- GK B plan GO (items 1 and 3), conditions: item 1 signed p from `split_signed_ac_p`, collision and cross-bus stay
  out, split == StorageUnit within REL, no-split byte-equal to master, mutant discharger p0 for -p1; item 3 the
  double-count pin becomes the correct value (named in the PR body and release note), `_Storage` consumers listed,
  no-collision byte-equal, mutant ignoring `comp`.
- GK A plan verdict (adequacy follow-ups PR): GO on 1–3 and 5(a), NO-GO on 4's derate rule.
  1. `levers.LeverNotApplicable(LeverScenarioError)` at the three asset-absence sites (wording unchanged);
     eh_study soft-skips by type, no substring check left (the only one was eh_study's); `<= 0` and the
     infeasible-split error stay plain; handlers keyed on `LeverScenarioError` catch the subclass unchanged.
  2. `split_storage.duration_problem` shared by `add_split_storage` and `storage_duration`; a missing/NaN
     `c_rate_max` still skips the C-rate check (no behaviour change); add_split_storage's tests unchanged.
  3. site_peak `committed_storage`: one `live` rule for both layouts; a StorageUnit's own `active` false is out
     of service; a missing `active` column means active.
  4. Closed by design (monotonic): every inflow-fed unit keeps charge side 1.0, so adding a pump never lowers
     adequacy below the pure reservoir; pin test (p_min_pu -0.5 and 0, with inflow, count the pre-AS-11 P and E).
  5. Declined: untagged PyPSA-Eur battery Stores stay named, not counted; carrier/bus-shape matching would
     silently change results for every imported network. Migrate/tag to count them.
