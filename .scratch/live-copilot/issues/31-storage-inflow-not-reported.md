<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-31 — no copilot tool reports a StorageUnit's inflow

Status: needs-triage

Found by LC-06 (7) while building S13 (LC-D09). A hydro StorageUnit's `inflow`
(static or `storage_units_t.inflow`) is an input the solve reads, but no
assistant tool reads it back:

- `get_results` kinds `storage_dispatch` and `storage` (`routers/results.py`) carry
  signed power and state of charge;
- `get_asset_results` (`services/asset_results/registry.py`) has `spill` and
  `spilled_mwh`, but no inflow metric.

So the copilot cannot state a reservoir's water balance (inflow = dispatch −
store + spill + Δ state of charge). LC-06 S13 checks spill through the seam
(`tests/test_lc06_d09_inflow_spill.py`). The family manifest marks
`LC-D09.inflow` unavailable, pointing at the registry.

Options for whoever takes this:
- An `inflow` series and an `inflow_mwh` scalar in the asset-results registry
  (StorageUnit, origin input), so the balance can be read back and checked.
- Leave it, and keep the manifest row unavailable.
