<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# `eh_setup._flag` reads the text "no" as True

Status: done
Type: bug
Found by: GK A review of #1388 (issue 27).

## Problem

`services/adequacy/eh_setup.py` `_flag` used plain `bool()`, so the text "no", "false" or "0" in `eh_poc`, `eh_critical` or `p_nom_extendable` read True. It now uses the one flag rule (`process_chain.truthy`), with the same guarded import and fallback as issue 27.

Acceptance:
- [x] `_flag` equals `process_chain.truthy` on the pinned table (`tests/test_eh_setup_flag.py`).
- [x] "no", "false", "0" read False (red on master before the fix).
