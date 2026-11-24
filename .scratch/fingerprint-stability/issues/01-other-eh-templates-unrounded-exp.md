<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The other eh_* template modules still build profiles with unrounded array np.exp

Status: needs-triage
Size: S
Blocked by: none
Likely files: pypsa-gui/backend/project_templates/eh_site.py, eh_ipp.py, eh_concentrator.py, _build.py
Type: task
Found by: the d2a fix, #1881.

## Problem

Array `np.exp` (and `np.sin`/`np.cos`) differ in the last bit between CPU SIMD kernels. `_network_input_fingerprint(exact=True)` hashes `float.hex`, so a template built with them has an exact fingerprint that depends on the CPU. #1881 fixed this in `eh_templates.py` only, by rounding each profile to 9 decimals (`_cpu_stable`) right after the call.

The same pattern is unrounded in `eh_site.py` (lines 177-185, 568), `eh_ipp.py` (122-124), `eh_concentrator.py` (168-172) and `_build.py` (42-43). GK A and GK B4 (3) found no exact pin that reads them today, so nothing fails now. A future pin, or an exact-fingerprint comparison across machines, would.

## Expected

- Each profile in those files is rounded right after its transcendental call, with a comment saying why (CPU-dependent SIMD last bit). Reuse `_cpu_stable` rather than a second copy.
- A test that builds each template with array `np.exp` replaced by scalar `math.exp` and asserts the exact fingerprint is unchanged. On master's `eh_templates.py` this swap is what reproduced the CI value.
- Any pin that reads these templates is recomputed and listed old → new in the PR.

## Notes

GK B4 (3) also suggested a margin test (the closest pre-round value to a rounding boundary stays far above one ulp). Optional.
