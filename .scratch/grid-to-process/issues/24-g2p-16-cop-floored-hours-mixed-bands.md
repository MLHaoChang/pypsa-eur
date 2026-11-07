<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# G2P-16: `cop_floored_hours` sums across ids when banded and unbanded pumps mix

Status: done
Type: bug
Found by: G2P-16 (heat-pump COP by band, `16-heat-pump-cop-by-band-part-load.md`); owner list DOCS-1.

## Problem

When a project has banded and unbanded heat pumps, the summed `cop_floored_hours` adds hours from different ids, so the warning reports a total that no single pump has. Report it per id, or sum only like with like, and test the mixed case. Details: ask the coordinator.

## Where to look

`pypsa-gui/backend/services/profile_gen/series.py` (~line 260 sets `extra["cop_floored_hours"]`, ~line 318 `floored += e["cop_floored_hours"]`); test file `tests/test_profile_gen_series.py`.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `pypsa-gui/backend/services/profile_gen/series.py` (~260, ~318), `tests/test_profile_gen_series.py`.
- Blocked by: none.
- Model tier: sonnet.
- Gated: GK A.
- Size: S.
- Report per id or sum like with like; test the mixed banded/unbanded case.
